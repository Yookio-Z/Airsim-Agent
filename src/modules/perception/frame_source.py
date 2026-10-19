"""Frame source abstraction for the video pipeline.

The UI preview and camera tools only care about "give me the latest frame as
BGR numpy". AirSim RPC frames and real onboard-camera RTSP streams (Jetson +
图传) both implement this protocol, so the rest of the pipeline stays
source-agnostic.
"""

from __future__ import annotations

import os
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


def _normalize_rtsp_transport(transport: str) -> str:
    """Return "tcp"/"udp", or "" to keep OpenCV's own default."""
    value = str(transport or "").strip().lower()
    return value if value in {"tcp", "udp"} else ""


def _normalize_open_timeout(seconds: Any) -> float:
    """Clamp the RTSP handshake timeout; 0 keeps OpenCV's default."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return max(1.0, min(60.0, value))


def _open_rtsp_capture(cv2_module: Any, url: str, transport: str, open_timeout_sec: float = 0.0) -> Any:
    """Open an RTSP capture, preferring FFmpeg with an explicit transport.

    Two knobs, both matching what QGC's RTSP video source exposes:

    * transport -- FFmpeg takes it through the process-wide
      ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` env var (OpenCV has no per-capture
      API for it), so it is set immediately before opening.
    * handshake timeout -- passed per-capture via ``CAP_PROP_OPEN_TIMEOUT_MSEC``.

    Both are best-effort: an older OpenCV that rejects the parameterised
    constructor falls back to the plain call rather than failing the stream.
    """
    transport = _normalize_rtsp_transport(transport)
    if transport:
        # 只设置我们需要的键；其余交给 FFmpeg 默认值（含 stimeout 之类的
        # 版本差异选项，写死反而容易在新旧 FFmpeg 之间踩坑）。
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"
    timeout_ms = int(_normalize_open_timeout(open_timeout_sec) * 1000)
    if timeout_ms > 0 and hasattr(cv2_module, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        try:
            return cv2_module.VideoCapture(
                url,
                getattr(cv2_module, "CAP_FFMPEG", 0),
                [
                    int(cv2_module.CAP_PROP_OPEN_TIMEOUT_MSEC),
                    timeout_ms,
                ],
            )
        except TypeError:
            # 老版本 OpenCV 不支持带 params 的构造：退回普通调用，
            # 由 stale_after_sec 的重连逻辑兜底。
            pass
    return cv2_module.VideoCapture(url)


class CaptureSource(Protocol):
    """A camera-capable flight controller, as used by ``LazyControllerFrameSource``.

    This was previously an implicit contract: the frame source called
    ``capture_image`` on whatever object it was handed. Naming it makes the
    requirement explicit, so an alternative implementation (a ROS image bridge,
    a different autopilot) knows exactly what to provide.
    """

    def capture_image(self, camera_name: str = "0", image_type: int = 0, **kwargs: Any) -> bytes | None:
        """Return one encoded frame (PNG/JPEG bytes) or None.

        Implementations must enforce their own timeout: the caller is a
        background thread that cannot afford to block on a dead transport.
        """


class RtspFrameSource:
    """RTSP camera stream decoded with OpenCV (v4l2src -> h264 -> rtsp).

    Used for real onboard cameras (Jetson) and 图传 receivers that expose an
    RTSP endpoint. OpenCV's RTSP backend handles reconnection internally; we
    additionally re-open the stream when frames stop arriving (stale link).

    ``transport`` selects the RTSP transport handed to FFmpeg ("tcp" | "udp").
    OpenCV defaults to UDP, which over a WiFi / 图传 link shows up as a stream
    that opens and then tears/blocks instead of failing outright -- the same
    reason QGC exposes a UDP/TCP choice for its RTSP video source.
    """

    def __init__(
        self,
        url: str,
        stale_after_sec: float = 3.0,
        transport: str = "",
        open_timeout_sec: float = 0.0,
    ) -> None:
        self.url = url
        self.stale_after_sec = max(0.5, float(stale_after_sec))
        self.transport = _normalize_rtsp_transport(transport)
        self.open_timeout_sec = _normalize_open_timeout(open_timeout_sec)
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
                capture = _open_rtsp_capture(cv2, self.url, self.transport, self.open_timeout_sec)
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
            import os

            # AirSim stabilises the camera every render tick from the settings.json
            # "Gimbal" block (world pitch/roll held, yaw still follows the airframe).
            # That is the preferred path. The sampled RPC compensation below is only
            # a fallback for rigs that cannot use a Gimbal block -- and while it runs
            # it overwrites the engine gimbal target via simSetCameraPose -- so it is
            # opt-in and the default path never touches the controller at all.
            stabilize = getattr(controller, "configure_camera_stabilization", None)
            if (
                self.image_type == 0
                and os.environ.get("DRONE_CAMERA_STABILIZATION", "0") == "1"
                and getattr(controller, "_ip", "") in {"127.0.0.1", "localhost", "::1"}
                and callable(stabilize)
            ):
                stabilize(self.camera_name, enabled=True)
            capture_frame = getattr(controller, "capture_frame", None)
            use_raw = (
                self.image_type == 0
                and getattr(controller, "_ip", "") in {"127.0.0.1", "localhost", "::1"}
                and os.environ.get("DRONE_PERCEPTION_RAW_SCENE", "1") == "1"
                and callable(capture_frame)
            )
            if use_raw:
                frame = capture_frame(self.camera_name, self.image_type, timeout=self.timeout_sec)
                if frame is None:
                    raise ValueError("capture returned empty frame")
                self._consecutive_failures = 0
                self.last_error = ""
                return frame
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
