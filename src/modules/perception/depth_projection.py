"""
深度投影工具（DepthProjection）

把深度图上的像素/检测框投影到世界系（NED）——视觉接近链的几何核心，
由 perception_airsim_adapter 调用（project_point_to_world 等）。

坐标系: NED (x=North, y=East, z=Down)
深度图格式: AirSim DepthPlanar (float32, 单位米)
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np

from src.logging_config import get_logger

logger = get_logger(__name__)


class DepthProjection:
    """深度图到3D点云投影工具

    将深度图像素通过FOV和无人机位姿转换为NED坐标系下的3D点云。
    使用numpy向量化运算提升性能。
    """

    @staticmethod
    def project_depth_to_3d(
        depth_img: np.ndarray,
        drone_pos: tuple[float, float, float],
        drone_yaw: float,
        fov_h: float = 90.0,
        fov_v: float = 60.0,
    ) -> np.ndarray:
        """将深度图投影为3D点云 (NED坐标)

        Args:
            depth_img: 深度图 (H, W) float32，单位米
            drone_pos: 无人机位置 (x_north, y_east, z_down)
            drone_yaw: 无人机航向角 (度)，0=北，顺时针为正
            fov_h: 水平视场角 (度)
            fov_v: 垂直视场角 (度)

        Returns:
            Nx3 数组，每行为 (x_north, y_east, z_down) 的3D点
        """
        h, w = depth_img.shape[:2]
        if h == 0 or w == 0:
            return np.empty((0, 3), dtype=np.float32)

        # 有效深度掩码: 过滤无效像素
        valid_mask = (depth_img > 0.1) & (depth_img < 100.0)
        if not np.any(valid_mask):
            return np.empty((0, 3), dtype=np.float32)

        # 生成像素坐标网格 (向量化)
        # u: 列索引 (0~W-1)，v: 行索引 (0~H-1)
        u_coords, v_coords = np.meshgrid(np.arange(w), np.arange(h))

        # 计算每个像素对应的角度偏移
        # 水平: 中心偏左为负角度，中心偏右为正角度
        # 垂直: 中心偏上为负角度(向上看)，中心偏下为正角度(向下看)
        angle_h = (u_coords - w / 2.0) / w * fov_h  # 水平角度 (度)
        angle_v = (v_coords - h / 2.0) / h * fov_v  # 垂直角度 (度)

        # 转换为弧度
        angle_h_rad = np.deg2rad(angle_h)
        angle_v_rad = np.deg2rad(angle_v)

        # 在无人机机体坐标系下计算3D方向 (前=北方向)
        # x_body = depth * cos(v_angle) * cos(h_angle)  (前方)
        # y_body = depth * cos(v_angle) * sin(h_angle)  (右方)
        # z_body = depth * sin(v_angle)                   (下方)
        cos_v = np.cos(angle_v_rad)
        x_body = depth_img * cos_v * np.cos(angle_h_rad)
        y_body = depth_img * cos_v * np.sin(angle_h_rad)
        z_body = depth_img * np.sin(angle_v_rad)

        # 旋转到NED世界坐标系 (绕z轴旋转yaw角)
        yaw_rad = np.deg2rad(drone_yaw)
        cos_yaw = math.cos(yaw_rad)
        sin_yaw = math.sin(yaw_rad)

        # NED: x=North, y=East; 旋转矩阵: [cos -sin; sin cos]
        x_ned = x_body * cos_yaw - y_body * sin_yaw
        y_ned = x_body * sin_yaw + y_body * cos_yaw
        z_ned = z_body  # z轴不随yaw旋转

        # 平移到无人机位置
        x_ned += drone_pos[0]
        y_ned += drone_pos[1]
        z_ned += drone_pos[2]

        # 仅保留有效深度的点
        points = np.stack([x_ned, y_ned, z_ned], axis=-1)  # (H, W, 3)
        points = points[valid_mask]  # (N, 3)

        return points.astype(np.float32)

    @staticmethod
    def project_detection_to_world(
        bbox: list[int],
        depth_img: np.ndarray,
        drone_pos: tuple[float, float, float],
        drone_yaw: float,
        fov_h: float = 90.0,
        fov_v: float = 60.0,
        min_depth: float = 0.1,
        max_depth: float = 100.0,
    ) -> dict:
        """将 2D bbox 检测结果通过深度图投影到 3D 世界坐标 (NED)

        参考 PEACE DepthProjectionService 的投影逻辑:
        1. 在 bbox 中心区域采样深度值 (取中位数，鲁棒)
        2. 通过针孔相机模型计算水平角度
        3. 结合无人机位姿投影到 NED 世界坐标

        Args:
            bbox: 2D 边界框 [x1, y1, x2, y2] 像素坐标
            depth_img: 深度图 (H, W) float32，单位米
            drone_pos: 无人机位置 (x_north, y_east, z_down)
            drone_yaw: 无人机航向角 (度)，0=北，顺时针为正
            fov_h: 水平视场角 (度)
            fov_v: 垂直视场角 (度)
            min_depth: 最小有效深度 (米)
            max_depth: 最大有效深度 (米)

        Returns:
            {
                "valid": bool,           # 深度是否有效
                "depth_meters": float,   # 目标深度 (米)
                "world_pos": dict,       # NED世界坐标 {x, y, z}
                "distance_to_drone": float,  # 目标到无人机的3D距离
                "angle_h_deg": float,    # 目标相对无人机前方的水半角度 (度)
                "angle_v_deg": float,    # 目标相对无人机前方的垂直角度 (度)
            }
        """
        h, w = depth_img.shape[:2]
        x1, y1, x2, y2 = bbox

        # 限制 bbox 在图像范围内
        x1 = max(0, min(x1, w - 1))
        x2 = max(0, min(x2, w - 1))
        y1 = max(0, min(y1, h - 1))
        y2 = max(0, min(y2, h - 1))

        if x2 <= x1 or y2 <= y1:
            return {"valid": False, "depth_meters": 0.0, "world_pos": {"x": 0, "y": 0, "z": 0},
                    "distance_to_drone": 0.0, "angle_h_deg": 0.0, "angle_v_deg": 0.0}

        # bbox 中心像素
        cx_px = (x1 + x2) // 2
        cy_px = (y1 + y2) // 2

        # 在 bbox 内部采样深度。窗口随 bbox 大小缩放，而不是固定像素数：
        # 固定窗口（曾经是 cy-5..cy+20 共 25 行）在低分辨率深度图上会跨过很大
        # 的俯仰范围，把目标下方更近的地面一起算进来，实测让距离偏小一半以上
        # （车在 640x480 检测框里 15px 高，映射到 144 行的深度图只有 5 行，而
        # 窗口有 25 行）。向下略偏仍保留：目标顶边常常取到其后方背景的深度。
        box_w = max(1, x2 - x1)
        box_h = max(1, y2 - y1)
        half_w = max(1, box_w // 2)
        half_h = max(1, box_h // 2)
        center_y = cy_px + max(0, box_h // 6)
        patch_y1 = max(0, center_y - half_h)
        patch_y2 = min(h, center_y + half_h)
        patch_x1 = max(0, cx_px - half_w)
        patch_x2 = min(w, cx_px + half_w)

        patch = depth_img[patch_y1:patch_y2, patch_x1:patch_x2]
        valid_pixels = patch[(patch > min_depth) & (patch < max_depth)]

        if len(valid_pixels) == 0:
            return {"valid": False, "depth_meters": 0.0, "world_pos": {"x": 0, "y": 0, "z": 0},
                    "distance_to_drone": 0.0, "angle_h_deg": 0.0, "angle_v_deg": 0.0}

        # 取中位数深度 (鲁棒，避免异常值)
        obj_depth = float(np.median(valid_pixels))

        # 针孔相机模型: 像素 → 水平/垂直角度
        # cx_px - w/2 是相对图像中心的偏移，除以 w/2 归一化后乘以 fov_h/2
        angle_h_deg = (cx_px - w / 2.0) / (w / 2.0) * (fov_h / 2.0)
        angle_v_deg = (cy_px - h / 2.0) / (h / 2.0) * (fov_v / 2.0)

        angle_h_rad = math.radians(angle_h_deg)
        angle_v_rad = math.radians(angle_v_deg)

        # 机体坐标系下的3D偏移 (前=北方向)
        x_body = obj_depth * math.cos(angle_v_rad) * math.cos(angle_h_rad)
        y_body = obj_depth * math.cos(angle_v_rad) * math.sin(angle_h_rad)
        z_body = obj_depth * math.sin(angle_v_rad)

        # 旋转到 NED 世界坐标系 (绕z轴旋转yaw角)
        yaw_rad = math.radians(drone_yaw)
        cos_yaw = math.cos(yaw_rad)
        sin_yaw = math.sin(yaw_rad)

        world_x = x_body * cos_yaw - y_body * sin_yaw + drone_pos[0]
        world_y = x_body * sin_yaw + y_body * cos_yaw + drone_pos[1]
        world_z = z_body + drone_pos[2]

        # 3D 距离
        dist_3d = math.sqrt(
            (world_x - drone_pos[0]) ** 2 +
            (world_y - drone_pos[1]) ** 2 +
            (world_z - drone_pos[2]) ** 2
        )

        return {
            "valid": True,
            "depth_meters": round(obj_depth, 2),
            "world_pos": {
                "x": round(world_x, 2),
                "y": round(world_y, 2),
                "z": round(world_z, 2),
            },
            "distance_to_drone": round(dist_3d, 2),
            "angle_h_deg": round(angle_h_deg, 1),
            "angle_v_deg": round(angle_v_deg, 1),
        }
