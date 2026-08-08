"""
语义搜索规划器
基于场景知识和目标类型，生成优先级排序的搜索航点

设计思路:
  - 利用场景语义信息（区域、优先级）指导搜索顺序
  - 高优先级区域生成更多航点、优先搜索
  - 低优先级区域生成较少航点、延后搜索
  - 规则驱动，不依赖 LLM（LLM 调用在 MCP 工具层完成）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..logging_config import get_logger
from .search_pattern import generate_spiral_waypoints

logger = get_logger(__name__)

# 目标-区域知识库：不同目标类型最可能出现的区域及优先级
TARGET_REGIONS: dict[str, list[dict]] = {
    "car": [
        {"name": "道路", "priority": 0.9, "reason": "车辆通常在道路上行驶或停放"},
        {"name": "停车场", "priority": 0.85, "reason": "车辆常停放在停车场"},
        {"name": "建筑周边", "priority": 0.5, "reason": "车辆可能停在建筑旁"},
        {"name": "开阔区域", "priority": 0.3, "reason": "车辆可能在开阔地"},
    ],
    "person": [
        {"name": "建筑入口", "priority": 0.9, "reason": "人员常在建筑出入口"},
        {"name": "道路", "priority": 0.7, "reason": "人员可能在道路上行走"},
        {"name": "开阔区域", "priority": 0.5, "reason": "人员可能在广场等开阔地"},
    ],
    "truck": [
        {"name": "主干道", "priority": 0.9, "reason": "卡车通常在主干道行驶"},
        {"name": "仓库区域", "priority": 0.85, "reason": "卡车常在仓库装卸"},
        {"name": "停车场", "priority": 0.6, "reason": "卡车可能停放在停车场"},
    ],
}

# 关键词到区域的映射（用于 parse_scene_description）
_KEYWORD_REGION_MAP: list[dict] = [
    {
        "keywords": ["道路", "路", "road", "street"],
        "region": {"name": "道路", "center_x": 0.0, "center_y": 0.0, "radius": 15.0},
    },
    {
        "keywords": ["建筑", "楼", "building"],
        "region": {"name": "建筑", "center_x": 10.0, "center_y": 10.0, "radius": 12.0},
    },
    {
        "keywords": ["停车场", "parking"],
        "region": {"name": "停车场", "center_x": -10.0, "center_y": 5.0, "radius": 10.0},
    },
    {
        "keywords": ["仓库", "warehouse"],
        "region": {"name": "仓库区域", "center_x": 15.0, "center_y": -5.0, "radius": 12.0},
    },
    {
        "keywords": ["广场", "开阔", "plaza", "open"],
        "region": {"name": "开阔区域", "center_x": 0.0, "center_y": -10.0, "radius": 20.0},
    },
    {
        "keywords": ["入口", "entrance", "门"],
        "region": {"name": "建筑入口", "center_x": 10.0, "center_y": 10.0, "radius": 5.0},
    },
    {
        "keywords": ["高速", "highway", "主干道"],
        "region": {"name": "主干道", "center_x": 0.0, "center_y": 0.0, "radius": 20.0},
    },
]


@dataclass
class SceneKnowledge:
    """搜索场景知识描述"""

    # 场景自然语言描述
    description: str = ""
    # 命名区域列表，每项含 name, center_x, center_y, radius, priority
    regions: list[dict] = field(default_factory=list)
    # 目标可能出现的提示位置
    target_hints: list[str] = field(default_factory=list)


class SemanticSearchPlanner:
    """语义搜索规划器

    根据目标类型和场景知识，生成按语义优先级排序的搜索航点。
    高优先级区域先搜索、航点更密集；低优先级区域后搜索、航点更稀疏。
    """

    def __init__(self) -> None:
        # 使用内置知识库，可在外部替换
        self.target_regions = TARGET_REGIONS

    def plan_search(
        self,
        target_class: str,
        scene: SceneKnowledge,
        altitude: float = -3.0,
        coverage_radius: float = 25.0,
    ) -> list[dict]:
        """生成按语义优先级排序的搜索航点

        Args:
            target_class: 目标类型（car/person/truck 等）
            scene: 场景知识
            altitude: 搜索高度（NED，负值=向上）
            coverage_radius: 搜索覆盖半径（米）

        Returns:
            航点列表，每项含 x, y, z, yaw, region, priority
        """
        # 收集所有区域及其优先级
        search_regions = self._resolve_regions(target_class, scene)

        if not search_regions:
            # 没有区域信息时，在原点做一次默认螺旋搜索
            logger.warning("plan_search_no_regions", target=target_class)
            waypoints = generate_spiral_waypoints(
                center_x=0.0,
                center_y=0.0,
                altitude=altitude,
                max_radius=coverage_radius,
            )
            return [
                {**wp, "region": "默认", "priority": 0.5} for wp in waypoints
            ]

        # 按优先级降序排列
        search_regions.sort(key=lambda r: r.get("priority", 0.0), reverse=True)

        all_waypoints: list[dict] = []
        max_priority = search_regions[0].get("priority", 1.0)

        for region_info in search_regions:
            priority = region_info.get("priority", 0.5)
            center_x = region_info.get("center_x", 0.0)
            center_y = region_info.get("center_y", 0.0)
            radius = region_info.get("radius", coverage_radius)
            region_name = region_info.get("name", "未知")

            # 根据优先级决定航点密度：高优先级→更多圈，低优先级→更少圈
            # 优先级比例决定最大搜索半径
            priority_ratio = priority / max_priority if max_priority > 0 else 0.5
            effective_radius = radius * priority_ratio

            # 优先级越高，半径步进越小（航点更密集）
            radius_step = max(6.0, 6.0 / priority_ratio)

            logger.info(
                "plan_search_region",
                region=region_name,
                priority=priority,
                center_x=center_x,
                center_y=center_y,
                effective_radius=round(effective_radius, 1),
            )

            region_waypoints = generate_spiral_waypoints(
                center_x=center_x,
                center_y=center_y,
                altitude=altitude,
                radius_step=radius_step,
                max_radius=effective_radius,
                points_per_circle=4,
            )

            # 为每个航点附加区域和优先级信息
            for wp in region_waypoints:
                all_waypoints.append({
                    **wp,
                    "region": region_name,
                    "priority": priority,
                })

        # 按优先级降序排列（同优先级内保持螺旋顺序）
        all_waypoints.sort(key=lambda wp: -wp["priority"])

        logger.info(
            "plan_search_complete",
            target=target_class,
            total_waypoints=len(all_waypoints),
            regions_count=len(search_regions),
        )

        return all_waypoints

    def suggest_regions(self, target_class: str) -> list[dict]:
        """根据目标类型推荐搜索区域

        Args:
            target_class: 目标类型

        Returns:
            推荐区域列表，每项含 name, priority, reason
        """
        # 查找精确匹配
        regions = self.target_regions.get(target_class)
        if regions:
            return [dict(r) for r in regions]

        # 模糊匹配：目标类型包含已知类别，或已知类别包含目标类型
        target_lower = target_class.lower()
        for key, val in self.target_regions.items():
            if key in target_lower or target_lower in key:
                return [dict(r) for r in val]

        # 未找到匹配，返回通用建议
        logger.info("suggest_regions_fallback", target=target_class)
        return [
            {"name": "开阔区域", "priority": 0.5, "reason": "未识别目标类型，建议从开阔区域开始搜索"},
            {"name": "道路", "priority": 0.4, "reason": "道路是常见目标出现区域"},
        ]

    def parse_scene_description(self, description: str) -> SceneKnowledge:
        """从自然语言描述解析场景知识（规则驱动，非 LLM）

        通过关键词匹配提取区域信息，支持中英文关键词。

        Args:
            description: 场景自然语言描述

        Returns:
            SceneKnowledge 实例
        """
        regions: list[dict] = []
        target_hints: list[str] = []
        desc_lower = description.lower()

        for mapping in _KEYWORD_REGION_MAP:
            keywords = mapping["keywords"]
            # 检查是否有任意关键词出现在描述中
            matched = any(kw in desc_lower for kw in keywords)
            if matched:
                region = dict(mapping["region"])
                # 根据关键词出现顺序赋予默认优先级（先出现的更高）
                region["priority"] = round(0.9 - 0.1 * len(regions), 2)
                regions.append(region)
                # 同时作为目标提示
                target_hints.append(region["name"])

        if not regions:
            logger.info("parse_scene_no_keywords", description=description[:50])

        return SceneKnowledge(
            description=description,
            regions=regions,
            target_hints=target_hints,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_regions(
        self, target_class: str, scene: SceneKnowledge
    ) -> list[dict]:
        """合并场景区域与目标推荐区域，生成最终搜索区域列表

        优先使用场景中已有优先级的区域；
        对于场景中缺少的区域，用目标推荐区域补充。
        """
        result: list[dict] = []

        # 场景中已有区域（带优先级）直接使用
        if scene.regions:
            for r in scene.regions:
                region = dict(r)
                # 如果区域没有优先级，从目标推荐中查找
                if "priority" not in region:
                    region["priority"] = self._lookup_priority(
                        target_class, region.get("name", "")
                    )
                result.append(region)

        # 用目标推荐区域补充场景中未覆盖的区域
        suggested = self.suggest_regions(target_class)
        existing_names = {r.get("name") for r in result}
        for sug in suggested:
            if sug["name"] not in existing_names:
                # 补充区域使用默认中心位置（原点附近偏移）
                result.append({
                    "name": sug["name"],
                    "center_x": 0.0,
                    "center_y": 0.0,
                    "radius": 15.0,
                    "priority": sug["priority"],
                })
                existing_names.add(sug["name"])

        return result

    def _lookup_priority(self, target_class: str, region_name: str) -> float:
        """从目标推荐区域中查找指定区域名称的优先级"""
        suggested = self.suggest_regions(target_class)
        for sug in suggested:
            if sug["name"] == region_name:
                return sug["priority"]
        return 0.5  # 默认优先级
