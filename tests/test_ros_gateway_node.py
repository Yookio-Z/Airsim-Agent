from __future__ import annotations

import importlib.util
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path


def _load_gateway_module():
    path = Path(__file__).resolve().parents[1] / "ros2" / "airsim_agent_ros" / "airsim_agent_ros" / "gateway_node.py"
    spec = importlib.util.spec_from_file_location("airsim_agent_ros_gateway_node_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gateway_for_status(module):
    gateway = object.__new__(module.Px4RosGatewayNode)
    now = time.time()
    gateway._lock = threading.RLock()
    gateway._task_lock = threading.RLock()
    gateway._local_position = {"x": 1.0, "y": 2.0, "z": -3.0, "vx": 0.0, "vy": 0.0, "vz": 0.0}
    gateway._vehicle_status = {
        "nav_state_name": "NAVIGATION_STATE_POSCTL",
        "arming_state_name": "ARMING_STATE_STANDBY",
        "failsafe": False,
        "pre_flight_checks_pass": False,
    }
    gateway._attitude = {"roll": 0.0, "pitch": 0.0, "yaw": 0.1}
    gateway._battery_status = {}
    gateway._global_position = {}
    gateway._last_local_position_at = now
    gateway._last_vehicle_status_at = now - 60.0
    gateway._last_attitude_at = now
    gateway._last_battery_status_at = 0.0
    gateway._last_global_position_at = 0.0
    gateway._last_scan_at = 0.0
    gateway._scan_summary = {}
    gateway._offboard_stream_enabled = False
    gateway._offboard_deadline_at = None
    gateway._last_setpoint_command_at = 0.0
    gateway._last_offboard_watchdog = None
    gateway._offboard_tick_times = deque()
    gateway._no_fly_zones = []
    gateway._tasks = {}
    gateway._task_queue = deque()
    gateway.setpoint_hz = 10.0
    gateway.offboard_watchdog_sec = 1.5
    gateway.px4_topic_stale_sec = 5.0
    gateway.px4_status_stale_sec = 30.0
    gateway.require_preflight_for_arm = False
    gateway.min_battery_remaining = -1.0
    gateway.min_battery_voltage_v = 0.0
    return gateway


def test_px4_seen_allows_stale_vehicle_status_when_core_telemetry_is_fresh():
    module = _load_gateway_module()
    payload = _gateway_for_status(module).status_payload()

    assert payload["ok"] is True
    assert payload["data"]["px4_seen"] is True
    assert payload["data"]["control_ready"] is True
    assert payload["data"]["vehicle_status_fresh"] is False


def test_status_payload_derives_heading_degrees_from_attitude_yaw():
    module = _load_gateway_module()
    gateway = _gateway_for_status(module)
    gateway._attitude["yaw"] = math.pi / 2.0

    payload = gateway.status_payload()

    assert payload["data"]["heading_deg"] == 90.0


def test_rotate_to_waits_for_yaw_error_when_requested():
    module = _load_gateway_module()
    gateway = _gateway_for_status(module)
    gateway._attitude["yaw"] = math.radians(44.0)
    setpoints = []
    waits = []

    gateway._check_command_safety = lambda target, action, require_preflight=False: {"ok": True}
    gateway._set_position_setpoint = lambda x, y, z, yaw, max_age_sec=None: setpoints.append(
        {"x": x, "y": y, "z": z, "yaw": yaw, "max_age_sec": max_age_sec}
    )
    gateway._ensure_offboard_mode = lambda: True
    gateway._wait_until = lambda predicate, timeout_sec, interval_sec=0.1: waits.append(timeout_sec) or predicate()

    result = gateway.rotate_to({"heading_deg": 45.0, "timeout_sec": 2.0, "wait": True})

    assert result["ok"] is True
    assert waits == [2.0]
    assert math.isclose(setpoints[0]["yaw"], math.radians(45.0))
    assert setpoints[0]["max_age_sec"] == 4.0


def test_json_safe_replaces_non_finite_numbers():
    module = _load_gateway_module()

    assert module._json_safe({"x": math.nan, "items": [math.inf, -math.inf, 1.0]}) == {
        "x": None,
        "items": [None, None, 1.0],
    }
