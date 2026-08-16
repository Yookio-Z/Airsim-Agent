"""Tests for the formation/coverage control module (formation.py)."""

from __future__ import annotations

import math
import time

import pytest

from src.modules.formation import (
    FLIGHT_ACTIONS,
    FormationController,
    formation_offsets,
    plan_coverage,
)


# ---------------------------------------------------------------------------
# pure geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formation_type,count",
    [
        ("line", 1),
        ("line", 4),
        ("v_shape", 5),
        ("triangle", 6),
        ("diamond", 4),
        ("square", 4),
        ("square", 8),
        ("hexagon", 6),
        ("circle", 8),
        ("arrow", 7),
    ],
)
def test_formation_offsets_shape_and_count(formation_type, count):
    offsets = formation_offsets(formation_type, count, spacing=5.0)
    assert len(offsets) == count
    assert all(set(offset) == {"x", "y", "z"} for offset in offsets)
    assert all(offset["z"] == 0.0 for offset in offsets)
    # no duplicates
    unique = {(offset["x"], offset["y"]) for offset in offsets}
    assert len(unique) == count


def test_formation_offsets_spacing_scales():
    tight = formation_offsets("line", 3, spacing=2.0)
    wide = formation_offsets("line", 3, spacing=10.0)
    tight_xs = [offset["x"] for offset in tight]
    wide_xs = [offset["x"] for offset in wide]
    assert wide_xs[1] - wide_xs[0] == 5 * (tight_xs[1] - tight_xs[0])


def test_formation_offsets_symmetric():
    # full-ring counts keep exact symmetry; partial rings are spread evenly
    for formation_type, count in (("line", 8), ("diamond", 9), ("square", 9), ("circle", 8)):
        offsets = formation_offsets(formation_type, count, spacing=5.0)
        xs = [offset["x"] for offset in offsets]
        ys = [offset["y"] for offset in offsets]
        assert math.isclose(sum(xs), 0.0, abs_tol=1e-6), formation_type
        assert math.isclose(sum(ys), 0.0, abs_tol=1e-6), formation_type


def test_formation_offsets_unknown_type():
    with pytest.raises(ValueError):
        formation_offsets("pyramid", 3)


# ---------------------------------------------------------------------------
# pure coverage planning
# ---------------------------------------------------------------------------


def test_coverage_rectangle_all_cells_assigned():
    tasks = plan_coverage(
        {"shape": "rectangle", "width": 20, "height": 20, "altitude": 10, "x": 0, "y": 0},
        resolution=5.0,
        partition="balanced",
        path_algo="boustrophedon",
        drone_ids=["d0", "d1"],
    )
    total = sum(len(waypoints) for waypoints in tasks.values())
    assert total == 16  # 4x4 grid
    assert all(len(waypoints) == 8 for waypoints in tasks.values())  # balanced
    assert all(abs(wp["z"]) == 10.0 for waypoints in tasks.values() for wp in waypoints)
    # every cell covered exactly once across drones
    seen = {(wp["x"], wp["y"]) for waypoints in tasks.values() for wp in waypoints}
    assert len(seen) == 16


def test_coverage_circle():
    tasks = plan_coverage(
        {"shape": "circle", "radius": 10, "altitude": 5},
        resolution=5.0,
        partition="balanced",
        path_algo="nearest",
        drone_ids=["d0"],
    )
    assert tasks["d0"]
    for wp in tasks["d0"]:
        assert wp["x"] ** 2 + wp["y"] ** 2 <= 10 ** 2 + 1e-6


def test_coverage_paths_visit_all():
    area = {"shape": "rectangle", "width": 30, "height": 30, "altitude": 8}
    for path_algo in ("boustrophedon", "spiral", "nearest"):
        tasks = plan_coverage(area, resolution=5.0, partition="stripe", path_algo=path_algo, drone_ids=["d0"])
        cells = tasks["d0"]
        assert len(cells) == 36
        unique = {(wp["x"], wp["y"]) for wp in cells}
        assert len(unique) == 36


def test_coverage_invalid_args():
    with pytest.raises(ValueError):
        plan_coverage({"shape": "rectangle", "width": 10, "height": 10}, partition="nope", drone_ids=["d0"])
    with pytest.raises(ValueError):
        plan_coverage({"shape": "rectangle", "width": 10, "height": 10}, path_algo="nope", drone_ids=["d0"])
    with pytest.raises(ValueError):
        plan_coverage({"shape": "rectangle", "width": 1, "height": 1}, drone_ids=["d0"])  # no cells
    with pytest.raises(ValueError):
        plan_coverage({"shape": "rectangle", "width": 10, "height": 10}, drone_ids=[])


# ---------------------------------------------------------------------------
# control loop with fake controller
# ---------------------------------------------------------------------------


class FakeStatus:
    def __init__(self, position: dict, flying: bool = True) -> None:
        self._position = position
        self._flying = flying

    def to_dict(self) -> dict:
        return {"position_ned": dict(self._position), "flying": self._flying}


class FakeController:
    def __init__(self, positions: dict[str, dict] | None = None) -> None:
        self.positions: dict[str, dict] = positions or {
            "d0": {"x": 0.0, "y": 0.0, "z": -10.0},
            "d1": {"x": 0.0, "y": 0.0, "z": -10.0},
        }
        self.velocity_calls: list[tuple[str, dict, float]] = []
        self.hover_calls: list[str] = []
        self.land_calls: list[str] = []
        self.takeoff_calls: list[str] = []
        self.arm_calls: list[str] = []

    def list_vehicles(self) -> list[str]:
        return list(self.positions)

    def get_status(self, vehicle_name: str = ""):
        return FakeStatus(self.positions.get(vehicle_name, {"x": 0, "y": 0, "z": -10}))

    def arm(self, vehicle_name: str = "") -> bool:
        self.arm_calls.append(vehicle_name)
        return True

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.takeoff_calls.append(vehicle_name)
        self.positions[vehicle_name]["z"] = -abs(altitude)
        return True

    def land(self, vehicle_name: str = "") -> bool:
        self.land_calls.append(vehicle_name)
        return True

    def hover(self, vehicle_name: str = "") -> bool:
        self.hover_calls.append(vehicle_name)
        return True

    def move_by_velocity(self, vx, vy, vz, duration=0.0, vehicle_name: str = "") -> bool:
        self.velocity_calls.append((vehicle_name, {"vx": vx, "vy": vy, "vz": vz}, duration))
        return True


def _controller(fake: FakeController) -> FormationController:
    return FormationController(fake, hz=10.0)


def test_set_drones_filters_unknown():
    fake = FakeController()
    fc = _controller(fake)
    result = fc.set_drones(["d0", "ghost"])
    assert result["status"] == "ok"
    assert result["drones"] == ["d0"]
    assert result["unknown"] == ["ghost"]


def test_takeoff_activates_formation_mode():
    fake = FakeController()
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line", spacing=5.0)
    result = fc.takeoff(altitude=12.0)
    assert result["status"] == "ok"
    assert fake.arm_calls == ["d0", "d1"]
    assert fake.takeoff_calls == ["d0", "d1"]
    assert fc.mode == "formation"
    assert fc.center["z"] == -12.0


def test_takeoff_partial_failure_reports():
    class _FailingTakeoff(FakeController):
        def takeoff(self, altitude=3.0, vehicle_name=""):
            if vehicle_name == "d1":
                return False
            return super().takeoff(altitude, vehicle_name)

    fake = _FailingTakeoff()
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("square", spacing=5.0)
    result = fc.takeoff()
    assert result["status"] == "error"
    assert result["failed"] == ["d1"]
    assert result["succeeded"] == ["d0"]
    assert fc.mode == "idle"  # not activated on partial failure


def test_p_control_converges_toward_target():
    fake = FakeController(
        {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 50.0, "y": 0.0, "z": -10.0}}
    )
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line", spacing=5.0)
    fc.takeoff(altitude=10.0)
    fc.move_center(0.0, 0.0)
    fc.tick()
    velocities = {name: vel for name, vel, _ in fake.velocity_calls}
    assert "d0" in velocities and "d1" in velocities
    # d1 is 50m east of its target (2.5,0) -> strong negative vx (back toward center)
    assert velocities["d1"]["vx"] < 0
    # d0 near target -> smaller velocity than d1
    assert abs(velocities["d0"]["vx"]) < abs(velocities["d1"]["vx"])
    # speed limit respected
    for vel in velocities.values():
        speed = math.hypot(vel["vx"], vel["vy"], vel["vz"])
        assert speed <= fc.max_velocity + 1e-6


def test_rotate_and_scale_offsets():
    fake = FakeController()
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line", spacing=10.0)  # offsets: (-5,0),(5,0)
    fc.rotate(90.0)
    offset1 = fc.offsets["d1"]
    assert abs(abs(offset1["x"]) - 0.0) < 1e-6 and abs(abs(offset1["y"]) - 5.0) < 1e-6
    fc.scale(2.0)
    assert abs(fc.offsets["d1"]["y"]) - 10.0 < 1e-6


def test_coverage_loop_advances_waypoints_and_decelerates():
    fake = FakeController(
        {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}}
    )
    fc = _controller(fake)
    fc.set_drones(["d0"])
    plan = fc.coverage_plan(
        {"shape": "rectangle", "width": 10, "height": 10, "altitude": 10},
        resolution=5.0,
        partition="balanced",
        path_algo="boustrophedon",
    )
    assert plan["status"] == "ok"
    assert plan["total_waypoints"] == 4
    fc.coverage_start()
    assert fc.mode == "coverage"
    # drone at (0,0), first waypoint (-2.5,-2.5): outside the 3m deceleration
    # radius -> full cruise speed
    fc.tick()
    vel = fake.velocity_calls[-1][1]
    assert math.hypot(vel["vx"], vel["vy"]) > fc.coverage_speed * 0.9
    # move drone within the deceleration radius but outside the 0.5m arrival
    # threshold -> speed drops, waypoint not yet advanced
    fake.positions["d0"] = {"x": -1.0, "y": -1.0, "z": -10.0}
    fc.tick()
    vel_near = fake.velocity_calls[-1][1]
    assert math.hypot(vel_near["vx"], vel_near["vy"]) < fc.coverage_speed
    assert fc.coverage_indices["d0"] == 0
    # place drone on the waypoint -> advances to next
    fake.positions["d0"] = {"x": -2.5, "y": -2.5, "z": -10.0}
    fc.tick()
    assert fc.coverage_indices["d0"] == 1


def test_should_stop_triggers_hover():
    fake = FakeController()
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("square", spacing=5.0)
    fc.takeoff()
    fc.should_stop = lambda: True
    fc.tick()
    assert fc.mode == "idle"
    assert sorted(fake.hover_calls) == ["d0", "d1"]


def test_land_all_clears_mode():
    fake = FakeController()
    fc = _controller(fake)
    fc.set_drones(["d0"])
    fc.set_formation("line")
    fc.takeoff()
    result = fc.land_all()
    assert result["status"] == "ok"
    assert fake.land_calls == ["d0"]
    assert fc.mode == "idle"


def test_status_reports_stable():
    fake = FakeController()
    fc = _controller(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line", spacing=5.0)
    fc.takeoff(altitude=10.0)
    # both drones at their targets -> stable
    fc.offsets = {"d0": {"x": 0.0, "y": 0.0, "z": 0.0}, "d1": {"x": 5.0, "y": 0.0, "z": 0.0}}
    fc.states = {
        "d0": {"position_ned": {"x": 0.0, "y": 0.0, "z": -10.0}, "flying": True},
        "d1": {"position_ned": {"x": 5.0, "y": 0.0, "z": -10.0}, "flying": True},
    }
    status = fc.status()
    assert status["stable"] is True
    assert status["mode"] == "formation"
    assert status["drones"][1]["target"] == {"x": 5.0, "y": 0.0, "z": -10.0}


def test_flight_actions_constant():
    assert "takeoff" in FLIGHT_ACTIONS
    assert "move_center" in FLIGHT_ACTIONS
    assert "coverage_start" in FLIGHT_ACTIONS


# ---------------------------------------------------------------------------
# post-review fixes: duration cap, coverage gating, stale-state rules
# ---------------------------------------------------------------------------


def test_effective_duration_is_capped():
    """M1: adaptive duration must never exceed 1s so a dead control thread
    leaves the swarm hovering within ~5m."""
    class _SlowController(FakeController):
        def move_by_velocity(self, vx, vy, vz, duration=0.0, vehicle_name=""):
            time.sleep(0.15)  # simulate a slow RPC per command
            return True

    fake = _SlowController()
    fc = FormationController(fake, hz=10.0)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line", spacing=5.0)
    fc.takeoff(altitude=10.0)
    fc.start()
    try:
        time.sleep(0.6)
        assert fc._effective_duration <= 1.01
        assert fc._effective_duration >= 0.2
    finally:
        fc.shutdown("test")


def test_coverage_start_requires_airborne():
    class _Grounded(FakeController):
        def get_status(self, vehicle_name=""):
            return FakeStatus(self.positions.get(vehicle_name, {"x": 0, "y": 0, "z": -10}), flying=False)

    fake = _Grounded()
    fc = FormationController(fake)
    fc.set_drones(["d0", "d1"])
    plan = fc.coverage_plan({"shape": "rectangle", "width": 10, "height": 10, "altitude": 10})
    assert plan["status"] == "ok"
    result = fc.coverage_start()
    assert result["status"] == "error"
    assert "takeoff" in result["message"]
    assert fc.mode == "idle"


def test_reconfiguration_rejected_while_active():
    fake = FakeController()
    fc = FormationController(fake)
    fc.set_drones(["d0", "d1"])
    fc.set_formation("line")
    fc.takeoff(altitude=10.0)
    assert fc.mode == "formation"
    assert fc.set_drones(["d0"])["status"] == "error"
    assert fc.set_formation("square")["status"] == "error"
    assert fc.coverage_plan({"shape": "rectangle", "width": 10, "height": 10})["status"] == "error"


def test_shutdown_clears_coverage_state():
    fake = FakeController()
    fc = FormationController(fake)
    fc.set_drones(["d0"])
    fc.coverage_plan({"shape": "rectangle", "width": 10, "height": 10, "altitude": 10})
    assert fc.coverage_tasks
    active = fc.shutdown("run_end")
    assert active is False  # was idle, no mission active
    assert fc.coverage_tasks == {}
    assert fc.coverage_indices == {}
    # drone ids and offsets survive so a later run can re-engage
    assert fc.drone_ids == ["d0"]
    # idle shutdown of a plain controller without mission returns False
    fc2 = FormationController(FakeController())
    fc2.set_drones(["d0"])
    assert fc2.shutdown("test") is False
