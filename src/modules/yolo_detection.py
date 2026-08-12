"""Shared YOLO and AirSim projection helpers.

This module keeps reusable perception code out of legacy workflow tool files.
Search, tracking, patrol, and formation strategy should live in skills or
provider-backed services, while these helpers remain single-frame perception
building blocks.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from src.logging_config import get_logger
from src.modules.airsim_controller import AirSimController
from src.modules.occupancy_map import DepthProjection

logger = get_logger(__name__)

_yolo_model: Any | None = None
_yolo_model_classes: tuple[str, ...] | None = None
_yolo_model_lock = threading.Lock()

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "vehicle"}
PERSON_CLASSES = {"person", "pedestrian"}

TARGET_ALIASES = {
    "car": VEHICLE_CLASSES | {"suv", "sedan", "minivan", "cab", "taxi"},
    "truck": VEHICLE_CLASSES | {"pickup", "lorry", "van"},
    "person": PERSON_CLASSES,
    "vehicle": VEHICLE_CLASSES,
}

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
    """Build a compact YOLO-World vocabulary for a requested target class."""

    if not target_class:
        return ["car", "person", "truck", "bus"]
    classes = [target_class]
    aliases = TARGET_ALIASES.get(target_class.lower(), set())
    for alias in aliases:
        if alias not in classes:
            classes.append(alias)
        if len(classes) >= 5:
            break
    return classes


def run_yolo_detection(model: Any, img: Any, target_class: str, confidence: float) -> list[dict[str, Any]]:
    """Run YOLO inference and return detections matching the requested target."""

    results = model(img, verbose=False)
    boxes = results[0].boxes
    aliases = TARGET_ALIASES.get(target_class.lower(), set()) if target_class else set()

    detections: list[dict[str, Any]] = []
    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = str(model.names[cls_id])
        conf = float(box.conf[0])
        if conf < confidence:
            continue
        if target_class and cls_name.lower() != target_class.lower() and cls_name.lower() not in aliases:
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


def get_depth_image(
    controller: AirSimController,
    camera_name: str,
    vehicle_name: str,
) -> np.ndarray | None:
    """Read an AirSim DepthPlanar image as a float32 array in meters."""

    try:
        import airsim

        responses = controller._rpc_call(
            lambda: controller._client.simGetImages(
                [airsim.ImageRequest(camera_name, airsim.ImageType.DepthPlanar, False, False)],
                vehicle_name=vehicle_name,
            ),
            timeout=10.0,
        )
        if not responses:
            return None
        img_data = responses[0]
        if not img_data.image_data_float:
            return None
        depth_1d = np.array(img_data.image_data_float, dtype=np.float32)
        width = int(img_data.width)
        height = int(img_data.height)
        if width > 0 and height > 0 and len(depth_1d) == width * height:
            return depth_1d.reshape((height, width))
    except Exception as exc:
        logger.warning("depth_image_failed", error=str(exc))
    return None


def project_detections_to_3d(
    detections: list[dict[str, Any]],
    controller: AirSimController,
    camera_name: str,
    vehicle_name: str,
    fov_h: float = 90.0,
    fov_v: float = 60.0,
) -> list[dict[str, Any]]:
    """Project 2D detection boxes into local 3D coordinates using depth."""

    depth_img = get_depth_image(controller, camera_name, vehicle_name)
    if depth_img is None:
        logger.warning("depth_image_missing_for_projection")
        for det in detections:
            det["world_3d"] = {"valid": False}
        return detections

    try:
        status = controller.get_status(vehicle_name)
        drone_pos = (
            status.position_ned["x"],
            status.position_ned["y"],
            status.position_ned["z"],
        )
        drone_yaw = controller.get_heading(vehicle_name)
    except Exception as exc:
        logger.warning("vehicle_pose_missing_for_projection", error=str(exc))
        for det in detections:
            det["world_3d"] = {"valid": False}
        return detections

    for det in detections:
        projection = DepthProjection.project_detection_to_world(
            bbox=det["bbox"],
            depth_img=depth_img,
            drone_pos=drone_pos,
            drone_yaw=drone_yaw,
            fov_h=fov_h,
            fov_v=fov_v,
        )
        det["world_3d"] = projection
        if projection["valid"]:
            logger.info(
                "detection_projected_to_3d",
                class_name=det.get("class"),
                depth_m=projection.get("depth_meters"),
                world_pos=projection.get("world_pos"),
                distance_m=projection.get("distance_to_drone"),
            )

    return detections

