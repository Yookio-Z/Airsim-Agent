"""
感知查询工具 - 相机拍照、传感器查询
优化: simGetImage + 超时封装 + 静止检查 + 冷却间隔
"""

from __future__ import annotations

import base64
import io
import os
import threading
import time
from typing import Callable

import airsim

from src.modules.flight_controller import FlightController
from src.modules.airsim_controller import AirSimController


def _encode_image_base64(raw_bytes: bytes) -> str:
    """将 AirSim 返回的 PNG 数据编码为 base64"""
    return base64.b64encode(raw_bytes).decode("ascii")


def _get_default_capture_dir(image_type: str) -> str:
    """获取默认保存图片目录"""
    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "captures")
    type_to_dir = {
        "scene": "scene",
        "depth": "depth",
        "segmentation": "segmentation",
        "infrared": "infrared",
        "depth_planar": "depth",
        "depth_perspective": "depth",
        "surface_normals": "depth",
    }
    subdir = type_to_dir.get(image_type.lower(), "scene")
    return os.path.join(base_dir, subdir)


def _save_image(raw_bytes: bytes, final_path: str) -> str:
    """保存 AirSim 返回的 PNG 数据"""
    final_path_fixed = final_path.replace("\\", "/")

    if os.name != "nt" and ":" in final_path_fixed:
        drive = final_path_fixed.split(":")[0].lower()
        rest = final_path_fixed.split(":", 1)[1]
        dir_path = os.path.dirname(f"/mnt/{drive}{rest}")
    else:
        dir_path = os.path.dirname(final_path_fixed)

    os.makedirs(dir_path, exist_ok=True)
    save_path = os.path.join(dir_path, os.path.basename(final_path_fixed))
    with open(save_path, "wb") as f:
        f.write(raw_bytes)
    return save_path


def _call_with_timeout(func: Callable, timeout: float = 10.0):
    """带超时的函数调用，防止 AirSim RPC 卡住"""
    result = {"value": None, "error": None}

    def wrapper():
        try:
            result["value"] = func()
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=wrapper)
    t.daemon = True
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        return None, f"调用超时 ({timeout}s)，AirSim 未响应"
    if result["error"]:
        return None, result["error"]
    return result["value"], None


def _is_vehicle_stationary(ctrl: AirSimController, vehicle_name: str, threshold: float = 0.1) -> bool:
    """检查无人机是否基本静止（线程隔离版）。"""
    try:
        state = ctrl.rpc("getMultirotorState", vehicle_name=vehicle_name)
        vel = state.kinematics_estimated.linear_velocity
        speed = (vel.x_val ** 2 + vel.y_val ** 2 + vel.z_val ** 2) ** 0.5
        return speed < threshold
    except Exception:
        return False


def register_perception_tools(mcp, controller: FlightController, _fmt_result):
    if not isinstance(controller, AirSimController):
        return

    def _get_vehicles():
        return controller._vehicles

    @mcp.tool()
    def airsim_take_photo(
        camera_name: str = "0",
        vehicle_name: str = "",
        image_type: str = "scene",
        save_path: str = "",
        auto_save: bool = True,
        max_retries: int = 3,
        save_to_cwd: bool = False,
        timeout_sec: float = 30.0,
        verify_target_class: str = "",
        verify_min_confidence: float = 0.2,
    ) -> str:
        """拍照。返回 base64 PNG 图像，自动保存到 captures 目录。

        优化措施:
        - 使用 simGetImage 轻量 API
        - 调用超时保护（默认 30 秒，防止 AirSim 卡死）
        - 拍照前先做健康检查（ping）
        - 拍照前检查无人机是否静止
        - 每次拍照后冷却 1 秒
        - 可选 YOLO 验证: 设置 verify_target_class 后自动检测确认画面是否包含目标

        Args:
            camera_name: 相机ID，默认"0"（前方视角，当前仅配置前向相机）
            vehicle_name: 无人机名称。留空则第一架
            image_type: 图像类型：scene/depth/segmentation/infrared，默认scene
            save_path: 保存路径。留空则用默认目录
            auto_save: 是否自动保存，默认True
            max_retries: 最大重试次数，默认3
            save_to_cwd: 是否保存到当前工作目录，默认False
            timeout_sec: RPC 调用超时秒数，默认30
            verify_target_class: 验证目标类别(英文)。如"car"，拍照后YOLO检测确认画面中有该目标才返回
            verify_min_confidence: YOLO验证最低置信度，默认0.2
        """
        if not controller.is_connected:
            return _fmt_result({"status": "error", "message": "AirSim 未连接"})

        names = [vehicle_name] if vehicle_name else list(_get_vehicles())
        if not names:
            return _fmt_result({"status": "error", "message": f"未找到无人机: {vehicle_name}"})

        target_name = names[0]

        type_map = {
            "scene": airsim.ImageType.Scene,
            "depth": airsim.ImageType.DepthVis,
            "segmentation": airsim.ImageType.Segmentation,
            "infrared": airsim.ImageType.Infrared,
            "depth_planar": airsim.ImageType.DepthPlanar,
            "depth_perspective": airsim.ImageType.DepthPerspective,
            "surface_normals": airsim.ImageType.SurfaceNormals,
        }
        img_type_val = type_map.get(image_type.lower(), airsim.ImageType.Scene)

        # 检查是否静止
        if not _is_vehicle_stationary(controller, target_name):
            stationary_warn = "无人机正在运动中，图像可能有动态模糊"
        else:
            stationary_warn = ""

        response = None
        last_error = ""

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(2.0 * (attempt - 1))

            try:
                resp = controller.capture_image_with_retry(
                    camera_name, img_type_val,
                    vehicle_name=target_name,
                    timeout=timeout_sec,
                    max_retries=2,
                )
            except (TimeoutError, RuntimeError) as e:
                last_error = f"第{attempt}次: {e}"
                continue

            if resp is None or resp == "" or resp == b"":
                last_error = f"第{attempt}次: 获取图像为空（相机{camera_name}）"
                continue

            raw_bytes = bytes(resp) if not isinstance(resp, bytes) else resp

            img_size_kb = round(len(raw_bytes) / 1024, 1)
            if img_size_kb < 0.5:
                last_error = f"第{attempt}次: 图像数据异常（仅{img_size_kb}KB）"
                continue

            response = raw_bytes
            break

        time.sleep(1.0)

        if response is None:
            diag = _diagnose_camera_issue(controller, target_name, camera_name)
            return _fmt_result({
                "status": "error",
                "message": f"拍照失败，已重试{max_retries}次。{last_error}",
                "diagnosis": diag,
            })

        try:
            img_b64 = _encode_image_base64(response)
        except Exception as e:
            return _fmt_result({
                "status": "error",
                "message": f"图像编码失败: {e}",
            })

        result = {
            "status": "ok",
            "vehicle": target_name,
            "camera": camera_name,
            "image_type": image_type,
            "size_kb": img_size_kb,
            "message": f"已拍摄{image_type}图像({img_size_kb}KB)" + (f"，注意:{stationary_warn}" if stationary_warn else ""),
            "image_base64": img_b64,
        }

        # ── YOLO 验证: 确认画面中是否包含目标 ──
        if verify_target_class:
            try:
                import cv2
                import numpy as np
                # 延迟导入避免循环依赖
                from src.modules.yolo_detection import (
                    build_search_classes,
                    get_yolo_model,
                    run_yolo_detection,
                )

                nparr = np.frombuffer(response, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    search_classes = build_search_classes(verify_target_class)
                    model = get_yolo_model(search_classes)
                    detections = run_yolo_detection(model, img, verify_target_class, verify_min_confidence)

                    if not detections:
                        # 画面中没有目标，返回错误
                        return _fmt_result({
                            "status": "error",
                            "message": (
                                f"拍照审核失败: 画面中未检测到'{verify_target_class}'类目标。"
                                f"当前画面可能没有目标，或目标不在视野内。"
                                f"建议先用 detect_objects 确认目标可见后再拍照，"
                                f"或调整无人机位置使目标进入视野。"
                            ),
                            "camera": camera_name,
                            "verify_target_class": verify_target_class,
                            "verify_confidence_threshold": verify_min_confidence,
                        })

                    # 验证通过，记录检测信息
                    result["verified"] = True
                    result["verify_detections"] = detections
                    result["message"] += f" (YOLO验证通过: 检测到{len(detections)}个{verify_target_class})"
                else:
                    result["verified"] = False
                    result["message"] += " (YOLO验证跳过: 图像解码失败)"
            except Exception as e:
                result["verified"] = False
                result["verify_error"] = str(e)
                result["message"] += f" (YOLO验证异常: {e})"

        if save_path:
            final_path = save_path
        elif save_to_cwd:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            final_path = os.path.join(os.getcwd(), f"{target_name}_{camera_name}_{timestamp}.png")
        elif auto_save:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_dir = _get_default_capture_dir(image_type)
            final_path = os.path.join(default_dir, f"{target_name}_{camera_name}_{timestamp}.png")
        else:
            final_path = ""

        if final_path:
            try:
                saved_to = _save_image(response, final_path)
                result["saved_to"] = saved_to
                result["message"] += f"，已保存到:{saved_to}"
            except Exception as e:
                result["save_error"] = str(e)

        return _fmt_result(result)

    def _diagnose_camera_issue(ctrl: AirSimController, vehicle_name: str, camera_name: str) -> str:
        """诊断相机问题"""
        try:
            state = ctrl.rpc("getMultirotorState", vehicle_name=vehicle_name)
            pos = state.kinematics_estimated.position
            landed = state.landed_state
            pos_info = f"位置: ({pos.x_val:.1f}, {pos.y_val:.1f}, {pos.z_val:.1f}), 状态: {'ground' if landed == 0 else 'flying'}"
        except Exception as e:
            pos_info = f"无法获取状态: {e}"

        try:
            ping = ctrl.rpc("ping")
            conn_info = f"连接: {'正常' if ping else '异常'}"
        except Exception as e:
            conn_info = f"连接测试失败: {e}"

        return f"{conn_info} | {pos_info}"

    @mcp.tool()
    def airsim_get_sensors(vehicle_name: str = "", sensor_types: str = "all", imu_name: str = "", gps_name: str = "", distance_sensor_name: str = "") -> str:
        """获取传感器数据。默认返回所有传感器（IMU+GPS+距离），也可指定类型。

        Args:
            vehicle_name: 无人机名称。留空则查询所有
            sensor_types: 传感器类型，逗号分隔。all/imu/gps/distance，默认all
            imu_name: IMU名称。留空则默认
            gps_name: GPS名称。留空则默认
            distance_sensor_name: 距离传感器名称。留空则默认
        """
        if not controller.is_connected:
            return _fmt_result({"status": "error", "message": "AirSim 未连接"})

        names = [vehicle_name] if vehicle_name else list(_get_vehicles())
        if not names:
            return _fmt_result({"status": "error", "message": f"未找到无人机: {vehicle_name}"})

        types = [t.strip().lower() for t in sensor_types.split(",")]
        if "all" in types:
            types = ["imu", "gps", "distance"]

        fix_type_names = {0: "NO_FIX", 1: "TIME_ONLY", 2: "2D_FIX", 3: "3D_FIX"}

        results = {}
        for name in names:
            vehicle_data = {}

            if "imu" in types:
                try:
                    data = controller.rpc("getImuData", imu_name, name)
                    ori = data.orientation
                    ang_vel = data.angular_velocity
                    lin_acc = data.linear_acceleration
                    vehicle_data["imu"] = {
                        "orientation": {"w": round(ori.w_val, 4), "x": round(ori.x_val, 4), "y": round(ori.y_val, 4), "z": round(ori.z_val, 4)},
                        "angular_velocity_rad_s": {"x": round(ang_vel.x_val, 4), "y": round(ang_vel.y_val, 4), "z": round(ang_vel.z_val, 4)},
                        "linear_acceleration_m_s2": {"x": round(lin_acc.x_val, 4), "y": round(lin_acc.y_val, 4), "z": round(lin_acc.z_val, 4)},
                        "timestamp": str(data.time_stamp),
                    }
                except Exception as e:
                    vehicle_data["imu"] = {"error": str(e)}

            if "gps" in types:
                try:
                    data = controller.rpc("getGpsData", gps_name, name)
                    gnss = data.gnss
                    geo = gnss.geo_point
                    vel = gnss.velocity
                    fix = gnss.fix_type
                    fix_val = int(fix) if not isinstance(fix, int) else fix
                    fix_name = fix_type_names.get(fix_val, f"UNKNOWN({fix_val})")

                    vehicle_data["gps"] = {
                        "geo_point": {"latitude": round(geo.latitude, 7), "longitude": round(geo.longitude, 7), "altitude": round(geo.altitude, 2)},
                        "velocity_m_s": {"x": round(vel.x_val, 4), "y": round(vel.y_val, 4), "z": round(vel.z_val, 4)},
                        "fix_type": fix_name,
                        "eph": round(gnss.eph, 2),
                        "epv": round(gnss.epv, 2),
                        "is_valid": data.is_valid,
                        "timestamp": str(data.time_stamp),
                    }
                except Exception as e:
                    vehicle_data["gps"] = {"error": str(e)}

            if "distance" in types:
                try:
                    data = controller.rpc("getDistanceSensorData", distance_sensor_name, name)
                    vehicle_data["distance"] = {
                        "distance_m": round(data.distance, 3),
                        "min_distance_m": round(data.min_distance, 3),
                        "max_distance_m": round(data.max_distance, 3),
                        "timestamp": str(data.time_stamp),
                    }
                except Exception as e:
                    vehicle_data["distance"] = {"error": str(e)}

            results[name] = vehicle_data

        return _fmt_result({"status": "ok", "vehicles": results})
