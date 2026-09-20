"""AirSim adapter for the perception axis.

Everything AirSim-specific on the perception path lives here: the camera frame
source factory and the depth-projection callback. The detection/tracking core
(``perception_axis``, ``yolo_detection``, ``target_tracker``) deliberately does
not import ``airsim``, so a future extraction into a standalone package -- or a
re-host on a ROS image topic -- does not have to drag the simulator along.

The depth callback exists because ``DepthProjection`` needs a metric depth
image plus the vehicle pose at the same instant. Both must come from the flight
controller's RPC path (worker thread + hard timeout + runtime reset); a
hand-rolled ``MultirotorClient`` in this process has repeatedly hung on
``simGetImages`` because ``Session.call`` has no timeout.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

from src.logging_config import get_logger

logger = get_logger(__name__)


# Depth images are metric float data, so the request must ask for float pixels
# (``pixels_as_float=True``) and skip compression. Asking for uint8 while
# reading ``image_data_float`` yields an empty array -- that silently disabled
# 3D target localisation before.
_DEPTH_PIXELS_AS_FLOAT = True
_DEPTH_COMPRESS = False

# AirSim publishes one "0" scene camera per vehicle; the perception axis follows
# the same convention as the rest of the runtime.
DEFAULT_CAMERA_NAME = "0"
DEFAULT_IMAGE_TYPE = 0


def _resolve_controller(frame_provider: Any) -> Any | None:
    """Resolve the frame provider into a live controller instance.

    ``frame_provider`` may be the controller itself or a zero-arg callable that
    returns one (the runtime passes a bound method so the axis never pins a
    stale backend instance). Returns ``None`` when no usable client exists.
    """
    controller = frame_provider() if callable(frame_provider) else frame_provider
    if controller is None:
        return None
    if getattr(controller, "_client", None) is None:
        return None
    return controller


def build_frame_source(
    frame_provider: Any = None,
    ip: str = "",
    port: int = 0,
    camera_name: str = DEFAULT_CAMERA_NAME,
    image_type: int = DEFAULT_IMAGE_TYPE,
    timeout_sec: float = 8.0,
) -> Any | None:
    """Build an AirSim-backed ``FrameSource``.

    Prefers the flight controller's capture path when a provider is available;
    otherwise falls back to an own msgpack client after a fast TCP probe, so a
    missing simulator fails the axis in ~1s instead of blocking on RPC timeouts.
    """
    from src.modules.frame_source import AirSimFrameSource, LazyControllerFrameSource

    if frame_provider is not None:
        return LazyControllerFrameSource(frame_provider, camera_name=camera_name, image_type=image_type)

    import socket

    import airsim

    with socket.socket() as sock:
        sock.settimeout(1.0)
        sock.connect((ip, port))
    # The axis owns an independent AirSim client, decoupled from the flight
    # backend controller.
    client = airsim.MultirotorClient(ip=ip, port=port)
    return AirSimFrameSource(client, camera_name=camera_name, image_type=image_type, timeout_sec=timeout_sec, host=ip, port=port)


def get_depth_image(
    controller: Any,
    camera_name: str = DEFAULT_CAMERA_NAME,
    vehicle_name: str = "",
    timeout_sec: float = 3.0,
) -> Any | None:
    """Read an AirSim DepthPlanar image as a float32 array in metres.

    Keep this short. AirSim serialises uncompressed float images very slowly and
    its RPC server is single-threaded, so a slow depth request starves colour
    capture too. At the intended depth resolution (256x144) a sample takes
    ~300 ms, so 3 s is ample; anything slower is a misconfiguration and should
    fail fast into the engine's depth circuit breaker."""
    if controller is None or getattr(controller, "_client", None) is None:
        return None
    try:
        import airsim
        import numpy as np

        responses = controller._rpc(
            controller._client.simGetImages,
            [airsim.ImageRequest(camera_name, airsim.ImageType.DepthPlanar, _DEPTH_PIXELS_AS_FLOAT, _DEPTH_COMPRESS)],
            timeout=timeout_sec,
            vehicle_name=vehicle_name,
        )
        if not responses:
            return None
        img_data = responses[0]
        # ``image_data_float`` is a scalar 0.0 when the request asked for uint8
        # pixels or when AirSim returned an empty frame.
        if not getattr(img_data, "image_data_float", None):
            return None
        depth_1d = np.array(img_data.image_data_float, dtype=np.float32)
        width = int(getattr(img_data, "width", 0) or 0)
        height = int(getattr(img_data, "height", 0) or 0)
        if width > 0 and height > 0 and len(depth_1d) == width * height:
            return depth_1d.reshape((height, width))
    except Exception as exc:  # noqa: BLE001 — perception must never break the run
        logger.warning("perception_depth_image_failed", error=str(exc))
    return None


def _derive_fov_v(fov_h: float, width: int, height: int) -> float:
    """Vertical FOV from the horizontal one and the image aspect ratio.

    AirSim's FOV_Degrees is the horizontal FOV. Treating the vertical FOV as a
    fixed constant (it used to be hardcoded at 60 deg) makes the pixel-to-angle
    conversion wrong for every aspect ratio, and the error grows with the
    off-axis angle -- i.e. exactly where targets sit when you are approaching
    them. Uses the pinhole relation, not a linear ratio.
    """
    if width <= 0 or height <= 0:
        return fov_h
    half = math.radians(max(1.0, min(179.0, fov_h))) / 2.0
    return math.degrees(2.0 * math.atan(math.tan(half) * (height / float(width))))


def build_depth_fn(
    frame_provider: Any = None,
    camera_name: str = DEFAULT_CAMERA_NAME,
    vehicle_name: str = "",
    timeout_sec: float = 3.0,
    fov_h: float = 90.0,
) -> Optional[Callable[[Any, dict[str, Any]], Optional[dict[str, Any]]]]:
    """Return a depth-projection callback, or ``None`` when unavailable.

    The callback matches the engine contract
    ``depth_fn(frame, detection) -> world position dict | None``.

    Note the provider is resolved *inside* the callback rather than captured
    once: the runtime rebuilds the camera controller after an AirSim restart, and
    a captured reference would pin the dead client. For the same reason there is
    deliberately **no** readiness check here -- the axis is built before the
    camera controller finishes connecting, so an eager check permanently
    disabled depth localisation even after the controller came up.
    """
    if frame_provider is None:
        return None

    def depth_fn(frame: Any, detection: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            from src.modules.airsim_controller import AirSimController
            from src.modules.occupancy_map import DepthProjection

            controller = _resolve_controller(frame_provider)
            if controller is None:
                return None
            bbox = detection.get("bbox")
            if not bbox:
                return None
            stabilization_status = getattr(controller, "camera_stabilization_status", None)
            if callable(stabilization_status) and stabilization_status(camera_name, vehicle_name).get("enabled"):
                # This projector models only vehicle yaw, not a moving camera's extrinsics.
                return {"valid": False, "reason": "stabilized camera requires pose-aware depth projection"}

            depth_img = get_depth_image(controller, camera_name, vehicle_name, timeout_sec)
            if depth_img is None:
                return None

            state = controller._rpc(controller._client.getMultirotorState, timeout=timeout_sec, vehicle_name=vehicle_name)
            if state is None:
                return None
            kinematics = getattr(state, "kinematics_estimated", None)
            if kinematics is None:
                return None
            position = kinematics.position
            drone_pos = (position.x_val, position.y_val, position.z_val)
            _, _, yaw_rad = AirSimController._quat_to_euler(kinematics.orientation)
            drone_yaw = math.degrees(yaw_rad)

            depth_h, depth_w = int(depth_img.shape[0]), int(depth_img.shape[1])
            return DepthProjection.project_detection_to_world(
                bbox=_scale_bbox_to_depth(bbox, frame, depth_img, fov_h),
                depth_img=depth_img,
                drone_pos=drone_pos,
                drone_yaw=drone_yaw,
                fov_h=fov_h,
                fov_v=_derive_fov_v(fov_h, depth_w, depth_h),
            )
        except Exception as exc:  # noqa: BLE001 — a missing depth must not kill detection
            logger.warning("perception_depth_projection_failed", error=str(exc))
            return None

    return depth_fn


def _scale_bbox_to_depth(bbox: Any, frame: Any, depth_img: Any, fov_h: float = 90.0) -> list[int]:
    """Map a colour-frame bbox into the depth image's pixel space.

    The two images differ in size, and they do not necessarily cover the same
    area: ``FOV_Degrees`` is the *horizontal* FOV, so a 4:3 colour frame and a
    16:9 depth frame see different vertical extents (~73.7 deg vs ~58.7 deg at
    fov_h=90). Scaling y linearly by the height ratio then samples the wrong
    depth row for anything off the image centre, and the error grows with the
    offset -- exactly the situation being measured while approaching a target.

    Horizontal scaling is linear because both images share fov_h. Vertical is
    mapped through the actual angle, so any depth resolution is handled
    correctly without having to keep the two aspect ratios in sync by hand.

    Coordinates left in colour space would also be clamped to the depth image
    edge, collapsing the box into ``valid=False`` -- which is what made depth
    localisation look broken even when the depth fetch itself worked.
    """
    try:
        coords = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return []
    if len(coords) != 4:
        return []

    frame_shape = getattr(frame, "shape", None)
    depth_shape = getattr(depth_img, "shape", None)
    if frame_shape is None or depth_shape is None or len(frame_shape) < 2 or len(depth_shape) < 2:
        return [int(round(v)) for v in coords]

    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    depth_h, depth_w = int(depth_shape[0]), int(depth_shape[1])
    if frame_w <= 0 or frame_h <= 0 or depth_w <= 0 or depth_h <= 0:
        return [int(round(v)) for v in coords]
    if frame_w == depth_w and frame_h == depth_h:
        return [int(round(v)) for v in coords]

    scale_x = depth_w / float(frame_w)
    # Shared fov_h makes x linear; the vertical extents differ, so map y through
    # the angle instead of the height ratio.
    fov_v_frame = math.radians(_derive_fov_v(fov_h, frame_w, frame_h)) / 2.0
    fov_v_depth = math.radians(_derive_fov_v(fov_h, depth_w, depth_h)) / 2.0
    tan_frame = math.tan(fov_v_frame)
    tan_depth = math.tan(fov_v_depth)

    def map_y(y: float) -> int:
        if tan_frame <= 0 or tan_depth <= 0:
            return int(round(y * depth_h / float(frame_h)))
        normalised = (y - frame_h / 2.0) / (frame_h / 2.0)
        angle = math.atan(normalised * tan_frame)
        return int(round(depth_h / 2.0 * (1.0 + math.tan(angle) / tan_depth)))

    return [
        int(round(coords[0] * scale_x)),
        map_y(coords[1]),
        int(round(coords[2] * scale_x)),
        map_y(coords[3]),
    ]
