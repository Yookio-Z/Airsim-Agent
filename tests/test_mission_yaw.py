"""Native-mission yaw: unspecified waypoint heading must defer to the FC.

The waypoint panel has no yaw field, so every uploaded mission item used to
carry ``param4 = 0.0``. In MAVLink a numeric param4 is an *order*: "turn to
this absolute heading at this waypoint". Uploading 0.0 therefore pinned the
nose to due north for the whole mission — the exact "机头朝向一直一个角度"
symptom, and the flight never turns regardless of MPC_YAW_MODE.

The convention (used by QGC's Yaw field, ``nanUnchanged``) is that param4 = NaN
means "unspecified"; PX4 then applies MPC_YAW_MODE, whose default (0) faces the
next waypoint.
"""

from __future__ import annotations

import math

import pytest
from pymavlink import mavutil

from src.modules.mavlink_controller import _mission_params_for_command


WAYPOINT = mavutil.mavlink.MAV_CMD_NAV_WAYPOINT
TAKEOFF = mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
LAND = mavutil.mavlink.MAV_CMD_NAV_LAND
RTL = mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH


def test_unspecified_yaw_is_nan_not_north():
    """A missing yaw must not become an order to face north."""
    _, _, _, param4 = _mission_params_for_command(WAYPOINT, {"hold_s": 0, "acceptance_radius_m": 2})
    assert math.isnan(param4)


def test_explicit_yaw_is_sent_as_given():
    """When something does specify a heading, it must survive verbatim."""
    _, _, _, param4 = _mission_params_for_command(WAYPOINT, {"yaw_deg": 137.0})
    assert param4 == pytest.approx(137.0)


def test_zero_yaw_is_honoured_not_treated_as_missing():
    """yaw_deg=0 is a real order ("face north here") and must not be dropped."""
    _, _, _, param4 = _mission_params_for_command(WAYPOINT, {"yaw_deg": 0.0})
    assert param4 == pytest.approx(0.0)


@pytest.mark.parametrize("command", [TAKEOFF, LAND])
def test_takeoff_and_land_also_defer_when_unspecified(command):
    """Takeoff/landing param4 is a yaw too; 0.0 would pin the nose on the ground."""
    _, _, _, param4 = _mission_params_for_command(command, {})
    assert math.isnan(param4)


def test_rtl_has_no_yaw_parameter():
    """RTL yaw is governed by the FC; param4 stays a plain 0."""
    params = _mission_params_for_command(RTL, {})
    assert params == (0.0, 0.0, 0.0, 0.0)


def test_waypoint_params_keep_hold_and_acceptance():
    """The NaN change must not disturb the other waypoint fields."""
    param1, param2, param3, param4 = _mission_params_for_command(
        WAYPOINT, {"hold_s": 4.0, "acceptance_radius_m": 3.5}
    )
    assert param1 == pytest.approx(4.0)
    assert param2 == pytest.approx(3.5)
    assert param3 == pytest.approx(0.0)
    assert math.isnan(param4)


def test_pymavlink_accepts_nan_param4():
    """The real send path must encode NaN rather than choke on it."""
    mav = mavutil.mavlink.MAVLink(file=None, srcSystem=255, srcComponent=190)
    captured: dict = {}

    def _send(*args, **kwargs):
        captured["args"] = args

    mav.mav = type("M", (), {})()
    mav.mav.mission_item_int_send = _send
    mav.mav.mission_item_int_send(
        1, 1, 0, mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT, WAYPOINT, 0, 1,
        0.0, 2.0, 0.0, float("nan"), 488583700, 22944810, 35.0,
    )
    assert math.isnan(captured["args"][10])
