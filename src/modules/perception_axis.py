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
    if primary and (primary.get("predicted") or primary.get("stale")
                    or primary.get("visible") is False or snapshot.get("detection_stale")):
        primary = None
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
        detect_fps: float = 2.0,
        model: str = "",
        imgsz: int = 0,
        depth_sample_every: int = 15,
        depth_fn: Optional[Callable[[Any, dict[str, Any]], dict[str, Any] | None]] = None,
        detect_fn: Optional[Callable[[Any], list[dict[str, Any]]]] = None,
    ) -> None:
        self._frame_source = frame_source
        self._target_class = target_class
        self._confidence = confidence
        self._model_choice = str(model or "")
        # Inference resolution; 0 lets the detector pick its measured default.
        self._imgsz = max(0, int(imgsz or 0))
        self._depth_sample_every = max(1, int(depth_sample_every or 15))
        self._update_fps = max(0.5, float(update_fps))
        # YOLO 推理频率（与采集解耦）：画面按 update_fps 刷新，检测按 detect_fps
        # 节流，避免每帧推理把预览拖慢。
        self._detect_fps = max(0.5, float(detect_fps))
        # Depth-localisation observability (see health()).
        self._depth_samples = 0
        self._depth_valid = 0
        self._depth_last_error = ""
        self._last_world_pos: dict[Any, dict[str, Any]] = {}
        # A carried world position goes stale as the target moves; 6 s matches
        # the tracker keep-alive, so a carried position is never older than the
        # track it belongs to.
        # Derived from the sampling cadence rather than fixed: at 1-in-15 with a
        # ~1 Hz detect loop the interval is ~15 s, so a 6 s TTL expired long
        # before the next sample and world_pos read null in between. The age is
        # always reported as world_pos_age_s -- consumers must not read a
        # carried position as if it were fresh.
        # Derived from the *worst credible* achieved rate, not the target: using
        # the target detect_fps (2.0) gave an 11 s TTL while the real interval at
        # 0.7 Hz achieved was 21 s, so a valid position expired before the next
        # sample and world_pos read null much of the time. world_pos_age_s is
        # always reported so consumers cannot mistake a carried value for fresh.
        self._depth_carry_ttl = max(6.0, self._depth_sample_every / 0.5 * 1.5)
        # 深度熔断：深度是可选功能，绝不能拖垮主链路。实测把 DepthPlanar
        # 配成高分辨率时，AirSim 对未压缩浮点图的序列化要 50s 以上，且它是
        # 单线程服务——一次深度调用会把彩色取帧一起饿死。连续失败后停用一段
        # 时间，再单次探测恢复。
        self._depth_consecutive_failures = 0
        self._depth_disabled_until = 0.0
        # 阈值取 1：深度失败有两种，其中超时会在控制器内部触发 RPC 运行时
        # 重置，而采集线程此时可能正持有同一把锁——结果就是锁被释放两次、
        # 该控制器实例永久损坏（日志表现为 release unlocked lock，之后彩色
        # 取帧也全部超时）。深度只是可选功能，绝不能为此牺牲摄像头，所以
        # 第一次失败就退避，冷却结束后只放一次探测。
        self._depth_breaker_threshold = 1
        self._depth_cooldown_s = 60.0
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
        # 独占检测器（COCO + ByteTrack 或 YOLO-World），在检测线程内惰性创建
        self._detector: Optional[Any] = None

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
        # 每条航迹最后一次"实测"置信度（外推帧沿用，避免显示值来回跳）
        self._last_measured_conf: dict[int, float] = {}
        # 每条航迹最后一次"实测"时刻：显示层 TTL 以它为准。检测线程即使还在跑，
        # 只要某条航迹长时间没有被真实命中，它对外就不能再算 visible/locked。
        self._last_measured_ts: dict[Any, float] = {}
        # Source capture time of the last completed detection, NOT completion
        # time. Slow inference must never make old pixels look fresh.
        self._last_detect_ts = 0.0
        self._latest_frame_ts = 0.0
        # Briefly bridge detection intervals, but never show boxes for seconds
        # just because the requested detector cadence is low. The floor has to
        # cover more than one interval: YOLO at 1280 takes 300ms+ on a slow tick,
        # and a 1.0s window made every box blink out (and cleared the axis
        # primary, which then refused every approach step).
        self._detect_display_ttl = max(1.2, min(2.5, 2.5 / self._detect_fps))
        # Annotated frame cache: single AirSim frame source for UI + Agent.
        self._annotated_jpeg = None
        self._annotated_raw = None
        self._annotations = []
        self._annotated_ts = 0.0
        # 近窗口时间戳：用于 report 瞬时 fps（平均值会被模型加载期拖低）
        from collections import deque

        self._recent_frames: "deque[float]" = deque(maxlen=200)
        self._recent_detects: "deque[float]" = deque(maxlen=200)
        # 采集/检测线程共享的最新帧与最新检测结果
        self._latest_frame: Any = None
        self._latest_frame_id = 0
        self._latest_targets: list[dict[str, Any]] = []
        self._latest_primary: dict[str, Any] | None = None
        self._frame_count = 0
        self._detect_thread: Optional[threading.Thread] = None
        # 检测是"按需开启"的：系统启动只把画面跑起来，YOLO 模型要等操作员点
        # "开始检测"（或任务调用 perception_start）才加载，避免一开机就吃算力。
        self._detect_enabled = False
        # Internal coasting retains IDs for reacquisition for 4s. Display lifetime
        # is separate: predictions are never visible and measurements use TTL.
        from src.modules.target_tracker import IouTracker

        self._tracker = IouTracker(keep_alive_s=4.0, iou_threshold=0.2)

    # -- lifecycle -----------------------------------------------------

    def start(self, *, detect: bool = False) -> bool:
        """启动相机采集；``detect=True`` 时才连带启动目标检测。

        默认只出画面不检测：模型加载（YOLO）与逐帧推理都按需开始，操作员在
        相机面板点"开始检测"、或任务调用 perception_start 时才加载。
        """
        if self._running:
            if detect:
                self.start_detection()
            return True
        if not self._frame_source.open():
            self._error = f"frame source open failed: {getattr(self._frame_source, 'last_error', '') or ''}"
            logger.warning("perception_frame_open_failed", error=self._error)
            return False
        self._running = True
        self._start_ts = time.time()
        self._last_update_ts = time.time()
        # 双线程解耦：采集线程只负责抓帧并缓存（画面流畅），检测线程按
        # detect_fps 跑 YOLO。串行实现下抓帧(0.3s)与推理(0.35s)互相拖累，
        # 画面只能到 ~1fps（PX4 lockstep 下更明显）。
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perception-axis")
        self._thread.start()
        if detect:
            self.start_detection()
        # Model load happens lazily inside the detect thread so a slow or
        # missing YOLO model never blocks runtime startup; health reports
        # the loading state until detection is actually available.
        logger.info(
            "perception_axis_started",
            source=type(self._frame_source).__name__,
            target=self._target_class,
            detect=self._detect_enabled,
        )
        return True

    def start_detection(self) -> bool:
        """按需启动 YOLO 检测线程（模型在线程内惰性加载）；画面不受影响。"""
        if not self._running:
            return False
        self._detect_enabled = True
        thread = getattr(self, "_detect_thread", None)
        if thread is None or not thread.is_alive():
            self._detect_thread = threading.Thread(target=self._detect_loop, daemon=True, name="perception-detect")
            self._detect_thread.start()
        return True

    def stop_detection(self) -> None:
        """停止检测但保留画面；同时清掉目标快照，避免"已停止但旧框还挂着"。"""
        self._detect_enabled = False
        thread = getattr(self, "_detect_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._detect_thread = None
        with self._lock:
            self._latest_targets = []
            self._snapshot["targets"] = []
            self._snapshot["primary"] = None

    def stop(self) -> None:
        self._running = False
        self._detect_enabled = False
        for t in (self._thread, getattr(self, "_detect_thread", None)):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self._thread = None
        self._detect_thread = None
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
                "fps": self._window_fps(self._recent_frames),
                "detect_fps": self._window_fps(self._recent_detects),
                "update_fps_target": round(self._update_fps, 2),
                "detect_fps_target": round(self._detect_fps, 2),
                **self._freshness(time.time()),
                "latency_ms": round((time.time() - self._snapshot.get("timestamp", 0.0)) * 1000, 0) if self._snapshot.get("timestamp") else 0,
                "last_update_ts": self._snapshot.get("timestamp", 0.0),
                "total_frames": self._snapshot.get("total_frames", 0),
                "error": self._error,
                "source": self._snapshot.get("source", ""),
                "detector": self._detector.describe if self._detector is not None else {},
                # 3D 定位可观测性：深度链路是"静默失败"最严重的一条（拿不到深度
                # 只会让 world_pos 变 null，从表面看不出原因）。这两个计数让操作员
                # 能直接区分"没试"和"试了但失败"。
                "depth_wired": self._depth_fn is not None,
                "depth_samples": self._depth_samples,
                "depth_valid": self._depth_valid,
                "depth_last_error": self._depth_last_error,
                "depth_paused_s": round(max(0.0, self._depth_disabled_until - time.time()), 1),
            }

    def _trip_depth_breaker(self) -> None:
        """Stop sampling depth after repeated failures so the camera survives.

        Depth is optional; a pathological depth configuration (e.g. an
        oversized DepthPlanar the simulator cannot serialise in time) must
        not keep stalling the frame loop. After the cooldown one sample is
        allowed through to test recovery.
        """
        self._depth_consecutive_failures += 1
        if self._depth_consecutive_failures >= self._depth_breaker_threshold:
            self._depth_disabled_until = time.time() + self._depth_cooldown_s
            self._depth_consecutive_failures = 0
            logger.warning(
                "perception_depth_breaker_tripped",
                error=self._depth_last_error,
                cooldown_s=self._depth_cooldown_s,
            )

    @staticmethod
    def _window_fps(recent: Any, window_s: float = 3.0) -> float:
        """近 window_s 秒的瞬时帧率（自启动平均值会被模型加载期拖低，误导判断）。"""
        now = time.time()
        points = [t for t in list(recent) if now - t <= window_s]
        if len(points) < 2:
            return 0.0
        span = max(now - points[0], 0.1)
        return round(len(points) / span, 1)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            snap = dict(self._snapshot)
            # Retain diagnostic tracks, explicitly invisible/unlocked when old
            # or predicted, even if no further detector tick arrives.
            targets = [self._target_status(t, now) for t in self._latest_targets]
            snap["targets"] = targets
            snap["primary"] = self._tracker.pick_primary([t for t in targets if t["visible"]])
            snap.update(self._freshness(now))
            return snap

    def pop_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    # -- internals -----------------------------------------------------

    def _target_status(self, target: dict[str, Any], now: float) -> dict[str, Any]:
        """Copy a track with display flags; never refresh its measurement time."""
        entry = dict(target)
        measured_ts = float(entry.get("last_measured_ts") or 0.0)
        age = now - measured_ts if measured_ts else None
        stale = age is None or age < 0 or age > self._detect_display_ttl
        visible = not stale and not entry.get("predicted", False)
        entry.update(
            age_s=round(max(0.0, age), 2) if age is not None else None,
            stale=stale,
            visible=visible,
            locked=visible and entry.get("track_id") is not None,
        )
        return entry

    def _freshness(self, now: float) -> dict[str, Any]:
        """Called under the state lock; capture health is not detector health."""
        capture_ts = self._snapshot.get("timestamp", 0.0)
        age = now - self._last_detect_ts if self._last_detect_ts else None
        return {
            "detection_timestamp": self._last_detect_ts,
            "detection_age_s": round(max(0.0, age), 2) if age is not None else None,
            "detection_stale": age is None or age < 0 or age > self._detect_display_ttl,
            "capture_age_s": round(max(0.0, now - capture_ts), 2) if capture_ts else None,
            "display_ttl_s": self._detect_display_ttl,
        }


    def _cache_annotated(
        self, frame: Any, targets: list[dict[str, Any]], capture_ts: float | None = None,
    ) -> None:
        """Overlay detection boxes on the newest frame and cache the JPEG.

        The perception axis is the single AirSim frame source: the UI camera
        panel and the Agent both consume this cache, so nobody re-pulls the
        simulator for display purposes.
        """
        try:
            import cv2

            # Drawing must never write into the raw buffer shared with detection.
            img = frame.copy()
            if img.ndim == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            annotations = []
            now = time.time()
            ordered = [self._target_status(t, now) for t in targets]
            ordered = [t for t in ordered if t["visible"]]
            # Suppress duplicate measured boxes for one physical object.
            drawn_boxes: list[tuple[int, int, int, int]] = []

            def _overlaps(box: tuple[int, int, int, int]) -> bool:
                ax1, ay1, ax2, ay2 = box
                for bx1, by1, bx2, by2 in drawn_boxes:
                    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
                    if ix2 <= ix1 or iy2 <= iy1:
                        continue
                    inter = float((ix2 - ix1) * (iy2 - iy1))
                    area_a = float(max(1, ax2 - ax1) * max(1, ay2 - ay1))
                    area_b = float(max(1, bx2 - bx1) * max(1, by2 - by1))
                    if inter / (area_a + area_b - inter) > 0.3:
                        return True
                return False

            for det in ordered:
                x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
                if _overlaps((x1, y1, x2, y2)):
                    continue
                drawn_boxes.append((x1, y1, x2, y2))
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 2)
                label = f"{det.get('class', '')} {float(det.get('confidence') or 0.0):.2f}"
                if det.get("track_id"):
                    label += f" #{det['track_id']}"
                color = (0, 255, 255)
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                bx1, by1 = x1, max(0, y1 - th - 8)
                bx2, by2 = min(img.shape[1], x1 + tw + 8), max(0, y1 - 2)
                cv2.rectangle(img, (bx1, by1), (bx2, by2), (0, 0, 0), -1)
                cv2.putText(img, label, (bx1 + 4, by2 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                annotations.append({
                    "class": det["class"],
                    "confidence": float(det["confidence"]),
                    "bbox": det["bbox"],
                    "track_id": det.get("track_id"),
                    "predicted": False,
                    "visible": True,
                    "locked": det["locked"],
                    "last_measured_ts": det["last_measured_ts"],
                })
            # 帧率/检测率不再烧进画面：画面用 object-fit: cover 铺满画面区，烧在左上角
            # 的文字在小面板上会被裁掉（只有放大面板才看得到）。数值改由 UI 面板固定位置
            # 的徽标显示（预览接口的 meta 里带 fps/detect_fps），画面本身保持干净，
            # 送给视觉模型的图也少一层无关文字。
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                self._annotated_jpeg = buf.tobytes()
                # Keep an isolated clean image to remove burned-in boxes on read
                # if capture ALSO stalls. No source RPC or detector tick required.
                self._annotated_raw = frame.copy() if annotations else None
                self._annotations = annotations
                self._annotated_ts = now if capture_ts is None else capture_ts
        except Exception:
            pass

    def annotated_frame(self) -> tuple:
        """Return (jpeg_bytes, detections, ts); empty when no frame cached yet."""
        with self._lock:
            now = time.time()
            if self._annotations and any(
                not self._target_status(t, now)["visible"] for t in self._annotations
            ):
                if self._annotated_raw is not None:
                    self._cache_annotated(self._annotated_raw, self._annotations, self._annotated_ts)
                # Encoding failure must not leak a stale burned-in overlay.
                if any(not self._target_status(t, now)["visible"] for t in self._annotations):
                    return None, [], self._annotated_ts
            return self._annotated_jpeg, list(self._annotations), self._annotated_ts



    def _detect(self, frame: Any):
        if self._detect_fn is not None:
            return self._detect_fn(frame)
        # 感知轴专用检测器：标准类别走 COCO 权重 + ByteTrack 跟踪，
        # 生僻词才回退 YOLO-World；模型实例独占，跟踪状态不被打扰。
        if self._detector is None:
            raise RuntimeError("detector not loaded")
        return self._detector.track(frame)

    def _loop(self) -> None:
        """采集线程：只抓帧 + 缓存最新画面（用最近一次检测结果画框）。

        推理在 _detect_loop 里按 detect_fps 独立进行。
        """
        frame_count = 0
        interval = 1.0 / self._update_fps
        batch_start = time.perf_counter()
        timing_rows = []
        while self._running:
            loop_start = time.perf_counter()
            try:
                frame = self._frame_source.get_frame()
                captured_at = time.perf_counter()
                if frame is None:
                    time.sleep(min(0.2, interval))
                    continue
                frame_count += 1
                now = time.time()
                self._recent_frames.append(now)
                # Frame size travels in the snapshot so consumers that need to
                # normalise a bbox no longer decode the annotated JPEG just to
                # learn how wide the image is.
                shape = getattr(frame, "shape", None)
                frame_h = int(shape[0]) if shape is not None and len(shape) >= 2 else 0
                frame_w = int(shape[1]) if shape is not None and len(shape) >= 2 else 0
                lock_started = time.perf_counter()
                with self._lock:
                    locked_at = time.perf_counter()
                    self._frame_count = frame_count
                    self._latest_frame = frame
                    self._latest_frame_ts = now
                    self._latest_frame_id += 1
                    self._snapshot = {
                        "targets": list(self._latest_targets),
                        "primary": self._latest_primary,
                        "total_frames": frame_count,
                        "fps": 0.0,
                        "timestamp": now,
                        "source": type(self._frame_source).__name__,
                        "frame_width": frame_w,
                        "frame_height": frame_h,
                    }
                    self._last_update_ts = now
                    self._cache_annotated(frame, self._latest_targets, now)
                finished_at = time.perf_counter()
                timing_rows.append(((captured_at - loop_start) * 1000,
                                    (locked_at - lock_started) * 1000,
                                    (finished_at - locked_at) * 1000))
            except Exception as exc:
                self._error = str(exc)
                logger.warning("perception_loop_error", error=str(exc))
                time.sleep(0.2)

            elapsed = time.perf_counter() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)
            if frame_count % 50 == 0 and timing_rows:
                import statistics

                batch_end = time.perf_counter()
                logger.info(
                    "perception_capture_timing",
                    sustained_fps=round(len(timing_rows) / (batch_end - batch_start), 3),
                    capture_ms=round(statistics.median(r[0] for r in timing_rows), 3),
                    lock_ms=round(statistics.median(r[1] for r in timing_rows), 3),
                    publish_ms=round(statistics.median(r[2] for r in timing_rows), 3),
                )
                timing_rows.clear()
                batch_start = batch_end
                logger.info(
                    "perception_axis_health",
                    frames=frame_count,
                    fps=self._window_fps(self._recent_frames),
                    detect_fps=self._window_fps(self._recent_detects),
                    error=self._error or "",
                )

    def _detect_loop(self) -> None:
        """检测线程：按 detect_fps 对最新帧跑 YOLO，更新目标快照与事件。"""
        model_ready = self._detect_fn is not None
        detect_count = 0
        last_seen_visible = False
        seen_frame_id = -1
        last_detect_ts = 0.0
        detect_interval = 1.0 / max(0.5, float(self._detect_fps))
        while self._running and self._detect_enabled:
            try:
                if not model_ready:
                    # 惰性加载：模型加载不阻塞采集线程（预览照常出图）
                    try:
                        from src.modules.yolo_detection import AxisDetector

                        self._detector = AxisDetector(
                            self._target_class, self._confidence, model=self._model_choice, imgsz=self._imgsz
                        )
                        model_ready = True
                        self._error = ""
                        logger.info(
                            "perception_detector_ready",
                            source=type(self._frame_source).__name__,
                            **self._detector.describe,
                        )
                    except Exception as exc:
                        self._error = f"yolo model load failed: {exc}"
                        logger.warning("perception_model_load_failed", error=str(exc))
                        time.sleep(1.0)
                        continue

                now = time.time()
                if now - last_detect_ts < detect_interval:
                    time.sleep(min(0.1, detect_interval))
                    continue
                with self._lock:
                    frame = self._latest_frame
                    frame_id = self._latest_frame_id
                    source_ts = self._latest_frame_ts
                if frame is None or frame_id == seen_frame_id:
                    time.sleep(0.05)
                    continue
                seen_frame_id = frame_id

                dets = self._detect(frame)
                # Tracker retention is for reacquisition, not visible lock.
                tracked = self._tracker.update(dets, source_ts)
                live_ids = {t.get("track_id") for t in tracked}
                for tid in self._last_measured_ts.keys() - live_ids:
                    self._last_measured_ts.pop(tid, None)
                    self._last_measured_conf.pop(tid, None)
                targets: list[dict[str, Any]] = []
                for t in tracked:
                    conf = float(t.get("confidence") or 0.0)
                    tid = t.get("track_id")
                    predicted = bool(t.get("predicted"))
                    # 显示稳定性：漏检外推帧继续沿用该航迹最后一次实测置信度，
                    # 否则画面/状态里的置信度会在 0.49↔0.20 之间来回跳，看着像
                    # "锁定不稳定"。原始值放在 measured_confidence 里。
                    if not predicted and tid is not None:
                        self._last_measured_conf[tid] = conf
                        self._last_measured_ts[tid] = source_ts
                    measured_conf = self._last_measured_conf.get(tid, conf)
                    targets.append({
                        "class": t.get("class", ""),
                        "raw_class": t.get("raw_class", ""),
                        "confidence": round(measured_conf if predicted else conf, 2),
                        "measured_confidence": round(measured_conf, 2),
                        "predicted": predicted,
                        "last_measured_ts": self._last_measured_ts.get(tid, 0.0) if predicted else source_ts,
                        "bbox": t.get("bbox"),
                        "center": t.get("center"),
                        "track_id": tid,
                        "age_s": t.get("age_s", 0.0),
                        "world_pos": None,
                        "depth_m": 0.0,
                        "distance": 0.0,
                    })
                targets = [self._target_status(t, time.time()) for t in targets]
                primary = self._tracker.pick_primary([t for t in targets if t["visible"]])
                if primary is not None:
                    # 深度投影要额外抓一帧深度图（同样受 lockstep 限制），
                    # 每 5 次检测做一次即可：持续测距对画面/检测频率都不划算。
                    depth_ready = (
                        self._depth_fn is not None
                        and detect_count % self._depth_sample_every == 0
                        and time.time() >= self._depth_disabled_until
                    )
                    if depth_ready:
                        self._depth_samples += 1
                        try:
                            world = self._depth_fn(frame, primary)
                            # 只有 valid 才写入。投影失败时返回的是
                            # world_pos={x:0,y:0,z:0} 的占位值，直接采纳会让
                            # Agent 以为目标在原点。
                            if world and world.get("valid"):
                                self._depth_valid += 1
                                self._depth_last_error = ""
                                self._depth_consecutive_failures = 0
                                primary["world_pos"] = world.get("world_pos")
                                primary["depth_m"] = float(world.get("depth_meters") or 0.0)
                                primary["distance"] = float(world.get("distance_to_drone") or 0.0)
                                tid = primary.get("track_id")
                                if tid is not None:
                                    self._last_world_pos[tid] = {
                                        "world_pos": primary["world_pos"],
                                        "depth_m": primary["depth_m"],
                                        "distance": primary["distance"],
                                        "ts": time.time(),
                                    }
                            else:
                                # 回调返回 None / valid=False 都是静默失败：区分
                                # 它们才能定位是接线问题还是投影问题。
                                self._depth_last_error = (
                                    "depth projection returned no valid depth"
                                    if world is not None else "depth callback returned None"
                                )
                                self._trip_depth_breaker()
                        except Exception as exc:
                            self._depth_last_error = f"{type(exc).__name__}: {exc}"
                            logger.warning("perception_depth_failed", error=str(exc))
                            self._trip_depth_breaker()
                    # 世界坐标回带：测距只做 1/5 的 tick，若只在采样那一拍赋值，
                    # 下一拍 primary 就是全新字典、world_pos 又变 null——对外部
                    # 观察者（快照/工具/UI）等于"永远没有 3D 位置"。与置信度回带
                    # 同理：带上每条航迹最后一次有效测距结果，并标注其年龄。
                    tid = primary.get("track_id")
                    cached = self._last_world_pos.get(tid) if tid is not None else None
                    if cached and not primary.get("world_pos"):
                        age = time.time() - cached["ts"]
                        if age <= self._depth_carry_ttl:
                            primary["world_pos"] = cached["world_pos"]
                            primary["depth_m"] = cached["depth_m"]
                            primary["distance"] = cached["distance"]
                            primary["world_pos_age_s"] = round(age, 2)

                last_detect_ts = now
                detect_count += 1
                self._recent_detects.append(now)
                with self._lock:
                    # Depth callbacks can also be slow; re-evaluate at publish.
                    targets = [self._target_status(t, time.time()) for t in targets]
                    primary = self._tracker.pick_primary([t for t in targets if t["visible"]])
                    self._last_detect_ts = source_ts
                    self._latest_targets = targets
                    self._latest_primary = primary
                    visible_now = primary is not None
                    if visible_now and not last_seen_visible:
                        self._events.append({"type": "target_found", "time": time.time(), "data": {"class": primary["class"], "confidence": primary["confidence"]}})
                    elif not visible_now and last_seen_visible:
                        self._events.append({"type": "target_lost", "time": time.time(), "data": {}})
                    last_seen_visible = visible_now
                    if len(self._events) > 50:
                        self._events = self._events[-50:]
                    self._snapshot["targets"] = list(targets)
                    self._snapshot["primary"] = primary
            except Exception as exc:
                self._error = str(exc)
                logger.warning("perception_detect_loop_error", error=str(exc))
                time.sleep(0.2)


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

    def start(self, *, detect: bool = False) -> bool:
        # 远程部署下画面与检测都在远端进程里，detect 由远端自己决定；这里只
        # 保证与本地引擎的调用签名一致。
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

    def __init__(
        self,
        profile: Any,
        camera_index: int = 0,
        rtsp_url: str = "",
        frame_provider: Any = None,
        rtsp_transport: str = "",
        rtsp_config_provider: Any = None,
    ) -> None:
        self._profile = profile
        self._camera_index = camera_index
        self._rtsp_url = rtsp_url
        self._rtsp_transport = str(rtsp_transport or "").strip().lower()
        # Optional: 返回 (url, transport) 的回调。相机面板是操作员的直接入口，
        # 所以取流参数以面板为准、.env 只是兜底；每次启动/重试都重新读一次，
        # 面板改完不必重启进程（轴自己重连时会用上新值）。
        self._rtsp_config_provider = rtsp_config_provider
        # Optional: frame fetcher backed by the flight controller instead of
        # an own msgpack client (ground-station process safe).
        self._frame_provider = frame_provider
        self._engine: Optional[Any] = None
        self._started = False
        self._start_error = ""
        # 启动时的检测意图（默认只出画面）；后台重试也沿用。
        self._detect_on_start = False

    def _rtsp_config(self) -> tuple[str, str]:
        """当前应使用的 RTSP (url, transport)：面板优先，其次 .env。"""
        if callable(self._rtsp_config_provider):
            try:
                provided = self._rtsp_config_provider() or ()
            except Exception:
                provided = ()
            if isinstance(provided, (tuple, list)) and len(provided) >= 2:
                url = str(provided[0] or "").strip()
                transport = str(provided[1] or "").strip().lower()
                if url:
                    return url, transport
        return str(self._rtsp_url or "").strip(), self._rtsp_transport

    @property
    def enabled(self) -> bool:
        return self._profile is not None

    @property
    def profile(self) -> Any:
        return self._profile

    def start(self, *, detect: bool = False) -> bool:
        # 记住"启动时要不要开检测"：后台重试（AirSim 比 UI 后起来）必须沿用同一
        # 意图，否则重试成功后检测会莫名其妙地自己开起来。
        self._detect_on_start = bool(detect)
        if not self.enabled or self._started:
            if self._started and detect:
                self.start_detection()
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
                # detect_fps was previously never passed, so the engine always ran
                # its hardcoded 2.0 while capture ran at update_fps.
                detect_fps=float(getattr(profile, "detect_fps", 2.0) or 2.0),
                imgsz=int(getattr(profile, "imgsz", 0) or 0),
                depth_sample_every=int(getattr(profile, "depth_sample_every", 15) or 15),
                health_timeout_sec=profile.health_timeout_sec,
                model=str(getattr(profile, "model", "") or ""),
                depth_fn=depth_fn,
            )
        ok = self._engine.start(detect=self._detect_on_start)
        if ok:
            self._started = True
            self._start_error = ""
        else:
            self._start_error = getattr(self._engine, "_error", "") or "engine start failed"
        return ok

    def start_detection(self) -> bool:
        """按需开启检测（画面已在跑时只加载 YOLO 并启动检测线程）。"""
        starter = getattr(self._engine, "start_detection", None)
        return bool(starter()) if callable(starter) else False

    def stop_detection(self) -> None:
        """停止检测但保留画面。"""
        stopper = getattr(self._engine, "stop_detection", None)
        if callable(stopper):
            stopper()

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
            # Always present: consumers (UI link panel, /api/link) must not have
            # to know that the key only appears once an engine exists.
            "online": False,
            "source": "",
            "fps": 0.0,
        }
        if self._engine is not None:
            base.update(self._engine.health())
        return base

    def snapshot(self) -> dict[str, Any]:
        if self._engine is None:
            return {
                "targets": [],
                "primary": None,
                "timestamp": 0.0,
                "frame_width": 0,
                "frame_height": 0,
            }
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
                cam = str(getattr(profile, "camera_name", "") or "0")
                # AirSim specifics live in the adapter so the detection core
                # stays free of the simulator; see perception_airsim_adapter.
                from src.config import config as _cfg
                from src.modules.perception_airsim_adapter import build_frame_source

                return build_frame_source(
                    frame_provider=self._frame_provider,
                    ip=str(_cfg.airsim_ip),
                    port=int(_cfg.airsim_port),
                    camera_name=cam,
                )
            if fs == "rtsp":
                url, transport = self._rtsp_config()
                if not url:
                    self._start_error = "rtsp frame source requires rtsp_url"
                    return None
                from src.modules.frame_source import RtspFrameSource

                return RtspFrameSource(url, transport=transport)
            if fs == "usb":
                from src.modules.frame_source import CameraFrameSource

                return CameraFrameSource(self._camera_index)
        except Exception as exc:
            self._start_error = f"frame source init failed: {exc}"
            logger.warning("perception_frame_init_failed", error=str(exc))
        return None

    def _build_depth_fn(self, profile: Any, frame_source: Any):
        """Return a depth projection callback for AirSim frames, else None.

        Depth must come from the flight controller's RPC path (that is the one
        with a hard timeout and a runtime reset). ``LazyControllerFrameSource``
        deliberately hides its controller, so the provider is passed in
        explicitly instead of being dug out of the frame source -- reaching for
        ``frame_source._client`` is what silently disabled 3D target
        localisation before.
        """
        if profile.frame_source != "airsim":
            return None
        provider = self._frame_provider
        if provider is None:
            # Standalone axis (no flight controller wired): fall back to the
            # own-client frame source, which exposes its client directly.
            provider = getattr(frame_source, "_client", None)
        if provider is None:
            return None
        from src.modules.perception_airsim_adapter import build_depth_fn

        return build_depth_fn(
            frame_provider=provider,
            camera_name=str(getattr(profile, "camera_name", "") or "0"),
            fov_h=float(getattr(profile, "fov_h", 90.0) or 90.0),
        )