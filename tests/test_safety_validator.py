"""SafetyValidator tests: geofence, altitude bounds, no-fly zones, velocity
norm clamping. The safety layer is the last gate before flight commands, so
its correction semantics deserve direct coverage."""

from __future__ import annotations

import math

import pytest

from src.modules.safety_validator import FlightConstraint, SafetyValidator


def _validator(**overrides) -> SafetyValidator:
    constraint = FlightConstraint(max_altitude=50.0, min_altitude=0.5, max_velocity=8.0, max_distance_from_home=100.0)
    for key, value in overrides.items():
        setattr(constraint, key, value)
    return SafetyValidator(constraint)


def test_altitude_below_ground_is_danger_and_corrected() -> None:
    result = _validator().validate_position(0.0, 0.0, 2.0)  # z>0 = underground
    assert result.level == "danger"
    assert not result.is_safe
    assert result.corrected["z"] == -0.5


def test_altitude_too_low_is_danger_and_corrected() -> None:
    result = _validator().validate_position(0.0, 0.0, -0.2)
    assert result.level == "danger"
    assert result.corrected["z"] == -0.5


def test_altitude_too_high_is_warning_and_clamped() -> None:
    result = _validator().validate_position(0.0, 0.0, -80.0)
    assert result.level == "warning"  # clamped, not blocked
    assert result.corrected["z"] == -50.0


def test_geofence_clamps_toward_home() -> None:
    result = _validator().validate_position(150.0, 0.0, -10.0)
    assert result.level == "danger"
    corrected = result.corrected
    assert corrected["x"] == 100.0  # clamped to the fence radius
    assert corrected["y"] == 0.0


def test_no_fly_zone_blocks_position() -> None:
    validator = _validator(no_fly_zones=[{"x": 10.0, "y": 10.0, "radius": 5.0}])
    result = validator.validate_position(12.0, 12.0, -10.0)
    assert result.level == "danger"
    assert "禁飞区" in result.violations[0]


def test_velocity_norm_is_corrected_proportionally() -> None:
    result = _validator().validate_velocity(8.0, 6.0, 0.0)  # speed 10 > 8
    assert result.level == "danger"
    speed = math.sqrt(result.corrected["vx"] ** 2 + result.corrected["vy"] ** 2)
    assert abs(speed - 8.0) < 1e-6
    assert result.corrected["vx"] == pytest.approx(6.4)
    assert result.corrected["vy"] == pytest.approx(4.8)


def test_velocity_within_limit_is_safe() -> None:
    result = _validator().validate_velocity(2.0, 1.0, -0.5)
    assert result.is_safe
    assert result.level == "safe"


def test_move_path_crosses_no_fly_zone() -> None:
    validator = _validator(no_fly_zones=[{"x": 5.0, "y": 0.0, "radius": 3.0}])
    result = validator.validate_move((0.0, 0.0, -10.0), (20.0, 0.0, -10.0), (2.0, 0.0, 0.0))
    assert result.level == "danger"
    assert any("穿越禁飞区" in v for v in result.violations)


def test_clamp_position_pushes_out_of_no_fly_zone() -> None:
    validator = _validator(no_fly_zones=[{"x": 10.0, "y": 0.0, "radius": 5.0}])
    x, y, z = validator.clamp_position(11.0, 0.0, -10.0)
    assert math.hypot(x - 10.0, y - 0.0) >= 5.0 - 1e-6
    assert x >= 10.0


def test_get_safe_move_corrects_both_position_and_velocity() -> None:
    result = _validator().get_safe_move((0.0, 0.0, -10.0), (300.0, 0.0, -80.0), (12.0, 0.0, 0.0))
    assert result["was_corrected"]
    assert result["to_pos"][0] == 100.0  # geofence clamp
    assert result["to_pos"][2] == -50.0  # altitude clamp
    speed = math.hypot(result["velocity"][0], result["velocity"][1], result["velocity"][2])
    assert abs(speed - 8.0) < 1e-6  # velocity clamp


def test_validate_and_execute_decorator_blocks_danger() -> None:
    from src.modules.safety_validator import validate_and_execute

    class _Controller:
        safety_validator = _validator()

        @validate_and_execute(pos_arg_index=0)
        def move(self, pos):
            raise AssertionError("must not execute on danger")

    controller = _Controller()
    result = controller.move((0.0, 0.0, 5.0))  # underground: danger
    assert result["success"] is False
    assert "安全验证拦截" in result["error"]
