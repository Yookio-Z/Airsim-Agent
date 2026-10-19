"""
核心模块层（按职责分四个子包 + 安全验证）
- backends/:  飞行控制后端——抽象契约 base + AirSim / PX4 MAVLink / ROS2 网关实现，共用朝向/链路工具
- perception/: 感知——帧源（AirSim / RTSP / USB）、深度投影、配置档、相机辅助
- formation/:  多机编队与区域覆盖（确定性 10Hz 控制器）
- dormant/:    冻结能力（目标检测/跟踪旧路径，保留不用）
- safety_validator.py: 飞行安全验证层
"""

from src.modules.backends.base import FlightController, DroneStatus, ConnectionInfo
from src.modules.safety_validator import SafetyValidator, FlightConstraint, ValidationResult, validate_and_execute

def __getattr__(name: str):
    if name == "AirSimController":
        from src.modules.backends.airsim import AirSimController

        return AirSimController
    if name == "MavlinkController":
        from src.modules.backends.mavlink import MavlinkController

        return MavlinkController
    raise AttributeError(name)


__all__ = [
    "FlightController",
    "DroneStatus",
    "ConnectionInfo",
    "AirSimController",
    "MavlinkController",
    "SafetyValidator",
    "FlightConstraint",
    "ValidationResult",
    "validate_and_execute",
]
