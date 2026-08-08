"""
3D 占据栅格地图模块

基于深度图构建3D体素占据地图，用于实时避障和路径规划。
核心组件:
  - VoxelGrid3D: 3D体素占据地图，支持贝叶斯更新和A*路径搜索
  - DepthProjection: 深度图到3D点云投影工具

坐标系: NED (x=North, y=East, z=Down)
深度图格式: AirSim DepthPlanar (float32, 单位米)
"""

from __future__ import annotations

import heapq
import math
import time
from typing import Optional

import numpy as np

from ..logging_config import get_logger

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

        # 在 bbox 中心区域采样深度 (偏下方采样，避免目标顶部无效深度)
        patch_y1 = max(0, cy_px - 5)
        patch_y2 = min(h, cy_px + 20)
        patch_x1 = max(0, cx_px - 5)
        patch_x2 = min(w, cx_px + 20)

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


class VoxelGrid3D:
    """3D体素占据地图

    使用对数几率(log-odds)表示进行贝叶斯更新，
    支持从深度图增量构建占据地图、射线投射、路径搜索等功能。

    对数几率表示:
      - log_odds = 0 对应概率 0.5 (未知)
      - log_odds > 0 对应概率 > 0.5 (占据概率增大)
      - log_odds < 0 对应概率 < 0.5 (空闲概率增大)
    """

    # 对数几率上下限，防止过度自信
    LOG_ODDS_MIN = -5.0
    LOG_ODDS_MAX = 5.0

    # 占据/空闲概率阈值
    OCC_THRESHOLD = 0.7
    FREE_THRESHOLD = 0.3

    # 对数几率更新增量
    LOG_ODDS_OCC = 0.7   # 检测到占据时的增量
    LOG_ODDS_FREE = 0.4  # 射线经过(空闲)时的减量

    def __init__(self, resolution: float = 0.5, map_size: float = 50.0) -> None:
        """初始化3D体素地图

        Args:
            resolution: 体素尺寸 (米)，默认0.5m
            map_size: 地图平面尺寸 (米)，默认50m x 50m x 20m
        """
        self.resolution = resolution
        self.map_size_xy = map_size
        self.map_size_z = 20.0  # z方向固定20m

        # 计算各轴体素数量
        self.x_bins = int(self.map_size_xy / resolution)
        self.y_bins = int(self.map_size_xy / resolution)
        self.z_bins = int(self.map_size_z / resolution)

        # 对数几率栅格 (0=未知)，形状 (x_bins, y_bins, z_bins)
        self.log_odds = np.zeros((self.x_bins, self.y_bins, self.z_bins), dtype=np.float32)

        # 地图原点在NED坐标 (0,0,0)，体素索引0对应坐标 -map_size/2
        self.origin_x = -self.map_size_xy / 2.0
        self.origin_y = -self.map_size_xy / 2.0
        self.origin_z = -self.map_size_z / 2.0

        # 射线投射像素采样间隔 (每N个像素采样一次)
        self.ray_sample_step: int = 4

        # 单帧最大更新时间 (毫秒)
        self.max_update_ms: float = 100.0

        logger.info(
            "voxel_grid_init",
            resolution=resolution,
            map_size=map_size,
            grid_shape=(self.x_bins, self.y_bins, self.z_bins),
            total_voxels=self.x_bins * self.y_bins * self.z_bins,
        )

    def _world_to_voxel(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        """NED世界坐标转体素索引

        Args:
            x, y, z: NED坐标 (米)

        Returns:
            (ix, iy, iz) 体素索引
        """
        ix = int((x - self.origin_x) / self.resolution)
        iy = int((y - self.origin_y) / self.resolution)
        iz = int((z - self.origin_z) / self.resolution)
        return ix, iy, iz

    def _voxel_to_world(self, ix: int, iy: int, iz: int) -> tuple[float, float, float]:
        """体素索引转NED世界坐标 (体素中心)

        Args:
            ix, iy, iz: 体素索引

        Returns:
            (x, y, z) NED坐标 (米)
        """
        x = self.origin_x + (ix + 0.5) * self.resolution
        y = self.origin_y + (iy + 0.5) * self.resolution
        z = self.origin_z + (iz + 0.5) * self.resolution
        return x, y, z

    def _in_bounds(self, ix: int, iy: int, iz: int) -> bool:
        """检查体素索引是否在地图范围内"""
        return 0 <= ix < self.x_bins and 0 <= iy < self.y_bins and 0 <= iz < self.z_bins

    def _log_odds_to_prob(self, log_odds: float) -> float:
        """对数几率转概率: p = exp(l) / (1 + exp(l))"""
        return 1.0 / (1.0 + math.exp(-log_odds))

    def _prob_to_log_odds(self, prob: float) -> float:
        """概率转对数几率: l = log(p / (1-p))"""
        p = max(min(prob, 0.999), 0.001)  # 防止log(0)
        return math.log(p / (1.0 - p))

    def update_from_depth(
        self,
        depth_img: np.ndarray,
        drone_x: float,
        drone_y: float,
        drone_z: float,
        drone_yaw: float,
        fov_h: float = 90.0,
        fov_v: float = 60.0,
    ) -> int:
        """从深度图更新占据栅格

        对每个采样像素进行射线投射:
          1. 从无人机位置沿射线方向投射到深度距离
          2. 射线经过的体素标记为空闲 (降低占据概率)
          3. 射线终点的体素标记为占据 (提高占据概率)

        Args:
            depth_img: 深度图 (H, W) float32，单位米
            drone_x, drone_y, drone_z: 无人机NED位置
            drone_yaw: 无人机航向角 (度)
            fov_h: 水平视场角 (度)
            fov_v: 垂直视场角 (度)

        Returns:
            更新的体素数量
        """
        start_time = time.time()

        h, w = depth_img.shape[:2]
        if h == 0 or w == 0:
            return 0

        # 无人机位置对应的体素索引
        drone_ix, drone_iy, drone_iz = self._world_to_voxel(drone_x, drone_y, drone_z)

        # 采样像素: 每隔 ray_sample_step 个像素取一个
        step = self.ray_sample_step
        sample_v = np.arange(0, h, step)
        sample_u = np.arange(0, w, step)
        uu, vv = np.meshgrid(sample_u, sample_v)
        uu = uu.ravel()
        vv = vv.ravel()

        # 采样深度值
        sampled_depths = depth_img[vv, uu]

        # 有效深度掩码
        valid = (sampled_depths > 0.1) & (sampled_depths < 100.0)
        uu = uu[valid]
        vv = vv[valid]
        sampled_depths = sampled_depths[valid]

        if len(sampled_depths) == 0:
            return 0

        # 计算每个采样像素的3D终点 (向量化)
        angle_h = (uu - w / 2.0) / w * fov_h
        angle_v = (vv - h / 2.0) / h * fov_v
        angle_h_rad = np.deg2rad(angle_h)
        angle_v_rad = np.deg2rad(angle_v)

        cos_v = np.cos(angle_v_rad)
        x_body = sampled_depths * cos_v * np.cos(angle_h_rad)
        y_body = sampled_depths * cos_v * np.sin(angle_h_rad)
        z_body = sampled_depths * np.sin(angle_v_rad)

        yaw_rad = np.deg2rad(drone_yaw)
        cos_yaw = math.cos(yaw_rad)
        sin_yaw = math.sin(yaw_rad)

        # 终点NED坐标
        end_x = x_body * cos_yaw - y_body * sin_yaw + drone_x
        end_y = x_body * sin_yaw + y_body * cos_yaw + drone_y
        end_z = z_body + drone_z

        updated_voxels = 0
        deadline = start_time + self.max_update_ms / 1000.0

        # 逐射线更新 (射线投射)
        for i in range(len(end_x)):
            # 超时检查: 单帧更新不超过 max_update_ms
            if time.time() > deadline:
                logger.warning("update_timeout", rays_processed=i, total_rays=len(end_x))
                break

            ex, ey, ez = end_x[i], end_y[i], end_z[i]
            eix, eiy, eiz = self._world_to_voxel(ex, ey, ez)

            # 射线经过的体素标记为空闲
            ray_voxels = self.ray_cast(
                (drone_ix, drone_iy, drone_iz), (eix, eiy, eiz)
            )
            for vx, vy, vz in ray_voxels:
                if self._in_bounds(vx, vy, vz):
                    # 空闲更新: 减少对数几率
                    old = self.log_odds[vx, vy, vz]
                    self.log_odds[vx, vy, vz] = max(
                        self.LOG_ODDS_MIN, old - self.LOG_ODDS_FREE
                    )
                    updated_voxels += 1

            # 射线终点标记为占据
            if self._in_bounds(eix, eiy, eiz):
                old = self.log_odds[eix, eiy, eiz]
                self.log_odds[eix, eiy, eiz] = min(
                    self.LOG_ODDS_MAX, old + self.LOG_ODDS_OCC
                )
                updated_voxels += 1

        elapsed_ms = (time.time() - start_time) * 1000.0
        logger.debug(
            "depth_update",
            rays=len(end_x),
            voxels_updated=updated_voxels,
            elapsed_ms=f"{elapsed_ms:.1f}",
        )

        return updated_voxels

    def is_occupied(self, x: float, y: float, z: float) -> bool:
        """检查指定坐标的体素是否被占据 (概率 > 0.7)

        Args:
            x, y, z: NED坐标 (米)

        Returns:
            True if occupied
        """
        ix, iy, iz = self._world_to_voxel(x, y, z)
        if not self._in_bounds(ix, iy, iz):
            return False
        prob = self._log_odds_to_prob(float(self.log_odds[ix, iy, iz]))
        return prob > self.OCC_THRESHOLD

    def is_free(self, x: float, y: float, z: float) -> bool:
        """检查指定坐标的体素是否空闲 (概率 < 0.3)

        Args:
            x, y, z: NED坐标 (米)

        Returns:
            True if free
        """
        ix, iy, iz = self._world_to_voxel(x, y, z)
        if not self._in_bounds(ix, iy, iz):
            return False
        prob = self._log_odds_to_prob(float(self.log_odds[ix, iy, iz]))
        return prob < self.FREE_THRESHOLD

    def get_occupied_voxels(self) -> np.ndarray:
        """获取所有被占据体素的世界坐标

        Returns:
            Nx3 数组，每行为 (x, y, z) NED坐标
        """
        # 找到对数几率 > 占据阈值的体素
        occ_log_odds = self._prob_to_log_odds(self.OCC_THRESHOLD)
        occupied_mask = self.log_odds > occ_log_odds

        indices = np.argwhere(occupied_mask)  # (N, 3) 体素索引
        if len(indices) == 0:
            return np.empty((0, 3), dtype=np.float32)

        # 转换为世界坐标
        world_coords = np.zeros_like(indices, dtype=np.float32)
        world_coords[:, 0] = self.origin_x + (indices[:, 0] + 0.5) * self.resolution
        world_coords[:, 1] = self.origin_y + (indices[:, 1] + 0.5) * self.resolution
        world_coords[:, 2] = self.origin_z + (indices[:, 2] + 0.5) * self.resolution

        return world_coords

    def ray_cast(self, start: tuple, end: tuple) -> list[tuple]:
        """3D射线投射: 返回射线路径上的体素索引列表

        使用简化的Bresenham 3D线算法，沿最长轴步进。

        Args:
            start: 起始体素索引 (ix, iy, iz)
            end: 终止体素索引 (ix, iy, iz)

        Returns:
            体素索引列表 [(ix, iy, iz), ...]，不包含起点
        """
        sx, sy, sz = start
        ex, ey, ez = end

        # 计算各轴步数和方向
        dx = ex - sx
        dy = ey - sy
        dz = ez - sz

        steps = max(abs(dx), abs(dy), abs(dz))
        if steps == 0:
            return []

        # 浮点步进增量
        x_inc = dx / steps
        y_inc = dy / steps
        z_inc = dz / steps

        voxels = []
        x, y, z = float(sx), float(sy), float(sz)

        for _ in range(steps):
            x += x_inc
            y += y_inc
            z += z_inc
            ix, iy, iz = int(round(x)), int(round(y)), int(round(z))
            voxels.append((ix, iy, iz))

        return voxels

    def check_path_clear(
        self, start: tuple, end: tuple, safety_margin: float = 1.0
    ) -> dict:
        """检查从起点到终点的路径是否畅通

        沿路径采样点，检查每个采样点周围 safety_margin 范围内是否有障碍物。

        Args:
            start: 起点 NED坐标 (x, y, z)
            end: 终点 NED坐标 (x, y, z)
            safety_margin: 安全距离 (米)，检查周围体素的范围

        Returns:
            {"clear": bool, "obstacle_at": tuple|None, "min_clearance": float}
        """
        sx, sy, sz = start
        ex, ey, ez = end

        # 路径总距离
        dist = math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2)
        if dist < 1e-6:
            return {"clear": True, "obstacle_at": None, "min_clearance": float("inf")}

        # 沿路径采样，采样间隔为分辨率的一半
        sample_step = self.resolution * 0.5
        num_samples = max(int(dist / sample_step), 1)

        min_clearance = float("inf")
        obstacle_at = None

        # 安全距离对应的体素范围
        margin_bins = max(1, int(math.ceil(safety_margin / self.resolution)))

        for i in range(num_samples + 1):
            t = i / num_samples
            px = sx + t * (ex - sx)
            py = sy + t * (ey - sy)
            pz = sz + t * (ez - sz)

            cix, ciy, ciz = self._world_to_voxel(px, py, pz)

            # 检查周围体素是否占据
            for dx in range(-margin_bins, margin_bins + 1):
                for dy in range(-margin_bins, margin_bins + 1):
                    for dz in range(-margin_bins, margin_bins + 1):
                        # 跳过超出安全距离的角落体素
                        dist_to_center = math.sqrt(
                            (dx * self.resolution) ** 2
                            + (dy * self.resolution) ** 2
                            + (dz * self.resolution) ** 2
                        )
                        if dist_to_center > safety_margin:
                            continue

                        nix, niy, niz = cix + dx, ciy + dy, ciz + dz
                        if not self._in_bounds(nix, niy, niz):
                            continue

                        prob = self._log_odds_to_prob(float(self.log_odds[nix, niy, niz]))
                        if prob > self.OCC_THRESHOLD:
                            # 计算到障碍物的实际距离
                            obs_x, obs_y, obs_z = self._voxel_to_world(nix, niy, niz)
                            clearance = math.sqrt(
                                (px - obs_x) ** 2 + (py - obs_y) ** 2 + (pz - obs_z) ** 2
                            )
                            if clearance < min_clearance:
                                min_clearance = clearance
                                obstacle_at = (obs_x, obs_y, obs_z)

        is_clear = obstacle_at is None
        return {
            "clear": is_clear,
            "obstacle_at": obstacle_at,
            "min_clearance": min_clearance if not is_clear else float("inf"),
        }

    def find_path(
        self,
        start: tuple,
        end: tuple,
        safety_margin: float = 1.0,
    ) -> Optional[list[tuple]]:
        """A*路径搜索 (2D，固定高度)

        在start的z高度上进行2D A*搜索，使用4连通邻居。
        考虑安全距离，避开占据体素附近的区域。

        Args:
            start: 起点 NED坐标 (x, y, z)
            end: 终点 NED坐标 (x, y, z)
            safety_margin: 安全距离 (米)

        Returns:
            路径点列表 [(x,y,z), ...]，或 None 表示无路径
        """
        # 固定在start的z高度
        fixed_z = start[2]

        # 起终点体素索引
        s_ix, s_iy, _ = self._world_to_voxel(start[0], start[1], fixed_z)
        e_ix, e_iy, _ = self._world_to_voxel(end[0], end[1], fixed_z)
        e_iz = self._world_to_voxel(0, 0, fixed_z)[2]

        # 检查起终点是否在地图内
        if not self._in_bounds(s_ix, s_iy, e_iz) or not self._in_bounds(e_ix, e_iy, e_iz):
            logger.warning("path_out_of_bounds", start=start, end=end)
            return None

        # 安全距离对应的体素范围
        margin_bins = max(1, int(math.ceil(safety_margin / self.resolution)))

        # 预计算不可通行区域 (占据体素 + 安全距离膨胀)
        occ_log_odds = self._prob_to_log_odds(self.OCC_THRESHOLD)
        blocked = np.zeros((self.x_bins, self.y_bins), dtype=bool)

        # 找到当前高度附近的占据体素
        z_lo = max(0, e_iz - margin_bins)
        z_hi = min(self.z_bins, e_iz + margin_bins + 1)
        for iz in range(z_lo, z_hi):
            occ_xy = self.log_odds[:, :, iz] > occ_log_odds
            blocked |= occ_xy

        # 对占据区域做膨胀 (安全距离)
        if margin_bins > 0:
            # 使用简单的方形膨胀
            from scipy.ndimage import binary_dilation
            structure = np.ones((2 * margin_bins + 1, 2 * margin_bins + 1), dtype=bool)
            blocked = binary_dilation(blocked, structure=structure)

        # A*搜索
        # 优先队列: (f_score, counter, ix, iy)
        counter = 0
        open_set: list[tuple[float, int, int, int]] = []
        heapq.heappush(open_set, (0.0, counter, s_ix, s_iy))

        # g_score: 从起点到当前节点的实际代价
        g_score = np.full((self.x_bins, self.y_bins), float("inf"), dtype=np.float32)
        g_score[s_ix, s_iy] = 0.0

        # came_from: 记录路径
        came_from: dict[tuple[int, int], tuple[int, int]] = {}

        # 已访问
        closed_set: set[tuple[int, int]] = set()

        # 启发函数: 曼哈顿距离
        def heuristic(ix: int, iy: int) -> float:
            return abs(ix - e_ix) + abs(iy - e_iy)

        # 4连通邻居: 上下左右
        neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        max_iterations = self.x_bins * self.y_bins  # 防止无限循环
        iterations = 0

        while open_set and iterations < max_iterations:
            iterations += 1
            _, _, cur_ix, cur_iy = heapq.heappop(open_set)

            if (cur_ix, cur_iy) in closed_set:
                continue
            closed_set.add((cur_ix, cur_iy))

            # 到达终点
            if cur_ix == e_ix and cur_iy == e_iy:
                # 重建路径
                path = []
                node = (cur_ix, cur_iy)
                while node in came_from:
                    wx, wy = self._voxel_to_world(node[0], node[1], e_iz)[:2]
                    path.append((wx, wy, fixed_z))
                    node = came_from[node]
                # 添加起点
                wx, wy = self._voxel_to_world(s_ix, s_iy, e_iz)[:2]
                path.append((wx, wy, fixed_z))
                path.reverse()
                logger.info("path_found", waypoints=len(path), iterations=iterations)
                return path

            for dx, dy in neighbors:
                nix, niy = cur_ix + dx, cur_iy + dy

                # 边界检查
                if nix < 0 or nix >= self.x_bins or niy < 0 or niy >= self.y_bins:
                    continue
                if (nix, niy) in closed_set:
                    continue
                # 障碍物检查
                if blocked[nix, niy]:
                    continue

                # 代价: 每步1个体素
                tentative_g = g_score[cur_ix, cur_iy] + 1.0
                if tentative_g < g_score[nix, niy]:
                    g_score[nix, niy] = tentative_g
                    came_from[(nix, niy)] = (cur_ix, cur_iy)
                    f = tentative_g + heuristic(nix, niy)
                    counter += 1
                    heapq.heappush(open_set, (f, counter, nix, niy))

        logger.warning("path_not_found", start=start, end=end, iterations=iterations)
        return None

    def get_2d_occupancy(
        self, altitude: float = 0.0, altitude_range: float = 2.0
    ) -> np.ndarray:
        """获取指定高度的2D占据地图切片

        将3D栅格在指定高度范围内投影为2D地图:
          - 1.0 = 占据 (该列中存在占据体素)
          - 0.0 = 空闲 (该列中所有体素均为空闲)
          - 0.5 = 未知 (该列中既无占据也无空闲)

        Args:
            altitude: 目标高度 NED z坐标 (米)
            altitude_range: 高度范围 (米)，在 [altitude - range/2, altitude + range/2] 内投影

        Returns:
            (H, W) 数组，值域 {0.0, 0.5, 1.0}
        """
        occ_log_odds = self._prob_to_log_odds(self.OCC_THRESHOLD)
        free_log_odds = self._prob_to_log_odds(self.FREE_THRESHOLD)

        # 高度范围对应的体素索引
        iz_lo = max(0, int((altitude - altitude_range / 2 - self.origin_z) / self.resolution))
        iz_hi = min(self.z_bins, int((altitude + altitude_range / 2 - self.origin_z) / self.resolution) + 1)

        if iz_lo >= iz_hi:
            # 高度范围无效，返回全未知
            return np.full((self.x_bins, self.y_bins), 0.5, dtype=np.float32)

        # 取高度范围内的切片
        slice_data = self.log_odds[:, :, iz_lo:iz_hi]  # (x, y, z_slice)

        # 沿z轴: 检查是否存在占据/空闲体素
        has_occupied = np.any(slice_data > occ_log_odds, axis=2)  # (x, y)
        has_free = np.any(slice_data < free_log_odds, axis=2)    # (x, y)

        # 构建2D地图
        result = np.full((self.x_bins, self.y_bins), 0.5, dtype=np.float32)
        result[has_free & ~has_occupied] = 0.0   # 空闲
        result[has_occupied] = 1.0                # 占据

        return result

    def reset(self) -> None:
        """清除所有占据数据，重置为未知状态"""
        self.log_odds.fill(0.0)
        logger.info("voxel_grid_reset")
