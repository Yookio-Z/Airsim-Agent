"""
Replay 播放器 - 回放记录的遥测数据
支持无硬件/无仿真环境下的开发与测试
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.logging_config import get_logger

logger = get_logger(__name__)


class ReplayPlayer:
    """回放记录的仿真会话数据"""

    def __init__(self, session_dir: str) -> None:
        self.session_dir = Path(session_dir)
        self._telemetry_frames: list[dict[str, Any]] = []
        self._airsim_frames: list[dict[str, Any]] = []
        self._playing = False
        self._index = 0
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []

    def load(self) -> bool:
        """加载记录文件"""
        telem_file = self.session_dir / "telemetry.jsonl"
        airsim_file = self.session_dir / "airsim.jsonl"

        if telem_file.exists():
            with open(telem_file) as f:
                self._telemetry_frames = [json.loads(line) for line in f if line.strip()]

        if airsim_file.exists():
            with open(airsim_file) as f:
                self._airsim_frames = [json.loads(line) for line in f if line.strip()]

        loaded = len(self._telemetry_frames) + len(self._airsim_frames)
        logger.info(f"Replay loaded {loaded} frames from {self.session_dir}")
        return loaded > 0

    def play(self, speed: float = 1.0) -> None:
        """按记录时序回放"""
        if not self._telemetry_frames:
            logger.warning("No telemetry frames to replay")
            return

        self._playing = True
        self._index = 0
        start_time = time.time()
        first_ts = self._telemetry_frames[0].get("ts", 0)

        logger.info(f"Replay started at {speed}x speed")

        while self._playing and self._index < len(self._telemetry_frames):
            frame = self._telemetry_frames[self._index]
            target_ts = (frame["ts"] - first_ts) / speed
            elapsed = time.time() - start_time

            if elapsed < target_ts:
                time.sleep(0.01)
                continue

            for cb in self._callbacks:
                cb(frame["data"])

            self._index += 1

        self._playing = False
        logger.info("Replay finished")

    def step(self) -> dict[str, Any] | None:
        """单步回放一帧"""
        if self._index >= len(self._telemetry_frames):
            return None
        frame = self._telemetry_frames[self._index]
        self._index += 1
        return frame["data"]

    def seek(self, index: int) -> None:
        """跳转到指定帧"""
        self._index = max(0, min(index, len(self._telemetry_frames) - 1))

    def stop(self) -> None:
        """停止回放"""
        self._playing = False

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """订阅回放数据回调"""
        self._callbacks.append(callback)

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def frame_count(self) -> int:
        return len(self._telemetry_frames)

    @property
    def current_index(self) -> int:
        return self._index
