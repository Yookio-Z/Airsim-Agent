"""
视觉辅助工具 - 深度感知
不包含AI识别，所有图像分析由LLM的多模态模型完成
仅 AirSim 模式可用
"""

from __future__ import annotations

import base64

from src.modules.flight_controller import FlightController
from src.modules.airsim_controller import AirSimController


def _encode_image_png(raw_bytes: bytes) -> str:
    return base64.b64encode(raw_bytes).decode("ascii")


def _decode_image_response(response) -> bytes:
    if isinstance(response, str):
        return response.encode('latin-1')
    elif isinstance(response, bytes):
        return response
    else:
        return bytes(response)


# 公制深度超过这个值基本就是"没命中"（本场景实测天空 ≈ 15840 m）。它同时是
# 判断"这个数能不能拿去规划一段移动"的上界。
_DEPTH_PLAUSIBLE_MAX_M = 200.0
_DEPTH_PLAUSIBLE_MIN_M = 0.2


def _plausible_depth(distance: float) -> bool:
    """这个深度值能不能当作一个真距离用。"""
    return _DEPTH_PLAUSIBLE_MIN_M <= distance <= _DEPTH_PLAUSIBLE_MAX_M


def _parse_depth_query_point(text: str, depth_w: int, depth_h: int) -> tuple[int, int]:
    """解析一个深度查询点，返回深度图上的整数像素坐标。

    两种写法：
    - 归一化小数 "0.36,0.52" → 按深度图尺寸换算；
    - 整数像素 "92,72" → 按深度图分辨率（调用方自己负责）。

    归一化写法存在的理由：深度图（256x144）和彩色图（800x600）分辨率不同，而调用
    方（模型）通常是从彩色图/VLM 那里得到位置。把彩色图像素直接传进来不会越界报错，
    却会读到另一个位置——一个"看起来正常"的错误距离。归一化坐标与分辨率无关。
    """
    parts = [item.strip() for item in str(text or "").strip().split(",")]
    if len(parts) != 2:
        raise ValueError("格式错误，应为'x,y'（归一化小数或深度图像素）")
    try:
        raw_x, raw_y = (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise ValueError(f"坐标不是数字: {text!r}") from exc
    # 规则：两个分量都落在 0~1 → 归一化；只要有一个 >1 → 按深度图像素。
    # 归一化(0,0) 与像素(0,0) 本来就指向同一点，所以只有 (1,1) 有歧义——那种情况
    # 按归一化理解（右下角）才有意义，像素 (1,1) 是个没人会查的角落。
    is_normalized = 0.0 <= raw_x <= 1.0 and 0.0 <= raw_y <= 1.0
    if is_normalized:
        qx = int(round(raw_x * max(1, depth_w - 1)))
        qy = int(round(raw_y * max(1, depth_h - 1)))
        return qx, qy
    return int(raw_x), int(raw_y)


def register_vision_tools(mcp, controller: FlightController, _fmt_result):
    if not isinstance(controller, AirSimController):
        return

    def _get_client():
        return controller.client

    def _get_vehicles():
        return controller._vehicles

    @mcp.tool()
    def airsim_get_depth_map(
        camera_name: str = "0",
        vehicle_name: str = "",
        return_vis: bool = False,
        query_points: str = "",
    ) -> str:
        """深度感知 - 获取场景深度图并计算距离。

        Args:
            camera_name: 相机ID，默认"0"
            vehicle_name: 无人机名称。留空则第一架
            return_vis: 是否返回深度可视化图像base64，默认False
            query_points: 查询点，格式"q1;q2"（分号分隔多个点）。两种写法：
                - 小数归一化 "0.36,0.52"：0~1 比例，按深度图尺寸换算；
                - 整数像素 "92,72"：按**深度图**分辨率（见返回 image_size）。
                **请用归一化写法**：深度图与彩色图分辨率不同（256x144 vs 800x600），
                把彩色图的像素直接传进来不会报错，却会读到另一个位置，得到一个
                看起来正常的错误距离。
        """
        if not controller.is_connected:
            return _fmt_result({"status": "error", "message": "AirSim 未连接"})

        names = [vehicle_name] if vehicle_name else list(_get_vehicles())
        if not names:
            return _fmt_result({"status": "error", "message": f"未找到无人机: {vehicle_name}"})

        target_name = names[0]

        try:
            import cv2
            import numpy as np

            # 必须走 simGetImages + pixels_as_float：simGetImage(DepthPlanar) 返回的
            # 是 **8 位 RGBA 可视化图**（实测 144x256x4、值 0~255），既不是公制深度，
            # 还会让后续的标量读取抛 "only 0-dimensional arrays..."。感知轴一直用
            # 的就是这条浮点路径，这里复用它，保证"怎么读深度"只有一个实现。
            from src.modules.perception_airsim_adapter import get_depth_image

            depth_img = get_depth_image(controller, camera_name, target_name, timeout_sec=3.0)
            if depth_img is None:
                return _fmt_result({"status": "error", "message": "深度相机不可用（拿不到公制深度图）"})
            depth_h, depth_w = depth_img.shape[:2]

            result_dict = {
                "status": "ok",
                "vehicle": target_name,
                "camera": camera_name,
                "image_size": {"width": depth_w, "height": depth_h},
                "message": "深度图获取成功",
            }

            if query_points:
                point_results = []
                points = query_points.split(";")
                for pt_str in points:
                    try:
                        qx, qy = _parse_depth_query_point(pt_str, depth_w, depth_h)
                    except ValueError as exc:
                        point_results.append({"pixel": pt_str, "error": str(exc)})
                        continue
                    if 0 <= qx < depth_w and 0 <= qy < depth_h:
                        # 现在是真正的 float32 米值，标量读取即可
                        distance = float(depth_img[qy, qx])
                        item = {
                            "pixel": {"x": qx, "y": qy},
                            "distance_m": round(distance, 3),
                        }
                        # 天空/无命中会给出上万米（实测本场景 max ≈ 15840）。这种值不
                        # 是距离，直接标出来，免得调用方拿它去"飞 15840 米"。
                        if not _plausible_depth(distance):
                            item["plausible"] = False
                            item["error"] = "该点没有有效深度（天空/无命中）"
                        point_results.append(item)
                    else:
                        point_results.append({
                            "pixel": {"x": qx, "y": qy},
                            "error": f"超出深度图范围({depth_w}x{depth_h})",
                        })
                result_dict["query_results"] = point_results
                distances = [
                    float(item["distance_m"])
                    for item in point_results
                    if item.get("plausible", True) and isinstance(item.get("distance_m"), (int, float))
                ]
                if distances:
                    # 多点查询（同一目标附近取几个点）时给出中位数：单像素深度会
                    # 落在物体边缘上，中位数比任何单点都稳。感知轴内部也是这么做的。
                    ordered = sorted(distances)
                    median = ordered[len(ordered) // 2]
                    result_dict["distance_median_m"] = round(median, 3)
                result_dict["message"] = f"深度图获取成功，查询了{len(point_results)}个点"

            if return_vis:
                # 浮点深度没有可显示的对比度，按有效范围归一化后着色（仅用于人看）
                finite = depth_img[np.isfinite(depth_img)]
                headroom = float(np.percentile(finite, 95)) if finite.size else 1.0
                scale = 255.0 / max(1.0, min(headroom, _DEPTH_PLAUSIBLE_MAX_M))
                depth_colored = cv2.applyColorMap(
                    cv2.convertScaleAbs(depth_img, alpha=scale), cv2.COLORMAP_JET
                )
                success, buffer = cv2.imencode('.png', depth_colored)
                result_dict["depth_image_base64"] = (
                    base64.b64encode(buffer.tobytes()).decode('ascii') if success else ""
                )

            return _fmt_result(result_dict)

        except Exception as e:
            return _fmt_result({"status": "error", "message": f"深度感知失败: {e}"})
