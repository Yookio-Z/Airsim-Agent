"""Deterministic display freshness tests; no camera, detector model, or flight."""

import numpy as np
import pytest

from src.modules import perception_axis
from src.modules.perception_axis import LocalPerceptionEngine, to_target_state


@pytest.fixture
def clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(perception_axis.time, "time", lambda: clock[0])
    monkeypatch.setattr(perception_axis.time, "sleep", lambda _: None)
    return clock


def target(ts=100.0, **kwargs):
    return {
        "class": "car", "confidence": 0.82, "bbox": [10, 20, 60, 80],
        "track_id": 1, "predicted": False, "last_measured_ts": ts, **kwargs,
    }


def test_fresh_capture_drops_stale_cached_overlay_without_detector_tick(clock):
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    engine = LocalPerceptionEngine(object(), detect_fn=lambda _: [])
    engine._latest_targets = [target()]
    engine._latest_primary = engine._latest_targets[0]
    engine._last_detect_ts = 100.0
    engine._cache_annotated(frame, engine._latest_targets, 100.0)
    assert engine.annotated_frame()[1]
    clock[0] += engine._detect_display_ttl + 0.1

    class OneFrame:
        def get_frame(self):
            engine._running = False
            return frame

    engine._frame_source = OneFrame()
    engine._running = True
    engine._loop()
    jpeg, annotations, ts = engine.annotated_frame()
    assert jpeg and annotations == []
    assert ts == clock[0]
    assert not np.any(frame)
    snap = engine.snapshot()
    assert snap["primary"] is None
    assert snap["targets"][0]["visible"] is False
    assert snap["targets"][0]["locked"] is False
    assert snap["capture_age_s"] == 0.0
    assert snap["detection_stale"] is True
    assert engine.health()["detection_age_s"] == snap["detection_age_s"]


@pytest.mark.parametrize("channels", [3, 4])
def test_annotation_never_touches_raw_buffer(clock, channels):
    frame = np.zeros((120, 160, channels), dtype=np.uint8)
    engine = LocalPerceptionEngine(object(), detect_fn=lambda _: [])
    engine._cache_annotated(frame, [target()], 100.0)
    jpeg, annotations, _ = engine.annotated_frame()
    assert jpeg and annotations
    assert not frame.any(), "detector raw buffer must stay clean"


def test_predicted_track_never_visible_or_locked(clock):
    engine = LocalPerceptionEngine(object(), detect_fn=lambda _: [])
    engine._last_detect_ts = 100.0  # source fresh; gating must come per-target
    engine._latest_targets = [
        target(ts=99.0),
        target(ts=100.0, track_id=2, predicted=True, confidence=0.4),
    ]
    snap = engine.snapshot()
    pred = next(t for t in snap["targets"] if t["track_id"] == 2)
    assert pred["visible"] is False and pred["locked"] is False
    assert snap["primary"] is None or snap["primary"]["track_id"] != 2
    # Even if a predicted dict ever reaches primary, the agent contract vetoes it.
    assert to_target_state({"primary": target(ts=100.0, predicted=True)})["visible"] is False


def test_fresh_measured_target_stays_visible_and_primary(clock):
    engine = LocalPerceptionEngine(object(), detect_fn=lambda _: [])
    engine._last_detect_ts = 100.0
    engine._latest_targets = [target(ts=99.9)]
    snap = engine.snapshot()
    assert snap["primary"] is not None
    assert snap["primary"]["visible"] is True and snap["primary"]["locked"] is True
    assert to_target_state(snap)["visible"] is True


def test_stale_detection_source_blocks_target_state_even_with_fresh_track(clock):
    snap = {"primary": target(ts=100.0), "targets": [target(ts=100.0)], "detection_stale": True}
    assert to_target_state(snap)["visible"] is False


def test_fresh_detection_does_not_refresh_unmeasured_targets(clock):
    engine = LocalPerceptionEngine(object())
    engine._last_detect_ts = 100.0
    engine._latest_targets = [target(ts=95.0), target(ts=100.0, track_id=2)]
    engine._latest_primary = engine._latest_targets[0]
    snap = engine.snapshot()
    assert snap["detection_stale"] is False
    assert snap["targets"][0]["visible"] is False
    assert snap["targets"][0]["locked"] is False
    assert snap["primary"]["track_id"] == 2
    engine._cache_annotated(np.zeros((120, 160, 3), dtype=np.uint8), engine._latest_targets)
    assert [t["track_id"] for t in engine.annotated_frame()[1]] == [2]


def test_slow_detection_cannot_freshen_timestamp(clock):
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    engine = LocalPerceptionEngine(object(), detect_fn=lambda _: [])

    def stop_after_first(_frame):
        # Inference finishes after new captures have already arrived.
        clock[0] += 5.0
        engine._latest_frame_ts = clock[0]
        engine._snapshot["timestamp"] = clock[0]
        engine._running = False
        return [{"class": "car", "confidence": 0.9, "bbox": [10, 20, 60, 80]}]

    engine._detect_fn = stop_after_first
    engine._latest_frame = frame
    engine._latest_frame_id = 7
    engine._latest_frame_ts = 90.0  # capture is already 10s old
    engine._running = True
    engine._detect_enabled = True  # 检测现在是按需开启的
    engine._detect_loop()
    snap = engine.snapshot()
    assert snap["detection_timestamp"] == 90.0, "detection age must use source capture time"
    assert snap["detection_age_s"] == 15.0
    assert snap["capture_age_s"] == 0.0
    assert snap["detection_stale"] is True
    assert snap["primary"] is None
    assert snap["targets"][0]["last_measured_ts"] == 90.0
    assert snap["targets"][0]["visible"] is False
    assert snap["targets"][0]["locked"] is False
    engine._cache_annotated(frame, engine._latest_targets)
    assert engine.annotated_frame()[1] == []


def test_cached_pixels_expire_even_when_capture_and_detector_stop(clock):
    import cv2

    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    engine = LocalPerceptionEngine(object())
    engine._cache_annotated(frame, [target()], 100.0)
    assert engine.annotated_frame()[1]
    clock[0] += engine._detect_display_ttl + 0.1
    jpeg, annotations, ts = engine.annotated_frame()
    assert annotations == []
    assert ts == 100.0  # re-encoding must not freshen capture time either
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert not decoded.any(), "expired boxes must be removed from JPEG pixels"


def test_tracker_prediction_retains_measurement_time_but_not_visible_lock(clock):
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    engine = LocalPerceptionEngine(object())

    def tick(detections):
        def detect(raw):
            assert not raw.any()
            engine._running = False
            return detections

        engine._detect_fn = detect
        engine._latest_frame = frame
        engine._latest_frame_ts = clock[0]
        engine._latest_frame_id += 1
        engine._running = True
        engine._detect_enabled = True  # 检测现在是按需开启的
        engine._detect_loop()

    tick([target()])
    assert engine.snapshot()["primary"]["locked"] is True
    clock[0] += 0.2
    tick([])
    snap = engine.snapshot()
    predicted = snap["targets"][0]
    assert predicted["predicted"] is True
    assert predicted["last_measured_ts"] == 100.0
    assert predicted["visible"] is False and predicted["locked"] is False
    assert snap["primary"] is None
    engine._cache_annotated(frame, engine._latest_targets)
    assert engine.annotated_frame()[1] == []
    clock[0] += 0.2
    tick([target()])
    assert engine.snapshot()["primary"]["track_id"] == predicted["track_id"]
