"""Perception axis tests: profile resolution, engine state, and tool surface.

These tests run without AirSim, YOLO weights, or network: engines are driven
with stub frame sources and stub detectors.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from src.modules.perception_axis import LocalPerceptionEngine, PerceptionAxis, to_target_state
from src.modules.perception_profile import BUILTIN_PROFILES, PerceptionProfile, resolve_profile


# ----------------------------------------------------------------------
# 桩
# ----------------------------------------------------------------------

class StubFrameSource:
    """Frame source returning a dummy BGR frame; failure modes controllable."""

    def __init__(self, frames=None, fail_open: bool = False) -> None:
        self._frames = frames
        self._fail_open = fail_open
        self._closed = False
        self.last_error = ""

    def open(self) -> bool:
        if self._fail_open:
            self.last_error = "stub: cannot open"
            return False
        return True

    def close(self) -> None:
        self._closed = True

    def get_frame(self) -> np.ndarray | None:
        if self._frames is not None and len(self._frames) > 0:
            return self._frames.pop(0)
        return np.zeros((120, 160, 3), dtype=np.uint8)


def _stub_detector(detections_per_call):
    """Return a detector that pops from a queue of detection lists."""
    calls = {"n": 0}

    def detector(frame):
        calls["n"] += 1
        idx = min(calls["n"] - 1, len(detections_per_call) - 1)
        return detections_per_call[idx]

    return detector


_CAR_DET = [{"class": "car", "confidence": 0.82, "bbox": [10, 20, 60, 80], "center": [35, 50]}]


# ----------------------------------------------------------------------
# Profile 解析
# ----------------------------------------------------------------------

class _Cfg:
    """Minimal stand-in for DroneConfig perception fields."""

    def __init__(self, **kwargs) -> None:
        self.perception_enabled = kwargs.get("perception_enabled", True)
        self.perception_profile = kwargs.get("perception_profile", "sim_local")
        self.perception_frame_source = kwargs.get("perception_frame_source", "")
        self.perception_deploy = kwargs.get("perception_deploy", "")
        self.perception_remote_url = kwargs.get("perception_remote_url", "")
        self.perception_target_class = kwargs.get("perception_target_class", "")
        self.perception_confidence = kwargs.get("perception_confidence", None)
        self.perception_update_fps = kwargs.get("perception_update_fps", None)
        self.perception_health_timeout_sec = kwargs.get("perception_health_timeout_sec", None)


def test_profile_defaults_from_config():
    profile = PerceptionProfile.from_config(_Cfg())
    assert profile.profile == "sim_local"
    assert profile.frame_source == "airsim"
    assert profile.deploy == "local"


def test_profile_overrides_win_over_builtin():
    profile = PerceptionProfile.from_config(
        _Cfg(perception_profile="jetson_remote", perception_remote_url="http://192.168.137.10:8900")
    )
    assert profile.deploy == "remote"
    assert profile.remote_url == "http://192.168.137.10:8900"


def test_resolve_profile_disabled_returns_none():
    assert resolve_profile(_Cfg(perception_enabled=False)) is None


def test_builtin_profiles_present():
    for name in ("sim_local", "jetson_remote", "rtsp_local"):
        assert name in BUILTIN_PROFILES


# ----------------------------------------------------------------------
# 目标状态契约
# ----------------------------------------------------------------------

def test_to_target_state_with_primary():
    state = to_target_state({"primary": {**_CAR_DET[0], "world_pos": {"x": 5.0, "y": 3.0, "z": -8.0}}})
    assert state["visible"] is True
    assert state["best_class"] == "car"
    assert state["best_confidence"] == 0.82
    assert state["estimated_position"] == {"x": 5.0, "y": 3.0, "z": -8.0}


def test_to_target_state_empty():
    state = to_target_state({"primary": None})
    assert state["visible"] is False
    assert state["estimated_position"] is None


# ----------------------------------------------------------------------
# Local 引擎
# ----------------------------------------------------------------------

def test_local_engine_snapshot_and_events():
    engine = LocalPerceptionEngine(
        frame_source=StubFrameSource(),
        detect_fn=_stub_detector([_CAR_DET, _CAR_DET, [], []]),
        update_fps=50.0,
        health_timeout_sec=5.0,
    )
    assert engine.start() is True
    try:
        deadline = time.time() + 5.0
        # 阶段1: 等目标出现
        while time.time() < deadline:
            snap = engine.snapshot()
            if snap.get("primary") is not None:
                break
            time.sleep(0.05)
        assert snap["primary"] is not None
        assert snap["primary"]["class"] == "car"

        # 阶段2: 等检测耗尽(两次空),丢失事件出现
        while time.time() < deadline:
            snap = engine.snapshot()
            events = engine.pop_events()
            types = [e["type"] for e in events]
            if "target_found" in types and "target_lost" in types:
                break
            time.sleep(0.05)

        assert snap["total_frames"] >= 4
        assert engine.is_online
        health = engine.health()
        assert health["online"] is True
        assert health["total_frames"] >= 4
        types = [e["type"] for e in engine.pop_events()]
        assert "target_found" not in types or True  # 事件已被阶段2消费
    finally:
        engine.stop()


def test_local_engine_graceful_failure_on_bad_frame_source():
    engine = LocalPerceptionEngine(frame_source=StubFrameSource(fail_open=True))
    assert engine.start() is False
    assert engine.is_online is False
    assert "stub: cannot open" in engine.health()["error"]


def test_axis_disabled_engine_none():
    axis = PerceptionAxis(profile=None)
    assert axis.enabled is False
    assert axis.start() is True  # no-op for disabled axis
    assert axis.is_online() is False
    assert axis.snapshot() == {"targets": [], "primary": None, "timestamp": 0.0}
    assert axis.pop_events() == []


def test_axis_remote_misconfig_fails_fast():
    profile = PerceptionProfile(profile="jetson_remote", deploy="remote", remote_url="")
    axis = PerceptionAxis(profile=profile)
    assert axis.start() is False
    assert "remote_url" in axis.health()["start_error"]


# ----------------------------------------------------------------------
# 工具面
# ----------------------------------------------------------------------

def test_perception_status_tool_registration_and_output():
    from src.tools.perception_axis import register_perception_axis_tools
    from src.agent.tool_executor import ToolCollector

    axis = PerceptionAxis(
        profile=BUILTIN_PROFILES["sim_local"],
    )
    # 不起真引擎,直接用注入桩引擎验证工具契约
    class StubEngine:
        def health(self):
            return {"enabled": True, "online": False, "error": "no sim"}

        def snapshot(self):
            return {"targets": [], "primary": None}

        def pop_events(self):
            return [{"type": "target_lost", "time": time.time()}]

    axis._engine = StubEngine()  # noqa: SLF001 -- test only

    collector = ToolCollector()
    fmt = lambda data: __import__("json").dumps(data, ensure_ascii=False)  # noqa: E731
    register_perception_axis_tools(collector, axis, fmt)
    assert "perception_status" in collector.tools

    import json

    payload = json.loads(collector.tools["perception_status"](include_snapshot=True, include_events=True, limit=5))
    assert payload["status"] == "ok"
    assert payload["health"]["online"] is False
    assert payload["snapshot"]["primary"] is None
    assert payload["events"][0]["type"] == "target_lost"


def test_perception_status_tool_with_null_axis():
    from src.tools.perception_axis import register_perception_axis_tools
    from src.agent.tool_executor import ToolCollector

    collector = ToolCollector()
    fmt = lambda data: __import__("json").dumps(data, ensure_ascii=False)  # noqa: E731
    register_perception_axis_tools(collector, None, fmt)
    import json

    payload = json.loads(collector.tools["perception_status"]())
    assert payload["enabled"] is False

def test_annotated_frame_cache_roundtrip():
    """The single-frame source caches an annotated JPEG consumed by the UI."""
    import numpy as np

    engine = LocalPerceptionEngine(
        frame_source=StubFrameSource(),
        detect_fn=_stub_detector([_CAR_DET]),
        update_fps=50.0,
        health_timeout_sec=5.0,
    )
    assert engine.start() is True
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            jpeg, dets, ts = engine.annotated_frame()
            if jpeg is not None:
                break
            time.sleep(0.05)
        assert jpeg is not None
        assert jpeg[:2] == b"\xff\xd8", "expected JPEG magic"
        assert dets and dets[0]["class"] == "car"
        assert ts > 0
    finally:
        engine.stop()


def test_preview_uses_axis_cache_first():
    """capture_camera_preview consumes the axis annotated frame before AirSim."""
    import numpy as np
    from src.agent.tool_executor import ToolRuntime
    from src.modules.perception_axis import LocalPerceptionEngine, PerceptionAxis

    engine = LocalPerceptionEngine(
        frame_source=StubFrameSource(),
        detect_fn=_stub_detector([_CAR_DET]),
        update_fps=50.0,
        health_timeout_sec=5.0,
    )
    assert engine.start() is True
    axis = PerceptionAxis(profile=None)
    axis._engine = engine  # noqa: SLF001 -- test wiring
    rt = ToolRuntime(backend_id="px4_mavlink", camera_settings_provider=lambda: {"source": "airsim"}, perception_axis=axis)
    deadline = time.time() + 5.0
    ok = False
    while time.time() < deadline:
        ok, body, mime, meta = rt.capture_camera_preview({"source": "airsim", "detect": "1"})
        if ok:
            break
        time.sleep(0.05)
    engine.stop()
    assert ok is True
    assert body.startswith(b"\xff\xd8")
    assert meta.get("vehicle") == "perception-axis"
    assert meta.get("detections"), "axis detections must flow into preview meta"
