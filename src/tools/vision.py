"""
视觉辅助工具 - 深度感知
不包含AI识别，所有图像分析由LLM的多模态模型完成
仅 AirSim 模式可用
"""

from __future__ import annotations

import base64

import airsim

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
            query_points: 查询点坐标，格式"x1,y1;x2,y2"（像素坐标），留空不查询
        """
        if not controller.is_connected:
            return _fmt_result({"status": "error", "message": "AirSim 未连接"})

        names = [vehicle_name] if vehicle_name else list(_get_vehicles())
        if not names:
            return _fmt_result({"status": "error", "message": f"未找到无人机: {vehicle_name}"})

        target_name = names[0]

        try:
            depth_response = _get_client().simGetImage(camera_name, airsim.ImageType.DepthPlanar, vehicle_name=target_name)
            if depth_response is None:
                try:
                    depth_response = _get_client().simGetImage(camera_name, airsim.ImageType.DepthVis, vehicle_name=target_name)
                    if depth_response is None:
                        return _fmt_result({"status": "error", "message": "深度相机不可用"})
                    depth_vis = True
                except Exception:
                    return _fmt_result({"status": "error", "message": "深度相机不可用"})
            else:
                depth_vis = False

            depth_bytes = _decode_image_response(depth_response)

            import cv2
            import numpy as np
            nparr = np.frombuffer(depth_bytes, np.uint8)
            depth_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
            if depth_img is None:
                depth_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
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
                        qx, qy = map(int, pt_str.strip().split(","))
                        if 0 <= qx < depth_w and 0 <= qy < depth_h:
                            pixel_value = depth_img[qy, qx]
                            if depth_vis:
                                if len(pixel_value.shape) > 0:
                                    pixel_value = pixel_value[0]
                                distance = float(pixel_value) / 255.0 * 100.0
                            else:
                                distance = float(pixel_value)
                            point_results.append({
                                "pixel": {"x": qx, "y": qy},
                                "distance_m": round(distance, 3),
                            })
                        else:
                            point_results.append({
                                "pixel": {"x": qx, "y": qy},
                                "error": f"超出图像范围({depth_w}x{depth_h})",
                            })
                    except ValueError:
                        point_results.append({
                            "pixel": pt_str,
                            "error": "格式错误，应为'x,y'",
                        })
                result_dict["query_results"] = point_results
                result_dict["message"] = f"深度图获取成功，查询了{len(point_results)}个点"

            if return_vis:
                if depth_vis:
                    depth_b64 = _encode_image_png(depth_bytes)
                else:
                    depth_colored = cv2.applyColorMap(depth_img, cv2.COLORMAP_JET)
                    success, buffer = cv2.imencode('.png', depth_colored)
                    if success:
                        depth_b64 = base64.b64encode(buffer.tobytes()).decode('ascii')
                    else:
                        depth_b64 = ""
                result_dict["depth_image_base64"] = depth_b64

            return _fmt_result(result_dict)

        except Exception as e:
            return _fmt_result({"status": "error", "message": f"深度感知失败: {e}"})



# ---------------------------------------------------------------------
# Legacy aliases -- forward to the perception axis's inspect_current_frame.
# These existed as cards for the LLM to pick, but had no real implementations
# in this module. Forwarding keeps old plans working and lets the LLM land on
# the same actual analysis path regardless of which name it picked.
# ---------------------------------------------------------------------

def register_vision_aliases(mcp, controller: Any, fmt_result: Callable[[dict], str], inspect_runner: Callable[[str], str]) -> None:
    """Wire legacy airsim_vlm_* tools as thin shims over inspect_current_frame."""

    @mcp.tool()
    def airsim_vlm_analyze_image(
        question: str = "请描述画面里看到了什么,目标是什么颜色",
        source: str = "last_image",
        image_base64: str = "",
    ) -> str:
        """[DEPRECATED alias] Forward to inspect_current_frame (multimodal)."""
        return inspect_runner(question)

    @mcp.tool()
    def airsim_vlm_confirm_target(
        target_description: str,
        source: str = "last_image",
        image_base64: str = "",
    ) -> str:
        """[DEPRECATED alias] Forward to inspect_current_frame (multimodal)."""
        return inspect_runner(f"请在画面中确认目标: {target_description}")
