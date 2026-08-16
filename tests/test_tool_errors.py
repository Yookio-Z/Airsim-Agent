"""Tests for the tool layer: structured error codes, output schema validation,
and bounded read-only timeout retry."""

from __future__ import annotations

import json
import threading
import time

import pytest

from src.agent.tool_executor import (
    TOOL_OUTPUT_SCHEMAS,
    ToolCollector,
    ToolCallResult,
    ToolRuntime,
)
from src.modules.safety_validator import FlightConstraint, SafetyValidator


def _runtime(collector: ToolCollector) -> ToolRuntime:
    rt = object.__new__(ToolRuntime)
    rt.backend_id = "fake"
    rt.collector = collector
    rt._lock = threading.RLock()
    rt.safety = SafetyValidator(
        FlightConstraint(max_altitude=50.0, min_altitude=0.5, max_velocity=8.0, max_distance_from_home=100.0)
    )
    rt.ensure_ready = lambda: True  # type: ignore[method-assign]
    rt._camera_source_enabled = lambda: False  # type: ignore[method-assign]
    rt.controller = None
    rt._last_connect_params = {}
    rt._real_vehicle = False
    rt.camera_controller = None
    rt.backend_profile = None
    rt.available = True
    rt.init_error = ""
    return rt


# ---------------------------------------------------------------------------
# error code classification
# ---------------------------------------------------------------------------


def test_error_code_for_mappings():
    assert ToolRuntime._error_code_for("drone_get_status", {"status": "ok"}, True) == ""
    assert ToolRuntime._error_code_for("x", {"status": "blocked", "message": "nope"}, False) == "BLOCKED"
    assert ToolRuntime._error_code_for("x", {"status": "cancelled"}, False) == "CANCELLED"
    assert ToolRuntime._error_code_for("x", {"status": "error", "message": "operation timed out"}, False) == "TIMEOUT"
    assert ToolRuntime._error_code_for("x", {"status": "error", "message": "not connected"}, False) == "NOT_CONNECTED"
    assert ToolRuntime._error_code_for("x", {"status": "error", "message": "boom"}, False) == "TOOL_ERROR"


def test_classify_exception_mappings():
    assert ToolRuntime._classify_exception("x", "rpc call timed out") == "TIMEOUT"
    assert ToolRuntime._classify_exception("x", "connection refused") == "CONNECTION"
    assert ToolRuntime._classify_exception("x", "unexpected null") == "TOOL_ERROR"


def test_retryable_read_tools_exclude_flight():
    retryable = ToolRuntime._RETRYABLE_READ_TOOLS
    assert "drone_get_status" in retryable
    assert "airsim_task_status" in retryable
    assert "drone_takeoff" not in retryable
    assert "drone_move_relative" not in retryable
    assert "drone_fly_to" not in retryable


# ---------------------------------------------------------------------------
# bounded retry
# ---------------------------------------------------------------------------


def test_read_tool_timeout_is_retried_once():
    calls = {"n": 0}

    def flaky(**kwargs) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rpc call timed out")
        return '{"status": "ok"}'

    collector = ToolCollector()
    collector.tools["drone_get_status"] = flaky
    rt = _runtime(collector)
    result = rt.execute("drone_get_status", {}, allow_reconnect=False)
    assert result.ok is True
    assert calls["n"] == 2


def test_flight_tool_timeout_is_not_retried():
    calls = {"n": 0}

    def flaky(**kwargs) -> str:
        calls["n"] += 1
        raise RuntimeError("rpc call timed out")

    collector = ToolCollector()
    collector.tools["drone_move_relative"] = flaky
    rt = _runtime(collector)
    result = rt.execute("drone_move_relative", {"forward_m": 1.0}, allow_reconnect=False)
    assert result.ok is False
    assert result.error_code == "TIMEOUT"
    assert calls["n"] == 1


def test_non_timeout_error_is_not_retried():
    calls = {"n": 0}

    def failing(**kwargs) -> str:
        calls["n"] += 1
        raise RuntimeError("unexpected null")

    collector = ToolCollector()
    collector.tools["drone_get_status"] = failing
    rt = _runtime(collector)
    result = rt.execute("drone_get_status", {}, allow_reconnect=False)
    assert result.ok is False
    assert result.error_code == "TOOL_ERROR"
    assert calls["n"] == 1


def test_status_blocked_result_carries_error_code():
    def blocked(**kwargs) -> str:
        return '{"status": "blocked", "message": "safety gate"}'

    collector = ToolCollector()
    collector.tools["drone_get_status"] = blocked
    rt = _runtime(collector)
    result = rt.execute("drone_get_status", {})
    assert result.ok is False
    assert result.error_code == "BLOCKED"
    assert result.to_dict()["error_code"] == "BLOCKED"


# ---------------------------------------------------------------------------
# output schema validation
# ---------------------------------------------------------------------------


def test_output_schema_records_validation_errors():
    def bad_status(**kwargs) -> str:
        return '{"status": "ok", "flying": "yes"}'

    collector = ToolCollector()
    collector.tools["drone_get_status"] = bad_status
    rt = _runtime(collector)
    result = rt.execute("drone_get_status", {})
    # shape violations gate the result: ok=False + INVALID_TOOL_OUTPUT
    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_OUTPUT"
    assert "validation_errors" in result.data
    assert any("expected boolean" in v for v in result.data["validation_errors"])


def test_output_schema_accepts_good_shapes():
    def good_status(**kwargs) -> str:
        return '{"status": "ok", "flying": false, "armed": true, "position_ned": {"x": 1, "y": 2, "z": -3}}'

    collector = ToolCollector()
    collector.tools["drone_get_status"] = good_status
    rt = _runtime(collector)
    result = rt.execute("drone_get_status", {})
    assert result.ok is True
    assert "validation_errors" not in result.data


def test_vlm_confirm_output_schema_exists():
    schema = TOOL_OUTPUT_SCHEMAS["airsim_vlm_confirm_target"]
    assert schema["properties"]["target_found"]["type"] == "boolean"
    assert schema["properties"]["confidence"]["type"] == "number"


# ---------------------------------------------------------------------------
# reconnect retry safety (M6): control tools are never auto-redispatched
# ---------------------------------------------------------------------------


def test_control_tool_not_redispatched_after_reconnect():
    collector = ToolCollector()

    def flaky(**kwargs) -> str:
        raise RuntimeError("connection refused")

    collector.tools["drone_move_relative"] = flaky
    rt = _runtime(collector)
    rt.reconnect = lambda: ToolCallResult("drone_connect", {}, True, {"status": "ok"}, time.time(), time.time())  # type: ignore[method-assign]
    failed = ToolCallResult(
        "drone_move_relative",
        {"forward_m": 1.0},
        False,
        {"status": "error", "message": "connection refused"},
        time.time(),
        time.time(),
        error_code="CONNECTION",
    )
    result = rt._retry_after_reconnect("drone_move_relative", {"forward_m": 1.0}, False, None, failed)
    # reconnect happened but the command was NOT re-dispatched
    assert result.ok is False
    assert result.data["auto_reconnect"]["redispatched"] is False
    assert "not auto-redispatched" in result.data["auto_reconnect"]["reason"]


def test_read_tool_is_redispatched_after_reconnect():
    collector = ToolCollector()
    calls = {"n": 0}

    def status_fn(**kwargs) -> str:
        calls["n"] += 1
        return '{"status": "ok", "flying": false}'

    collector.tools["drone_get_status"] = status_fn
    rt = _runtime(collector)
    rt.reconnect = lambda: ToolCallResult("drone_connect", {}, True, {"status": "ok"}, time.time(), time.time())  # type: ignore[method-assign]
    failed = ToolCallResult(
        "drone_get_status",
        {},
        False,
        {"status": "error", "message": "connection refused"},
        time.time(),
        time.time(),
        error_code="CONNECTION",
    )
    result = rt._retry_after_reconnect("drone_get_status", {}, False, None, failed)
    assert result.ok is True
    assert result.data["auto_reconnect"]["redispatched"] is True
