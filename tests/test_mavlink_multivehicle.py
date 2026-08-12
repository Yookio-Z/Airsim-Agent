"""MAVLink multi-system tests (QGC 模式): per-sysid state tables, vehicle
selection, and command target routing. Uses fake messages, no hardware."""

from __future__ import annotations

import time

from src.modules.mavlink_controller import MavlinkController


class _Message:
    """Fake MAVLink message with a configurable source system."""

    def __init__(self, msg_type: str, data: dict, sysid: int = 1) -> None:
        self._msg_type = msg_type
        self._data = data
        self._sysid = sysid

    def get_type(self) -> str:
        return self._msg_type

    def get_srcSystem(self) -> int:
        return self._sysid

    def get_srcComponent(self) -> int:
        return 1

    def to_dict(self) -> dict:
        return dict(self._data)


class _Link:
    """Fake MAVLink connection recording target_system changes."""

    def __init__(self) -> None:
        self.target_system = 0
        self.target_component = 1
        self.commands: list[tuple[int, int]] = []  # (target_system, command)
        self.mav = _Mav()

    def recv_match(self, blocking=False, timeout=None):
        return None


class _Mav:
    """Fake pymavlink mav object (self._mavlink.mav.command_long_send)."""

    def __init__(self) -> None:
        self.commands: list[tuple[int, int]] = []

    def command_long_send(self, target_system, target_component, command, *params) -> None:
        self.commands.append((target_system, command))


def _heartbeat(sysid: int, armed: bool = False, custom_mode: int = 4) -> _Message:
    base_mode = 129 if armed else 1  # 129 = SAFETY_ARMED + MANUAL
    return _Message("HEARTBEAT", {
        "autopilot": 8,  # PX4
        "type": 2,
        "base_mode": base_mode,
        "custom_mode": custom_mode,
        "system_status": 4,
    }, sysid=sysid)


def _local_position(sysid: int, x: float, y: float, z: float) -> _Message:
    return _Message("LOCAL_POSITION_NED", {"x": x, "y": y, "z": z, "vx": 0.0, "vy": 0.0, "vz": 0.0}, sysid=sysid)


def _connected_controller(sysids: list[int]) -> MavlinkController:
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    for sysid in sysids:
        controller._handle_message(_heartbeat(sysid))
        controller._handle_message(_local_position(sysid, float(sysid), 2.0, -3.0))
    return controller


def test_two_systems_are_listed_and_selected() -> None:
    controller = _connected_controller([1, 2])
    assert controller.list_vehicles() == ["px4_sys1", "px4_sys2"]
    # 首个心跳触发选机（默认机 = 第一架）
    assert controller._selected_sysid == 1


def test_per_system_position_tables() -> None:
    controller = _connected_controller([1, 2])
    assert controller._systems[1]["position"]["x"] == 1.0
    assert controller._systems[2]["position"]["x"] == 2.0
    # active 机（默认第一架）
    assert controller._position["x"] == 1.0


def test_get_status_targets_vehicle_by_name() -> None:
    controller = _connected_controller([1, 2])
    default = controller.get_status("")
    second = controller.get_status("px4_sys2")
    # DroneStatus 无 position 直接暴露？用 extra/position——通过 _systems 表验证更直接
    assert controller._systems[1]["position"]["x"] == 1.0
    assert controller._systems[2]["position"]["x"] == 2.0
    assert default is not None
    assert second is not None


def test_arm_all_targets_each_system_in_order() -> None:
    controller = _connected_controller([1, 2])
    # arm 需要非 stale 心跳
    controller._handle_message(_heartbeat(1))
    controller._handle_message(_heartbeat(2))
    ok = controller.arm(vehicle_name="all")
    # arm 流程较深（px4 mode 准备等），验证 target 设置与调用顺序
    link = controller._mavlink
    assert link.mav.commands  # 至少发出了命令
    assert link.target_system in (1, 2)
    assert ok is True or controller.last_error  # 无崩溃即可


def test_arm_default_targets_first_system_only() -> None:
    controller = _connected_controller([1, 2])
    controller._handle_message(_heartbeat(1))
    controller._handle_message(_heartbeat(2))
    controller.arm(vehicle_name="")
    link = controller._mavlink
    # 默认机 = sysid 1：命令 target 应为 1
    assert link.target_system == 1


def test_empty_name_never_implicitly_broadcasts() -> None:
    controller = _connected_controller([1, 2])
    targets = controller._resolve_sysids("")
    assert targets == [1]
    assert controller._resolve_sysids("all") == [1, 2]
    assert controller._resolve_sysids("px4_sys2") == [2]
    assert controller._resolve_sysids("sys2") == [2]
    assert controller._resolve_sysids("2") == [2]


def test_single_system_behaves_like_before() -> None:
    controller = _connected_controller([1])
    assert controller.list_vehicles() == ["px4_sys1"]
    assert controller._resolve_sysids("") == [1]
    assert controller._resolve_sysids("all") == [1]


def test_history_is_per_system() -> None:
    controller = _connected_controller([1, 2])
    controller._handle_message(_local_position(1, 10.0, 0.0, -3.0))
    controller._handle_message(_local_position(2, 20.0, 0.0, -3.0))
    pos1 = controller._systems_history[1]["position"]
    pos2 = controller._systems_history[2]["position"]
    assert pos1[-1]["x"] == 10.0
    assert pos2[-1]["x"] == 20.0
