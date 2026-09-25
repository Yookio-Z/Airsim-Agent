"""Yaw / heading behaviour: math, mode gating, and the lock-step PX4 channel.

The vehicle used to fly every leg with a frozen nose. Two things caused that:
every AirSim move call omitted ``yaw`` (so the airframe kept its heading), and
``rotate_to_heading`` was refused outright whenever settings.json declared
``PX4Multirotor`` — the exact configuration this project uses for PX4-in-sim
lock-step. Meanwhile the PX4 MAVLink backend set ``YAW_IGNORE`` on its movement
setpoints, pinning the nose there too, including on real vehicles.

These tests pin the replacement behaviour: heading math that agrees with
PX4/MAVLink conventions, a yaw mode that defaults to "face the next waypoint"
(PX4 ``MPC_YAW_MODE=0`` / ArduPilot ``WP_YAW_BEHAVIOR=2``) rather than the
non-default "hold", and a real MAVLink path for the lock-step simulator.
"""

from __future__ import annotations

import math

import pytest

from src.config import config
from src.modules.px4_yaw_link import (
    Px4YawLink,
    bearing_to_deg,
    heading_error_deg,
    normalize_heading_deg,
)


# ----------------------------------------------------------------------
# Heading maths
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("delta_east", "delta_north", "expected"),
    [
        (0.0, 10.0, 0.0),     # due north
        (10.0, 0.0, 90.0),    # due east
        (0.0, -10.0, 180.0),  # due south
        (-10.0, 0.0, 270.0),  # due west
        (10.0, 10.0, 45.0),   # north-east
    ],
)
def test_bearing_uses_ned_east_north_convention(delta_east, delta_north, expected):
    """bearing_to_deg takes (east, north) and returns 0=north, clockwise."""
    assert bearing_to_deg(delta_east, delta_north) == pytest.approx(expected, abs=1e-6)


def test_bearing_rejects_zero_length_vector():
    """A target directly overhead has no bearing; callers must not rotate."""
    with pytest.raises(ValueError):
        bearing_to_deg(0.0, 0.0)


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (350.0, 10.0, 20.0),    # shortest way across north
        (10.0, 350.0, -20.0),   # and the other way round
        (0.0, 180.0, 180.0),    # dead-on tie resolves to +180
        (0.0, 181.0, -179.0),   # just past the tie flips to the short way
        (45.0, 45.0, 0.0),      # already there
    ],
)
def test_heading_error_takes_the_short_way_round(current, target, expected):
    assert heading_error_deg(current, target) == pytest.approx(expected, abs=1e-6)


def test_normalize_heading_folds_into_zero_to_360():
    assert normalize_heading_deg(360.0) == 0.0
    assert normalize_heading_deg(-90.0) == 270.0
    assert normalize_heading_deg(725.0) == pytest.approx(5.0)


# ----------------------------------------------------------------------
# Yaw mode configuration
# ----------------------------------------------------------------------
def test_default_yaw_mode_matches_px4_and_ardupilot_defaults():
    """Both stacks default multi-rotors to "face the next waypoint".

    PX4 MPC_YAW_MODE=0 and ArduPilot WP_YAW_BEHAVIOR=2. Pinning this stops a
    silent regression back to the non-default "hold the nose" behaviour.
    """
    assert (config.yaw_mode or "").strip().lower() == "face_waypoint"


def test_yaw_limits_stay_inside_px4_defaults():
    """MIS_YAW_ERR is 12 deg; MPC_YAWRAUTO_MAX is 45-60 deg/s. Stay conservative."""
    assert config.yaw_tolerance_deg == pytest.approx(12.0)
    assert 0 < config.yaw_max_rate_dps <= 60.0


# ----------------------------------------------------------------------
# Px4YawLink: "unknown heading" must never read as north
# ----------------------------------------------------------------------
class _Msg:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Mav:
    """Stand-in for the pymavlink module handle held at ``link._mav.mav``."""

    def __init__(self):
        self.sent: list[float] = []

    def set_position_target_local_ned_send(self, *args):
        self.sent.append(args[-1])  # yaw rate is the last positional argument

    def command_long_send(self, *args):
        pass

    def close(self):
        pass


class _Link:
    def __init__(self, telemetry=None):
        self.target_system = 1
        self.target_component = 1
        self.mav = _Mav()
        self._telemetry = list(telemetry or [])

    def recv_match(self, type=None, blocking=False, timeout=None):
        if not self._telemetry:
            return None
        return self._telemetry.pop(0)

    def close(self):
        pass


def _link(telemetry):
    link = Px4YawLink("udp:127.0.0.1:14540")
    link._mav = _Link(telemetry)
    return link


@pytest.mark.parametrize("raw", [65535, 65536, -1, None])
def test_yaw_link_treats_unknown_hdg_as_no_feedback(raw):
    """hdg is centidegrees and 65535 is the documented "unknown" sentinel.

    Reading that as 0 would make the vehicle snap to north, so it has to
    surface as None and stop the caller from steering blind.
    """
    link = _link([_Msg(hdg=raw)])
    assert link.current_heading_deg() is None


def test_yaw_link_accepts_zero_as_a_valid_north_heading():
    """0 centidegrees is a real heading (due north), not a missing value.

    Rejecting it would make a north-facing vehicle look like it had no
    feedback, and the servo would silently give up.
    """
    assert _link([_Msg(hdg=0)]).current_heading_deg() == pytest.approx(0.0)


def test_yaw_link_converts_centidegrees_to_degrees():
    link = _link([_Msg(hdg=9000)])  # 90.00 deg
    assert link.current_heading_deg() == pytest.approx(90.0)


def test_yaw_link_refuses_to_rotate_without_heading_feedback():
    """No feedback must not become a blind spin."""
    link = _link([_Msg(hdg=65535)])
    # rotate_to needs to get past the connect/heartbeat gate first.
    link._ensure_connected = lambda: True  # type: ignore[method-assign]
    assert link.rotate_to(90.0, timeout=1.0) is False


def test_yaw_link_streams_a_clamped_yaw_rate():
    """Large errors saturate at the configured rate instead of spinning faster.

    hdg is centidegrees, so 8950 is 89.50 deg — inside the 8 deg tolerance of
    a 90 deg target, which is what converges the loop.
    """
    link = _link([
        _Msg(hdg=0),      # 0.00 deg
        _Msg(hdg=0),
        _Msg(hdg=8950),   # 89.50 deg -> within tolerance
    ])
    link._ensure_connected = lambda: True  # type: ignore[method-assign]
    link._prime_offboard = lambda seconds=1.0: None  # type: ignore[method-assign]
    link._set_mode = lambda mode: True  # type: ignore[method-assign]
    link._current_mode = lambda: 4  # type: ignore[method-assign]

    assert link.rotate_to(90.0, timeout=3.0) is True
    commanded = [r for r in link._mav.mav.sent if r]
    assert commanded, "expected at least one non-zero yaw rate"
    cap = math.radians(config.yaw_max_rate_dps)
    for rate in commanded:
        assert abs(rate) <= cap + 1e-9, "yaw rate exceeded the configured limit"
    # 90 deg of error at gain 0.4 asks for 36 deg/s, under the 45 deg/s cap.
    assert commanded[0] == pytest.approx(math.radians(36.0), abs=1e-6)


# ----------------------------------------------------------------------
# MAVLink backend: the pre-rotation gate
# ----------------------------------------------------------------------
def _mavlink_controller():
    from src.modules.mavlink_controller import MavlinkController

    controller = MavlinkController()
    controller._connected = True
    controller._telemetry = {"GLOBAL_POSITION_INT": {"hdg": 0.0}}
    controller._position = {"x": 0.0, "y": 0.0, "z": -3.0}
    return controller


def test_mavlink_skips_rotation_when_target_is_directly_overhead():
    """Overhead has no heading to aim at."""
    controller = _mavlink_controller()
    called: list[float] = []
    controller.rotate_to_heading = lambda h, timeout=0.0: called.append(h) or True  # type: ignore[method-assign]
    controller._face_waypoint_before_move(0.0, 0.0)
    assert called == []


def test_mavlink_skips_rotation_without_heading_feedback():
    """An autopilot that does not report hdg must not be steered blind."""
    controller = _mavlink_controller()
    controller._telemetry = {"GLOBAL_POSITION_INT": {"hdg": 65535}}
    called: list[float] = []
    controller.rotate_to_heading = lambda h, timeout=0.0: called.append(h) or True  # type: ignore[method-assign]
    controller._face_waypoint_before_move(50.0, 0.0)
    assert called == []


def test_mavlink_skips_rotation_when_already_facing_the_target():
    controller = _mavlink_controller()
    controller._telemetry = {"GLOBAL_POSITION_INT": {"hdg": 0.0}}  # facing north
    called: list[float] = []
    controller.rotate_to_heading = lambda h, timeout=0.0: called.append(h) or True  # type: ignore[method-assign]
    controller._face_waypoint_before_move(50.0, 0.0)  # due north, already facing it
    assert called == []


def test_mavlink_rotates_to_the_target_bearing():
    """Aircraft starts due north; each leg must aim at that leg's bearing.

    move_to_position takes (x, y) = (north, east), so a due-east target is
    (0, 50) and due-south is (-50, 0).
    """
    controller = _mavlink_controller()
    controller._telemetry = {"GLOBAL_POSITION_INT": {"hdg": 0.0}}  # facing north
    called: list[float] = []
    controller.rotate_to_heading = lambda h, timeout=0.0: called.append(h) or True  # type: ignore[method-assign]

    controller._face_waypoint_before_move(0.0, 50.0)   # due east  -> 90
    assert called and called[0] == pytest.approx(90.0, abs=1e-6)

    called.clear()
    controller._face_waypoint_before_move(-50.0, 0.0)  # due south -> 180
    assert called and called[0] == pytest.approx(180.0, abs=1e-6)

    called.clear()
    controller._face_waypoint_before_move(0.0, -50.0)  # due west  -> 270
    assert called and called[0] == pytest.approx(270.0, abs=1e-6)


def test_mavlink_hold_mode_never_rotates(monkeypatch):
    """yaw_mode=hold is the escape hatch for fixed-nose airframes."""
    monkeypatch.setattr(config, "yaw_mode", "hold")
    controller = _mavlink_controller()
    called: list[float] = []
    controller.rotate_to_heading = lambda h, timeout=0.0: called.append(h) or True  # type: ignore[method-assign]
    controller._face_waypoint_before_move(0.0, -50.0)
    assert called == []


# ----------------------------------------------------------------------
# AirSim backend: lock-step PX4 must get a MAVLink path, not a refusal
# ----------------------------------------------------------------------
def test_airsim_lockstep_yaw_routes_to_mavlink_instead_of_refusing(monkeypatch):
    from src.modules.airsim_controller import AirSimController

    controller = AirSimController.__new__(AirSimController)
    controller.last_error = ""
    controller._vehicles = ["PX4"]
    controller._settings_vehicle_types = {"PX4": "px4multirotor"}
    controller._connected = True
    controller._ensure_connected = lambda: True  # type: ignore[method-assign]
    controller._uses_external_px4_controller = lambda vehicle_name="": True  # type: ignore[method-assign]
    seen: list[float] = []
    controller._rotate_via_px4 = lambda h, timeout=30.0: seen.append(h) or True  # type: ignore[method-assign]

    assert controller.rotate_to_heading(123.0) is True
    assert seen == [123.0]
    assert controller.last_error == ""


def test_airsim_release_yaw_closes_the_link():
    from src.modules.airsim_controller import AirSimController

    closed: list[bool] = []
    controller = AirSimController.__new__(AirSimController)
    controller._yaw_link = type("L", (), {"close": lambda self: closed.append(True)})()
    controller._yaw_link_endpoint = "udp:127.0.0.1:14540"
    assert controller.release_yaw_control() is True
    assert closed == [True]
    assert controller._yaw_link is None
    assert controller._yaw_link_endpoint == ""


def test_airsim_endpoint_prefers_control_port_local(monkeypatch, tmp_path):
    from src.modules.airsim_controller import AirSimController

    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"Vehicles": {"PX4": {"VehicleType": "PX4Multirotor", "ControlPortLocal": 14750}}}',
        encoding="utf-8",
    )
    monkeypatch.delenv("AIRSIM_PX4_YAW_ENDPOINT", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Documents" / "AirSim").mkdir(parents=True)
    (tmp_path / "Documents" / "AirSim" / "settings.json").write_text(
        settings.read_text(encoding="utf-8"), encoding="utf-8"
    )

    controller = AirSimController.__new__(AirSimController)
    assert controller._px4_mavlink_endpoint() == "udp:127.0.0.1:14750"


def test_airsim_endpoint_env_override_wins(monkeypatch):
    from src.modules.airsim_controller import AirSimController

    monkeypatch.setenv("AIRSIM_PX4_YAW_ENDPOINT", "tcp:127.0.0.1:5790")
    controller = AirSimController.__new__(AirSimController)
    assert controller._px4_mavlink_endpoint() == "tcp:127.0.0.1:5790"
