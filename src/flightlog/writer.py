"""飞行记录的写盘：一个会话一个目录。

目录布局（每次飞行一条，绝不互相追加）::

    data/flightlog/<session_name>/
        meta.json        ← schema 版本、run_id/指令、后端、机型、采样率、起止时间
        frames.jsonl     ← 每行一帧 {t: 相对秒, data: {...}}
        summary.json     ← 停止时写一次：时长、里程、高度/速度极值、状态计数

写入用"临时文件 + replace"保证 meta/summary 不会出现半截文件；frames 逐行 flush，
进程被杀也最多丢最后一帧。目录名只接受安全字符（由 `safe_session_name` 统一清洗），
读取侧还会再做一次路径穿越校验。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .schema import FLIGHTLOG_SCHEMA_VERSION, summarize_frames

from src.paths import DATA_HOME

DEFAULT_FLIGHTLOG_DIR = DATA_HOME / "flightlog"

_SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def safe_session_name(name: str) -> str:
    """会话名清洗：只留字母数字与 ._-，其余替换为 _；空名回落到时间戳。"""
    raw = str(name or "").strip()
    cleaned = "".join(ch if ch in _SAFE_NAME else "_" for ch in raw)[:120].strip("._-")
    return cleaned or f"flight_{int(time.time())}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class FlightLogWriter:
    """一次飞行记录的写盘器（不含线程；线程在 sessions.py）。"""

    def __init__(
        self,
        session_name: str,
        meta: dict[str, Any] | None = None,
        data_dir: str | Path | None = None,
        interval_s: float = 0.2,
    ) -> None:
        self.name = safe_session_name(session_name)
        self.session_dir = Path(data_dir or DEFAULT_FLIGHTLOG_DIR) / self.name
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.interval_s = float(interval_s)
        self.started_at = 0.0
        self.finished_at = 0.0
        self._file: Any | None = None
        self._frames = 0
        self._sample = {
            "schema_version": FLIGHTLOG_SCHEMA_VERSION,
            "name": self.name,
            "sample_interval_s": self.interval_s,
            "created_at": time.time(),
            **(meta or {}),
        }
        _atomic_write_json(self.session_dir / "meta.json", self._sample)

    def start(self) -> None:
        if self._file is not None:
            return
        self.started_at = time.time()
        self._file = open(self.session_dir / "frames.jsonl", "w", encoding="utf-8")

    # 兼容旧调用方的名字
    def write_meta(self, meta: dict[str, Any]) -> None:
        self._sample.update(meta or {})
        _atomic_write_json(self.session_dir / "meta.json", self._sample)

    def record_frame(self, data: dict[str, Any]) -> None:
        if self._file is None:
            return
        frame = {"t": round(time.time() - self.started_at, 3), "data": data}
        self._file.write(json.dumps(frame, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
        self._frames += 1

    def stop(self) -> dict[str, Any]:
        """关闭文件、写 summary，返回本次会话摘要。"""
        if self._file is not None:
            self._file.close()
            self._file = None
        self.finished_at = time.time()
        frames = read_frames(self.session_dir, max_frames=None)
        summary = summarize_frames(frames, duration_s=self.finished_at - self.started_at)
        summary.update(
            {
                "schema_version": FLIGHTLOG_SCHEMA_VERSION,
                "name": self.name,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
        )
        _atomic_write_json(self.session_dir / "summary.json", summary)
        return summary


def read_frames(session_dir: Path, max_frames: int | None = 5000) -> list[dict[str, Any]]:
    """读 frames.jsonl；max_frames=None 读全部，否则取**最后** N 帧。"""
    path = Path(session_dir) / "frames.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 最后一行可能被截断，跳过而不是整份报废
    except OSError:
        return []
    if max_frames is not None and max_frames > 0:
        return rows[-max_frames:]
    return rows
