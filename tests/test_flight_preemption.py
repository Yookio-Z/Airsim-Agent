"""Emergency-stop / cancel preemption of blocking flight commands.

A blocking single-vehicle move must exit promptly when the wired stop
provider fires, leaving OFFBOARD through the safe hold path instead of
letting the vehicle keep flying after an emergency stop.
"""

from __future__ import annotations

import threading
import time

from src.modules.mavlink_controller import MavlinkController


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


class _Mav:
    def __init__(self) -> None:
        self.commands: list[tuple[int, int]] = []
        self.mode = ""

    def command_long_send(self, target_system, target_component, command, *params) -> None:
        self.commands.append((target_system, command))

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def set_position_target_local_ned_send(self, *args, **kwargs) -> None:
        pass


class _Link:
    def __init__(self) -> None:
        self.target_system = 0
        self.target_component = 1
        self.mav = _Mav()

    def set_mode(self, mode: str) -> None:
        self.mav.mode = mode

    def recv_match(self, blocking=False, timeout=None):
        return None


def _heartbeat(sysid: int, custom_mode: int = 4) -> _Message:
    return _Message(
        "HEARTBEAT",
        {"autopilot": 12, "type": 2, "base_mode": 129, "custom_mode": custom_mode, "system_status": 4},
        sysid=sysid,
    )


def _local_position(sysid: int, x: float, y: float, z: float) -> _Message:
    return _Message("LOCAL_POSITION_NED", {"x": x, "y": y, "z": z, "vx": 0.0, "vy": 0.0, "vz": 0.0}, sysid=sysid)


def _connected_controller() -> MavlinkController:
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    controller._handle_message(_heartbeat(1))
    controller._handle_message(_local_position(1, 0.0, 0.0, -3.0))
    # short-circuit the mode-ACK/transition waits (they block for seconds)
    controller._wait_command_ack = lambda *args, **kwargs: True  # type: ignore[method-assign]
    controller._wait_mode = lambda *args, **kwargs: True  # type: ignore[method-assign]
    return controller


def test_stop_provider_wired_into_blocking_move() -> None:
    controller = _connected_controller()
    flag = {"stop": False}
    controller.set_stop_provider(lambda: flag["stop"])
    # fire the stop shortly after the 1s OFFBOARD priming window
    threading.Timer(1.2, lambda: flag.__setitem__("stop", True)).start()
    started = time.time()
    ok = controller.move_to_position(10.0, 0.0, -5.0, velocity=2.0)
    elapsed = time.time() - started
    assert ok is False
    assert elapsed < 3.0, f"blocking move was not preempted promptly ({elapsed:.1f}s)"
    assert "interrupted" in controller._last_path_error.get("message", "")


def test_stop_during_prime_returns_immediately() -> None:
    controller = _connected_controller()
    controller.set_stop_provider(lambda: True)  # stop is already requested
    started = time.time()
    ok = controller.move_to_position(10.0, 0.0, -5.0, velocity=2.0)
    elapsed = time.time() - started
    assert ok is False
    assert elapsed < 0.5, f"prime was not interruptible ({elapsed:.2f}s)"
    assert "interrupted" in controller._last_path_error.get("message", "")


def test_stop_provider_not_set_keeps_old_semantics() -> None:
    controller = _connected_controller()
    assert controller._stop_requested() is False
    controller.set_stop_provider(None)
    assert controller._stop_requested() is False


def test_velocity_move_interrupted_leaves_offboard_cleanly() -> None:
    controller = _connected_controller()
    flag = {"stop": False}
    controller.set_stop_provider(lambda: flag["stop"])
    threading.Timer(0.3, lambda: flag.__setitem__("stop", True)).start()
    started = time.time()
    ok = controller.move_by_velocity(1.0, 0.0, 0.0, duration=5.0)
    elapsed = time.time() - started
    assert ok is False
    assert elapsed < 3.0
    # the safe exit (hold + LOITER) still ran after the interrupted stream
    assert controller._offboard_hold_thread is None


# ---------------------------------------------------------------------------
# AirSim move-arrival wait (telemetry polling; task.join is unreliable with
# timeout_value=5 — it returns after ~6s regardless of task state)
# ---------------------------------------------------------------------------


class _FakeStatus:
    """Minimal DroneStatus-like object for _wait_move_arrival tests."""

    def __init__(self, pos: dict, vel: dict, raw_landed: int = 1) -> None:
        self._d = {
            "position_ned": pos,
            "velocity_ned": vel,
            "extra": {"landed_state_raw": raw_landed},
            "flying": True,
            "armed": True,
        }

    def to_dict(self) -> dict:
        return dict(self._d)


def test_airsim_wait_move_arrival_preempts_on_stop() -> None:
    from src.modules.airsim_controller import AirSimController

    controller = AirSimController()
    flag = {"stop": False}
    controller.set_stop_provider(lambda: flag["stop"])
    threading.Timer(0.1, lambda: flag.__setitem__("stop", True)).start()
    started = time.time()
    ok = controller._wait_move_arrival("drone_0", (10.0, 0.0, -5.0), timeout=30.0)
    assert ok is False
    assert time.time() - started < 2.0
    assert "interrupted" in controller.last_error


def test_airsim_wait_move_arrival_completes_on_arrival() -> None:
    from src.modules.airsim_controller import AirSimController

    controller = AirSimController()
    controller.get_status = lambda name="drone_0": _FakeStatus(  # type: ignore[method-assign]
        {"x": 9.9, "y": 0.0, "z": -5.0}, {"vx": 0.1, "vy": 0.0, "vz": 0.0}
    )
    ok = controller._wait_move_arrival("drone_0", (10.0, 0.0, -5.0), timeout=5.0)
    assert ok is True
    assert controller.last_error == ""


def test_airsim_wait_move_arrival_detects_stall() -> None:
    from src.modules.airsim_controller import AirSimController

    controller = AirSimController()
    # hovering far from the target: simulator-side task ended without arrival
    controller.get_status = lambda name="drone_0": _FakeStatus(  # type: ignore[method-assign]
        {"x": 0.0, "y": 0.0, "z": 0.0}, {"vx": 0.0, "vy": 0.0, "vz": 0.0}
    )
    started = time.time()
    ok = controller._wait_move_arrival("drone_0", (10.0, 0.0, -5.0), timeout=30.0)
    assert ok is False
    assert time.time() - started < 10.0


def test_airsim_wait_move_no_provider_no_behavior_change() -> None:
    from src.modules.airsim_controller import AirSimController

    controller = AirSimController()
    assert controller._stop_requested() is False
    controller.get_status = lambda name="drone_0": _FakeStatus(  # type: ignore[method-assign]
        {"x": 10.0, "y": 0.0, "z": -5.0}, {"vx": 0.0, "vy": 0.0, "vz": 0.0}
    )
    assert controller._wait_move_arrival("drone_0", (10.0, 0.0, -5.0), timeout=5.0) is True
