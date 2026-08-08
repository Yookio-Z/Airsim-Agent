"""
WorldState - 统一态势感知

PolicyEngine 每 tick 依赖的唯一输入源。
聚合来自飞控、视觉、传感器、环境的所有信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import time


@dataclass
class SelfState:
    """本机状态"""
    position_ned: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    velocity_ned: dict[str, float] = field(default_factory=lambda: {"vx": 0.0, "vy": 0.0, "vz": 0.0})
    attitude_rad: dict[str, float] = field(default_factory=lambda: {"roll": 0.0, "pitch": 0.0, "yaw": 0.0})
    heading_deg: float = 0.0
    
    armed: bool = False
    flying: bool = False
    flight_mode: str = ""
    
    battery_pct: float = 100.0
    battery_voltage: float = 0.0
    
    gps_status: str = "good"            # "good" | "weak" | "jammed" | "lost"
    rc_link_status: str = "connected"   # "connected" | "weak" | "lost"
    
    api_control_enabled: bool = False
    
    # 轨迹历史（用于预测和回溯）
    position_history: list[dict[str, float]] = field(default_factory=list, repr=False)
    velocity_history: list[dict[str, float]] = field(default_factory=list, repr=False)
    
    def predict_position(self, dt: float) -> dict[str, float]:
        """基于当前速度预测 dt 秒后的位置"""
        return {
            "x": self.position_ned["x"] + self.velocity_ned["vx"] * dt,
            "y": self.position_ned["y"] + self.velocity_ned["vy"] * dt,
            "z": self.position_ned["z"] + self.velocity_ned["vz"] * dt,
        }


@dataclass
class Detection:
    """视觉检测结果"""
    class_name: str = ""
    confidence: float = 0.0
    bbox: list[float] = field(default_factory=list)       # [x1, y1, x2, y2]
    center_px: list[float] = field(default_factory=list)   # [cx, cy]
    
    # 3D 信息（如果有深度图/点云）
    depth_meters: Optional[float] = None
    world_position: Optional[dict[str, float]] = None      # NED
    distance_to_drone: Optional[float] = None
    
    # 时序信息
    timestamp: float = field(default_factory=time.time)
    frame_id: int = 0
    
    # 跟踪信息
    track_id: Optional[int] = None      # 多目标跟踪 ID
    velocity_estimate: Optional[dict[str, float]] = None   # 基于时序估算的目标速度


@dataclass
class TargetState:
    """目标状态（融合多帧检测结果）"""
    detections: list[Detection] = field(default_factory=list)
    
    # 融合后的最佳估计
    best_class: str = ""
    best_confidence: float = 0.0
    estimated_position: Optional[dict[str, float]] = None
    estimated_velocity: Optional[dict[str, float]] = None
    
    # 跟踪状态
    tracking: bool = False
    lost_time: float = 0.0              # 丢失目标持续的秒数
    visible: bool = False
    
    # 历史
    position_history: list[dict[str, float]] = field(default_factory=list, repr=False)
    
    def predict_position(self, dt: float) -> Optional[dict[str, float]]:
        if self.estimated_position and self.estimated_velocity:
            return {
                "x": self.estimated_position["x"] + self.estimated_velocity.get("vx", 0) * dt,
                "y": self.estimated_position["y"] + self.estimated_velocity.get("vy", 0) * dt,
                "z": self.estimated_position["z"] + self.estimated_velocity.get("vz", 0) * dt,
            }
        return self.estimated_position


@dataclass
class EnvironmentState:
    """环境状态"""
    wind_vector: dict[str, float] = field(default_factory=lambda: {"vx": 0.0, "vy": 0.0, "vz": 0.0})
    wind_speed_ms: float = 0.0
    
    obstacles: list[dict[str, Any]] = field(default_factory=list)  # [{"position": {...}, "distance": 5.0, "type": "tree"}]
    nearest_obstacle_distance: float = float("inf")
    
    terrain_height_m: float = 0.0       # 地面高度（用于地形跟随/避障）
    
    # 人群/禁飞区动态检测
    detected_human_crowd: bool = False
    active_no_fly_zone: Optional[dict[str, Any]] = None
    
    # 通信环境
    communication_jammed: bool = False


@dataclass
class WorldState:
    """统一世界状态：PolicyEngine 的唯一输入"""
    timestamp: float = field(default_factory=time.time)
    tick_count: int = 0
    
    self_state: SelfState = field(default_factory=SelfState)
    target_state: TargetState = field(default_factory=TargetState)
    environment: EnvironmentState = field(default_factory=EnvironmentState)
    
    # 原始检测结果（未融合）
    raw_detections: list[Detection] = field(default_factory=list)
    
    # 任务进度
    mission_elapsed_seconds: float = 0.0
    search_coverage: float = 0.0        # 搜索覆盖率 0-1
    current_behavior: str = "idle"      # 当前正在执行的行为标签
    
    # 信念图（概率空间表示）
    belief_map: Optional[Any] = None    # 可接入你的 BeliefGrid
    
    # 异常标记
    anomalies: list[str] = field(default_factory=list)  # 当前活跃的异常标签
    
    # 快速查询方法
    def get(self, key: str, default: Any = None) -> Any:
        """支持条件表达式的快速取值"""
        flat = self._flatten()
        return flat.get(key, default)
    
    def _flatten(self) -> dict[str, Any]:
        """展平为单层字典，用于条件表达式求值"""
        s = self.self_state
        t = self.target_state
        e = self.environment
        return {
            "battery": s.battery_pct,
            "battery_voltage": s.battery_voltage,
            "altitude": abs(s.position_ned.get("z", 0)),
            "x": s.position_ned.get("x", 0),
            "y": s.position_ned.get("y", 0),
            "z": s.position_ned.get("z", 0),
            "vx": s.velocity_ned.get("vx", 0),
            "vy": s.velocity_ned.get("vy", 0),
            "vz": s.velocity_ned.get("vz", 0),
            "heading": s.heading_deg,
            "armed": s.armed,
            "flying": s.flying,
            "gps_status": s.gps_status,
            "rc_link_status": s.rc_link_status,
            "wind_speed": e.wind_speed_ms,
            "obstacle_distance": e.nearest_obstacle_distance,
            "target_detected": t.visible and t.best_confidence > 0.3,
            "target_confidence": t.best_confidence,
            "target_lost": t.lost_time,
            "tracking": t.tracking,
            "detected_human_crowd": e.detected_human_crowd,
            "communication_jammed": e.communication_jammed,
            "mission_elapsed": self.mission_elapsed_seconds,
            "search_coverage": self.search_coverage,
            "current_behavior": self.current_behavior,
        }
