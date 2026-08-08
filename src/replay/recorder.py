"""
Replay 记录器 - 记录 AirSim 状态和 MAVLink 遥测数据
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from src.logging_config import get_logger

logger = get_logger(__name__)


class ReplayRecorder:
    """记录仿真会话的遥测数据，用于后续回放"""

    def __init__(self, session_name: str | None = None, data_dir: str | None = None) -> None:
        self.session_name = session_name or f"session_{int(time.time())}"
        self.data_dir = Path(data_dir or os.path.expanduser("~/src/replay_data"))
        self.session_dir = self.data_dir / self.session_name
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._telemetry_file: Any | None = None
        self._airsim_file: Any | None = None
        self._recording = False
        self._start_time: float = 0.0

    def start(self) -> None:
        """开始记录"""
        if self._recording:
            return
        self._start_time = time.time()
        self._telemetry_file = open(self.session_dir / "telemetry.jsonl", "w")
        self._airsim_file = open(self.session_dir / "airsim.jsonl", "w")
        self._recording = True
        logger.info(f"Replay recording started: {self.session_dir}")

    def record_telemetry(self, telemetry: dict[str, Any]) -> None:
        """记录 MAVLink 遥测帧"""
        if not self._recording or self._telemetry_file is None:
            return
        frame = {
            "ts": time.time() - self._start_time,
            "data": telemetry,
        }
        self._telemetry_file.write(json.dumps(frame, default=str) + "\n")
        self._telemetry_file.flush()

    def record_airsim_state(self, state: dict[str, Any]) -> None:
        """记录 AirSim 状态帧"""
        if not self._recording or self._airsim_file is None:
            return
        frame = {
            "ts": time.time() - self._start_time,
            "data": state,
        }
        self._airsim_file.write(json.dumps(frame, default=str) + "\n")
        self._airsim_file.flush()

    def stop(self) -> None:
        """停止记录"""
        if not self._recording:
            return
        self._recording = False
        if self._telemetry_file:
            self._telemetry_file.close()
        if self._airsim_file:
            self._airsim_file.close()
        logger.info(f"Replay recording stopped: {self.session_dir}")

    def __enter__(self) -> ReplayRecorder:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
