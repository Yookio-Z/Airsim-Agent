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


def project_point_to_world(
    frame_provider: Any = None,
    x_norm: float = 0.5,
    y_norm: float = 0.5,
    camera_name: str = DEFAULT_CAMERA_NAME,
    vehicle_name: str = "",
    timeout_sec: float = 3.0,
    fov_h: float = 90.0,
    frame_shape: tuple[int, int] | None = None,
    patch_ratio: float = 0.015,
    hint_w: float = 0.0,
    hint_h: float = 0.0,
) -> dict[str, Any]:
    """把**一个归一化图像点**通过深度图与无人机位姿投影成世界坐标（NED）。

    这是"检测框 + 深度"那套融合的免检测替代：视觉模型给一个粗点，深度给公制
    距离，位姿把相机方向转进世界系。几何只在这一个实现里（``DepthProjection``），
    和感知轴当年用的是同一份代码。

    ``frame_shape`` 是归一化坐标的基准画幅 ``(h, w)``——视觉模型看到的是彩色帧，
    深度图分辨率/纵向视场与它不同，纵向必须按角度映射（水平共用 fov_h，线性）。
    拿不到基准画幅时按深度图自身的画幅近似，并在返回里标注 ``frame_basis``。

    返回始终是 dict：``{"valid": True, "world_pos": {...}, "distance_m": ...,
    "depth_m": ..., "pixel": {...}}`` 或 ``{"valid": False, "reason": ...}``。
    """
    try:
        from src.modules.occupancy_map import DepthProjection

        controller = _resolve_controller(frame_provider)
        if controller is None:
            return {"valid": False, "reason": "no_controller", "message": "AirSim 未连接"}
        stabilization_status = getattr(controller, "camera_stabilization_status", None)
        if callable(stabilization_status) and stabilization_status(camera_name, vehicle_name).get("enabled"):
            # 与 build_depth_fn 同一约束：本投影只建模机头偏航，不含云台外参。
            return {
                "valid": False,
                "reason": "stabilized_camera",
                "message": "相机处于云台稳定模式，本投影只建模机头偏航，无法给出可信坐标。",
            }
        depth_img = get_depth_image(controller, camera_name, vehicle_name, timeout_sec)
        if depth_img is None:
            return {"valid": False, "reason": "no_depth", "message": "深度相机不可用（拿不到公制深度图）"}
        state = controller._rpc(
            controller._client.getMultirotorState, timeout=timeout_sec, vehicle_name=vehicle_name
        )
        if state is None:
            return {"valid": False, "reason": "no_state", "message": "读取无人机位姿失败"}
        kinematics = getattr(state, "kinematics_estimated", None)
        if kinematics is None:
            return {"valid": False, "reason": "no_state", "message": "读取无人机位姿失败"}
        from src.modules.airsim_controller import AirSimController

        position = kinematics.position
        drone_pos = (position.x_val, position.y_val, position.z_val)
        _, _, yaw_rad = AirSimController._quat_to_euler(kinematics.orientation)
        drone_yaw = math.degrees(yaw_rad)

        depth_h, depth_w = int(depth_img.shape[0]), int(depth_img.shape[1])
        cx, cy, basis = _map_normalised_point(
            x_norm, y_norm, frame_shape, depth_h, depth_w, fov_h
        )
        radius = max(2.0, float(patch_ratio) * max(depth_w, depth_h))
        bbox = [
            int(max(0.0, min(depth_w - 1.0, cx - radius))),
            int(max(0.0, min(depth_h - 1.0, cy - radius))),
            int(max(0.0, min(depth_w - 1.0, cx + radius))),
            int(max(0.0, min(depth_h - 1.0, cy + radius))),
        ]
        projected = DepthProjection.project_detection_to_world(
            bbox=bbox,
            depth_img=depth_img,
            drone_pos=drone_pos,
            drone_yaw=drone_yaw,
            fov_h=fov_h,
            fov_v=_derive_fov_v(fov_h, depth_w, depth_h),
        )
        out = dict(projected or {})
        out.setdefault("valid", False)
        out["pixel"] = {"x": int(cx), "y": int(cy), "space": "depth"}
        out["frame_basis"] = basis
        out["source"] = "depth_point_projection"
        # 只发布**对外承诺的名字**（卡片/技能里的 distance_m / depth_m）；内部命名
        # （depth_meters / distance_to_drone）不再外泄——同一个数字出现四遍是操作员
        # 反馈过的"这返回怎么这么怪"。
        # 但**取值必须在 pop 之前**：上一版先 pop 再读，`distance_m` 恒为 None
        # （运行记录里抓到 distance_m=null），模型据此以为这条工具没有距离。
        internal_distance = out.pop("distance_to_drone", None)
        internal_depth = out.pop("depth_meters", None)
        distance_value = out.get("distance_m", internal_distance)
        depth_value = out.get("depth_m", internal_depth)
        if distance_value is not None:
            out["distance_m"] = round(float(distance_value), 2)
        if depth_value is not None:
            out["depth_m"] = round(float(depth_value), 2)
        if out.get("valid"):
            out["message"] = "该点深度有效：distance_m 是距离，world_pos 是 NED 世界坐标（米）。"
            # 视线明显朝下 → 这条深度射线打在目标**前面**的地面/路面上（实测：
            # 探到 30 m 外的路面，模型据此 drone_fly_to 过去，结果飞过目标、目标
            # 出画）。深度值本身没错，错的是"这个点不是目标"——必须说出来，否则
            # "world_pos 为 NED 世界坐标"会被读成"目标就在这里"。
            try:
                vertical = float(out.get("angle_v_deg"))
            except (TypeError, ValueError):
                vertical = 0.0
            if vertical <= -8.0:
                out["probe_warning"] = (
                    f"视线明显朝下（angle_v_deg={vertical:.1f}°）：这个点很可能落在目标前面的"
                    "地面/路面上，而不是目标本身。不要据此把飞机飞到该坐标——用有界相对步进"
                    "（drone_move_relative）逐步靠近，每步复核目标仍在画面里。"
                )
                out["message"] = out["message"] + " ⚠ " + out["probe_warning"]
        else:
            out.setdefault("message", "该点没有有效深度（天空/过远/落在物体边缘外）。")
        # 视觉框 × 深度图的交叉验证（把两种模态的信息在一次调用里合起来用）。
        # 只有调用方给了粗尺寸才做——没有框就没有"内外对比"可言。
        if hint_w > 0.0 and hint_h > 0.0:
            check = _surface_consistency(
                depth_img, frame_shape, x_norm, y_norm, hint_w, hint_h, fov_h
            )
            if check:
                out.update(check)
                # 按深度换算的目标尺寸：给模型一个可 sanity-check 的量级
                # （"车宽 1.8 m" 合理，"车宽 0.4 m" 说明点错了）。
                depth_for_size = out.get("distance_m") or out.get("depth_m")
                try:
                    depth_value = float(depth_for_size)
                except (TypeError, ValueError):
                    depth_value = None
                if depth_value:
                    out["metric_size"] = {
                        "width_m": round(depth_value * float(hint_w) * 2.0, 2),
                        "height_m": round(depth_value * float(hint_h) * 2.0, 2),
                        "note": "按“深度 × 归一化尺寸 × 2”（90° 水平 FOV）换算；视觉尺寸是粗值。",
                    }
                if check.get("surface_check") == "same_surface":
                    out["message"] = str(out.get("message") or "") + " ⚠ " + str(check.get("surface_hint"))
        return out
    except Exception as exc:  # noqa: BLE001 — 投影失败要给模型一句可执行的话
        logger.warning("perception_point_projection_failed", error=str(exc))
        return {"valid": False, "reason": "projection_error", "message": f"点位投影失败: {exc}"}


def _depth_at_normalised(
    depth_img: Any,
    x_norm: float,
    y_norm: float,
    frame_shape: tuple[int, int] | None,
    fov_h: float,
    patch_ratio: float = 0.012,
) -> float | None:
    """归一化点上的深度中值（米）；不可信/越界返回 None。"""
    try:
        import numpy as np

        depth_h, depth_w = int(depth_img.shape[0]), int(depth_img.shape[1])
        cx, cy, _basis = _map_normalised_point(x_norm, y_norm, frame_shape, depth_h, depth_w, fov_h)
        radius = max(1.0, float(patch_ratio) * max(depth_w, depth_h))
        x0 = int(max(0.0, min(depth_w - 1.0, cx - radius)))
        x1 = int(max(0.0, min(depth_w - 1.0, cx + radius)))
        y0 = int(max(0.0, min(depth_h - 1.0, cy - radius)))
        y1 = int(max(0.0, min(depth_h - 1.0, cy + radius)))
        patch = np.asarray(depth_img[y0 : y1 + 1, x0 : x1 + 1], dtype=np.float32)
        values = patch[np.isfinite(patch)]
        # 与 airsim_get_depth_map 同一套可信区间：天空/无命中会给上万米。
        values = values[(values > 0.2) & (values < 200.0)]
        if values.size == 0:
            return None
        return float(np.median(values))
    except Exception:  # noqa: BLE001 — 一致性检查失败不能影响主结果
        return None


def _surface_consistency(
    depth_img: Any,
    frame_shape: tuple[int, int] | None,
    x_norm: float,
    y_norm: float,
    hint_w: float,
    hint_h: float,
    fov_h: float,
) -> dict[str, Any]:
    """把视觉模型的**框**和深度图结合起来，判断"这个框里是不是一个独立物体"。

    起因（2026-10-13 实跑）：视觉模型给的归一化点在画面里是"浅蓝色汽车"，但深度
    射线打中的是 30 m 外的**路面**——深度值本身没错，错的是那个点不是车。以前这种
    错误没有任何机制能发现：单点深度无法区分"目标表面"和"目标所在的背景表面"。

    做法（不改焦距、不需要标定）：在**框内**取 4 点、在**框外**紧邻处取 4 点，
    比较两者的深度中值：
    - 框内 ≈ 框外（±20% 以内）→ 这一片很可能是同一个连续表面（地面/墙面/背景），
      框画的不是独立目标；
    - 框内明显更近（< 0.85×）→ 像是一个独立的前景目标（立体物在背景之前）；
    - 框内明显更远（> 1.2×）→ 可能是凹陷/洞里/框画到了远处。
    ``hint_w``/``hint_h`` 是视觉模型给的**粗**归一化宽高（±30%），所以阈值取得宽。
    """
    w = max(0.02, min(0.9, float(hint_w or 0.0)))
    h = max(0.02, min(0.9, float(hint_h or 0.0)))
    if hint_w <= 0.0 or hint_h <= 0.0:
        return {}

    def _clamp(value: float) -> float:
        return max(0.02, min(0.98, value))

    inside_points = [
        (_clamp(x_norm - w / 4.0), _clamp(y_norm - h / 4.0)),
        (_clamp(x_norm + w / 4.0), _clamp(y_norm - h / 4.0)),
        (_clamp(x_norm - w / 4.0), _clamp(y_norm + h / 4.0)),
        (_clamp(x_norm + w / 4.0), _clamp(y_norm + h / 4.0)),
    ]
    outside_points = [
        (_clamp(x_norm - (w / 2.0 + 0.03)), _clamp(y_norm - (h / 2.0 + 0.03))),
        (_clamp(x_norm + (w / 2.0 + 0.03)), _clamp(y_norm - (h / 2.0 + 0.03))),
        (_clamp(x_norm - (w / 2.0 + 0.03)), _clamp(y_norm + (h / 2.0 + 0.03))),
        (_clamp(x_norm + (w / 2.0 + 0.03)), _clamp(y_norm + (h / 2.0 + 0.03))),
    ]

    def _median(points: list[tuple[float, float]]) -> float | None:
        values = [
            value
            for value in (
                _depth_at_normalised(depth_img, px, py, frame_shape, fov_h) for px, py in points
            )
            if value is not None
        ]
        if not values:
            return None
        ordered = sorted(values)
        return float(ordered[len(ordered) // 2])

    inside = _median(inside_points)
    outside = _median(outside_points)
    check: dict[str, Any] = {}
    if inside is None and outside is None:
        return {}
    if inside is not None:
        check["inside_median_m"] = round(inside, 2)
    if outside is not None:
        check["outside_median_m"] = round(outside, 2)
    if inside is None or outside is None or outside <= 0.0:
        check["surface_check"] = "unknown"
        check["surface_hint"] = "框内或框外的深度取不到，无法交叉验证——先换更靠近目标中心的位置再试。"
        return check
    ratio = inside / outside
    if ratio < 0.85:
        check["surface_check"] = "object_in_front"
        check["surface_hint"] = (
            f"框内比框外近（{inside:.1f} m vs {outside:.1f} m）：像是一个独立的前景目标，"
            "这个点大概率真的在目标上。"
        )
    elif ratio > 1.2:
        check["surface_check"] = "recessed_or_far"
        check["surface_hint"] = (
            f"框内比框外远（{inside:.1f} m vs {outside:.1f} m）：像是洞里/门后/远处背景，"
            "这个框可能没有套住目标本体。"
        )
    else:
        check["surface_check"] = "same_surface"
        check["surface_hint"] = (
            f"框内外深度几乎一样（{inside:.1f} m vs {outside:.1f} m）：这一片很可能是同一个"
            "连续表面（地面/墙面/背景），框画的不是独立目标——不要据此把飞机飞过去。"
        )
    return check


def _map_normalised_point(
    x_norm: float,
    y_norm: float,
    frame_shape: tuple[int, int] | None,
    depth_h: int,
    depth_w: int,
    fov_h: float,
) -> tuple[float, float, str]:
    """归一化点（彩色帧画幅）→ 深度图像素坐标。

    横向：两个画幅共用水平 FOV，线性缩放即可。
    纵向：画幅纵横比不同则纵向视场不同，必须按角度映射（同
    ``_scale_bbox_to_depth`` 的理由），否则离开画面中心后行号就是错的。
    """
    try:
        xn = float(x_norm)
        yn = float(y_norm)
    except (TypeError, ValueError):
        xn, yn = 0.5, 0.5
    xn = max(0.0, min(1.0, xn))
    yn = max(0.0, min(1.0, yn))
    cx = xn * max(1.0, depth_w - 1.0)
    if not frame_shape or len(tuple(frame_shape)) < 2:
        # 基准画幅未知：按深度图自身近似。横向共性保留，纵向可能偏差。
        return cx, yn * max(1.0, depth_h - 1.0), "depth_image"
    frame_h, frame_w = int(tuple(frame_shape)[0]), int(tuple(frame_shape)[1])
    if frame_w <= 0 or frame_h <= 0:
        return cx, yn * max(1.0, depth_h - 1.0), "depth_image"
    tan_frame = math.tan(math.radians(_derive_fov_v(fov_h, frame_w, frame_h)) / 2.0)
    tan_depth = math.tan(math.radians(_derive_fov_v(fov_h, depth_w, depth_h)) / 2.0)
    normalised = yn - 0.5
    if tan_frame <= 0 or tan_depth <= 0:
        return cx, (0.5 + normalised) * max(1.0, depth_h - 1.0), "colour_frame"
    angle = math.atan(normalised * 2.0 * tan_frame)
    cy = depth_h / 2.0 * (1.0 + math.tan(angle) / tan_depth)
    return cx, cy, "colour_frame"


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
