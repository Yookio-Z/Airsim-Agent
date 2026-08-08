"""
Basic Blueprint - 基础连接层
组合 AirSim RPC 连接和 MAVLink/pymavlink 连接
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.client_manager import AirSimClientManager
from src.modules.mavlink_connection import MavlinkConnection
from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class BasicBlueprint:
    """基础蓝图：管理 AirSim 和 MAVLink 连接生命周期"""

    airsim_ip: str = "127.0.0.1"
    airsim_port: int = 41451
    mavlink_url: str = "udp:127.0.0.1:14540"
    outdoor: bool = False
    max_velocity: float = 5.0

    _airsim_mgr: AirSimClientManager | None = field(default=None, repr=False)
    _mavlink: MavlinkConnection | None = field(default=None, repr=False)

    @property
    def airsim(self) -> AirSimClientManager:
        if self._airsim_mgr is None:
            self._airsim_mgr = AirSimClientManager.get()
        return self._airsim_mgr

    @property
    def mavlink(self) -> MavlinkConnection:
        if self._mavlink is None:
            self._mavlink = MavlinkConnection(
                connection_string=self.mavlink_url,
                outdoor=self.outdoor,
                max_velocity=self.max_velocity,
            )
        return self._mavlink

    def connect_airsim(self) -> dict:
        """连接 AirSim"""
        return self.airsim.connect(self.airsim_ip, self.airsim_port)

    def connect_mavlink(self) -> bool:
        """连接 MAVLink"""
        return self.mavlink.connect()

    def connect_all(self) -> dict:
        """连接所有服务"""
        results = {}
        results["airsim"] = self.connect_airsim()
        results["mavlink"] = self.connect_mavlink()
        logger.info(f"Blueprint connected: {results}")
        return results

    def disconnect_all(self) -> None:
        """断开所有连接"""
        self.airsim.disconnect()
        if self._mavlink:
            self._mavlink.disconnect()
        logger.info("Blueprint disconnected")

    def __enter__(self) -> BasicBlueprint:
        self.connect_all()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect_all()
