"""
概率信念状态模块 — 基于贝叶斯更新的目标搜索信念网格

核心组件:
  - BeliefGrid: 2D 概率网格，维护目标位置的后验概率分布
  - InformationGainCalculator: 计算搜索位置的信息增益
  - SearchStrategy: 结合信念网格与搜索路径规划

坐标系: NED (North-East-Down)
  X = North, Y = East, Z = Down (负值=向上)

贝叶斯更新公式:
  负检测更新: P(H|¬D) = P(¬D|H) * P(H) / P(¬D)
    H = "目标在该格子", D = "检测到目标"
    P(¬D|H) = 1 - detection_prob (漏检率)
    P(¬D|¬H) = 1 (YOLO-World 无误检)
  正检测更新: 在检测位置附近增加概率（高斯分布）
"""

from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from ..logging_config import get_logger

logger = get_logger(__name__)


def _label_connected_components(binary: np.ndarray) -> tuple[np.ndarray, int]:
    """纯 numpy 实现的 4-连通域标记（替代 scipy.ndimage.label）

    使用两遍扫描算法（union-find）标记连通区域。

    Args:
        binary: 二值图像（0/1 整数数组）

    Returns:
        (labeled_array, num_features): 标记数组和连通域数量
    """
    rows, cols = binary.shape
    labels = np.zeros_like(binary, dtype=np.int32)
    parent = [0]  # 等价关系表，parent[0] 不使用

    def find(x: int) -> int:
        """查找根节点（路径压缩）"""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        """合并两个集合"""
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    next_label = 1

    # 第一遍扫描：临时标记 + 记录等价关系
    for r in range(rows):
        for c in range(cols):
            if binary[r, c] == 0:
                continue
            neighbors = []
            # 上方邻居
            if r > 0 and binary[r - 1, c] > 0:
                neighbors.append(labels[r - 1, c])
            # 左方邻居
            if c > 0 and binary[r, c - 1] > 0:
                neighbors.append(labels[r, c - 1])

            if not neighbors:
                labels[r, c] = next_label
                parent.append(next_label)
                next_label += 1
            else:
                min_label = min(neighbors)
                labels[r, c] = min_label
                for nb in neighbors:
                    union(nb, min_label)

    # 第二遍扫描：解析等价关系，重新编号
    label_map = {}
    new_label = 0
    for r in range(rows):
        for c in range(cols):
            if labels[r, c] == 0:
                continue
            root = find(labels[r, c])
            if root not in label_map:
                new_label += 1
                label_map[root] = new_label
            labels[r, c] = label_map[root]

    return labels, new_label


class BeliefGrid:
    """2D 概率网格 — 维护目标位置的信念分布

    网格以 (center_x, center_y) 为中心，覆盖 size × size 的区域，
    每个格子大小为 resolution × resolution 米。
    概率存储为 numpy 2D 数组，所有概率之和为 1.0。
    """

    def __init__(
        self,
        center_x: float = 0.0,
        center_y: float = 0.0,
        size: float = 50.0,
        resolution: float = 1.0,
    ) -> None:
        self.center_x = center_x
        self.center_y = center_y
        self.size = size
        self.resolution = resolution

        # 网格尺寸（格子数）
        self.grid_size = int(size / resolution)
        # 均匀先验概率
        self.probability = np.ones((self.grid_size, self.grid_size), dtype=np.float64)
        self.probability /= self.probability.sum()

        # 搜索标记：记录哪些格子已被搜索过（概率显著降低）
        self._searched = np.zeros((self.grid_size, self.grid_size), dtype=bool)

        # 网格左下角（NED 坐标）
        self._origin_x = center_x - size / 2.0
        self._origin_y = center_y - size / 2.0

        logger.info(
            "belief_grid_init",
            center_x=center_x,
            center_y=center_y,
            size=size,
            resolution=resolution,
            grid_cells=self.grid_size,
        )

    # ── 坐标转换 ──────────────────────────────────────────────

    def _ned_to_cell(self, x: float, y: float) -> tuple[int, int]:
        """NED 坐标 → 网格索引 (row, col)"""
        col = int((x - self._origin_x) / self.resolution)
        row = int((y - self._origin_y) / self.resolution)
        # 裁剪到有效范围
        col = max(0, min(self.grid_size - 1, col))
        row = max(0, min(self.grid_size - 1, row))
        return row, col

    def _cell_to_ned(self, row: int, col: int) -> tuple[float, float]:
        """网格索引 (row, col) → NED 坐标（格子中心）"""
        x = self._origin_x + (col + 0.5) * self.resolution
        y = self._origin_y + (row + 0.5) * self.resolution
        return x, y

    def _make_distance_grid(self, cx: float, cy: float) -> np.ndarray:
        """生成每个格子中心到 (cx, cy) 的欧氏距离网格"""
        rows, cols = np.meshgrid(
            np.arange(self.grid_size),
            np.arange(self.grid_size),
            indexing="ij",
        )
        xs = self._origin_x + (cols + 0.5) * self.resolution
        ys = self._origin_y + (rows + 0.5) * self.resolution
        return np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)

    # ── 先验设置 ──────────────────────────────────────────────

    def set_prior(self, regions: list[dict]) -> None:
        """根据语义区域设置先验概率

        Args:
            regions: 先验区域列表，每个区域包含:
                - center_x: 区域中心 X (NED)
                - center_y: 区域中心 Y (NED)
                - radius: 区域半径 (米)
                - priority: 优先级 (越高概率越大)
        """
        if not regions:
            return

        self.probability = np.zeros_like(self.probability)

        for region in regions:
            cx = region["center_x"]
            cy = region["center_y"]
            radius = region["radius"]
            priority = region.get("priority", 1.0)

            # 高斯分布：σ = radius/2，使 95% 概率落在 radius 内
            sigma = radius / 2.0
            dist_grid = self._make_distance_grid(cx, cy)
            # 高斯权重 × 优先级
            gaussian_weight = priority * np.exp(-0.5 * (dist_grid / sigma) ** 2)
            self.probability += gaussian_weight

        # 归一化：总概率 = 1.0
        total = self.probability.sum()
        if total > 0:
            self.probability /= total
        else:
            # 退化情况：回退到均匀分布
            self.probability = np.ones_like(self.probability)
            self.probability /= self.probability.sum()

        logger.info(
            "prior_set",
            num_regions=len(regions),
            max_prob=self.probability.max(),
        )

    # ── 贝叶斯更新 ────────────────────────────────────────────

    def update_negative(
        self,
        x: float,
        y: float,
        radius: float,
        detection_prob: float = 0.8,
    ) -> None:
        """负检测更新 — 搜索某区域后未发现目标

        贝叶斯更新:
          P(H|¬D) = P(¬D|H) * P(H) / P(¬D)
          P(¬D|H) = 1 - detection_prob (漏检率)
          P(¬D|¬H) = 1 (无误检)

        Args:
            x: 搜索中心 X (NED)
            y: 搜索中心 Y (NED)
            radius: 搜索半径 (米)
            detection_prob: 检测概率（目标存在时能检测到的概率），默认 0.8
        """
        miss_rate = 1.0 - detection_prob  # 漏检率

        # 计算每个格子到搜索中心的距离
        dist_grid = self._make_distance_grid(x, y)

        # 搜索覆盖权重：在 radius 内逐渐衰减
        # 使用平滑衰减而非硬截断，避免边界效应
        search_weight = np.exp(-0.5 * (dist_grid / (radius / 2.0)) ** 2)
        # 只在 radius 范围内生效
        search_weight[dist_grid > radius * 1.5] = 0.0

        # 贝叶斯更新：P(¬D|H) 在搜索区域内为 miss_rate，区域外为 1.0
        likelihood = np.ones_like(self.probability)
        likelihood = 1.0 - search_weight * (1.0 - miss_rate)
        # likelihood = 1.0 - search_weight * detection_prob
        # 等价于：搜索区域内 P(¬D|H) = miss_rate，区域外 P(¬D|H) = 1.0

        # 后验 ∝ 似然 × 先验
        posterior = likelihood * self.probability
        total = posterior.sum()
        if total > 0:
            self.probability = posterior / total

        # 标记已搜索区域
        self._searched[dist_grid <= radius] = True

        logger.debug(
            "update_negative",
            x=round(x, 1),
            y=round(y, 1),
            radius=radius,
            detection_prob=detection_prob,
        )

    def update_positive(
        self,
        x: float,
        y: float,
        radius: float = 2.0,
        confidence: float = 0.8,
    ) -> None:
        """正检测更新 — 在 (x, y) 检测到目标

        在检测位置附近增加概率（高斯分布），然后归一化。

        Args:
            x: 检测位置 X (NED)
            y: 检测位置 Y (NED)
            radius: 检测不确定性半径 (米)，默认 2.0
            confidence: 检测置信度，默认 0.8
        """
        sigma = radius / 2.0
        dist_grid = self._make_distance_grid(x, y)

        # 高斯权重表示检测不确定性
        detection_weight = confidence * np.exp(-0.5 * (dist_grid / sigma) ** 2)

        # 乘法更新：增强检测位置附近的概率
        self.probability *= (1.0 + detection_weight)

        # 归一化
        total = self.probability.sum()
        if total > 0:
            self.probability /= total

        logger.info(
            "update_positive",
            x=round(x, 1),
            y=round(y, 1),
            radius=radius,
            confidence=confidence,
        )

    # ── 搜索规划 ──────────────────────────────────────────────

    def get_next_search_waypoint(
        self,
        current_x: float,
        current_y: float,
        altitude: float = -3.0,
    ) -> dict:
        """选择下一个搜索航点 — 基于信息增益评分

        评分公式: score(cell) = P(cell) / distance(current, cell)
        高概率且距离近的格子优先。

        Args:
            current_x: 当前位置 X (NED)
            current_y: 当前位置 Y (NED)
            altitude: 搜索高度 (NED, 负值=向上)

        Returns:
            {"x", "y", "z", "yaw", "expected_info_gain", "cell_probability"}
        """
        # 距离网格
        dist_grid = self._make_distance_grid(current_x, current_y)

        # 避免除零：最小距离设为 resolution
        dist_grid = np.maximum(dist_grid, self.resolution)

        # 评分：概率 / 距离
        score = self.probability / dist_grid

        # 找到最高分的格子
        best_idx = np.unravel_index(score.argmax(), score.shape)
        best_row, best_col = best_idx

        # 转换为 NED 坐标
        target_x, target_y = self._cell_to_ned(best_row, best_col)

        # 计算偏航角：朝向目标位置
        dx = target_x - current_x
        dy = target_y - current_y
        yaw = math.degrees(math.atan2(dy, dx))

        # 估算信息增益
        cell_prob = float(self.probability[best_row, best_col])
        # 简化信息增益：该格子的概率 × log(1/概率)
        if cell_prob > 1e-12:
            info_gain = cell_prob * math.log(1.0 / cell_prob)
        else:
            info_gain = 0.0

        return {
            "x": round(target_x, 1),
            "y": round(target_y, 1),
            "z": altitude,
            "yaw": round(yaw, 1),
            "expected_info_gain": round(info_gain, 6),
            "cell_probability": round(cell_prob, 6),
        }

    def get_top_regions(self, n: int = 3) -> list[dict]:
        """返回概率最高的 N 个区域

        通过聚类高概率格子，返回区域中心、总概率和等效半径。

        Args:
            n: 返回的区域数量

        Returns:
            [{"center_x", "center_y", "probability", "radius"}, ...]
        """
        # 阈值：概率高于均值的格子视为高概率
        threshold = self.probability.mean() * 2.0
        binary = (self.probability > threshold).astype(np.int32)

        # 连通域标记（纯 numpy 实现）
        labeled_array, num_features = _label_connected_components(binary)

        if num_features == 0:
            # 没有显著区域，返回全局最高概率点
            best_idx = np.unravel_index(self.probability.argmax(), self.probability.shape)
            cx, cy = self._cell_to_ned(*best_idx)
            return [{"center_x": round(cx, 1), "center_y": round(cy, 1),
                      "probability": round(float(self.probability[best_idx]), 6),
                      "radius": round(self.resolution, 1)}]

        # 计算每个聚类的信息
        regions = []
        for i in range(1, num_features + 1):
            mask = labeled_array == i
            prob_sum = float(self.probability[mask].sum())
            # 概率加权质心
            weights = self.probability[mask]
            rows, cols = np.where(mask)
            if prob_sum > 0:
                center_row = np.average(rows, weights=weights)
                center_col = np.average(cols, weights=weights)
            else:
                center_row = rows.mean()
                center_col = cols.mean()

            cx, cy = self._cell_to_ned(int(center_row), int(center_col))

            # 等效半径：基于面积
            area = mask.sum() * self.resolution ** 2
            radius = math.sqrt(area / math.pi)

            regions.append({
                "center_x": round(cx, 1),
                "center_y": round(cy, 1),
                "probability": round(prob_sum, 6),
                "radius": round(radius, 1),
            })

        # 按概率降序排列，取前 N 个
        regions.sort(key=lambda r: r["probability"], reverse=True)
        return regions[:n]

    def get_search_coverage(self) -> float:
        """返回搜索覆盖率 — 已搜索格子占总格子数的比例"""
        return float(self._searched.sum()) / self._searched.size

    def get_probability_at(self, x: float, y: float) -> float:
        """获取指定位置的概率值

        Args:
            x: 位置 X (NED)
            y: 位置 Y (NED)

        Returns:
            该位置的概率值，越界返回 0.0
        """
        # 检查是否在网格范围内
        col = (x - self._origin_x) / self.resolution
        row = (y - self._origin_y) / self.resolution
        if col < 0 or col >= self.grid_size or row < 0 or row >= self.grid_size:
            return 0.0
        return float(self.probability[int(row), int(col)])

    def to_heatmap(self) -> np.ndarray:
        """返回概率网格的热力图数据（用于可视化）

        Returns:
            HxW float 数组，值域 [0, 1]（归一化到最大值）
        """
        max_val = self.probability.max()
        if max_val > 0:
            return self.probability / max_val
        return self.probability.copy()

    def get_stats(self) -> dict:
        """返回信念网格统计信息

        Returns:
            {"entropy", "max_probability", "max_location", "coverage", "total_cells"}
        """
        # 信息熵 H = -Σ P * log(P)
        with np.errstate(divide="ignore", invalid="ignore"):
            log_probs = np.where(self.probability > 1e-15,
                                 np.log(self.probability), 0.0)
            entropy = float(-np.sum(self.probability * log_probs))

        # 最大概率及其位置
        max_idx = np.unravel_index(self.probability.argmax(), self.probability.shape)
        max_x, max_y = self._cell_to_ned(*max_idx)

        return {
            "entropy": round(entropy, 4),
            "max_probability": round(float(self.probability[max_idx]), 6),
            "max_location": {"x": round(max_x, 1), "y": round(max_y, 1)},
            "coverage": round(self.get_search_coverage(), 4),
            "total_cells": self.grid_size * self.grid_size,
        }


class InformationGainCalculator:
    """信息增益计算器 — 评估搜索位置的价值

    信息增益衡量搜索某位置后期望减少的不确定性:
      IG(x,y) = H(belief_before) - E[H(belief_after)]
    简化计算: IG ≈ Σ P(cell) * log(1/P(cell)) 对视野范围内的格子求和
    """

    def expected_information_gain(
        self,
        belief: BeliefGrid,
        x: float,
        y: float,
        fov_radius: float,
    ) -> float:
        """计算在 (x, y) 搜索的期望信息增益

        Args:
            belief: 信念网格
            x: 搜索位置 X (NED)
            y: 搜索位置 Y (NED)
            fov_radius: 视野半径 (米)

        Returns:
            期望信息增益值
        """
        dist_grid = belief._make_distance_grid(x, y)

        # 视野范围内的格子
        in_fov = dist_grid <= fov_radius
        if not in_fov.any():
            return 0.0

        # 视野内格子的概率
        probs = belief.probability[in_fov]

        # 信息增益: Σ P(cell) * log(1/P(cell))
        with np.errstate(divide="ignore", invalid="ignore"):
            log_inv_probs = np.where(probs > 1e-15, np.log(1.0 / probs), 0.0)
            ig = float(np.sum(probs * log_inv_probs))

        return ig


class SearchStrategy:
    """搜索策略 — 结合信念网格与信息驱动路径规划

    贪心策略: 从当前位置出发，反复选择信息增益最高的航点，
    每次虚拟搜索后更新信念（保守假设负检测）。
    """

    def __init__(self, belief: BeliefGrid) -> None:
        self.belief = belief
        self._ig_calculator = InformationGainCalculator()

        logger.info(
            "search_strategy_init",
            grid_size=belief.grid_size,
        )

    def plan_search_path(
        self,
        start_x: float,
        start_y: float,
        altitude: float = -3.0,
        max_waypoints: int = 15,
    ) -> list[dict]:
        """生成信息驱动的搜索路径

        贪心算法: 每步选择期望信息增益最高的位置，
        虚拟执行负检测更新后继续规划。

        Args:
            start_x: 起始位置 X (NED)
            start_y: 起始位置 Y (NED)
            altitude: 搜索高度 (NED, 负值=向上)
            max_waypoints: 最大航点数

        Returns:
            航点列表 [{"x", "y", "z", "yaw", "priority", "expected_gain"}, ...]
        """
        # 深拷贝信念网格，避免规划过程修改原始信念
        planning_belief = deepcopy(self.belief)

        waypoints = []
        cur_x, cur_y = start_x, start_y

        # 视野半径：基于搜索高度估算（Pitch=-45° 时约等于 |altitude|）
        fov_radius = abs(altitude) * 1.5

        for i in range(max_waypoints):
            # 选择下一个最优航点
            waypoint = planning_belief.get_next_search_waypoint(
                cur_x, cur_y, altitude
            )

            # 计算信息增益
            ig = self._ig_calculator.expected_information_gain(
                planning_belief, waypoint["x"], waypoint["y"], fov_radius
            )

            # 如果信息增益极低，停止规划
            if ig < 1e-8 and i > 0:
                logger.debug("plan_early_stop", reason="low_info_gain", step=i)
                break

            waypoints.append({
                "x": waypoint["x"],
                "y": waypoint["y"],
                "z": altitude,
                "yaw": waypoint["yaw"],
                "priority": round(waypoint["cell_probability"], 6),
                "expected_gain": round(ig, 6),
            })

            # 保守更新：假设在该位置未发现目标
            planning_belief.update_negative(
                waypoint["x"], waypoint["y"], fov_radius
            )

            # 更新当前位置
            cur_x, cur_y = waypoint["x"], waypoint["y"]

        logger.info(
            "search_path_planned",
            num_waypoints=len(waypoints),
            start_x=round(start_x, 1),
            start_y=round(start_y, 1),
        )

        return waypoints

    def update_after_search(
        self,
        x: float,
        y: float,
        radius: float,
        found: bool,
        confidence: float = 0.8,
    ) -> None:
        """根据实际搜索结果更新信念

        Args:
            x: 搜索位置 X (NED)
            y: 搜索位置 Y (NED)
            radius: 搜索半径 (米)
            found: 是否发现目标
            confidence: 检测置信度，默认 0.8
        """
        if found:
            self.belief.update_positive(x, y, radius, confidence)
        else:
            self.belief.update_negative(x, y, radius, confidence)

        logger.info(
            "belief_updated_after_search",
            x=round(x, 1),
            y=round(y, 1),
            found=found,
            coverage=round(self.belief.get_search_coverage(), 4),
        )
