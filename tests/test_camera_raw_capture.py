from types import SimpleNamespace

import numpy as np
import pytest

from src.modules.airsim_controller import AirSimController
from src.modules.frame_source import LazyControllerFrameSource


def test_raw_frame_preserves_bgr_order_and_owns_memory():
    controller = AirSimController()
    payload = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(width=2, height=2, image_data_uint8=payload)

    controller._capture_response = capture
    frame = controller.capture_frame("CameraImage")
    assert frame.tolist() == [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]
    assert frame.flags.writeable
    assert frame.flags.owndata
    assert calls == [{"compressed": False}]
    controller._executor.shutdown()


def test_raw_frame_rejects_malformed_payload():
    controller = AirSimController()
    controller._capture_response = lambda *a, **kw: SimpleNamespace(width=2, height=2, image_data_uint8=b"123")
    with pytest.raises(ValueError, match="BGR"):
        controller.capture_frame()
    controller._executor.shutdown()


def test_photo_contract_stays_encoded():
    controller = AirSimController()
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(image_data_uint8=b"png-data")

    controller._capture_response = capture
    assert controller.capture_image() == b"png-data"
    assert calls == [{"compressed": True}]
    controller._executor.shutdown()


def test_local_source_uses_raw_and_switch_restores_png(monkeypatch):
    import cv2

    frame = np.zeros((4, 4, 3), np.uint8)
    calls = []
    controller = SimpleNamespace(
        _ip="127.0.0.1",
        capture_frame=lambda *a, **kw: calls.append("raw") or frame,
        capture_image=lambda *a, **kw: calls.append("png") or cv2.imencode(".png", frame)[1].tobytes(),
    )
    source = LazyControllerFrameSource(lambda: controller)
    source.open()
    monkeypatch.setenv("DRONE_PERCEPTION_RAW_SCENE", "1")
    assert source.get_frame() is frame
    monkeypatch.setenv("DRONE_PERCEPTION_RAW_SCENE", "0")
    assert np.array_equal(source.get_frame(), frame)
    assert calls == ["raw", "png"]


def test_default_capture_leaves_native_gimbal_alone(monkeypatch):
    # AirSim's own "Gimbal" block stabilises the camera at render rate. The sampled
    # software path must stay out of the way unless it is explicitly asked for:
    # simSetCameraPose overwrites the engine gimbal target while it runs.
    monkeypatch.delenv("DRONE_CAMERA_STABILIZATION", raising=False)
    frame = np.zeros((4, 4, 3), np.uint8)
    calls = []
    controller = SimpleNamespace(
        _ip="127.0.0.1",
        configure_camera_stabilization=lambda *a, **kw: calls.append((a, kw)),
        capture_frame=lambda *a, **kw: frame,
        capture_image=lambda *a, **kw: pytest.fail("raw scene path expected"),
    )
    source = LazyControllerFrameSource(lambda: controller)
    source.open()
    assert source.get_frame() is frame
    assert calls == []


def test_capture_opts_into_software_stabilization_only_when_requested(monkeypatch):
    monkeypatch.setenv("DRONE_CAMERA_STABILIZATION", "1")
    frame = np.zeros((4, 4, 3), np.uint8)
    calls = []
    controller = SimpleNamespace(
        _ip="127.0.0.1",
        configure_camera_stabilization=lambda *a, **kw: calls.append((a, kw)),
        capture_frame=lambda *a, **kw: frame,
        capture_image=lambda *a, **kw: pytest.fail("raw scene path expected"),
    )
    source = LazyControllerFrameSource(lambda: controller)
    source.open()
    assert source.get_frame() is frame
    assert calls == [(("0",), {"enabled": True})]


def test_remote_source_preserves_compressed_transport(monkeypatch):
    import cv2

    monkeypatch.setenv("DRONE_PERCEPTION_RAW_SCENE", "1")
    frame = np.zeros((4, 4, 3), np.uint8)
    controller = SimpleNamespace(
        _ip="192.0.2.1",
        capture_frame=lambda *a, **kw: pytest.fail("remote transport must remain compressed"),
        capture_image=lambda *a, **kw: cv2.imencode(".png", frame)[1].tobytes(),
    )
    source = LazyControllerFrameSource(lambda: controller)
    source.open()
    assert np.array_equal(source.get_frame(), frame)
