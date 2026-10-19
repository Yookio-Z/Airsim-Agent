"""传感器工具组：AirSim 仿真传感器读取（``airsim_get_sensors``）。

- 注册入口：``register_sensor_tools(collector, controller, fmt)``（仅 airsim 后端）
- 调用方：``src/agent/tool_executor.py`` 的 ``ToolRuntime.ensure_ready()``
- 另含 ``_get_default_capture_dir``（captures/ 子目录助手），被
  ``perception_axis.py``（证据帧）与 ``vehicle_api.py``（界面拍照）复用。
- 注意：深度测距不在这里——它是 ``perception_axis.py`` 的 ``locate_image_point``
  （一个几何工具，走相机链路，AirSim/PX4 都能用）。
"""

from __future__ import annotations

import os
import time

import airsim

from src.modules.backends.base import FlightController
from src.modules.backends.airsim import AirSimController


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


def register_sensor_tools(mcp, controller: FlightController, _fmt_result):
    if not isinstance(controller, AirSimController):
        return

    def _get_vehicles():
        return controller._vehicles

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
