"""Contract tests for the perception module boundaries.

These pin the two protocols that make the perception cluster extractable: the
camera contract the frame source depends on, and the read-only state surface
the Agent/UI depend on.
"""

from __future__ import annotations

from src.modules.frame_source import CaptureSource, FrameSource
from src.modules.perception_axis import PerceptionAxis, to_target_state
from src.modules.perception_service import PerceptionService


def test_nonexecutable_protocols_are_declarative():
    """`CaptureSource`/`FrameSource` are structural: nothing needs to subclass them."""
    assert CaptureSource is not None
    assert FrameSource is not None


def test_perception_axis_satisfies_the_service_protocol():
    """The concrete axis must expose the whole read-only surface the Agent uses."""
    axis = PerceptionAxis(profile=None)

    assert isinstance(axis, PerceptionService)
    for name in ("enabled", "is_online", "health", "snapshot", "pop_events", "start", "stop"):
        assert hasattr(axis, name), f"PerceptionAxis is missing {name}"


def test_disabled_axis_reports_itself_offline():
    """A disabled axis is inert rather than erroring: the Agent reads state, not exceptions."""
    axis = PerceptionAxis(profile=None)

    assert axis.enabled is False
    assert axis.is_online() is False
    health = axis.health()
    assert health["online"] is False
    # These keys must exist even with no engine, so consumers never KeyError.
    for key in ("enabled", "profile", "started", "start_error", "online", "source", "fps"):
        assert key in health, f"health() is missing {key}"
    assert axis.snapshot()["targets"] == []
    assert axis.snapshot()["primary"] is None
    assert axis.pop_events() == []
    assert axis.start() is True  # disabled counts as "nothing to start"


def test_snapshot_frame_size_defaults_to_zero_before_any_frame():
    """Consumers fall back to decoding only when the axis has no size yet."""
    axis = PerceptionAxis(profile=None)
    snapshot = axis.snapshot()

    assert snapshot["frame_width"] == 0
    assert snapshot["frame_height"] == 0


def test_target_state_contract_shape():
    """`to_target_state` is the documented bridge to the autonomy layer."""
    state = to_target_state({"primary": {"class": "car", "confidence": 0.7, "world_pos": {"x": 1, "y": 2, "z": -3}}})

    assert state["visible"] is True
    assert state["best_class"] == "car"
    assert state["best_confidence"] == 0.7
    assert state["estimated_position"] == {"x": 1, "y": 2, "z": -3}

    empty = to_target_state({})
    assert empty["visible"] is False
    assert empty["estimated_position"] is None
