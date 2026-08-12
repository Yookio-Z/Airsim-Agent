"""Frame source abstraction for the video pipeline.

The UI preview and camera tools only care about "give me the latest frame as
BGR numpy". AirSim RPC frames and real onboard-camera RTSP streams (Jetson +
图传) both implement this protocol, so the rest of the pipeline stays
source-agnostic.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

import numpy as np

from src.logging_config import get_logger

logger = get_logger(__name__)


class FrameSource(Protocol):
    """A source of BGR numpy frames."""

    def open(self) -> bool:
        """Establish the stream; returns False when unavailable."""

    def close(self) -> None:
        """Release the stream."""

    def get_frame(self) -> np.ndarray | None:
        """Return the latest BGR frame or None when no frame is available."""

    @property
    def is_open(self) -> bool:
        ...


class RtspFrameSource:
    """RTSP camera stream decoded with OpenCV (v4l2src -> h264 -> rtsp).

    Used for real onboard cameras (Jetson) and 图传 receivers that expose an
    RTSP endpoint. OpenCV's RTSP backend handles reconnection internally; we
    additionally re-open the stream when frames stop arriving (stale link).
    """

    def __init__(self, url: str, stale_after_sec: float = 3.0) -> None:
        self.url = url
        self.stale_after_sec = max(0.5, float(stale_after_sec))
        self._capture: Any | None = None
        self._lock = threading.RLock()
        self._last_frame_ts = 0.0
        self._last_error = ""

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._capture is not None and bool(getattr(self._capture, "isOpened", lambda: False)())

    @property
    def last_error(self) -> str:
        return self._last_error

    def open(self) -> bool:
        import cv2

        with self._lock:
            self.close()
            self._last_error = ""
            try:
                capture = cv2.VideoCapture(self.url)
                if not capture.isOpened():
                    capture.release()
                    self._last_error = f"无法打开 RTSP 流: {self.url}"
                    return False
                # 探测第一帧，避免"能打开但无画面"的假连接
                ok, frame = capture.read()
                if not ok or frame is None:
                    capture.release()
                    self._last_error = f"RTSP 流无画面: {self.url}"
                    return False
                self._capture = capture
                self._last_frame_ts = time.time()
                return True
            except Exception as exc:
                self._last_error = f"RTSP open failed: {exc}"
                return False

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception:
                    pass
                self._capture = None

    def get_frame(self) -> np.ndarray | None:
        import cv2

        with self._lock:
            if self._capture is None:
                return None
            try:
                ok, frame = self._capture.read()
                if not ok or frame is None:
                    # 链路停滞：重新打开（OpenCV RTSP 断流后 read 会持续失败）
                    if time.time() - self._last_frame_ts > self.stale_after_sec:
                        self._last_error = "RTSP 流停滞，正在重连"
                        self.close()
                        self.open()
                    return None
                self._last_frame_ts = time.time()
                self._last_error = ""
                if frame.ndim == 3 and frame.shape[2] == 3:
                    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # 统一 RGB 语义
                return frame
            except Exception as exc:
                self._last_error = f"RTSP read failed: {exc}"
                return None


class CameraFrameSource:
    """Local camera (webcam / USB camera) via cv2.VideoCapture(index).

    Useful for testing the video pipeline without AirSim or an RTSP stream:
    the UI camera panel can show the workstation camera directly.
    """

    def __init__(self, index: int = 0, stale_after_sec: float = 3.0) -> None:
        self.index = int(index)
        self.stale_after_sec = max(0.5, float(stale_after_sec))
        self._capture: Any | None = None
        self._lock = threading.RLock()
        self._last_frame_ts = 0.0
        self._last_error = ""

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._capture is not None and bool(getattr(self._capture, "isOpened", lambda: False)())

    @property
    def last_error(self) -> str:
        return self._last_error

    def open(self) -> bool:
        import cv2

        with self._lock:
            self.close()
            self._last_error = ""
            try:
                capture = cv2.VideoCapture(self.index)
                if not capture.isOpened():
                    capture.release()
                    self._last_error = f"无法打开本地摄像头 #{self.index}"
                    return False
                ok, frame = capture.read()
                if not ok or frame is None:
                    capture.release()
                    self._last_error = f"本地摄像头 #{self.index} 无画面"
                    return False
                self._capture = capture
                self._last_frame_ts = time.time()
                return True
            except Exception as exc:
                self._last_error = f"camera open failed: {exc}"
                return False

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception:
                    pass
                self._capture = None

    def get_frame(self) -> np.ndarray | None:
        import cv2

        with self._lock:
            if self._capture is None:
                return None
            try:
                ok, frame = self._capture.read()
                if not ok or frame is None:
                    if time.time() - self._last_frame_ts > self.stale_after_sec:
                        self._last_error = "本地摄像头停滞，正在重连"
                        self.close()
                        self.open()
                    return None
                self._last_frame_ts = time.time()
                self._last_error = ""
                if frame.ndim == 3 and frame.shape[2] == 3:
                    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return frame
            except Exception as exc:
                self._last_error = f"camera read failed: {exc}"
                return None


class AirSimFrameSource:
    """AirSim RPC frames (Scene camera) as a FrameSource.

    Kept thin: image decoding happens here so callers share one contract.
    """

    def __init__(self, client: Any, camera_name: str = "0", image_type: int = 0, timeout_sec: float = 15.0) -> None:
        self._client = client
        self.camera_name = camera_name
        self.image_type = image_type
        self.timeout_sec = timeout_sec
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def get_frame(self) -> np.ndarray | None:
        if not self._open:
            return None
        try:
            import airsim

            request = airsim.ImageRequest(
                self.camera_name,
                self.image_type,
                False,
                True,
            )
            result = self._client.simGetImages([request])[0]
            if not result or result.image_data_uint8 is None or not result.image_data_uint8:
                return None
            import cv2

            raw = bytes(result.image_data_uint8)
            frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if frame is None:
                return None
            if frame.ndim == 3 and frame.shape[2] == 3:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return frame
        except Exception as exc:
            logger.warning("airsim_frame_failed", error=str(exc))
            return None
