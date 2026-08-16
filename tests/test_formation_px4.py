"""PX4 MAVLink formation protocol tests.

Covers the duck-typed velocity-control protocol on FormationController (fake
PX4-style controller) and the four new MavlinkController methods (fake link,
no hardware).
"""

from __future__ import annotations

import time

from src.modules.formation import FormationController
from src.modules.mavlink_controller import MavlinkController


# ---------------------------------------------------------------------------
# fake PX4-style controller implementing the duck-typed protocol
# ---------------------------------------------------------------------------


class _Px4Status:
    def __init__(self, position: dict, flying: bool = True, offboard: bool = True) -> None:
        self._position = position
        self._flying = flying
        self._offboard = offboard

    def to_dict(self) -> dict:
        return {
            "position_ned": dict(self._position),
            "flying": self._flying,
            "mode": "OFFBOARD" if self._offboard else "LOITER",
        }


class _Px4FakeController:
    def __init__(self, vehicles: dict | None = None) -> None:
        self.positions: dict[str, dict] = vehicles or {
            "px4_sys1": {"x": 0.0, "y": 0.0, "z": -10.0},
            "px4_sys2": {"x": 0.0, "y": 0.0, "z": -10.0},
        }
        self.offboard: dict[str, bool] = {name: True for name in self.positions}
        self.prepared: list[str] = []
        self.released: list[str] = []
        self.hovered: list[str] = []
        self.velocity_setpoints: list[tuple[str, float, float, float]] = []
        self.move_by_velocity_calls: list[tuple[str, float, float, float]] = []
        self.prepare_fail: set[str] = set()

    def list_vehicles(self) -> list[str]:
        return list(self.positions)

    def get_status(self, vehicle_name: str = ""):
        return _Px4Status(
            self.positions.get(vehicle_name, {"x": 0, "y": 0, "z": -10}),
            offboard=self.offboard.get(vehicle_name, True),
        )

    def arm(self, vehicle_name: str = "") -> bool:
        return True

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.positions[vehicle_name]["z"] = -abs(altitude)
        return True

    def land(self, vehicle_name: str = "") -> bool:
        return True

    def hover(self, vehicle_name: str = "") -> bool:
        self.hovered.append(vehicle_name)
        return True

    def send_velocity_setpoint(self, vx, vy, vz, vehicle_name: str = "") -> bool:
        self.velocity_setpoints.append((vehicle_name, vx, vy, vz))
        return True

    def prepare_velocity_control(self, vehicle_name: str = "") -> bool:
        self.prepared.append(vehicle_name)
        return vehicle_name not in self.prepare_fail

    def is_velocity_control_active(self, vehicle_name: str = "") -> bool:
        return self.offboard.get(vehicle_name, True)

    def release_velocity_control(self, vehicle_name: str = "") -> bool:
        self.released.append(vehicle_name)
        return True

    def move_by_velocity(self, vx, vy, vz, duration=0.0, vehicle_name: str = "") -> bool:
        self.move_by_velocity_calls.append((vehicle_name, vx, vy, vz))
        return True


def _px4_formation(controller: _Px4FakeController) -> FormationController:
    fc = FormationController(controller)
    fc.set_drones(list(controller.positions))
    fc.set_formation("line", spacing=5.0)
    return fc


def test_px4_takeoff_prepares_offboard_per_vehicle():
    controller = _Px4FakeController()
    fc = _px4_formation(controller)
    result = fc.takeoff(altitude=10.0)
    assert result["status"] == "ok"
    assert result["mode"] == "formation"
    assert sorted(controller.prepared) == ["px4_sys1", "px4_sys2"]


def test_px4_prepare_failure_aborts_formation():
    controller = _Px4FakeController()
    controller.prepare_fail = {"px4_sys2"}
    fc = _px4_formation(controller)
    result = fc.takeoff(altitude=10.0)
    assert result["status"] == "error"
    assert "OFFBOARD" in result["message"]
    assert fc.mode == "idle"
    assert sorted(controller.hovered) == ["px4_sys1", "px4_sys2"]  # hovered back
    assert any(event["type"] == "prepare_failed" for event in fc.events)


def test_px4_tick_uses_single_setpoint_stream():
    controller = _Px4FakeController()
    fc = _px4_formation(controller)
    fc.takeoff(altitude=10.0)
    fc.move_center(5.0, 0.0)
    fc.tick()
    assert controller.velocity_setpoints, "must send via send_velocity_setpoint"
    assert controller.move_by_velocity_calls == [], "must NOT fall back to move_by_velocity"
    names = {name for name, _, _, _ in controller.velocity_setpoints}
    assert names == {"px4_sys1", "px4_sys2"}


def test_px4_mode_lost_stops_loop_and_hovers():
    controller = _Px4FakeController()
    fc = _px4_formation(controller)
    fc.takeoff(altitude=10.0)
    controller.offboard["px4_sys2"] = False  # RC takeover / mode switch
    fc.tick()
    assert fc.mode == "idle"
    assert sorted(controller.hovered) == ["px4_sys1", "px4_sys2"]
    assert any(event["type"] == "mode_lost" for event in fc.events)
    assert fc._running is False  # thread stopped


def test_px4_hover_and_shutdown_release_offboard():
    controller = _Px4FakeController()
    fc = _px4_formation(controller)
    fc.takeoff(altitude=10.0)
    fc.hover_all()
    assert sorted(controller.released) == ["px4_sys1", "px4_sys2"]
    controller.released.clear()
    fc.set_formation("line", spacing=5.0)
    fc.takeoff(altitude=10.0)
    fc.shutdown("test")
    assert sorted(controller.released) == ["px4_sys1", "px4_sys2"]


# ---------------------------------------------------------------------------
# MavlinkController protocol methods (fake link)
# ---------------------------------------------------------------------------


class _Mav:
    def __init__(self) -> None:
        self.velocity_sets: list[tuple] = []
        self.mode: str = ""

    def set_position_target_local_ned_send(self, *args) -> None:
        self.velocity_sets.append(args)

    def command_long_send(self, *args) -> None:
        pass

    def set_mode(self, mode: str) -> None:
        self.mode = mode


class _Link:
    def __init__(self) -> None:
        self.target_system = 0
        self.target_component = 1
        self.mav = _Mav()

    def set_mode(self, mode: str) -> None:
        self.mav.mode = mode

    def recv_match(self, blocking=False, timeout=None):
        return None


class _Message:
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


def _heartbeat(sysid: int, offboard: bool = True) -> _Message:
    main_mode = 6 if offboard else 3  # 6=OFFBOARD, 3=POSCTL
    return _Message(
        "HEARTBEAT",
        {"autopilot": 12, "type": 2, "base_mode": 129, "custom_mode": main_mode << 16, "system_status": 4},
        sysid=sysid,
    )


def _local_position(sysid: int) -> _Message:
    return _Message("LOCAL_POSITION_NED", {"x": float(sysid), "y": 2.0, "z": -5.0, "vx": 0.0, "vy": 0.0, "vz": 0.0}, sysid=sysid)


def _controller(sysids: list[int]) -> MavlinkController:
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    for sysid in sysids:
        controller._handle_message(_heartbeat(sysid))
        controller._handle_message(_local_position(sysid))
    # These tests exercise protocol routing, not the ACK/mode-verification
    # machinery (which has its own coverage elsewhere): short-circuit the waits.
    controller._wait_command_ack = lambda *args, **kwargs: True  # type: ignore[method-assign]
    controller._wait_mode = lambda *args, **kwargs: True  # type: ignore[method-assign]
    return controller


def test_mavlink_send_velocity_setpoint_one_message_per_system():
    controller = _controller([1, 2])
    ok = controller.send_velocity_setpoint(1.0, 2.0, 3.0, "all")
    assert ok is True
    sets = controller._mavlink.mav.velocity_sets
    assert len(sets) == 2  # one message per system, no streaming thread
    targets = {args[1] for args in sets}
    assert targets == {1, 2}
    assert controller._offboard_hold_thread is None  # no competing stream


def test_mavlink_prepare_velocity_control_enters_offboard():
    controller = _controller([1, 2])
    ok = controller.prepare_velocity_control("all")
    assert ok is True
    assert controller._mavlink.mav.mode == "OFFBOARD"


def test_mavlink_is_velocity_control_active_reads_mode():
    controller = _controller([1, 2])
    assert controller.is_velocity_control_active("all") is True
    # sys2 drops out of OFFBOARD (RC takeover)
    controller._handle_message(_heartbeat(2, offboard=False))
    assert controller.is_velocity_control_active("all") is False
    assert controller.is_velocity_control_active("px4_sys1") is True
    assert controller.is_velocity_control_active("px4_sys2") is False


def test_mavlink_release_velocity_control_leaves_offboard():
    controller = _controller([1, 2])
    controller.prepare_velocity_control("all")
    ok = controller.release_velocity_control("all")
    assert ok is True
    # _finish_offboard_position_hold tries LOITER/POSCTL and stops any hold
    assert controller._offboard_hold_thread is None


# ---------------------------------------------------------------------------
# post-review regression: per-vehicle target routing (M1), disconnect (M4),
# coverage prepare (M3), failed-send counting (M4)
# ---------------------------------------------------------------------------


def test_mavlink_release_targets_the_requested_vehicle():
    """M1: multi-vehicle ops must act on the CURRENT target, not the first
    vehicle. Releasing sys2 must leave sys2 (mode command + position read)."""
    controller = _controller([1, 2])
    controller.prepare_velocity_control("px4_sys2")
    assert controller._mavlink.target_system == 2
    ok = controller.release_velocity_control("px4_sys2")
    assert ok is True
    # the position hold fallback / mode switch operated on sys2's position
    assert controller._position["x"] == 2.0  # target-first property
    assert controller._mavlink.target_system == 2


def test_mavlink_send_velocity_setpoint_fails_when_disconnected():
    """M4: a dead link must not silently succeed."""
    controller = _controller([1, 2])
    controller._connected = False
    assert controller.send_velocity_setpoint(1.0, 0.0, 0.0, "all") is False
    assert controller._mavlink.mav.velocity_sets == []


def test_mavlink_is_velocity_control_active_rejects_stale_heartbeat():
    controller = _controller([1, 2])
    assert controller.is_velocity_control_active("all") is True
    controller._systems[1]["last_heartbeat"] = time.time() - 5.0
    assert controller.is_velocity_control_active("all") is False
    assert controller.is_velocity_control_active("px4_sys2") is True


def test_px4_coverage_start_prepares_offboard():
    """M3: coverage must enter OFFBOARD too, not only formation takeoff."""
    controller = _Px4FakeController()
    fc = FormationController(controller)
    fc.set_drones(list(controller.positions))
    fc.takeoff(altitude=10.0)  # no offsets -> stays idle, no prepare yet
    assert controller.prepared == []
    plan = fc.coverage_plan({"shape": "rectangle", "width": 10, "height": 10, "altitude": 10})
    assert plan["status"] == "ok"
    result = fc.coverage_start()
    assert result["status"] == "ok"
    assert result["mode"] == "coverage"
    assert sorted(controller.prepared) == ["px4_sys1", "px4_sys2"]


def test_px4_tick_counts_failed_sends():
    """M4: a failed velocity send must increment the error counter so the
    auto-stop threshold still works on link loss."""
    controller = _Px4FakeController()

    class _BrokenSend(_Px4FakeController):
        def send_velocity_setpoint(self, vx, vy, vz, vehicle_name: str = "") -> bool:
            self.velocity_setpoints.append((vehicle_name, vx, vy, vz))
            return False

    broken = _BrokenSend(controller.positions)
    fc = FormationController(broken)
    fc.set_drones(list(broken.positions))
    fc.set_formation("line", spacing=5.0)
    fc.takeoff(altitude=10.0)
    before = fc.consecutive_errors
    fc.tick()
    assert fc.consecutive_errors >= before + 1
