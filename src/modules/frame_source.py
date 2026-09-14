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


class LazyControllerFrameSource:
    """FrameSource backed by the flight controller's capture path.

    The ground-station process hosts several msgpack-rpc sessions; a
    hand-rolled MultirotorClient here has proven to hang on simGetImages
    (Session.call waits with no timeout), while the flight controller's
    capture_image (worker thread + hard timeout + runtime reset) keeps
    working. The controller is resolved lazily per frame so the axis never
    pins a stale backend instance.
    """

    def __init__(self, controller_provider: Any, camera_name: str = "0", image_type: int = 0, timeout_sec: float = 8.0) -> None:
        self._controller_provider = controller_provider
        self.camera_name = camera_name
        self.image_type = image_type
        self.timeout_sec = timeout_sec
        self._open = False
        self.last_error = ""
        # 失败退避：AirSim RPC 卡死时，capture_image 每次要等几十秒才超时；
        # 连续失败后暂停取帧一段时间，避免感知线程一直卡在超时里、也避免
        # 反复触发 RPC 重置。
        self._consecutive_failures = 0
        self._backoff_until = 0.0

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        self.last_error = ""
        return True

    def close(self) -> None:
        self._open = False

    def _warn_rate_limited(self, message: str, every_s: float = 5.0) -> None:
        """限流告警：取帧持续失败时便于定位原因，但不要刷屏。"""
        now = time.time()
        if now - getattr(self, "_last_warn_ts", 0.0) >= every_s:
            self._last_warn_ts = now
            logger.warning("perception_frame_unavailable", error=message)

    def _note_failure(self) -> None:
        """连续失败后退避：避免感知线程反复卡在 RPC 超时里。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._backoff_until = time.time() + 10.0
            self.last_error = (
                "AirSim RPC 无响应（模拟器未运行或服务已卡死）。"
                "请在 UE 中重新 Play / 重启 AirSim 后恢复。"
            )

    def get_frame(self) -> np.ndarray | None:
        if not self._open:
            return None
        if time.time() < self._backoff_until:
            return None
        controller = self._controller_provider() if callable(self._controller_provider) else None
        if controller is None:
            self.last_error = "flight controller unavailable"
            self._warn_rate_limited("controller unavailable")
            return None
        try:
            raw = controller.capture_image(self.camera_name, self.image_type, timeout=self.timeout_sec)
            self._consecutive_failures = 0
        except Exception as exc:
            self.last_error = f"capture failed: {exc}"
            self._warn_rate_limited(self.last_error)
            self._note_failure()
            return None
        if not raw:
            self.last_error = "capture returned empty frame"
            self._note_failure()
            self._warn_rate_limited(self.last_error)
            return None
        try:
            import cv2

            frame = cv2.imdecode(np.frombuffer(bytes(raw), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if frame is None:
                return None
            if frame.ndim == 3 and frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            return frame
        except Exception as exc:
            self.last_error = f"decode failed: {exc}"
            return None


class AirSimFrameSource:
    """AirSim RPC frames (Scene camera) as a FrameSource.

    Kept thin: image decoding happens here so callers share one contract.
    open() probes the connection (fast ping) so callers fail quickly when
    the simulator is not running.
    """

    def __init__(self, client: Any, camera_name: str = "0", image_type: int = 0, timeout_sec: float = 15.0, host: str = "", port: int = 0) -> None:
        self._client = client
        self.camera_name = camera_name
        self.image_type = image_type
        self.timeout_sec = timeout_sec
        self._host = host
        self._port = port
        self._open = False
        self.last_error = ""
        # 取帧调用必须带硬超时：msgpack-rpc 的 future.get() 没有超时，
        # 一次半开连接（模拟器重启/网络抖动）就会把调用线程永久卡死，
        # 表现为感知轴 online=false、0 帧。超时后重建客户端恢复。
        self._call_lock = threading.Lock()
        # 重建限流：AirSim 的 msgpack 服务是单线程的，每次失败就重连会留下
        # 大量半开连接（服务端 CloseWait 堆积）直到把 RPC 服务彻底堵死。
        # 因此只在连续失败达阈值、且距上次重建有冷却时间时才重建；重建前
        # 尽力关闭旧客户端，避免泄漏套接字。
        self._consecutive_failures = 0
        self._last_rebuild_ts = 0.0
        self._rebuild_cooldown_s = 5.0
        self._rebuild_after_failures = 3

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        if self._open:
            return True
        if self._host and self._port:
            # Fast TCP probe: fail clearly when the simulator is not running
            # instead of blocking on AirSim RPC timeouts.
            try:
                import socket

                with socket.socket() as s:
                    s.settimeout(1.0)
                    s.connect((self._host, self._port))
            except Exception as exc:
                self._open = False
                self.last_error = f"AirSim TCP probe failed: {exc}"
                return False
        self._open = True
        self.last_error = ""
        return True

    def close(self) -> None:
        self._open = False

    def _rebuild_client(self) -> None:
        """Replace the RPC client: a stuck session can never recover in place."""
        # 尽力关闭旧客户端，避免在 AirSim 服务端留下半开连接（CloseWait 堆积
        # 会把单线程 RPC 服务堵死）。不同 airsim 版本的关闭入口不一致，逐个尝试。
        old = self._client
        self._client = None
        if old is not None:
            for attr in ("close", "shutdown"):
                fn = getattr(old, attr, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
                    break
            inner = getattr(old, "client", None)
            for attr in ("close", "shutdown"):
                fn = getattr(inner, attr, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
                    break
        try:
            import airsim

            self._client = airsim.MultirotorClient(ip=self._host, port=self._port)
            self._last_rebuild_ts = time.time()
        except Exception as exc:
            self.last_error = f"client rebuild failed: {exc}"

    def _fetch_with_timeout(self) -> np.ndarray | None:
        import queue as _queue

        result_q: "queue.Queue[np.ndarray | None]" = _queue.Queue(maxsize=1)

        def _call():
            try:
                result_q.put(self._fetch_once())
            except Exception:
                result_q.put(None)

        worker = threading.Thread(target=_call, daemon=True, name="airsim-frame-fetch")
        worker.start()
        try:
            return result_q.get(timeout=self.timeout_sec)
        except Exception:
            # 超时：worker 卡在 msgpack future.get()（半开会话）——丢弃该
            # 客户端会话，下一次取帧用全新连接。卡死的 worker 线程无法
            # 回收，但只在连接故障时发生一次。
            return None

    def _fetch_once(self) -> np.ndarray | None:
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

    def get_frame(self) -> np.ndarray | None:
        if not self._open:
            return None
        # 连续失败后进入退避：不再高频发起 RPC，给卡死的 AirSim 服务留出恢复
        # 窗口，也避免我们自己把它的连接队列挤爆。
        if self._consecutive_failures >= 6 and (time.time() - self._last_rebuild_ts) < 10.0:
            return None
        with self._call_lock:
            frame = self._fetch_with_timeout()
        if frame is not None:
            self._consecutive_failures = 0
            return frame
        # 取帧失败：不要每帧都重连（会把 AirSim 单线程 RPC 服务用半开连接堵死）。
        # 连续失败达阈值且过了冷却时间才重建一次客户端，并且重建前关闭旧连接。
        self._consecutive_failures += 1
        now = time.time()
        if (
            self._consecutive_failures >= self._rebuild_after_failures
            and now - self._last_rebuild_ts >= self._rebuild_cooldown_s
        ):
            self._rebuild_client()
        if self._consecutive_failures == self._rebuild_after_failures:
            # 第一次达到阈值时给出明确原因，让前端显示"模拟器未就绪"而不是
            # 一直停在"正在连接视频流"。
            self.last_error = (
                "AirSim RPC 无响应（模拟器未运行或服务已卡死）。"
                "请在 UE 中重新 Play / 重启 AirSim 后恢复。"
            )
            logger.warning("airsim_frame_unavailable", consecutive_failures=self._consecutive_failures)
        return None
