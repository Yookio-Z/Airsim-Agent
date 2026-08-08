"""
深度图避障模块

利用 AirSim DepthPlanar 深度图实现实时避障。
策略:
  1. 飞行前/飞行中获取深度图
  2. 将深度图分为5个扇区(左/左前/正前/右前/右)
  3. 每个扇区取最近距离
  4. 根据最近障碍物距离决定: 安全/减速/停止/后退
  5. 生成避障速度修正向量
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ObstacleLevel(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"
    CRITICAL = "critical"


@dataclass
class ObstacleInfo:
    level: ObstacleLevel
    min_distance: float
    sector_distances: dict[str, float]
    avoid_vx: float
    avoid_vy: float
    message: str


@dataclass
class AvoidanceConfig:
    safe_distance: float = 5.0
    caution_distance: float = 3.0
    danger_distance: float = 1.5
    critical_distance: float = 0.8
    max_avoid_speed: float = 2.0
    sector_count: int = 5
    min_valid_pixels: int = 50


SECTOR_NAMES = ["left", "left_front", "front", "right_front", "right"]
SECTOR_LABELS = {
    "left": "左",
    "left_front": "左前",
    "front": "正前",
    "right_front": "右前",
    "right": "右",
}


def analyze_depth_for_obstacles(
    depth_img: np.ndarray,
    config: Optional[AvoidanceConfig] = None,
) -> ObstacleInfo:
    """分析深度图，检测障碍物并生成避障建议。

    Args:
        depth_img: AirSim DepthPlanar 深度图 (H, W) 单通道浮点，单位米
        config: 避障配置

    Returns:
        ObstacleInfo: 障碍物信息 + 避障速度建议
    """
    if config is None:
        config = AvoidanceConfig()

    h, w = depth_img.shape[:2]
    if h == 0 or w == 0:
        return ObstacleInfo(
            level=ObstacleLevel.SAFE,
            min_distance=999.0,
            sector_distances={},
            avoid_vx=0.0,
            avoid_vy=0.0,
            message="深度图为空",
        )

    if len(depth_img.shape) == 3:
        depth_img = depth_img[:, :, 0].astype(np.float32)

    valid_mask = (depth_img > 0.1) & (depth_img < 100.0)
    sector_width = w // config.sector_count

    sector_distances: dict[str, float] = {}
    for i, name in enumerate(SECTOR_NAMES):
        x_start = i * sector_width
        x_end = (i + 1) * sector_width if i < config.sector_count - 1 else w

        sector = depth_img[:, x_start:x_end]
        sector_valid = sector[valid_mask[:, x_start:x_end]]

        if len(sector_valid) >= config.min_valid_pixels:
            sector_distances[name] = float(np.percentile(sector_valid, 10))
        else:
            sector_distances[name] = 999.0

    min_distance = min(sector_distances.values())

    if min_distance <= config.critical_distance:
        level = ObstacleLevel.CRITICAL
    elif min_distance <= config.danger_distance:
        level = ObstacleLevel.DANGER
    elif min_distance <= config.caution_distance:
        level = ObstacleLevel.CAUTION
    else:
        level = ObstacleLevel.SAFE

    avoid_vx, avoid_vy = _compute_avoidance_vector(
        sector_distances, level, config
    )

    closest_sector = min(sector_distances, key=sector_distances.get)
    closest_label = SECTOR_LABELS.get(closest_sector, closest_sector)
    msg = (
        f"障碍等级={level.value} "
        f"最近={min_distance:.1f}m({closest_label}) "
        f"扇区距离: " + " ".join(
            f"{SECTOR_LABELS[k]}={v:.1f}m" for k, v in sector_distances.items()
        )
    )

    return ObstacleInfo(
        level=level,
        min_distance=min_distance,
        sector_distances=sector_distances,
        avoid_vx=avoid_vx,
        avoid_vy=avoid_vy,
        message=msg,
    )


def _compute_avoidance_vector(
    sector_distances: dict[str, float],
    level: ObstacleLevel,
    config: AvoidanceConfig,
) -> tuple[float, float]:
    """根据扇区距离计算避障速度向量。

    策略: 远离最近障碍物的方向，速度与距离成反比。
    NED坐标系: vx=前(北), vy=右(东)
    图像坐标系: 左侧=vy负, 右侧=vy正, 上方=vx正(远), 下方=vx负(近)
    """
    if level == ObstacleLevel.SAFE:
        return 0.0, 0.0

    left_dist = sector_distances.get("left", 999.0)
    left_front_dist = sector_distances.get("left_front", 999.0)
    front_dist = sector_distances.get("front", 999.0)
    right_front_dist = sector_distances.get("right_front", 999.0)
    right_dist = sector_distances.get("right", 999.0)

    left_weight = left_dist + left_front_dist
    right_weight = right_dist + right_front_dist

    if level == ObstacleLevel.CRITICAL:
        avoid_vx = -config.max_avoid_speed
        if left_weight > right_weight:
            avoid_vy = -config.max_avoid_speed * 0.5
        else:
            avoid_vy = config.max_avoid_speed * 0.5
    elif level == ObstacleLevel.DANGER:
        if front_dist < config.danger_distance:
            avoid_vx = -config.max_avoid_speed * 0.5
        else:
            avoid_vx = 0.0
        if left_weight > right_weight:
            avoid_vy = -config.max_avoid_speed * 0.3
        else:
            avoid_vy = config.max_avoid_speed * 0.3
    elif level == ObstacleLevel.CAUTION:
        avoid_vx = 0.0
        balance = (right_weight - left_weight) / max(left_weight + right_weight, 0.1)
        avoid_vy = balance * config.max_avoid_speed * 0.2
    else:
        avoid_vx = 0.0
        avoid_vy = 0.0

    return round(avoid_vx, 2), round(avoid_vy, 2)


def check_obstacle_before_move(
    controller,
    camera_name: str = "0",
    vehicle_name: str = "",
    config: Optional[AvoidanceConfig] = None,
) -> ObstacleInfo:
    """飞行前检查: 获取深度图并分析障碍物。

    Args:
        controller: AirSimController 实例
        camera_name: 相机ID
        vehicle_name: 无人机名称
        config: 避障配置

    Returns:
        ObstacleInfo: 障碍物分析结果
    """
    import airsim

    if config is None:
        config = AvoidanceConfig()

    try:
        request = airsim.ImageRequest(camera_name, airsim.ImageType.DepthPlanar, False, True)
        responses = controller._client.simGetImages([request], vehicle_name=vehicle_name)

        if not responses or len(responses) == 0:
            return ObstacleInfo(
                level=ObstacleLevel.SAFE,
                min_distance=999.0,
                sector_distances={},
                avoid_vx=0.0,
                avoid_vy=0.0,
                message="深度图获取失败，默认安全",
            )

        img_data = responses[0].image_data_uint8
        if not img_data or len(img_data) == 0:
            return ObstacleInfo(
                level=ObstacleLevel.SAFE,
                min_distance=999.0,
                sector_distances={},
                avoid_vx=0.0,
                avoid_vy=0.0,
                message="深度图数据为空，默认安全",
            )

        raw_bytes = bytes(img_data)
        nparr = np.frombuffer(raw_bytes, np.uint8)
        depth_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)

        if depth_img is None:
            depth_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if depth_img is None:
            return ObstacleInfo(
                level=ObstacleLevel.SAFE,
                min_distance=999.0,
                sector_distances={},
                avoid_vx=0.0,
                avoid_vy=0.0,
                message="深度图解码失败，默认安全",
            )

        if len(depth_img.shape) == 3:
            depth_float = depth_img[:, :, 0].astype(np.float32)
        else:
            depth_float = depth_img.astype(np.float32)

        return analyze_depth_for_obstacles(depth_float, config)

    except Exception as e:
        logger.error(f"避障检查失败: {e}")
        return ObstacleInfo(
            level=ObstacleLevel.SAFE,
            min_distance=999.0,
            sector_distances={},
            avoid_vx=0.0,
            avoid_vy=0.0,
            message=f"避障检查异常: {e}，默认安全",
        )
