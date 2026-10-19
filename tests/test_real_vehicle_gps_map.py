"""Real vehicle: GPS arrives → the map must land on the right place.

There is no hardware to test against, so the contract that matters is the one
between a flight controller's ``GLOBAL_POSITION_INT`` / ``HOME_POSITION`` stream
and what the map ends up showing. This file pins that contract:

- the lat/lon scaling MAVLink specifies (1e-7 degrees) is applied exactly —
  a missing or doubled factor puts the aircraft in the wrong country;
- a real vehicle is *not* allowed to present a local-NED-only position as a map
  position, because that is what would silently drop the aircraft at the wrong
  place before the GPS fix lands;
- once a good fix arrives, the position is declared usable for the map and for
  global missions;
- the geofence centre is derived from the real home, in the NED frame the
  validator works in.
"""

from __future__ import annotations

import math

import pytest

from src.modules.backends.mavlink import MavlinkController


# Somewhere unambiguous and far from the code's simulation origin (Beijing):
# a real airframe in rural France. If a scaling factor is wrong the map ends up
# thousands of kilometres away, which this fixture makes obvious.
REAL_LAT_DEG = 48.858370
REAL_LON_DEG = 2.294481


class _Msg:
    def __init__(self, msg_type: str, data: dict, sysid: int = 1) -> None:
        self._msg_type = msg_type
        self._data = dict(data)
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
    """Minimal MAVLink link; nothing here is exercised by these tests."""

    def __init__(self) -> None:
        self.target_system = 0
        self.target_component = 1

    def recv_match(self, blocking=False, timeout=None):
        return None


def _controller(real_vehicle: bool = True) -> MavlinkController:
    controller = MavlinkController()
    controller._connected = True
    controller._real_vehicle = real_vehicle
    controller._mavlink = _Link()
    controller._handle_message(
        _Msg(
            "HEARTBEAT",
            {
                "autopilot": 12,
                "type": 2,
                "base_mode": 129,
                "custom_mode": 4,
                "system_status": 4,
            },
        )
    )
    return controller


def _global_position(controller: MavlinkController, *, fix: int = 3, hdg: int = 4500) -> None:
    """Feed one GLOBAL_POSITION_INT, already scaled the way the wire carries it."""
    controller._handle_message(
        _Msg(
            "GLOBAL_POSITION_INT",
            {
                "lat": int(round(REAL_LAT_DEG * 1e7)),
                "lon": int(round(REAL_LON_DEG * 1e7)),
                "alt": 120_000,            # mm AMSL
                "relative_alt": 35_000,    # mm above home
                "vx": 0,
                "vy": 0,
                "vz": 0,
                "hdg": hdg,                # centidegrees
                "time_boot_ms": 0,
            },
        )
    )
    controller._handle_message(_Msg("GPS_RAW_INT", {"fix_type": fix, "satellites_visible": 14}))


def _home(controller: MavlinkController, lat: float, lon: float) -> None:
    controller._handle_message(
        _Msg(
            "HOME_POSITION",
            {
                "latitude": int(round(lat * 1e7)),
                "longitude": int(round(lon * 1e7)),
                "altitude": 50_000,
                "x": 0, "y": 0, "z": 0,
                "q": [1.0, 0.0, 0.0, 0.0],
                "approach_x": 0, "approach_y": 0, "approach_z": 0,
            },
        )
    )


# ----------------------------------------------------------------------
# Wire-format decoding
# ----------------------------------------------------------------------
def test_lat_lon_are_scaled_by_1e7_exactly() -> None:
    """MAVLink carries lat/lon in 1e-7 degrees; a wrong factor moves continents."""
    controller = _controller()
    _global_position(controller)

    gps = controller.get_status().to_dict()["gps"]
    assert gps["lat"] == pytest.approx(REAL_LAT_DEG, abs=1e-7)
    assert gps["lon"] == pytest.approx(REAL_LON_DEG, abs=1e-7)


def test_altitudes_are_scaled_to_metres() -> None:
    controller = _controller()
    _global_position(controller)

    gps = controller.get_status().to_dict()["gps"]
    # alt 是相对 home 的高度（地图/界面该显示的那个），absolute_alt 才是海拔
    assert gps["alt"] == pytest.approx(35.0, abs=1e-6)
    assert gps["absolute_alt"] == pytest.approx(120.0, abs=1e-6)


def test_heading_is_scaled_to_degrees() -> None:
    controller = _controller()
    _global_position(controller, hdg=4500)  # 45.00 deg

    assert controller.get_status().to_dict()["heading_deg"] == pytest.approx(45.0, abs=0.1)


def test_unknown_heading_sentinel_does_not_become_north() -> None:
    """hdg 65535 means unknown; it must not read as heading 0."""
    controller = _controller()
    _global_position(controller, hdg=65535)

    assert controller._current_heading_deg() is None


# ----------------------------------------------------------------------
# The real-vehicle map gate
# ----------------------------------------------------------------------
def test_real_vehicle_without_gps_does_not_offer_a_map_position() -> None:
    """Local NED alone must not satisfy the map gate on real hardware.

    Before the first fix there is no geodetic anchor, so anything the map draws
    would be an EKF-origin guess drawn as if it were the aircraft's real place.
    """
    controller = _controller(real_vehicle=True)
    controller._handle_message(
        _Msg("LOCAL_POSITION_NED", {"x": 120.0, "y": -45.0, "z": -3.0, "vx": 0.0, "vy": 0.0, "vz": 0.0})
    )

    status = controller.get_status().to_dict()
    # 这是守住"GPS 没到就不许乱画"的那道门。position_source 只是展示用字段，
    # 真正决定前端是否绘制的是 map_position_valid。
    assert status["map_position_valid"] is False


def test_simulation_may_use_local_ned_for_the_map() -> None:
    """The lenient path stays for SITL, where there is no home to wait for."""
    controller = _controller(real_vehicle=False)
    controller._handle_message(
        _Msg("LOCAL_POSITION_NED", {"x": 5.0, "y": 5.0, "z": -3.0, "vx": 0.0, "vy": 0.0, "vz": 0.0})
    )

    assert controller.get_status().to_dict()["map_position_valid"] is True


def test_gps_fix_makes_the_map_position_valid() -> None:
    """A 3D fix on real hardware is what unlocks map display."""
    controller = _controller(real_vehicle=True)
    assert controller.get_status().to_dict()["map_position_valid"] is False

    _global_position(controller, fix=3)

    status = controller.get_status().to_dict()
    assert status["map_position_valid"] is True
    assert status["position_source"] == "gps"


@pytest.mark.parametrize("fix", [0, 1, 2])
def test_incomplete_fix_does_not_unlock_the_map(fix: int) -> None:
    """2D / no fix is not good enough to place an aircraft on a map."""
    controller = _controller(real_vehicle=True)
    _global_position(controller, fix=fix)

    assert controller.get_status().to_dict()["map_position_valid"] is False


def test_stale_global_position_does_not_unlock_the_map() -> None:
    """A fix older than 3 s is stale and must stop counting as position."""
    controller = _controller(real_vehicle=True)
    _global_position(controller)
    assert controller.get_status().to_dict()["map_position_valid"] is True

    # Backdate the message so it looks stale
    table = controller._system_table(1)
    table["last_global_position"] -= 10.0

    assert controller.get_status().to_dict()["map_position_valid"] is False


def test_poor_horizontal_accuracy_rejects_the_fix() -> None:
    """A 60 m accuracy estimate is not a position you want to map an aircraft by."""
    controller = _controller(real_vehicle=True)
    _global_position(controller)
    controller._handle_message(
        _Msg("GPS_RAW_INT", {"fix_type": 3, "satellites_visible": 14, "eph": 8000})  # 80 m
    )

    assert controller.get_status().to_dict()["map_position_valid"] is False


# ----------------------------------------------------------------------
# Home drives the geofence
# ----------------------------------------------------------------------
def test_home_is_decoded_from_the_1e7_wire_format() -> None:
    controller = _controller()
    _home(controller, REAL_LAT_DEG, REAL_LON_DEG)

    home = controller.get_status().to_dict()["home_position"]
    assert home["lat"] == pytest.approx(REAL_LAT_DEG, abs=1e-7)
    assert home["lon"] == pytest.approx(REAL_LON_DEG, abs=1e-7)
    assert home["alt"] == pytest.approx(50.0, abs=1e-6)


def test_geofence_home_lands_where_home_actually_is() -> None:
    """The NED home must be consistent with the GPS home, not a stale guess.

    This is the check that would have caught the fence being pinned to the EKF
    origin: the aircraft is 250 m from home here, so a 100 m fence has to
    reject it, while a point 50 m from home is fine.
    """
    from src.agent.tool_executor import ToolRuntime
    from src.modules.safety_validator import FlightConstraint, SafetyValidator

    controller = _controller()
    # Aircraft reports itself at REAL_LAT/LON; home sits ~250 m to the south
    # (0.00225 deg of latitude is about 250 m), so the two must not collapse.
    _home(controller, REAL_LAT_DEG - 0.00225, REAL_LON_DEG)
    controller._position = {"x": 0.0, "y": 0.0, "z": -30.0, "vx": 0.0, "vy": 0.0, "vz": 0.0}
    _global_position(controller)

    rt = ToolRuntime.__new__(ToolRuntime)
    rt.controller = controller
    rt.safety = SafetyValidator(FlightConstraint(max_distance_from_home=100.0))
    rt._sync_geofence_home()

    home = rt.safety.constraints.home_position
    # Home lies ~250 m south of the aircraft's own NED position
    assert home[0] == pytest.approx(-250.0, abs=3.0)
    assert home[1] == pytest.approx(0.0, abs=1.0)

    # The aircraft is 250 m from home, so a 100 m fence must reject it...
    assert not rt.safety.validate_position(0.0, 0.0, -30.0).is_safe
    # ...while a point 50 m from home is fine.
    assert rt.safety.validate_position(home[0] + 50.0, home[1], -30.0).is_safe


def test_geofence_home_is_unknown_without_gps() -> None:
    """No GPS means no honest answer; returning the EKF origin would be a lie."""
    controller = _controller()
    assert controller.home_position_ned() is None


def test_home_offset_matches_the_great_circle_distance() -> None:
    """The NED offset must equal the real surface distance, not a local linearisation bug."""
    controller = _controller()
    _home(controller, REAL_LAT_DEG, REAL_LON_DEG)
    controller._position = {"x": 0.0, "y": 0.0, "z": -10.0, "vx": 0.0, "vy": 0.0, "vz": 0.0}
    # Aircraft reported 0.001 deg north of home
    _global_position(controller)
    table = controller._telemetry
    table["GLOBAL_POSITION_INT"] = dict(
        table["GLOBAL_POSITION_INT"], lat=REAL_LAT_DEG + 0.001
    )

    home_ned = controller.home_position_ned()
    assert home_ned is not None
    # 0.001 deg latitude is ~111 m, and home lies south of the aircraft
    assert home_ned["x"] == pytest.approx(-111.0, abs=2.0)
