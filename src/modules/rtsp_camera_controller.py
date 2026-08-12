"""Camera-only controller over an RTSP stream (real onboard camera / 图传).

Implements the camera subset of the controller contract so the existing
camera tool path (capture_image -> preview encoding) works unchanged for a
Jetson onboard camera pushed over RTSP.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from src.logging_config import get_logger
from src.modules.flight_controller import DroneStatus
from src.modules.frame_source import CameraFrameSource, RtspFrameSource

logger = get_logger(__name__)


class RtspCameraController:
    """Camera source over RTSP with automatic reconnection."""

    backend_name = "rtsp"

    def __init__(self, url: str, stale_after_sec: float = 3.0) -> None:
        self.url = url
        self.is_connected = False
        self.last_error = ""
        self._source = RtspFrameSource(url, stale_after_sec=stale_after_sec)
        self._lock = threading.RLock()

    # -- connection ---------------------------------------------------------

    def connect(self, **kwargs: Any) -> SimpleNamespace:
        with self._lock:
            ok = self._source.open()
            self.is_connected = ok
            self.last_error = "" if ok else self._source.last_error
            return SimpleNamespace(
                connected=ok,
                details={"message": self.last_error} if not ok else {},
            )

    def disconnect(self) -> None:
        with self._lock:
            self._source.close()
            self.is_connected = False

    def list_vehicles(self) -> list[str]:
        return ["rtsp"]

    def get_connection_info(self) -> dict[str, Any]:
        return {"url": self.url, "connected": self.is_connected}

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        return DroneStatus(
            position_ned={},
            velocity_ned={},
            attitude_rad={},
            extra={
                "backend": "rtsp",
                "vehicle_name": "rtsp",
                "connection_error": "" if self.is_connected else (self.last_error or "rtsp not connected"),
                "stream_url": self.url,
            },
        )

    # -- capture ------------------------------------------------------------

    def capture_image(
        self,
        camera_name: str = "0",
        image_type: Any = 0,
        vehicle_name: str = "",
        timeout: float = 10.0,
    ) -> bytes | None:
        """Latest frame as JPEG bytes (real cameras have no depth/segmentation)."""
        if isinstance(image_type, int):
            if image_type != 0:
                return None
        elif str(image_type or "").lower() not in {"scene", "0", ""}:
            return None
        frame = self._source.get_frame()
        if frame is None:
            self.last_error = self._source.last_error or "rtsp frame unavailable"
            return None
        import cv2

        try:
            ok, buffer = cv2.imencode(
                ".jpg",
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [int(cv2.IMWRITE_JPEG_QUALITY), 90],
            )
        except Exception as exc:
            self.last_error = f"rtsp jpeg encode failed: {exc}"
            return None
        if not ok:
            self.last_error = "rtsp jpeg encode failed"
            return None
        self.last_error = ""
        return buffer.tobytes()


class LocalCameraController(RtspCameraController):
    """Local webcam / USB camera source (cv2.VideoCapture(index)).

    Reuses the RTSP controller's capture/encode/status contract; only the
    frame source differs. Useful for testing the video pipeline on the
    workstation itself.
    """

    backend_name = "local"

    def __init__(self, index: int = 0, stale_after_sec: float = 3.0) -> None:
        self.url = f"camera:{int(index)}"
        self.is_connected = False
        self.last_error = ""
        self._source = CameraFrameSource(int(index), stale_after_sec=stale_after_sec)
        self._lock = threading.RLock()

    def list_vehicles(self) -> list[str]:
        return ["local"]
