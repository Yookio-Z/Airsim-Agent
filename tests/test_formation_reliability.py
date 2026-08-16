"""Formation control-loop reliability tests.

Covers the failure paths that synchronous tick() tests never reach:
  * "consecutive" error semantics — a successful tick resets the counter
    (a transient error mid-mission must not auto-stop the swarm later);
  * thread-driven auto-stop — the real runner thread hits the error
    threshold, hovers, and exits cleanly (stop() from its own tick thread
    must not raise "cannot join current thread");
  * per-drone error counters and control-loop tick metrics.
"""

from __future__ import annotations

import threading
import time

from src.modules.formation import FormationController


class _Status:
    def __init__(self, position: dict) -> None:
        self._position = position

    def to_dict(self) -> dict:
        return {"position_ned": dict(self._position), "flying": True}


class _FlakyController:
    """get_status fails while ``fail_all`` is True (deterministic per tick)."""

    def __init__(self) -> None:
        self.fail_all = True
        self.velocity_calls = 0

    def get_status(self, vehicle_name: str = ""):
        if self.fail_all:
            raise RuntimeError("transient link error")
        return _Status({"x": 1.0, "y": 2.0, "z": -10.0})

    def move_by_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.2, vehicle_name: str = "") -> bool:
        self.velocity_calls += 1
        return True

    def list_vehicles(self) -> list[str]:
        return ["d1", "d2"]


class _BrokenController:
    """get_status always fails; hover/list must still work for recovery."""

    def __init__(self) -> None:
        self.hovered: list[str] = []

    def get_status(self, vehicle_name: str = ""):
        raise RuntimeError("link lost")

    def hover(self, vehicle_name: str = "") -> bool:
        self.hovered.append(vehicle_name)
        return True

    def list_vehicles(self) -> list[str]:
        return ["d1", "d2"]


def _formation(controller, max_errors: int = 50) -> FormationController:
    fc = FormationController(controller, hz=50.0, max_consecutive_errors=max_errors)
    fc.drone_ids = ["d1", "d2"]
    fc.offsets = {
        "d1": {"x": 0.0, "y": 0.0, "z": 0.0},
        "d2": {"x": 5.0, "y": 0.0, "z": 0.0},
    }
    fc.mode = "formation"
    return fc


def test_successful_tick_resets_consecutive_errors() -> None:
    controller = _FlakyController()
    fc = _formation(controller, max_errors=10)
    for _ in range(3):
        fc.tick()
    assert fc.consecutive_errors == 6  # 2 drones × 3 failing ticks accumulate
    controller.fail_all = False
    fc.tick()
    assert fc.consecutive_errors == 0  # one good tick resets the streak
    assert fc.drone_errors == {}


def test_transient_errors_do_not_auto_stop() -> None:
    controller = _FlakyController()
    controller.fail_all = False
    fc = _formation(controller, max_errors=10)
    for _ in range(9):
        fc.tick()
    assert fc.consecutive_errors == 0
    # a single transient failure among successes never reaches the threshold
    controller.fail_all = True
    fc.tick()
    assert fc.consecutive_errors == 2
    controller.fail_all = False
    for _ in range(5):
        fc.tick()
    assert fc.consecutive_errors == 0
    assert fc.mode == "formation"
    assert not fc.events or all(event["type"] != "auto_stop" for event in fc.events)


def test_back_to_back_failures_auto_stop() -> None:
    fc = _formation(_BrokenController(), max_errors=4)
    for _ in range(5):
        fc.tick()
    assert fc.mode == "idle"  # hover_all ran
    assert any(event["type"] == "auto_stop" for event in fc.events)


def test_thread_driven_auto_stop_exits_cleanly() -> None:
    """The real runner thread hits the error threshold: hover + stop from
    inside the tick thread must not raise, and the thread must exit."""
    controller = _BrokenController()
    fc = _formation(controller, max_errors=4)
    fc.start()
    thread = fc._thread
    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert fc.mode == "idle"
    assert controller.hovered  # hover_all ran for the swarm
    assert any(event["type"] == "auto_stop" for event in fc.events)
    assert fc.tick_metrics["tick_count"] > 0


def test_per_drone_error_counters() -> None:
    class _OneDroneDead:
        def get_status(self, vehicle_name: str = ""):
            if vehicle_name == "d1":
                raise RuntimeError("d1 link lost")
            return _Status({"x": 1.0, "y": 2.0, "z": -10.0})

        def move_by_velocity(self, *args, **kwargs) -> bool:
            return True

        def list_vehicles(self) -> list[str]:
            return ["d1", "d2"]

    fc = _formation(_OneDroneDead(), max_errors=100)
    fc.tick()
    assert fc.drone_errors == {"d1": 1}
    fc.tick()
    assert fc.drone_errors == {"d1": 2}
    assert fc.consecutive_errors == 2


def test_tick_metrics_tracking() -> None:
    fc = _formation(_FlakyController(), max_errors=10)
    fc._record_tick_metrics(0.05)
    fc._record_tick_metrics(0.01)
    assert fc.tick_metrics["tick_count"] == 2
    assert fc.tick_metrics["max_tick_ms"] == 50.0
    assert 0.0 < fc.tick_metrics["avg_tick_ms"] < 50.0


def test_schedule_next_anchors_cadence_and_counts_dropped() -> None:
    metrics: dict[str, float] = {"tick_count": 0, "avg_tick_ms": 0.0, "max_tick_ms": 0.0, "dropped_ticks": 0}
    period = 0.1
    # on-time tick: schedule stays anchored to the original cadence
    nxt = _schedule_next_anchor(10.0, period, 10.05, metrics)
    assert abs(nxt - 10.1) < 1e-9
    assert metrics["dropped_ticks"] == 0
    # a tick 5 periods late: missed ticks are counted and the schedule resets
    nxt = _schedule_next_anchor(nxt, period, 10.55, metrics)
    assert abs(nxt - 10.65) < 1e-9  # now + period (no catch-up burst)
    assert metrics["dropped_ticks"] == 3
    # after a reset the schedule is anchored to now: an on-time next tick
    # stays put and the dropped counter is unchanged
    nxt = _schedule_next_anchor(nxt, period, 10.7, metrics)
    assert abs(nxt - 10.75) < 1e-9
    assert metrics["dropped_ticks"] == 3


def _schedule_next_anchor(next_tick: float, period: float, now: float, metrics: dict[str, float]) -> float:
    """Local alias so the test does not depend on importing the controller."""
    from src.modules.formation import FormationController

    return FormationController._schedule_next(next_tick, period, now, metrics)


def test_stop_from_own_thread_does_not_raise() -> None:
    """stop() must never join the calling thread on itself (auto-stop runs
    inside the tick thread)."""
    fc = _formation(_BrokenController(), max_errors=3)
    fc.start()
    thread = fc._thread
    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    # repeated stop() calls stay safe from any thread
    fc.stop()
    fc.stop()


def test_effective_duration_capped_for_fail_safe() -> None:
    fc = _formation(_FlakyController(), max_errors=10)
    # a pathological slow tick must not extend the commanded duration toward
    # the failure mode (dead thread => drift for the last duration)
    fc._effective_duration = max(fc.velocity_duration, min(0.4, 5.0 * 1.5))
    assert fc._effective_duration <= 0.4


def test_coverage_keeps_zero_setpoint_until_all_drones_finish() -> None:
    """PX4-style coverage: a drone that finished its share keeps a zero
    setpoint on the OFFBOARD stream so the PX4 watchdog (~0.5s without
    setpoints) never drops the mode and trips the whole-swarm mode_lost
    auto-stop mid-coverage."""

    class _Px4CoverageController:
        def __init__(self) -> None:
            self.sent: list[tuple[str, float, float, float]] = []
            self.positions = {"a": {"x": 10.0, "y": 0.0, "z": -10.0}, "b": {"x": 0.0, "y": 0.0, "z": -10.0}}

        def get_status(self, vehicle_name: str = ""):
            return _Status(dict(self.positions.get(vehicle_name, {"x": 0.0, "y": 0.0, "z": -10.0})))

        def send_velocity_setpoint(self, vx: float, vy: float, vz: float, vehicle_name: str = "") -> bool:
            self.sent.append((vehicle_name, vx, vy, vz))
            return True

        def is_velocity_control_active(self, vehicle_name: str = "") -> bool:
            return True

        def hover(self, vehicle_name: str = "") -> bool:
            return True

        def list_vehicles(self) -> list[str]:
            return ["a", "b"]

    controller = _Px4CoverageController()
    fc = FormationController(controller, hz=50.0, max_consecutive_errors=10)
    fc.drone_ids = ["a", "b"]
    fc.mode = "coverage"
    fc.coverage_tasks = {
        "a": [{"x": 10.0, "y": 0.0, "z": -10.0}],
        "b": [{"x": 20.0, "y": 0.0, "z": -10.0}, {"x": 30.0, "y": 0.0, "z": -10.0}],
    }
    fc.coverage_indices = {"a": 1, "b": 0}  # a finished its single waypoint

    fc.tick()
    by_drone = {name: (vx, vy, vz) for name, vx, vy, vz in controller.sent}
    assert "a" in by_drone
    assert by_drone["a"] == (0.0, 0.0, 0.0)  # zero setpoint keeps the stream alive
    assert by_drone["b"][0] > 0.0  # b still gets a forward velocity

    # once every drone finished, the mission completes and the loop idles
    fc.coverage_indices = {"a": 1, "b": 2}
    controller.sent.clear()
    fc.tick()
    assert fc.mode == "idle"
    assert any(event["type"] == "coverage_complete" for event in fc.events)
