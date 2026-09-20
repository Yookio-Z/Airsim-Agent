"""Shared YOLO and AirSim projection helpers.

This module keeps reusable perception code out of legacy workflow tool files.
Search, tracking, patrol, and formation strategy should live in skills or
provider-backed services, while these helpers remain single-frame perception
building blocks.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.logging_config import get_logger

logger = get_logger(__name__)

_yolo_model: Any | None = None
_yolo_model_classes: tuple[str, ...] | None = None
_yolo_model_lock = threading.Lock()
# Inference lock: the YOLO model singleton is shared by the perception axis
# thread, camera tools, and any diagnostics. Torch inference on the same
# model instance from two threads can deadlock inside the worker pool, so
# every inference call is serialized through this lock.
_yolo_infer_lock = threading.Lock()

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "vehicle"}
PERSON_CLASSES = {"person", "pedestrian"}

TARGET_ALIASES = {
    "car": VEHICLE_CLASSES | {"suv", "sedan", "minivan", "cab", "taxi"},
    "truck": VEHICLE_CLASSES | {"pickup", "lorry", "van"},
    "person": PERSON_CLASSES,
    "vehicle": VEHICLE_CLASSES,
}

# 基础词表：任何请求都在此基础上扩展，绝不把词表收缩成单个生僻短语。
# 之前 build_search_classes("blue car") 会把词表换成 ["blue car"] 一个短语，
# 导致 set_classes 反复重建（慢）+ 检测结果漂移（不稳定）。
_BASE_VOCABULARY = ["car", "truck", "bus", "person", "motorcycle", "bicycle"]

# 颜色/属性修饰词：从目标描述里剥离，只留规范类别（"blue car" → "car"）。
_MODIFIER_WORDS = {
    "blue", "red", "green", "yellow", "black", "white", "gray", "grey",
    "silver", "orange", "brown", "purple", "pink", "gold", "cyan", "dark",
    "light", "bright", "large", "small", "big", "tiny", "moving", "parked",
    "蓝", "蓝色", "红", "红色", "绿", "绿色", "黄", "黄色", "黑", "黑色",
    "白", "白色", "灰", "灰色", "银", "银色", "橙", "橙色", "棕", "棕色",
    "紫", "紫色", "粉", "粉色", "金", "金色", "大", "小", "移动", "静止",
}


def canonical_target_class(target_class: str) -> str:
    """把目标描述归一化为规范类别：'blue car' → 'car'，'行人' → 'person'。

    找不到已知类别时返回第一个非修饰词，保证调用方始终拿到一个可用类别，
    而不是一个颜色短语（YOLO-World 对生僻短语的检出既慢又不稳定）。
    """
    text = str(target_class or "").strip().lower()
    if not text:
        return ""
    tokens = [t for t in re.split(r"[\s_/,-]+", text) if t]
    known = set(TARGET_ALIASES) | {"bus", "motorcycle", "bicycle", "truck", "car", "person", "vehicle"}
    for token in tokens:
        if token in known:
            return token
    # 中文别名（中文没有空格分词，按子串匹配；长词必须排在短词前，
    # 否则 "车辆" 会被 "车" 抢先匹配成 car）
    zh_map = {"汽车": "car", "轿车": "car", "车辆": "vehicle", "卡车": "truck",
              "货车": "truck", "公交": "bus", "巴士": "bus", "客车": "bus",
              "摩托": "motorcycle", "自行车": "bicycle", "行人": "person",
              "车": "car", "人": "person"}
    for token in tokens:
        for zh_key, zh_val in zh_map.items():
            if zh_key in token:
                return zh_val
    for token in tokens:
        if token not in _MODIFIER_WORDS:
            return token
    return tokens[0] if tokens else ""

SIM_FALSE_POSITIVES = {
    "surfboard",
    "skateboard",
    "snowboard",
    "skis",
    "kite",
    "baseball bat",
    "baseball glove",
    "tennis racket",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "frisbee",
    "boogie board",
}


def _resolve_yolo_model_path() -> str:
    """Locate the YOLO-World weights: repo-local models/ dir, then CWD (legacy)."""

    repo_models = Path(__file__).resolve().parents[2] / "models" / "yolov8s-worldv2.pt"
    if repo_models.is_file():
        return str(repo_models)
    return "yolov8s-worldv2.pt"


def get_yolo_model(classes: list[str] | None = None) -> Any:
    """Load and cache YOLO-World, updating its class vocabulary when needed."""

    global _yolo_model, _yolo_model_classes
    with _yolo_model_lock:
        if _yolo_model is None:
            from ultralytics import YOLO

            _yolo_model = YOLO(_resolve_yolo_model_path())
            _yolo_model_classes = None
            logger.info("YOLO-World v2 model loaded")
        if classes and tuple(classes) != _yolo_model_classes:
            _yolo_model.set_classes(list(classes))
            _yolo_model_classes = tuple(classes)
            logger.info("YOLO-World classes updated", classes=classes)
        return _yolo_model


def build_search_classes(target_class: str) -> list[str]:
    """Build a stable YOLO-World vocabulary for a requested target class.

    返回的集合以基础词表为骨架（始终包含 car/truck/bus/person 等），再补上
    规范类别及其别名。这样不同调用方（感知轴 / 拍照验证 / 预览标注）传入
    "car"、"blue car"、"vehicle" 时得到的词表高度一致，避免 set_classes 反复
    重建与检测结果漂移。
    """

    canonical = canonical_target_class(target_class)
    classes: list[str] = []
    if canonical:
        classes.append(canonical)
        for alias in sorted(TARGET_ALIASES.get(canonical, ())):
            if alias not in classes:
                classes.append(alias)
    for base in _BASE_VOCABULARY:
        if base not in classes:
            classes.append(base)
    return classes[:8]


def run_yolo_detection(
    model: Any,
    img: Any,
    target_class: str,
    confidence: float,
    imgsz: int | float | str | None = None,
) -> list[dict[str, Any]]:
    """Run YOLO inference and return detections matching the requested target."""

    with _yolo_infer_lock:
        results = model(img, verbose=False, imgsz=_resolve_imgsz(imgsz))
    boxes = results[0].boxes
    # 归一化后再比对：这样 target="blue car" 也能匹配到模型输出的 "car"。
    canonical = canonical_target_class(target_class)
    aliases = TARGET_ALIASES.get(canonical, set()) if canonical else set()

    detections: list[dict[str, Any]] = []
    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = str(model.names[cls_id])
        conf = float(box.conf[0])
        if conf < confidence:
            continue
        if cls_name.lower() in SIM_FALSE_POSITIVES:
            continue
        if canonical and cls_name.lower() != canonical and cls_name.lower() not in aliases:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        detections.append(
            {
                "class": cls_name,
                "confidence": round(conf, 2),
                "bbox": [round(x1), round(y1), round(x2), round(y2)],
                "center": [round(cx), round(cy)],
            }
        )

    return detections



# ---------------------------------------------------------------------
# COCO 固定类别模型 + ByteTrack 跟踪
# ---------------------------------------------------------------------
#
# YOLO-World 是开放词表模型：对 car/person 这类标准类别的精度与稳定性都不如
# 常规 COCO 权重。因此标准类别走 COCO 模型（yolov8s.pt），生僻/自定义词才回退
# YOLO-World。跟踪用 ultralytics 内置 ByteTrack（Kalman + 低分二次关联），
# ID 比自研 IoU 稳定。

COCO_CANONICAL = {"car", "truck", "bus", "person", "motorcycle", "bicycle"}

_coco_model: Any | None = None
_coco_model_lock = threading.Lock()


def _resolve_coco_model_path() -> str:
    repo_models = Path(__file__).resolve().parents[2] / "models" / "yolov8s.pt"
    return str(repo_models) if repo_models.is_file() else "yolov8s.pt"


def get_coco_model() -> Any:
    """Load and cache the fixed-class COCO detector (yolov8s)."""
    global _coco_model
    with _coco_model_lock:
        if _coco_model is None:
            from ultralytics import YOLO

            _coco_model = YOLO(_resolve_coco_model_path())
            logger.info("COCO detector loaded", model=_resolve_coco_model_path())
        return _coco_model


def _coco_names_for(canonical: str) -> set[str]:
    """COCO 模型里与规范类别对应的类名集合。"""
    mapping = {
        "car": {"car"},
        "truck": {"truck"},
        "bus": {"bus"},
        "person": {"person"},
        "motorcycle": {"motorcycle"},
        "bicycle": {"bicycle"},
        "vehicle": {"car", "truck", "bus", "motorcycle"},
    }
    return mapping.get(canonical, {canonical})


class AxisDetector:
    """感知轴专用检测器：独占模型实例 + ByteTrack，状态与预览/拍照路径隔离。

    ultralytics 的跟踪状态挂在模型实例上，若与其它调用方共用同一个模型，
    track/predict 交替会打乱跟踪器。因此感知轴自己持有一个实例。
    """

    def __init__(self, target_class: str = "car", confidence: float = 0.25, tracker: str = "bytetrack.yaml",
                 model: str = "", imgsz: int | float | str | None = None) -> None:
        self.canonical = canonical_target_class(target_class) or "car"
        self.confidence = float(confidence)
        self.tracker = tracker
        self.imgsz = _resolve_imgsz(imgsz)
        self.kind = self._select_kind(self.canonical, model)
        if self.kind == "coco":
            self._model = get_coco_model()
            self._accept: set[str] = _coco_names_for(self.canonical)
        else:
            self._model = get_yolo_model(build_search_classes(self.canonical))
            self._accept = {self.canonical} | set(TARGET_ALIASES.get(self.canonical, ()))
        self._lock = threading.Lock()
        self._track_error = ""
        # 已锁定后降低检出阈值：飞行中常有运动模糊/小目标，阈值过高会频繁
        # 漏检导致锁定闪断。锁定期间用更低阈值，让 ByteTrack 与保持层接得住。
        self._locked_until = 0.0

    @staticmethod
    def _select_kind(canonical: str, model: str = "") -> str:
        """选择检测模型：auto|world|coco（来自配置，其次环境变量）。

        默认 auto（COCO 类别用固定类模型）。设成 world 可强制改用
        YOLO-World 开放词表模型——夜景/低对比度场景下它对"car"这类词的
        置信度往往更高；coco 则更保守。
        """
        choice = str(model or os.environ.get("DRONE_PERCEPTION_MODEL", "") or "").strip().lower()
        if choice in {"world", "yoloworld", "yolo-world"}:
            return "world"
        if choice in {"coco", "yolov8", "fixed"}:
            return "coco"
        return "coco" if canonical in COCO_CANONICAL else "world"

    @property
    def describe(self) -> dict[str, Any]:
        return {
            "model": self.kind,
            "target": self.canonical,
            "tracker": self.tracker,
            "imgsz": self.imgsz,
            "track_error": self._track_error,
        }

    def _iter_boxes(self, result: Any) -> list[dict[str, Any]]:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        names = getattr(self._model, "names", {}) or {}
        out: list[dict[str, Any]] = []
        ids = boxes.id.tolist() if getattr(boxes, "id", None) is not None else []
        for index, box in enumerate(boxes):
            cls_id = int(box.cls[0])
            cls_name = str(names.get(cls_id, cls_id)).lower()
            conf = float(box.conf[0])
            if conf < self.confidence or cls_name in SIM_FALSE_POSITIVES:
                continue
            if self.kind == "coco":
                if cls_name not in self._accept:
                    continue
            else:
                if cls_name != self.canonical and cls_name not in self._accept:
                    continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            entry = {
                # 对外统一报告规范类别名：YOLO-World 的开放词表会在 car/minivan/
                # van 之间摇摆，同一辆车每帧换标签会让画面/状态看着"跳"且不可信。
                # 原始标签保留在 raw_class 里便于排查。
                "class": self.canonical,
                "raw_class": cls_name,
                "confidence": round(conf, 2),
                "bbox": [round(x1), round(y1), round(x2), round(y2)],
                "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)],
            }
            if ids:
                try:
                    entry["track_id"] = int(ids[index])
                except Exception:
                    pass
            out.append(entry)
        return out

    def detect(self, frame: Any) -> list[dict[str, Any]]:
        """单帧检测（无跟踪状态），用于预览/验证等场景。"""
        frame = _to_three_channel(frame)
        with self._lock:
            results = self._model.predict(frame, verbose=False, conf=self.confidence, imgsz=self.imgsz)
        return self._iter_boxes(results[0]) if results else []

    def track(self, frame: Any) -> list[dict[str, Any]]:
        """带 ByteTrack 的检测：返回带 track_id 的目标；失败自动回退单帧检测。

        注意回退是**静默**的：一旦跟踪器抛错就走无状态检测，track_id 全部丢失、
        保活也失效——表现出来就是画面标注闪断，而日志里只有一条 warning。所以
        这里先把 4 通道帧转成 3 通道：BGRA 会让卷积层直接报维度错（实测
        "expected input[1, 4, ...] to have 3 channels"），而任何调用方都可能传入
        未去 alpha 的帧。
        """
        frame = _to_three_channel(frame)
        now = time.time()
        locked = now < self._locked_until
        conf = self.confidence * 0.6 if locked else self.confidence
        try:
            with self._lock:
                results = self._model.track(
                    frame, persist=True, tracker=self.tracker, verbose=False, conf=conf, imgsz=self.imgsz
                )
            self._track_error = ""
            dets = self._iter_boxes(results[0]) if results else []
            if dets:
                # 命中即续锁 5s：期间用低阈值维持
                self._locked_until = now + 5.0
            # Deliberately NO fallback on an empty result. Substituting stateless
            # detections here looks harmless but they carry no track id, so the
            # engine's IouTracker cannot associate them with the live track: the
            # track breaks and the box disappears for a sample or two. Measured:
            # this took the lock duty cycle from 100% to 92.8% with 1.4 s gaps.
            # An empty result here is better handled by the keep-alive, which
            # extrapolates the existing track instead of replacing it.
            return dets
        except Exception as exc:  # 跟踪器不可用时不能拖垮感知
            self._track_error = str(exc)
            logger.warning("bytetrack_failed", error=str(exc))
            return self.detect(frame)


def _to_three_channel(frame: Any) -> Any:
    """Drop an alpha channel if present (BGRA -> BGR).

    AirSim Scene frames arrive as BGRA; the frame sources normally strip it, but
    not every caller does, and a 4-channel tensor fails inside the first conv
    layer. That failure is not loud: `track()` degrades to stateless detection,
    so track ids and keep-alive silently disappear and the on-screen boxes start
    flickering -- a symptom that looks like a detector problem and is not one.
    """
    try:
        if hasattr(frame, "ndim") and frame.ndim == 3 and frame.shape[2] == 4:
            import cv2

            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    except Exception:
        pass
    return frame


def _resolve_imgsz(override: int | float | str | None = None) -> int:
    """Inference resolution.

    Defaults to 1280 rather than ultralytics' 640. Measured on live AirSim
    frames (a car 32 px across at the default camera geometry, 12 frames x 4
    repeats, warmup excluded):

        imgsz 640  -> median conf 0.509, min 0.454, 145 ms (6.9 Hz)
        imgsz 960  -> median conf 0.763, min 0.740, 319 ms (3.1 Hz)
        imgsz 1280 -> median conf 0.839, min 0.822, 492 ms (2.0 Hz)

    At 640 the margin above a sane threshold is thin, so one marginal frame can
    drop the target. 1280 costs 2.0 Hz, which is exactly the configured
    detect_fps, so the margin is bought for free in this pipeline. YOLO-World
    does not benefit (its conf fell 0.673 -> 0.625 at 1280), which is a further
    reason standard classes should route to COCO.
    """
    value = override
    if value in (None, "", 0, "0"):
        value = os.environ.get("DRONE_PERCEPTION_IMGSZ", "")
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return 1280
    return parsed if parsed >= 160 else 1280


def detect_objects_stateless(
    frame: Any,
    target_class: str = "car",
    confidence: float = 0.25,
    imgsz: int | float | str | None = None,
) -> list[dict[str, Any]]:
    """无跟踪状态的单帧检测（预览标注 / 拍照验证用）。

    标准类别走 COCO 固定类别模型（更准更快），生僻词回退 YOLO-World。
    """
    canonical = canonical_target_class(target_class) or "car"
    size = _resolve_imgsz(imgsz)
    if canonical in COCO_CANONICAL:
        model = get_coco_model()
        accept = _coco_names_for(canonical)
        with _yolo_infer_lock:
            results = model.predict(frame, verbose=False, conf=float(confidence), imgsz=size)
        boxes = getattr(results[0], "boxes", None) if results else None
        out: list[dict[str, Any]] = []
        if boxes is None:
            return out
        names = getattr(model, "names", {}) or {}
        for box in boxes:
            cls_name = str(names.get(int(box.cls[0]), "")).lower()
            conf = float(box.conf[0])
            if conf < confidence or cls_name not in accept or cls_name in SIM_FALSE_POSITIVES:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            out.append({
                "class": cls_name,
                "confidence": round(conf, 2),
                "bbox": [round(x1), round(y1), round(x2), round(y2)],
                "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)],
            })
        return out
    model = get_yolo_model(build_search_classes(canonical))
    return run_yolo_detection(model, frame, canonical, confidence, imgsz=size)
