"""RTSP camera source tests: RtspFrameSource open/read/reconnect, and the
RtspCameraController capture path used by the camera preview pipeline."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src.modules.frame_source import RtspFrameSource
from src.modules.rtsp_camera_controller import RtspCameraController


class _FakeVideoCapture:
    """Mimics cv2.VideoCapture for RTSP: open, read frames, isOpened."""

    def __init__(self, frame: np.ndarray | None, opened: bool = True) -> None:
        self._frame = frame
        self._opened = opened
        self._reads = 0

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        self._reads += 1
        if self._frame is None or self._reads > 3:
            return False, None
        return True, self._frame.copy()

    def release(self) -> None:
        self._opened = False


def _bgr_frame() -> np.ndarray:
    return np.full((120, 160, 3), 90, dtype=np.uint8)


def test_rtsp_source_open_fails_when_capture_unavailable(monkeypatch) -> None:
    import cv2 as cv2_module

    monkeypatch.setattr(cv2_module, "VideoCapture", lambda url: _FakeVideoCapture(None, opened=False))
    source = RtspFrameSource("rtsp://host/stream")
    assert source.open() is False
    assert source.last_error
    assert source.get_frame() is None


def test_rtsp_source_open_and_read_frame(monkeypatch) -> None:
    import cv2 as cv2_module

    capture = _FakeVideoCapture(_bgr_frame())
    monkeypatch.setattr(cv2_module, "VideoCapture", lambda url: capture)
    source = RtspFrameSource("rtsp://host/stream")
    assert source.open() is True
    frame = source.get_frame()
    assert frame is not None
    assert frame.shape == (120, 160, 3)
    assert source.is_open is True
    source.close()
    assert source.is_open is False


def test_rtsp_source_reads_rgb_not_bgr(monkeypatch) -> None:
    import cv2 as cv2_module

    bgr = np.zeros((10, 10, 3), dtype=np.uint8)
    bgr[:, :, 0] = 255  # pure blue in BGR
    monkeypatch.setattr(cv2_module, "VideoCapture", lambda url: _FakeVideoCapture(bgr))
    source = RtspFrameSource("rtsp://host/stream")
    assert source.open() is True
    frame = source.get_frame()
    assert frame is not None
    # BGR(255,0,0) -> RGB(0,0,255): red channel holds the blue input
    assert int(frame[0, 0, 2]) == 255
    assert int(frame[0, 0, 0]) == 0


def test_rtsp_controller_capture_returns_jpeg(monkeypatch) -> None:
    import cv2 as cv2_module

    monkeypatch.setattr(cv2_module, "VideoCapture", lambda url: _FakeVideoCapture(_bgr_frame()))
    controller = RtspCameraController("rtsp://host/stream")
    info = controller.connect()
    assert info.connected is True
    assert controller.is_connected is True

    raw = controller.capture_image(camera_name="0", image_type=0, timeout=2.0)
    assert raw is not None
    assert raw[:2] == b"\xff\xd8"  # JPEG SOI marker

    # 真实相机没有 depth/segmentation：非 scene 拒绝
    assert controller.capture_image(image_type="depth") is None


def test_rtsp_controller_rejects_wrong_image_type(monkeypatch) -> None:
    import cv2 as cv2_module

    monkeypatch.setattr(cv2_module, "VideoCapture", lambda url: _FakeVideoCapture(_bgr_frame()))
    controller = RtspCameraController("rtsp://host/stream")
    controller.connect()
    assert controller.capture_image(image_type="segmentation") is None
    assert controller.capture_image(image_type=2) is None


def test_rtsp_controller_status_reports_stream() -> None:
    controller = RtspCameraController("rtsp://host/stream")
    status = controller.get_status()
    assert status.extra["backend"] == "rtsp"
    assert status.extra["stream_url"] == "rtsp://host/stream"
    assert status.extra["connection_error"]
    assert controller.list_vehicles() == ["rtsp"]
