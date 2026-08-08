"""
核心模块层
- FlightController: 飞行控制抽象接口
- AirSimController: AirSim RPC 实现
- MavlinkController: pymavlink 实现
- SafetyValidator: 飞行安全验证层
"""

from .flight_controller import FlightController, DroneStatus, ConnectionInfo
from .safety_validator import SafetyValidator, FlightConstraint, ValidationResult, validate_and_execute

def __getattr__(name: str):
    if name == "AirSimController":
        from .airsim_controller import AirSimController

        return AirSimController
    if name == "MavlinkController":
        from .mavlink_controller import MavlinkController

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
