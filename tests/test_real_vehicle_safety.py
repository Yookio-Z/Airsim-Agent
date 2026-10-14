"""Real-vehicle safety hardening.

These tests pin the fixes that matter once the aircraft is real rather than
simulated. Each one corresponds to a defect that would have been invisible in
simulation:

- the geofence circle was pinned to the EKF origin, not the operator's home;
- ``real_vehicle`` could be silently dropped on a reconnect, switching the
  operator approval gate off on a live aircraft;
- in-air disarm had no altitude/landed assertion at all;
- the envelope watchdog reported "landed" even when the landing failed.
"""

from __future__ import annotations

import math

import pytest

from src.agent.run_state import _REAL_VEHICLE_APPROVAL_TOOLS
from src.modules.safety_validator import FlightConstraint


class _Status:
    def __init__(self, data: dict) -> None:
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class _Controller:
    def __init__(self, status: dict | None = None, home_ned: dict | None = None) -> None:
        self._status = status
        self._home_ned = home_ned

    def get_status(self):
        if self._status is None:
            raise RuntimeError("no telemetry")
        return _Status(self._status)

    def home_position_ned(self):
        return self._home_ned

    def home_position(self):  # AirSim exposes the NED form under this name
        return self._home_ned


def _runtime(controller):
    from src.agent.tool_executor import ToolRuntime

    rt = ToolRuntime.__new__(ToolRuntime)
    rt.controller = controller
    rt.safety = type("S", (), {})()
    from src.modules.safety_validator import SafetyValidator

    rt.safety = SafetyValidator(FlightConstraint(max_distance_from_home=100.0))
    return rt


# ----------------------------------------------------------------------
# Geofence centre must follow the real home
# ----------------------------------------------------------------------
def test_geofence_home_follows_flight_controller_home():
    """A home 300 m from the EKF origin must become the fence centre.

    Otherwise "stay within 100 m of home" is actually enforced around a
    different point and the aircraft can leave the operating area unnoticed.
    """
    controller = _Controller(home_ned={"x": 300.0, "y": -120.0, "z": 0.0})
    rt = _runtime(controller)
    assert rt.safety.constraints.home_position == (0.0, 0.0)

    rt._sync_geofence_home()
    assert rt.safety.constraints.home_position == (300.0, -120.0)


def test_geofence_home_survives_a_controller_without_home():
    """A backend that reports no home must not reset the circle to the origin.

    Falling back to (0,0) on a transient miss would move the fence while the
    aircraft is airborne — exactly when it matters most.
    """
    rt = _runtime(_Controller(home_ned={"x": 300.0, "y": -120.0, "z": 0.0}))
    rt._sync_geofence_home()
    assert rt.safety.constraints.home_position == (300.0, -120.0)

    rt.controller = _Controller(home_ned=None)
    rt._sync_geofence_home()
    assert rt.safety.constraints.home_position == (300.0, -120.0)


def test_geofence_home_ignores_non_finite_values():
    rt = _runtime(_Controller(home_ned={"x": float("nan"), "y": 0.0}))
    rt._sync_geofence_home()
    assert rt.safety.constraints.home_position == (0.0, 0.0)


def test_geofence_radius_is_measured_from_home_not_origin():
    """End-to-end: 60 m from home is inside a 100 m fence even at x=300."""
    from src.modules.safety_validator import SafetyValidator

    validator = SafetyValidator(
        FlightConstraint(max_distance_from_home=100.0, home_position=(300.0, 0.0))
    )
    result = validator.validate_position(360.0, 0.0, -10.0)
    assert result.is_safe, "60 m from home must be inside the fence"

    result = validator.validate_position(500.0, 0.0, -10.0)
    assert not result.is_safe, "200 m from home must trip the fence"


# ----------------------------------------------------------------------
# real_vehicle must survive a reconnect
# ----------------------------------------------------------------------
def test_reconnect_without_arguments_keeps_real_vehicle_flag():
    """Emergency-stop recovery calls reconnect() with no args.

    Defaulting the flag to False there turns the operator approval gate off on
    a live aircraft, so the model can then move it with no confirmation.
    """
    from src.agent.tool_executor import ToolRuntime

    rt = ToolRuntime.__new__(ToolRuntime)
    rt._lock = __import__("threading").RLock()
    rt._real_vehicle = True
    rt._last_connect_params = {"url": "serial:COM3:57600", "real_vehicle": True}
    rt.backend_id = "px4_mavlink"
    rt.backend_profile = type("P", (), {"default_connect_params": {}})()
    rt.reset_connection = lambda: None  # type: ignore[method-assign]
    captured: dict = {}

    def _fake_execute(name, params, **kwargs):
        captured["params"] = dict(params)
        return "ok"

    rt.execute = _fake_execute  # type: ignore[method-assign]

    rt.reconnect()
    assert captured["params"]["real_vehicle"] is True
    assert rt._real_vehicle is True


def test_reconnect_explicit_false_still_downgrades():
    """An explicit False must win — the fix is about omission, not override."""
    from src.agent.tool_executor import ToolRuntime

    rt = ToolRuntime.__new__(ToolRuntime)
    rt._lock = __import__("threading").RLock()
    rt._real_vehicle = True
    rt._last_connect_params = {"url": "udp:127.0.0.1:14550", "real_vehicle": True}
    rt.backend_id = "px4_mavlink"
    rt.backend_profile = type("P", (), {"default_connect_params": {}})()
    rt.reset_connection = lambda: None  # type: ignore[method-assign]
    captured: dict = {}
    rt.execute = lambda name, params, **kw: captured.setdefault("params", dict(params)) or "ok"  # type: ignore[method-assign]

    rt.reconnect(url="udp:127.0.0.1:14550", real_vehicle=False)
    assert captured["params"]["real_vehicle"] is False
    assert rt._real_vehicle is False


# ----------------------------------------------------------------------
# In-air disarm guard
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"position_ned": {"x": 0, "y": 0, "z": -20.0}, "landed_state": "airborne"}, True),
        ({"position_ned": {"x": 0, "y": 0, "z": -0.1}, "landed_state": "landed"}, False),
        ({"position_ned": {"x": 0, "y": 0, "z": -0.2}}, False),   # altitude says ground
        ({"position_ned": {"x": 0, "y": 0, "z": -0.2}, "flying": True}, True),
        ({}, None),                                            # no signal at all
    ],
)
def test_airborne_detection(status, expected):
    rt = _runtime(_Controller(status=status))
    assert rt._is_vehicle_airborne() is expected


def test_disarm_is_blocked_while_airborne():
    rt = _runtime(_Controller(status={"position_ned": {"x": 0, "y": 0, "z": -20.0}}))
    result = rt.validate("drone_disarm", {})
    assert result["level"] == "danger"
    assert any("解锁" in v for v in result["violations"])


def test_disarm_is_blocked_when_state_is_unknown():
    """No telemetry must not read as "safe to disarm" — that is a free fall."""
    rt = _runtime(_Controller(status=None))
    result = rt.validate("drone_disarm", {})
    assert result["level"] == "danger"


def test_disarm_allowed_on_ground():
    rt = _runtime(_Controller(status={"position_ned": {"x": 0, "y": 0, "z": -0.1}}))
    result = rt.validate("drone_disarm", {})
    assert result["level"] == "safe", result["violations"]


# ----------------------------------------------------------------------
# Approval coverage
# ----------------------------------------------------------------------
def test_visual_approach_requires_signature_on_real_vehicle():
    """靠近目标在真机上就是真的位移，必须走签字门。

    抵近工具已随 YOLO 移除；现在靠近由 drone_move_relative 完成（VLM 取点 +
    深度 + 有界一步），它同样在签字集里——不能让"靠近目标"绕过审批。
    """
    assert "drone_approach_target" not in _REAL_VEHICLE_APPROVAL_TOOLS
    assert "drone_move_relative" in _REAL_VEHICLE_APPROVAL_TOOLS
    assert "drone_fly_to" in _REAL_VEHICLE_APPROVAL_TOOLS
