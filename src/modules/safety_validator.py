"""
飞行安全验证层

在执行任何飞行指令前，对目标位置、速度、路径进行安全检查，
防止无人机飞出围栏、撞入禁飞区或超速飞行。

NED坐标系: z负值=高于地面，z正值=低于地面
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FlightConstraint:
    """飞行安全约束配置

    所有距离单位为米，速度单位为 m/s。
    NED坐标系下 z 为负值表示高于地面。
    """

    # 最大飞行高度（绝对值，米）
    max_altitude: float = 50.0
    # 最小飞行高度（绝对值，米），防止贴地或钻地
    min_altitude: float = 0.5
    # 最大飞行速度（m/s）
    max_velocity: float = 10.0
    # 地理围栏半径（米）
    max_distance_from_home: float = 100.0
    # 地理围栏中心（NED x, y）
    home_position: tuple[float, float] = (0.0, 0.0)
    # 禁飞区列表: {"x": float, "y": float, "radius": float}
    no_fly_zones: list[dict[str, float]] = field(default_factory=list)


@dataclass
class ValidationResult:
    """安全验证结果

    is_safe: 是否安全（无danger级别违规）
    violations: 违规描述列表
    corrected: 建议修正值（如夹紧后的位置、降速后的速度）
    level: 严重程度 — safe / warning / danger
    """

    is_safe: bool
    violations: list[str] = field(default_factory=list)
    corrected: dict[str, Any] | None = None
    level: str = "safe"

    def __bool__(self) -> bool:
        return self.is_safe


def _distance_2d(x1: float, y1: float, x2: float, y2: float) -> float:
    """计算二维欧几里得距离"""
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def _point_in_circle(px: float, py: float, cx: float, cy: float, r: float) -> bool:
    """判断点 (px, py) 是否在圆 (cx, cy, r) 内"""
    return _distance_2d(px, py, cx, cy) < r


def _segment_crosses_circle(
    ax: float, ay: float,
    bx: float, by: float,
    cx: float, cy: float, r: float,
) -> bool:
    """判断线段 (a→b) 是否穿过圆 (cx, cy, r)

    原理: 计算圆心到线段的最短距离，若小于半径则相交。
    """
    dx = bx - ax
    dy = by - ay
    fx = ax - cx
    fy = ay - cy

    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        # 线段退化为点
        return _point_in_circle(ax, ay, cx, cy, r)

    # 参数 t ∈ [0, 1]，投影到线段上
    t = -(fx * dx + fy * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    # 线段上最近点
    closest_x = ax + t * dx
    closest_y = ay + t * dy

    return _distance_2d(closest_x, closest_y, cx, cy) < r


class SafetyValidator:
    """飞行安全验证器

    用法:
        validator = SafetyValidator()
        result = validator.validate_position(10, 20, -5)
        if not result:
            print("不安全!", result.violations)
    """

    def __init__(self, constraints: FlightConstraint | None = None) -> None:
        self.constraints = constraints or FlightConstraint()

    # ------------------------------------------------------------------
    # 核心验证方法
    # ------------------------------------------------------------------

    def validate_position(self, x: float, y: float, z: float) -> ValidationResult:
        """验证目标位置是否安全

        检查项:
          1. 高度范围（NED: z为负=高于地面）
          2. 地理围栏
          3. 禁飞区
        """
        violations: list[str] = []
        level = "safe"
        corrected: dict[str, Any] = {}

        # --- 高度检查 ---
        altitude = abs(z)  # NED下高度取绝对值
        if z >= 0:
            # z >= 0 意味着在地面或地下
            violations.append(f"高度不合法: z={z:.2f}（NED坐标系下z应为负值表示高于地面）")
            level = "danger"
            corrected["z"] = -self.constraints.min_altitude
        elif altitude < self.constraints.min_altitude:
            violations.append(
                f"高度过低: |z|={altitude:.2f}m < 最小高度 {self.constraints.min_altitude}m"
            )
            level = "danger" if level != "danger" else level
            corrected["z"] = -self.constraints.min_altitude
        elif altitude > self.constraints.max_altitude:
            violations.append(
                f"高度过高: |z|={altitude:.2f}m > 最大高度 {self.constraints.max_altitude}m"
            )
            level = _worse_level(level, "warning")
            corrected["z"] = -self.constraints.max_altitude

        # --- 地理围栏检查 ---
        home_x, home_y = self.constraints.home_position
        dist_from_home = _distance_2d(x, y, home_x, home_y)
        if dist_from_home > self.constraints.max_distance_from_home:
            violations.append(
                f"超出地理围栏: 距离home {dist_from_home:.2f}m > "
                f"围栏半径 {self.constraints.max_distance_from_home}m"
            )
            level = _worse_level(level, "danger")
            # 修正: 沿home→目标方向夹紧到围栏边界
            if dist_from_home > 1e-6:
                scale = self.constraints.max_distance_from_home / dist_from_home
                corrected["x"] = home_x + (x - home_x) * scale
                corrected["y"] = home_y + (y - home_y) * scale
            else:
                corrected["x"] = home_x
                corrected["y"] = home_y

        # --- 禁飞区检查 ---
        for i, nfz in enumerate(self.constraints.no_fly_zones):
            nfz_x = nfz.get("x", 0.0)
            nfz_y = nfz.get("y", 0.0)
            nfz_r = nfz.get("radius", 0.0)
            if _point_in_circle(x, y, nfz_x, nfz_y, nfz_r):
                violations.append(
                    f"位于禁飞区 #{i}: 中心=({nfz_x}, {nfz_y}) 半径={nfz_r}m"
                )
                level = _worse_level(level, "danger")

        # 组装结果
        is_safe = level != "danger"
        corrected = corrected if corrected else None

        return ValidationResult(
            is_safe=is_safe,
            violations=violations,
            corrected=corrected,
            level=level,
        )

    def validate_velocity(self, vx: float, vy: float, vz: float) -> ValidationResult:
        """验证速度是否在安全范围内"""
        speed = math.sqrt(vx ** 2 + vy ** 2 + vz ** 2)

        if speed <= self.constraints.max_velocity:
            return ValidationResult(is_safe=True, violations=[], level="safe")

        # 超速: 按比例降速
        scale = self.constraints.max_velocity / speed
        corrected = {
            "vx": vx * scale,
            "vy": vy * scale,
            "vz": vz * scale,
        }
        violations = [
            f"超速: 当前速度 {speed:.2f}m/s > 最大速度 {self.constraints.max_velocity}m/s"
        ]

        return ValidationResult(
            is_safe=False,
            violations=violations,
            corrected=corrected,
            level="danger",
        )

    def validate_move(
        self,
        from_pos: tuple[float, float, float],
        to_pos: tuple[float, float, float],
        velocity: tuple[float, float, float] | None = None,
    ) -> ValidationResult:
        """验证完整移动操作

        检查: 起点位置、终点位置、速度、路径是否穿越禁飞区
        """
        all_violations: list[str] = []
        worst_level = "safe"
        corrected: dict[str, Any] = {}

        # 检查起点位置
        from_result = self.validate_position(*from_pos)
        if from_result.violations:
            all_violations.extend([f"起点: {v}" for v in from_result.violations])
            worst_level = _worse_level(worst_level, from_result.level)
            if from_result.corrected:
                corrected["from_x"] = from_result.corrected.get("x", from_pos[0])
                corrected["from_y"] = from_result.corrected.get("y", from_pos[1])
                corrected["from_z"] = from_result.corrected.get("z", from_pos[2])

        # 检查终点位置
        to_result = self.validate_position(*to_pos)
        if to_result.violations:
            all_violations.extend([f"终点: {v}" for v in to_result.violations])
            worst_level = _worse_level(worst_level, to_result.level)
            if to_result.corrected:
                corrected["to_x"] = to_result.corrected.get("x", to_pos[0])
                corrected["to_y"] = to_result.corrected.get("y", to_pos[1])
                corrected["to_z"] = to_result.corrected.get("z", to_pos[2])

        # 检查速度
        if velocity is not None:
            vel_result = self.validate_velocity(*velocity)
            if vel_result.violations:
                all_violations.extend([f"速度: {v}" for v in vel_result.violations])
                worst_level = _worse_level(worst_level, vel_result.level)
                if vel_result.corrected:
                    corrected.update({
                        "vx": vel_result.corrected["vx"],
                        "vy": vel_result.corrected["vy"],
                        "vz": vel_result.corrected["vz"],
                    })

        # 检查路径是否穿越禁飞区（仅检查水平面投影）
        for i, nfz in enumerate(self.constraints.no_fly_zones):
            nfz_x = nfz.get("x", 0.0)
            nfz_y = nfz.get("y", 0.0)
            nfz_r = nfz.get("radius", 0.0)
            if _segment_crosses_circle(
                from_pos[0], from_pos[1],
                to_pos[0], to_pos[1],
                nfz_x, nfz_y, nfz_r,
            ):
                all_violations.append(
                    f"路径穿越禁飞区 #{i}: 中心=({nfz_x}, {nfz_y}) 半径={nfz_r}m"
                )
                worst_level = _worse_level(worst_level, "danger")

        is_safe = worst_level != "danger"
        corrected = corrected if corrected else None

        return ValidationResult(
            is_safe=is_safe,
            violations=all_violations,
            corrected=corrected,
            level=worst_level,
        )

    # ------------------------------------------------------------------
    # 修正方法
    # ------------------------------------------------------------------

    def clamp_position(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """将位置夹紧到安全范围内

        返回夹紧后的 (x, y, z)。
        """
        # 高度夹紧
        if z >= 0:
            z = -self.constraints.min_altitude
        elif abs(z) < self.constraints.min_altitude:
            z = -self.constraints.min_altitude
        elif abs(z) > self.constraints.max_altitude:
            z = -self.constraints.max_altitude

        # 地理围栏夹紧
        home_x, home_y = self.constraints.home_position
        dist = _distance_2d(x, y, home_x, home_y)
        if dist > self.constraints.max_distance_from_home and dist > 1e-6:
            scale = self.constraints.max_distance_from_home / dist
            x = home_x + (x - home_x) * scale
            y = home_y + (y - home_y) * scale

        # 禁飞区: 如果在禁飞区内，推到最近的边界上
        for nfz in self.constraints.no_fly_zones:
            nfz_x = nfz.get("x", 0.0)
            nfz_y = nfz.get("y", 0.0)
            nfz_r = nfz.get("radius", 0.0)
            d = _distance_2d(x, y, nfz_x, nfz_y)
            if d < nfz_r and d > 1e-6:
                # 沿 nfz中心→当前位置 方向推到边界
                push_scale = nfz_r / d
                x = nfz_x + (x - nfz_x) * push_scale
                y = nfz_y + (y - nfz_y) * push_scale

        return (round(x, 4), round(y, 4), round(z, 4))

    def get_safe_move(
        self,
        from_pos: tuple[float, float, float],
        to_pos: tuple[float, float, float],
        velocity: tuple[float, float, float] | None = None,
    ) -> dict[str, Any]:
        """返回安全版本的移动参数

        对终点位置和速度进行夹紧/降速，确保移动安全。
        返回字典包含: from_pos, to_pos, velocity, was_corrected, corrections
        """
        # 先验证原始参数
        result = self.validate_move(from_pos, to_pos, velocity)

        # 夹紧终点位置
        safe_to = self.clamp_position(*to_pos)

        # 降速
        safe_vel = velocity
        if velocity is not None:
            speed = math.sqrt(velocity[0] ** 2 + velocity[1] ** 2 + velocity[2] ** 2)
            if speed > self.constraints.max_velocity and speed > 1e-6:
                scale = self.constraints.max_velocity / speed
                safe_vel = (
                    velocity[0] * scale,
                    velocity[1] * scale,
                    velocity[2] * scale,
                )

        # 检查夹紧后的路径是否仍穿越禁飞区（给出警告）
        path_violations: list[str] = []
        for i, nfz in enumerate(self.constraints.no_fly_zones):
            nfz_x = nfz.get("x", 0.0)
            nfz_y = nfz.get("y", 0.0)
            nfz_r = nfz.get("radius", 0.0)
            if _segment_crosses_circle(
                from_pos[0], from_pos[1],
                safe_to[0], safe_to[1],
                nfz_x, nfz_y, nfz_r,
            ):
                path_violations.append(
                    f"夹紧后路径仍穿越禁飞区 #{i}，建议绕行"
                )

        was_corrected = safe_to != tuple(round(v, 4) for v in to_pos) or safe_vel != velocity

        return {
            "from_pos": from_pos,
            "to_pos": safe_to,
            "velocity": safe_vel,
            "was_corrected": was_corrected,
            "original_result": result,
            "path_warnings": path_violations,
        }


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _worse_level(current: str, new: str) -> str:
    """取两个严重等级中更严重的

    优先级: danger > warning > safe
    """
    order = {"safe": 0, "warning": 1, "danger": 2}
    return new if order.get(new, 0) > order.get(current, 0) else current


# ------------------------------------------------------------------
# 装饰器: validate_and_execute
# ------------------------------------------------------------------

def validate_and_execute(
    pos_arg_index: int | None = None,
    vel_arg_index: int | None = None,
    validator_attr: str = "safety_validator",
) -> Callable:
    """飞行指令安全验证装饰器

    在执行飞行指令前自动验证参数安全性:
      - danger: 阻止执行，返回错误信息
      - warning: 记录警告，使用夹紧值继续执行
      - safe: 正常执行

    Args:
        pos_arg_index: 位置参数在被装饰方法参数列表中的索引
                       期望值为 (x, y, z) 元组
        vel_arg_index: 速度参数在被装饰方法参数列表中的索引
                       期望值为 (vx, vy, vz) 元组
        validator_attr: 控制器实例上 SafetyValidator 属性的名称

    用法:
        class MyController:
            safety_validator = SafetyValidator()

            @validate_and_execute(pos_arg_index=0)
            def move_to_position(self, pos, velocity=None):
                ...  # 实际飞行逻辑
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # 获取验证器实例
            validator: SafetyValidator | None = getattr(self, validator_attr, None)
            if validator is None:
                # 没有验证器，直接执行
                return func(self, *args, **kwargs)

            # 提取位置和速度参数
            pos = args[pos_arg_index] if pos_arg_index is not None and pos_arg_index < len(args) else None
            vel = args[vel_arg_index] if vel_arg_index is not None and vel_arg_index < len(args) else None

            # 如果位置是 (x, y, z) 元组形式
            if pos is not None and isinstance(pos, (list, tuple)) and len(pos) >= 3:
                result = validator.validate_position(pos[0], pos[1], pos[2])
            else:
                result = None

            # 验证速度
            vel_result = None
            if vel is not None and isinstance(vel, (list, tuple)) and len(vel) >= 3:
                vel_result = validator.validate_velocity(vel[0], vel[1], vel[2])

            # 综合判断
            if result is not None and result.level == "danger":
                logger.warning(
                    "flight_blocked",
                    violations=result.violations,
                    corrected=result.corrected,
                )
                return {
                    "success": False,
                    "error": "飞行指令被安全验证拦截",
                    "violations": result.violations,
                    "corrected": result.corrected,
                }

            if vel_result is not None and vel_result.level == "danger":
                logger.warning(
                    "velocity_blocked",
                    violations=vel_result.violations,
                    corrected=vel_result.corrected,
                )
                return {
                    "success": False,
                    "error": "速度超出安全限制",
                    "violations": vel_result.violations,
                    "corrected": vel_result.corrected,
                }

            # warning: 记录但继续（使用修正值）
            if result is not None and result.level == "warning":
                logger.warning(
                    "flight_warning",
                    violations=result.violations,
                    corrected=result.corrected,
                )
                # 用夹紧值替换参数
                if result.corrected and pos_arg_index is not None:
                    new_pos = (
                        result.corrected.get("x", pos[0]),
                        result.corrected.get("y", pos[1]),
                        result.corrected.get("z", pos[2]),
                    )
                    args = list(args)
                    args[pos_arg_index] = new_pos
                    args = tuple(args)

            if vel_result is not None and vel_result.level == "warning":
                logger.warning(
                    "velocity_warning",
                    violations=vel_result.violations,
                    corrected=vel_result.corrected,
                )

            # 安全: 正常执行
            return func(self, *args, **kwargs)

        return wrapper

    return decorator
