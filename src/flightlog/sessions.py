"""飞行记录会话：周期性采样 + 会话列表/读取 + 保留策略。

`FlightLogSession` 带一个守护线程，按固定间隔向快照提供方要一帧并落盘（任务执行与
手动飞行都用它）。`list_sessions()` / `read_session()` 是给界面与以后的分析工具用的
只读入口；两者都做路径穿越校验。

会话目录就是 `data/flightlog/<name>/`，每次飞行一个，不会互相追加。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.logging_config import get_logger

from .schema import build_frame
from .writer import DEFAULT_FLIGHTLOG_DIR, FlightLogWriter, read_frames, safe_session_name

logger = get_logger(__name__)

SnapshotProvider = Callable[[], dict[str, Any]]

# 列表与读取的上限：单次读取最多给这么多帧（最后 N 帧），避免把整个会话塞进内存。
DEFAULT_READ_FRAME_LIMIT = 5000
# 保留策略：最多保留这么多个会话，超出按开始时间删最旧的（0 = 不清理）。
DEFAULT_MAX_SESSIONS = 200


class FlightLogSession:
    """一次飞行记录的生命周期（start → 周期性采样 → stop 写 summary）。"""

    def __init__(
        self,
        name: str,
        snapshot_provider: SnapshotProvider,
        interval: float = 0.2,
        meta: dict[str, Any] | None = None,
        data_dir: str | None = None,
    ) -> None:
        self._provider = snapshot_provider
        self.interval = max(0.05, float(interval))
        self._writer = FlightLogWriter(
            name, meta=meta, data_dir=data_dir, interval_s=self.interval
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._error = ""

    @property
    def name(self) -> str:
        return self._writer.name

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._writer._frames  # noqa: SLF001 — 同模块内的写入器

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._writer.start()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._poll, name=f"flightlog-{self.name}", daemon=True
            )
            self._thread.start()
        logger.info("flightlog_started", name=self.name)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop.set()
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval * 5))
        summary = self._writer.stop()
        _update_index(self._writer.session_dir.parent, self.name, summary, enforce_retention=True)
        logger.info("flightlog_stopped", name=self.name, frames=summary.get("frame_count", 0))
        return summary

    def _poll(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                snapshot = self._provider() or {}
                self._writer.record_frame(build_frame(snapshot))
            except Exception as exc:  # noqa: BLE001 — 采样失败不能拖垮飞行
                self._error = str(exc)
                logger.warning("flightlog_frame_failed", error=str(exc))


# ---------------------------------------------------------------------------
# 只读入口
# ---------------------------------------------------------------------------


def _session_dir(data_dir: str | Path | None, name: str) -> Path | None:
    """把会话名解析成目录；非法名字/越界路径返回 None。"""
    base = Path(data_dir or DEFAULT_FLIGHTLOG_DIR)
    cleaned = safe_session_name(name)
    if cleaned != str(name or "").strip():
        return None  # 只在名字本来就安全时接受，避免 ".." 之类被清洗后命中别的目录
    target = (base / cleaned).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target if target.is_dir() else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _index_path(data_dir: Path) -> Path:
    return Path(data_dir) / "index.json"


def _load_index(data_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(_index_path(data_dir))
    rows = data.get("sessions")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _update_index(
    data_dir: Path, name: str, summary: dict[str, Any], enforce_retention: bool = False
) -> None:
    """把一次会话的摘要合并进 index.json（按 name 去重，失败不影响数据文件）。"""
    try:
        rows = [row for row in _load_index(data_dir) if row.get("name") != name]
        rows.append(
            {
                "name": name,
                "schema_version": summary.get("schema_version"),
                "started_at": summary.get("started_at"),
                "finished_at": summary.get("finished_at"),
                "duration_s": summary.get("duration_s"),
                "frame_count": summary.get("frame_count"),
                "distance_m": summary.get("distance_m"),
                "summary": summary,
            }
        )
        rows.sort(key=lambda row: float(row.get("started_at") or 0.0))
        if enforce_retention and DEFAULT_MAX_SESSIONS > 0 and len(rows) > DEFAULT_MAX_SESSIONS:
            for stale in rows[: len(rows) - DEFAULT_MAX_SESSIONS]:
                _remove_dir(data_dir / str(stale.get("name") or ""))
            rows = rows[-DEFAULT_MAX_SESSIONS:]
        tmp = _index_path(data_dir).with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"sessions": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_index_path(data_dir))
    except Exception as exc:  # noqa: BLE001 — 索引只是加速，坏了不能影响录制
        logger.warning("flightlog_index_failed", error=str(exc))


def _remove_dir(path: Path) -> None:
    try:
        if path.is_dir():
            for child in path.iterdir():
                if child.is_file():
                    child.unlink()
            path.rmdir()
    except OSError:
        pass


def list_sessions(data_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """列出所有飞行记录（新 → 旧）：名称、时间、时长、帧数、摘要。"""
    base = Path(data_dir or DEFAULT_FLIGHTLOG_DIR)
    rows = _load_index(base)
    if rows:
        return sorted(rows, key=lambda row: float(row.get("started_at") or 0.0), reverse=True)
    # 没有索引（老数据/索引被删）：扫目录，用 meta+summary 兜底
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for child in base.iterdir():
        if not child.is_dir():
            continue
        meta = _read_json(child / "meta.json")
        summary = _read_json(child / "summary.json")
        out.append(
            {
                "name": child.name,
                "schema_version": meta.get("schema_version"),
                "started_at": summary.get("started_at") or meta.get("created_at"),
                "finished_at": summary.get("finished_at"),
                "duration_s": summary.get("duration_s"),
                "frame_count": summary.get("frame_count"),
                "distance_m": summary.get("distance_m"),
                "summary": summary or meta,
            }
        )
    return sorted(out, key=lambda row: float(row.get("started_at") or 0.0), reverse=True)


def read_session(
    name: str,
    data_dir: str | Path | None = None,
    max_frames: int = DEFAULT_READ_FRAME_LIMIT,
) -> dict[str, Any] | None:
    """读取单个会话：meta + summary + 最后 N 帧。"""
    session_dir = _session_dir(data_dir, name)
    if session_dir is None:
        return None
    meta = _read_json(session_dir / "meta.json")
    summary = _read_json(session_dir / "summary.json")
    frames = read_frames(session_dir, max_frames=max_frames)
    return {
        "name": session_dir.name,
        "meta": meta,
        "summary": summary,
        "frames": frames,
        "frame_limit": max_frames,
    }
