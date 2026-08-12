"""ReplaySession — 周期性录制遥测快照，供回放/任务复盘使用。

与 ReplayRecorder 的区别：ReplaySession 自带一个守护线程，以固定频率
轮询快照提供方（如 ``ToolRuntime.status_snapshot``），把每次任务执行或
手动飞行的完整时间线写入 ``src/data/replay/<session>/telemetry.jsonl``。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.logging_config import get_logger
from src.replay.recorder import DEFAULT_REPLAY_DIR, ReplayRecorder

logger = get_logger(__name__)

SnapshotProvider = Callable[[], dict[str, Any]]

# Frames served per session (5 Hz * 10 min ≈ 3000); listing stays cheap.
MAX_FRAMES_PER_SESSION = 3600


@dataclass
class ReplaySessionSummary:
    """一次录制会话的统计摘要。"""

    name: str
    started_at: float
    finished_at: float
    frame_count: int
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(max(0.0, self.finished_at - self.started_at), 2),
            "frame_count": self.frame_count,
            "meta": self.meta,
            "error": self.error,
        }


class ReplaySession:
    """周期轮询快照提供方并落盘，可围绕一次任务或手动飞行启动/停止。

    轮询线程是 daemon：即使调用方忘记 stop()，也不会阻塞进程退出。
    """

    def __init__(
        self,
        name: str,
        snapshot_provider: SnapshotProvider,
        interval: float = 0.2,
        meta: dict[str, Any] | None = None,
        data_dir: str | None = None,
    ) -> None:
        self.name = name
        self._provider = snapshot_provider
        self.interval = max(0.05, float(interval))
        self._recorder = ReplayRecorder(session_name=name, data_dir=data_dir)
        self._recorder.write_meta(meta or {})
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._finished_at = 0.0
        self._frame_count = 0
        self._error = ""

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._recorder.start()
            self._stop.clear()
            self._started_at = time.time()
            self._thread = threading.Thread(
                target=self._poll,
                name=f"replay-{self.name}",
                daemon=True,
            )
            self._thread.start()
        logger.info("replay_session_started", name=self.name)

    def stop(self) -> ReplaySessionSummary:
        with self._lock:
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval * 4))
        with self._lock:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._finished_at = time.time()
            summary = self.summary()
        logger.info("replay_session_stopped", name=self.name, frames=summary.frame_count)
        return summary

    def summary(self) -> ReplaySessionSummary:
        with self._lock:
            return ReplaySessionSummary(
                name=self.name,
                started_at=self._started_at,
                finished_at=self._finished_at,
                frame_count=self._frame_count,
                meta=self._recorder.session_dir.joinpath("run.json").exists()
                and _read_json(self._recorder.session_dir / "run.json") or {},
                error=self._error,
            )

    def _poll(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                frame = self._provider()
                if isinstance(frame, dict) and frame:
                    self._recorder.record_telemetry(frame)
                    with self._lock:
                        self._frame_count += 1
            except Exception as exc:
                # 快照提供方异常不应无限刷日志：记录一次并结束会话。
                with self._lock:
                    self._error = str(exc)
                logger.warning("replay_snapshot_failed", name=self.name, error=str(exc))
                break


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_replay_sessions(data_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """列出所有录制会话：名称、帧数、元数据与起止时间。"""
    root = Path(data_dir or DEFAULT_REPLAY_DIR)
    if not root.is_dir():
        return []
    sessions: list[dict[str, Any]] = []
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        meta = _read_json(session_dir / "run.json")
        frame_count = _count_lines(session_dir / "telemetry.jsonl")
        sessions.append(
            {
                "name": session_dir.name,
                "frame_count": frame_count,
                "meta": meta,
                "path": str(session_dir),
            }
        )
    return sessions


def read_replay_session(
    name: str,
    data_dir: str | Path | None = None,
    max_frames: int = MAX_FRAMES_PER_SESSION,
) -> dict[str, Any] | None:
    """读取单个会话：元数据 + 遥测帧（限帧，时间戳为录制相对时间）。"""
    root = Path(data_dir or DEFAULT_REPLAY_DIR).resolve()
    # Resolve-then-compare: catches "..", absolute paths and empty names.
    session_dir = (root / str(name or "")).resolve()
    if session_dir.parent != root or not session_dir.is_dir():
        return None
    meta = _read_json(session_dir / "run.json")
    frames = _read_frames(session_dir / "telemetry.jsonl", max_frames=max_frames)
    return {
        "name": session_dir.name,
        "meta": meta,
        "frame_count": len(frames),
        "frames": frames,
    }


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8") if _.strip())
    except Exception:
        return 0


def _read_frames(path: Path, max_frames: int) -> list[dict[str, Any]]:
    try:
        lines = [line for line in path.open(encoding="utf-8") if line.strip()]
    except Exception:
        return []
    frames: list[dict[str, Any]] = []
    for line in lines[-max_frames:]:
        try:
            frames.append(json.loads(line))
        except Exception:
            continue
    return frames
