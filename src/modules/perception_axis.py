"""Perception axis: lifecycle manager and engine implementations.

Two orthogonal axes drive the system: the flight backend (controller) and the
perception axis (frame source + where detection runs). The perception axis
owns the detection/tracking pipeline; the Agent consumes its state through
read-only tools and never touches pixels or control loops.

Engines:
- LocalPerceptionEngine: frame source + YOLO detection inside this process
  (sim: AirSim camera; real: RTSP pod stream or USB camera).
- RemotePerceptionEngine: health/snapshot polling against a Jetson HTTP
  service running the same perception code elsewhere.

See docs/perception_axis_design.md.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from src.logging_config import get_logger

logger = get_logger(__name__)


# ======================================================================
# 目标状态契约（与 autonomy/world_state.TargetState 字段对齐）
# ======================================================================

def to_target_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Convert an engine snapshot into a TargetState-shaped dict.

    Defined as a pure function so the contract is testable without a running
    engine; the autonomy stack (PolicyEngine/TrackingSkill) will consume this
    field once it is wired to the axis.
    """
    primary = snapshot.get("primary")
    visible = bool(primary) and float(primary.get("confidence") or 0.0) > 0.0
    state: dict[str, Any] = {
        "visible": visible,
        "best_class": (primary or {}).get("class", ""),
        "best_confidence": float((primary or {}).get("confidence") or 0.0),
        "estimated_position": (primary or {}).get("world_pos"),
        "estimated_velocity": None,
        "tracking": False,
        "lost_time": 0.0,
    }
    if primary and visible:
        state["estimated_position"] = primary.get("world_pos")
    return state


# ======================================================================
# Local engine（仿真 / RTSP 回传形态）
# ======================================================================

class LocalPerceptionEngine:
    """Frame source + YOLO detection in a single background thread.

    Outputs a snapshot dict ({targets, primary, total_frames, fps, ts}) and a
    small event buffer (target_found / target_lost / target_recovered).
    """

    def __init__(
        self,
        frame_source: Any,
        target_class: str = "car",
        confidence: float = 0.25,
        update_fps: float = 5.0,
        health_timeout_sec: float = 3.0,
        depth_fn: Optional[Callable[[Any, dict[str, Any]], dict[str, Any] | None]] = None,
        detect_fn: Optional[Callable[[Any], list[dict[str, Any]]]] = None,
    ) -> None:
        self._frame_source = frame_source
        self._target_class = target_class
        self._confidence = confidence
        self._update_fps = max(0.5, float(update_fps))
        self._health_timeout_sec = max(0.5, float(health_timeout_sec))
        # Optional: project the best detection bbox into NED world coords.
        # (depth_fn(frame, detection) -> world_pos dict | None)
        self._depth_fn = depth_fn
        # Optional: injectable detector (tests / Jetson-agnostic backends).
        # When absent, the engine loads YOLO-World for target_class.
        self._detect_fn = detect_fn

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._model = None
        self._model_classes: Optional[list[str]] = None

        self._snapshot: dict[str, Any] = {
            "targets": [],
            "primary": None,
            "total_frames": 0,
            "fps": 0.0,
            "timestamp": 0.0,
            "source": type(frame_source).__name__,
        }
        self._events: list[dict[str, Any]] = []
        self._last_update_ts = 0.0
        self._start_ts = 0.0
        self._error = ""
        # Annotated frame cache: single AirSim frame source for UI + Agent.
        self._annotated_jpeg = None
        self._annotations = []
        self._annotated_ts = 0.0

    # -- lifecycle -----------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        if not self._frame_source.open():
            self._error = f"frame source open failed: {getattr(self._frame_source, 'last_error', '') or ''}"
            logger.warning("perception_frame_open_failed", error=self._error)
            return False
        self._running = True
        self._start_ts = time.time()
        self._last_update_ts = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perception-axis")
        self._thread.start()
        # Model load happens lazily inside the loop thread so a slow or
        # missing YOLO model never blocks runtime startup; health reports
        # the loading state until detection is actually available.
        logger.info("perception_axis_started", source=type(self._frame_source).__name__, target=self._target_class)
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self._frame_source.close()
        except Exception:
            pass

    # -- state ---------------------------------------------------------

    @property
    def is_online(self) -> bool:
        if not self._running:
            return False
        return (time.time() - self._last_update_ts) < self._health_timeout_sec

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "online": self.is_online,
                "fps": round(self._snapshot.get("fps", 0.0), 1),
                "latency_ms": round((time.time() - self._snapshot.get("timestamp", 0.0)) * 1000, 0) if self._snapshot.get("timestamp") else 0,
                "last_update_ts": self._snapshot.get("timestamp", 0.0),
                "total_frames": self._snapshot.get("total_frames", 0),
                "error": self._error,
                "source": self._snapshot.get("source", ""),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def pop_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    # -- internals -----------------------------------------------------

    def _cache_annotated(self, frame: Any, targets: list[dict[str, Any]]) -> None:
        """Overlay detection boxes on the newest frame and cache the JPEG.

        The perception axis is the single AirSim frame source: the UI camera
        panel and the Agent both consume this cache, so nobody re-pulls the
        simulator for display purposes.
        """
        try:
            import cv2

            img = frame
            if img.ndim == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            annotations = []
            for det in targets:
                x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 2)
                label = f"{det['class']} {float(det['confidence']):.2f}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                bx1, by1 = x1, max(0, y1 - th - 8)
                bx2, by2 = min(img.shape[1], x1 + tw + 8), max(0, y1 - 2)
                cv2.rectangle(img, (bx1, by1), (bx2, by2), (0, 0, 0), -1)
                cv2.putText(img, label, (bx1 + 4, by2 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                annotations.append({"class": det["class"], "confidence": float(det["confidence"]), "bbox": det["bbox"]})
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                self._annotated_jpeg = buf.tobytes()
                self._annotations = annotations
                self._annotated_ts = time.time()
        except Exception:
            pass

    def annotated_frame(self) -> tuple:
        """Return (jpeg_bytes, detections, ts); empty when no frame cached yet."""
        with self._lock:
            return self._annotated_jpeg, list(self._annotations), self._annotated_ts



    def _detect(self, frame: Any):
        if self._detect_fn is not None:
            return self._detect_fn(frame)
        if self._model is None:
            raise RuntimeError("model not loaded")
        from src.modules.yolo_detection import run_yolo_detection

        return run_yolo_detection(self._model, frame, self._target_class, self._confidence)

    def _loop(self) -> None:
        frame_count = 0
        last_seen_visible = False
        interval = 1.0 / self._update_fps
        model_ready = self._detect_fn is not None
        while self._running:
            loop_start = time.time()
            if not model_ready:
                # Lazy model load: never blocks runtime startup. Health keeps
                # reporting "model_loading" until detection is available.
                try:
                    from src.modules.yolo_detection import build_search_classes, get_yolo_model

                    self._model_classes = build_search_classes(self._target_class)
                    self._model = get_yolo_model(self._model_classes)
                    model_ready = True
                    self._error = ""
                except Exception as exc:
                    self._error = f"yolo model load failed: {exc}"
                    logger.warning("perception_model_load_failed", error=str(exc))
                    time.sleep(1.0)
                    continue
            try:
                frame = self._frame_source.get_frame()
                if frame is None:
                    time.sleep(min(0.2, interval))
                    continue
                frame_count += 1
                dets = self._detect(frame)
                targets = []
                primary = None
                for det in dets:
                    entry = {
                        "class": det["class"],
                        "confidence": det["confidence"],
                        "bbox": det["bbox"],
                        "center": det["center"],
                        "world_pos": None,
                        "depth_m": 0.0,
                        "distance": 0.0,
                    }
                    targets.append(entry)
                if targets:
                    primary = max(targets, key=lambda t: t["confidence"])
                    if self._depth_fn is not None and frame_count % 3 == 0:
                        try:
                            world = self._depth_fn(frame, primary)
                            if world:
                                primary["world_pos"] = world.get("world_pos")
                                primary["depth_m"] = world.get("depth_m", 0.0)
                                primary["distance"] = world.get("distance_to_drone", 0.0)
                        except Exception as exc:
                            logger.warning("perception_depth_failed", error=str(exc))

                with self._lock:
                    visible_now = primary is not None
                    if visible_now and not last_seen_visible:
                        self._events.append({"type": "target_found", "time": time.time(), "data": {"class": primary["class"], "confidence": primary["confidence"]}})
                    elif not visible_now and last_seen_visible:
                        self._events.append({"type": "target_lost", "time": time.time(), "data": {}})
                    last_seen_visible = visible_now
                    self._snapshot = {
                        "targets": targets,
                        "primary": primary,
                        "total_frames": frame_count,
                        "fps": frame_count / max(time.time() - self._start_ts, 0.1),
                        "timestamp": time.time(),
                        "source": type(self._frame_source).__name__,
                    }
                    if len(self._events) > 50:
                        self._events = self._events[-50:]
                    self._last_update_ts = time.time()
                    self._cache_annotated(frame, targets)
            except Exception as exc:
                self._error = str(exc)
                logger.warning("perception_loop_error", error=str(exc))
                time.sleep(0.2)

            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)
            # periodic health visibility: frame pacing and last error at a glance
            if frame_count % 50 == 0:
                logger.info(
                    "perception_axis_health",
                    frames=frame_count,
                    fps=round(frame_count / max(time.time() - self._start_ts, 0.1), 1),
                    error=self._error or "",
                )


# ======================================================================
# Remote engine（Jetson 机载形态）
# ======================================================================

class RemotePerceptionEngine:
    """Poll health/snapshot/events from a Jetson HTTP perception service.

    The remote side runs the same perception code (a local engine behind a
    tiny HTTP wrapper); this engine only mirrors its state into the axis.
    Protocol is frozen in docs/perception_axis_design.md §7.
    """

    def __init__(
        self,
        base_url: str,
        health_timeout_sec: float = 3.0,
        poll_interval_sec: float = 1.0,
        request_timeout_sec: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._health_timeout_sec = max(0.5, float(health_timeout_sec))
        self._poll_interval = max(0.2, float(poll_interval_sec))
        self._request_timeout = max(0.5, float(request_timeout_sec))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {"targets": [], "primary": None}
        self._events: list[dict[str, Any]] = []
        self._last_update_ts = 0.0
        self._error = ""

    def start(self) -> bool:
        if self._running:
            return True
        # Probe once: unreachable remote must fail fast with a clear reason.
        if not self._probe():
            return False
        self._running = True
        self._last_update_ts = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perception-remote")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

    def _probe(self) -> bool:
        try:
            return self._get_json("/health") is not None
        except Exception as exc:
            self._error = f"remote probe failed: {exc}"
            logger.warning("perception_remote_probe_failed", url=self._base_url, error=str(exc))
            return False

    def _get_json(self, path: str) -> Optional[dict[str, Any]]:
        import json
        import urllib.request

        req = urllib.request.Request(self._base_url + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self._request_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _loop(self) -> None:
        while self._running:
            try:
                health = self._get_json("/health")
                if health is None:
                    self._error = "remote health unreachable"
                else:
                    snap = self._get_json("/snapshot")
                    if snap is not None:
                        with self._lock:
                            self._snapshot = snap
                            self._error = ""
                            self._last_update_ts = time.time()
                    ev = self._get_json("/events")
                    if ev and ev.get("events"):
                        with self._lock:
                            self._events.extend(ev["events"])
                            if len(self._events) > 50:
                                self._events = self._events[-50:]
            except Exception as exc:
                self._error = str(exc)
            time.sleep(self._poll_interval)

    @property
    def is_online(self) -> bool:
        if not self._running:
            return False
        return (time.time() - self._last_update_ts) < self._health_timeout_sec

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "online": self.is_online,
                "fps": round(self._snapshot.get("fps", 0.0), 1) if isinstance(self._snapshot.get("fps"), (int, float)) else 0.0,
                "latency_ms": round((time.time() - self._snapshot.get("timestamp", time.time())) * 1000, 0),
                "last_update_ts": self._snapshot.get("timestamp", 0.0),
                "error": self._error,
                "source": f"remote:{self._base_url}",
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def pop_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events


# ======================================================================
# PerceptionAxis：与运行时挂接的单一入口
# ======================================================================

class PerceptionAxis:
    """Lifecycle owner for the perception capability.

    Resolves the active profile (local module vs remote service), starts the
    matching engine, and presents one state surface to the Agent tools:
    is_online / health / snapshot / pop_events. Disabled when the config
    disables the axis -- the flight backend then behaves exactly as today.
    """

    def __init__(self, profile: Any, camera_index: int = 0, rtsp_url: str = "") -> None:
        self._profile = profile
        self._camera_index = camera_index
        self._rtsp_url = rtsp_url
        self._engine: Optional[Any] = None
        self._started = False
        self._start_error = ""

    @property
    def enabled(self) -> bool:
        return self._profile is not None

    @property
    def profile(self) -> Any:
        return self._profile

    def start(self) -> bool:
        if not self.enabled or self._started:
            return self._started or not self.enabled
        ok = self._try_start()
        if not ok:
            # AirSim/Jetson may come up later than the UI process: keep
            # retrying in the background so the axis self-heals instead of
            # staying dead until the next UI restart.
            threading.Thread(target=self._retry_loop, daemon=True, name="perception-retry").start()
        return ok

    def _try_start(self) -> bool:
        profile = self._profile
        if profile.deploy == "remote":
            if not (profile.remote_url or self._rtsp_url):
                self._start_error = "remote deploy requires remote_url"
                logger.warning("perception_axis_misconfig", error=self._start_error)
                return False
            self._engine = RemotePerceptionEngine(
                base_url=profile.remote_url,
                health_timeout_sec=profile.health_timeout_sec,
            )
        else:
            frame_source = self._build_frame_source(profile)
            if frame_source is None:
                self._start_error = self._start_error or f"unsupported frame_source: {profile.frame_source}"
                return False
            depth_fn = self._build_depth_fn(profile, frame_source)
            self._engine = LocalPerceptionEngine(
                frame_source=frame_source,
                target_class=profile.target_class,
                confidence=profile.confidence,
                update_fps=profile.update_fps,
                health_timeout_sec=profile.health_timeout_sec,
                depth_fn=depth_fn,
            )
        ok = self._engine.start()
        if ok:
            self._started = True
            self._start_error = ""
        else:
            self._start_error = getattr(self._engine, "_error", "") or "engine start failed"
        return ok

    def _retry_loop(self) -> None:
        while not self._started:
            time.sleep(30.0)
            if self._started:
                break
            logger.info("perception_axis_retry_start")
            if self._try_start():
                logger.info("perception_axis_started_after_retry")
                break

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.stop()
            self._engine = None
        self._started = False

    def is_online(self) -> bool:
        return self._engine is not None and self._engine.is_online

    def health(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "enabled": self.enabled,
            "profile": getattr(self._profile, "profile", ""),
            "started": self._started,
            "start_error": self._start_error,
        }
        if self._engine is not None:
            base.update(self._engine.health())
        return base

    def snapshot(self) -> dict[str, Any]:
        if self._engine is None:
            return {"targets": [], "primary": None, "timestamp": 0.0}
        return self._engine.snapshot()

    def pop_events(self) -> list[dict[str, Any]]:
        if self._engine is None:
            return []
        return self._engine.pop_events()

    def annotated_frame(self) -> tuple:
        """Latest annotated JPEG + detections for the UI panel (single frame source)."""
        if self._engine is None:
            return None, [], 0.0
        fn = getattr(self._engine, "annotated_frame", None)
        if fn is None:
            return None, [], 0.0
        return fn()

    # -- internals -----------------------------------------------------

    def _build_frame_source(self, profile: Any):
        fs = profile.frame_source
        try:
            if fs == "airsim":
                from src.config import config as _cfg
                from src.modules.frame_source import AirSimFrameSource

                ip = str(_cfg.airsim_ip)
                port = int(_cfg.airsim_port)
                # Fast TCP probe: a missing simulator must fail the axis in
                # ~1s instead of blocking on AirSim RPC timeouts.
                import socket

                with socket.socket() as s:
                    s.settimeout(1.0)
                    s.connect((ip, port))
                # The axis owns an independent AirSim client, decoupled from
                # the flight backend controller.
                import airsim

                client = airsim.MultirotorClient(ip=ip, port=port)
                return AirSimFrameSource(client, camera_name="0", image_type=0, host=ip, port=port)
            if fs == "rtsp":
                if not self._rtsp_url:
                    self._start_error = "rtsp frame source requires rtsp_url"
                    return None
                from src.modules.frame_source import RtspFrameSource

                return RtspFrameSource(self._rtsp_url)
            if fs == "usb":
                from src.modules.frame_source import CameraFrameSource

                return CameraFrameSource(self._camera_index)
        except Exception as exc:
            self._start_error = f"frame source init failed: {exc}"
            logger.warning("perception_frame_init_failed", error=str(exc))
        return None

    def _build_depth_fn(self, profile: Any, frame_source: Any):
        """Return a depth projection callback for AirSim frames, else None."""
        if profile.frame_source != "airsim":
            return None
        try:
            from src.modules.occupancy_map import DepthProjection

            client = getattr(frame_source, "_client", None)
        except Exception:
            client = None
        if client is None:
            return None

        def depth_fn(frame: Any, detection: dict[str, Any]) -> dict[str, Any] | None:
            try:
                import airsim
                import numpy as np

                responses = client.simGetImages(
                    [airsim.ImageRequest("0", airsim.ImageType.DepthPlanar, False, False)]
                )
                if not responses or not responses[0].image_data_float:
                    return None
                img_data = responses[0]
                depth_1d = np.array(img_data.image_data_float, dtype=np.float32)
                if img_data.width <= 0 or img_data.height <= 0 or len(depth_1d) != img_data.width * img_data.height:
                    return None
                depth_img = depth_1d.reshape((img_data.height, img_data.width))
                state = client.getMultirotorState()
                drone_pos = (state.kinematics_estimated.position.x_val, state.kinematics_estimated.position.y_val, state.kinematics_estimated.position.z_val)
                q = state.kinematics_estimated.orientation
                from src.modules.airsim_controller import AirSimController

                _, _, yaw_rad = AirSimController._quat_to_euler(q)
                yaw = math.degrees(yaw_rad)
                return DepthProjection.project_detection_to_world(
                    bbox=detection["bbox"], depth_img=depth_img,
                    drone_pos=drone_pos, drone_yaw=yaw,
                )
            except Exception:
                return None

        return depth_fn