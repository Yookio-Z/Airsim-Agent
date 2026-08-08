"""
PerceptionHub - 持续感知中枢

核心理念: YOLO 不是"用的时候才开"，而是持续运行，维护世界模型。
视觉和控制是合作关系: 控制为了更好的视觉，视觉需要控制拿更好的信息。

架构:
  ┌────────────────────────────────────────────────────────┐
  │  PerceptionHub (持续运行)                               │
  │                                                        │
  │  VideoStream ──→ YOLO检测 ──→ WorldModel               │
  │       ↑              ↑              ↓                   │
  │       │              │         可见目标列表              │
  │       │              │         3D位置/深度               │
  │       │              │         丢失/恢复事件             │
  │       │              │                                   │
  │  ┌────┴──────────────┴──────────────┐                  │
  │  │  Vision-Control Coordinator       │                  │
  │  │  视觉请求控制 → 控制服务视觉       │                  │
  │  │  目标偏了 → 修正航向              │                  │
  │  │  目标丢了 → 旋转搜索              │                  │
  │  │  需要更近 → 前进靠近              │                  │
  │  └──────────────────────────────────┘                  │
  │                                                        │
  │  关键事件 → 通知 LLM (不频繁，只在决策点)               │
  └────────────────────────────────────────────────────────┘

LLM 角色:
  - 知道一切 (通过 WorldModel 快照)
  - 不频繁介入 (只在关键事件时决策)
  - 战略决策: 确认目标、改变策略、环绕侦察、降落等
  - 不做战术操作: 不手动 fly_to、不手动 rotate、不手动 detect
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable

import cv2
import numpy as np

from src.modules.video_stream import AirSimVideoStream
from src.modules.visual_servoing import VisualServoingController
from src.logging_config import get_logger

logger = get_logger(__name__)


# ======================================================================
# 世界模型 - 持续感知维护的状态
# ======================================================================

class TargetStatus(Enum):
    """目标状态"""
    NOT_SEEN = auto()      # 从未见过
    VISIBLE = auto()       # 当前可见
    RECENTLY_LOST = auto() # 刚丢失 (<3秒)
    LOST = auto()          # 丢失较久


@dataclass
class TrackedObject:
    """被跟踪的目标"""
    class_name: str = ""
    confidence: float = 0.0
    bbox: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    center_px: tuple[float, float] = (0.0, 0.0)
    offset_x: float = 0.0  # 归一化偏移 -1~1
    offset_y: float = 0.0
    world_pos: Optional[dict] = None
    depth_meters: float = 0.0
    distance_meters: float = 0.0
    status: TargetStatus = TargetStatus.NOT_SEEN
    first_seen_time: float = 0.0
    last_seen_time: float = 0.0
    visible_frames: int = 0
    lost_frames: int = 0


@dataclass
class WorldModel:
    """世界模型: 无人机当前感知到的所有信息。

    由 PerceptionHub 持续更新，任何时刻都可以获取快照。
    LLM 通过快照了解"发生了什么"，不需要频繁调用工具。
    """
    # 当前可见目标
    targets: list[TrackedObject] = field(default_factory=list)

    # 主要跟踪目标 (置信度最高的)
    primary_target: Optional[TrackedObject] = None

    # 所有检测到的物体 (包括非目标)
    all_detections: list[dict] = field(default_factory=list)

    # 无人机状态
    drone_position: dict = field(default_factory=lambda: {"x": 0, "y": 0, "z": 0})
    drone_heading: float = 0.0
    drone_velocity: dict = field(default_factory=lambda: {"vx": 0, "vy": 0, "vz": 0})

    # 统计
    total_frames: int = 0
    yolo_fps: float = 0.0
    uptime_seconds: float = 0.0

    # 事件队列 (关键事件，供 LLM 消费)
    events: list[dict] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k) and k != "_lock":
                    setattr(self, k, v)

    def add_event(self, event_type: str, data: dict = None) -> None:
        """添加关键事件 (目标发现/丢失/恢复等)。"""
        with self._lock:
            self.events.append({
                "type": event_type,
                "time": time.time(),
                "data": data or {},
            })
            # 只保留最近 50 个事件
            if len(self.events) > 50:
                self.events = self.events[-50:]

    def snapshot(self) -> dict:
        """线程安全读取快照。"""
        with self._lock:
            return {
                "targets": [
                    {
                        "class": t.class_name,
                        "confidence": t.confidence,
                        "status": t.status.name,
                        "offset": (round(t.offset_x, 3), round(t.offset_y, 3)),
                        "world_pos": dict(t.world_pos) if t.world_pos else None,
                        "depth_m": round(t.depth_meters, 2),
                        "distance_m": round(t.distance_meters, 2),
                        "visible_frames": t.visible_frames,
                        "lost_frames": t.lost_frames,
                    }
                    for t in self.targets
                ],
                "primary_target": {
                    "class": self.primary_target.class_name,
                    "confidence": self.primary_target.confidence,
                    "status": self.primary_target.status.name,
                    "offset": (round(self.primary_target.offset_x, 3), round(self.primary_target.offset_y, 3)),
                    "world_pos": dict(self.primary_target.world_pos) if self.primary_target and self.primary_target.world_pos else None,
                    "depth_m": round(self.primary_target.depth_meters, 2),
                    "distance_m": round(self.primary_target.distance_meters, 2),
                } if self.primary_target else None,
                "all_detections": [
                    {"class": d.get("class", ""), "confidence": d.get("confidence", 0)}
                    for d in self.all_detections
                ],
                "drone_position": dict(self.drone_position),
                "drone_heading": round(self.drone_heading, 1),
                "total_frames": self.total_frames,
                "yolo_fps": round(self.yolo_fps, 1),
                "recent_events": list(self.events[-10:]),
            }

    def pop_events(self) -> list[dict]:
        """消费所有未读事件 (LLM 读取后清空)。"""
        with self._lock:
            events = list(self.events)
            self.events.clear()
            return events


# ======================================================================
# 视觉-控制协调策略
# ======================================================================

class VisionControlPolicy:
    """视觉-控制协调策略: 控制为了更好的视觉，视觉需要控制拿更好的信息。

    根据当前目标状态决定控制指令:
      - 目标居中且可见 → 前进靠近 (控制服务视觉: 靠近获取更清晰图像)
      - 目标偏左/偏右 → 侧向修正 (视觉请求控制: 让目标居中)
      - 目标偏上/偏下 → 高度修正 (视觉请求控制: 调整视角)
      - 目标丢失 → 旋转搜索 (视觉请求控制: 重新获取视觉信息)
    """

    def __init__(self, max_speed: float = 2.0) -> None:
        self._servo = VisualServoingController(
            x_params=VisualServoingController.OUTDOOR,
            y_params=VisualServoingController.OUTDOOR,
            max_velocity=max_speed,
        )
        self._max_speed = max_speed

    def compute_control(
        self,
        target: Optional[TrackedObject],
        drone_heading: float,
        approach_distance: float = 5.0,
    ) -> dict:
        """根据目标状态计算控制指令。

        Returns:
            {
                "action": "approach"|"correct"|"search"|"hover",
                "vx": float, "vy": float, "vz": float,
                "reason": str,
            }
        """
        if target is None or target.status == TargetStatus.NOT_SEEN:
            return {
                "action": "search",
                "vx": 0, "vy": 0, "vz": 0,
                "reason": "无目标，需要搜索",
            }

        if target.status in (TargetStatus.LOST, TargetStatus.RECENTLY_LOST):
            return {
                "action": "search",
                "vx": 0, "vy": 0, "vz": 0,
                "reason": f"目标丢失({target.status.name})，需要搜索恢复",
            }

        # 目标可见: 视觉伺服
        if target.status == TargetStatus.VISIBLE and target.visible_frames > 0:
            # 已到达指定距离
            if target.distance_meters > 0 and target.distance_meters <= approach_distance:
                return {
                    "action": "hover",
                    "vx": 0, "vy": 0, "vz": 0,
                    "reason": f"已到达 {target.distance_meters:.1f}m (要求 {approach_distance}m)",
                }

            # PID 视觉伺服
            # 注意: 这里需要图像尺寸来计算 PID
            # 简化: 直接用 offset 计算速度
            offset_mag = (target.offset_x ** 2 + target.offset_y ** 2) ** 0.5

            # 侧向修正 (让目标居中)
            vy_correct = target.offset_x * self._max_speed * 0.5
            # 高度修正
            vz_correct = -target.offset_y * self._max_speed * 0.3

            # 前进分量: 目标越居中，前进速度越大
            vx_forward = 0.0
            if offset_mag < 0.3:
                vx_forward = self._max_speed * (1.0 - offset_mag / 0.3) * 0.8

            # 转换到 NED 坐标系 (考虑航向)
            heading_rad = np.radians(drone_heading)
            cos_h = np.cos(heading_rad)
            sin_h = np.sin(heading_rad)

            vx_ned = vx_forward * cos_h - vy_correct * sin_h
            vy_ned = vx_forward * sin_h + vy_correct * cos_h

            # 限速
            speed = (vx_ned ** 2 + vy_ned ** 2) ** 0.5
            if speed > self._max_speed:
                scale = self._max_speed / speed
                vx_ned *= scale
                vy_ned *= scale

            action = "approach" if vx_forward > 0.3 else "correct"
            reason = (
                f"偏移=({target.offset_x:.2f},{target.offset_y:.2f}), "
                f"距离={target.distance_meters:.1f}m"
            )

            return {
                "action": action,
                "vx": round(vx_ned, 3),
                "vy": round(vy_ned, 3),
                "vz": round(vz_correct, 3),
                "reason": reason,
            }

        return {
            "action": "hover",
            "vx": 0, "vy": 0, "vz": 0,
            "reason": "等待目标状态更新",
        }


# ======================================================================
# PerceptionHub - 持续感知中枢
# ======================================================================

class PerceptionHub:
    """持续感知中枢: YOLO 一直开启，维护世界模型，协调视觉和控制。

    使用方式:
      hub = PerceptionHub(controller, vehicle_name, camera_name, "car")
      hub.start()  # 启动持续感知

      # 任何时刻获取世界模型快照
      world = hub.get_world_snapshot()
      # LLM 可以了解: 目标可见? 偏移多少? 距离多少? 丢了多久?

      # 获取控制指令 (视觉-控制协调)
      control = hub.get_control_command(approach_distance=5.0)
      # action: approach/correct/search/hover

      # 消费关键事件 (LLM 决策点)
      events = hub.pop_events()
      # target_found, target_lost, target_recovered, approach_complete

      hub.stop()
    """

    def __init__(
        self,
        controller,
        vehicle_name: str,
        camera_name: str,
        target_class: str,
        confidence: float = 0.25,
        max_speed: float = 2.0,
    ) -> None:
        self._controller = controller
        self._vehicle_name = vehicle_name
        self._camera_name = camera_name
        self._target_class = target_class
        self._confidence = confidence

        # 世界模型
        self._world = WorldModel()

        # 视觉-控制协调策略
        self._policy = VisionControlPolicy(max_speed=max_speed)

        # 视频流
        self._stream: Optional[AirSimVideoStream] = None

        # YOLO 模型 (延迟加载)
        self._model = None
        self._model_classes = None

        # 运行状态
        self._running = False
        self._vision_thread: Optional[threading.Thread] = None
        self._start_time = 0.0

        # 上次目标状态 (用于检测状态变化 → 生成事件)
        self._prev_target_status: TargetStatus = TargetStatus.NOT_SEEN
        self._prev_target_class: str = ""

    # ── 公共接口 ──────────────────────────────────────────────────────

    def get_world_snapshot(self) -> dict:
        """获取世界模型快照 (LLM 通过此接口了解一切)。"""
        snap = self._world.snapshot()
        snap["uptime_seconds"] = round(time.time() - self._start_time, 1)
        return snap

    def get_control_command(self, approach_distance: float = 5.0) -> dict:
        """获取视觉-控制协调指令 (控制服务视觉，视觉请求控制)。"""
        return self._policy.compute_control(
            target=self._world.primary_target,
            drone_heading=self._world.drone_heading,
            approach_distance=approach_distance,
        )

    def pop_events(self) -> list[dict]:
        """消费关键事件 (LLM 决策点: 目标发现/丢失/恢复/到达)。"""
        return self._world.pop_events()

    def is_target_visible(self) -> bool:
        return (self._world.primary_target is not None and
                self._world.primary_target.status == TargetStatus.VISIBLE)

    def get_target_distance(self) -> float:
        if self._world.primary_target:
            return self._world.primary_target.distance_meters
        return float("inf")

    # ── 生命周期 ──────────────────────────────────────────────────────

    def start(self) -> None:
        """启动持续感知。"""
        if self._running:
            return

        # 加载 YOLO 模型
        from src.modules.yolo_detection import build_search_classes, get_yolo_model
        self._model_classes = build_search_classes(self._target_class) if self._target_class else ["car", "person", "truck", "bus"]
        self._model = get_yolo_model(self._model_classes)

        # 启动视频流
        self._stream = AirSimVideoStream(
            client=self._controller.client,
            camera_name=self._camera_name,
            vehicle_name=self._vehicle_name,
            fps=5.0,
            timeout_sec=5.0,
        )
        self._stream.start()
        time.sleep(1.0)

        # 启动视觉线程
        self._running = True
        self._start_time = time.time()
        self._vision_thread = threading.Thread(target=self._vision_loop, daemon=True)
        self._vision_thread.start()

        logger.info(f"PerceptionHub: 启动 target={self._target_class}")

    def stop(self) -> None:
        """停止持续感知。"""
        self._running = False

        if self._vision_thread and self._vision_thread.is_alive():
            self._vision_thread.join(timeout=3.0)

        if self._stream:
            self._stream.stop()
            self._stream = None

        logger.info("PerceptionHub: 停止")

    # ── 视觉线程 (持续运行) ──────────────────────────────────────────

    def _vision_loop(self) -> None:
        """后台视觉线程: 持续 YOLO 检测，更新世界模型。"""
        from src.modules.yolo_detection import project_detections_to_3d, run_yolo_detection

        frame_count = 0

        while self._running:
            try:
                frame = self._stream.get_latest_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue

                h, w = frame.shape[:2]
                img_cx, img_cy = w / 2, h / 2
                frame_count += 1

                # ── YOLO 检测 ──
                target_dets = run_yolo_detection(
                    self._model, frame, self._target_class, self._confidence
                )
                all_dets = run_yolo_detection(
                    self._model, frame, "", self._confidence
                )

                # ── 更新无人机状态 ──
                try:
                    status = self._controller.get_status(self._vehicle_name)
                    self._world.update(
                        drone_position=status.position_ned,
                        drone_heading=self._controller.get_heading(self._vehicle_name),
                    )
                except Exception:
                    pass

                # ── 更新目标状态 ──
                if target_dets:
                    best = max(target_dets, key=lambda d: d["confidence"])
                    cx, cy = best["center"]

                    # 2D→3D 投影 (每5帧做一次，减少RPC调用)
                    world_pos = None
                    depth_m = 0.0
                    dist_m = 0.0
                    if frame_count % 5 == 0:
                        try:
                            dets_3d = project_detections_to_3d(
                                [best], self._controller, self._camera_name, self._vehicle_name,
                            )
                            if dets_3d and dets_3d[0].get("world_3d", {}).get("valid"):
                                world_pos = dets_3d[0]["world_3d"]["world_pos"]
                                depth_m = dets_3d[0]["world_3d"]["depth_meters"]
                                dist_m = dets_3d[0]["world_3d"]["distance_to_drone"]
                        except Exception:
                            pass

                    # 构建跟踪目标
                    tracked = TrackedObject(
                        class_name=best["class"],
                        confidence=best["confidence"],
                        bbox=best["bbox"],
                        center_px=(cx, cy),
                        offset_x=(cx - img_cx) / img_cx,
                        offset_y=(cy - img_cy) / img_cy,
                        world_pos=world_pos,
                        depth_meters=depth_m,
                        distance_meters=dist_m,
                        status=TargetStatus.VISIBLE,
                        visible_frames=self._world.primary_target.visible_frames + 1 if self._world.primary_target else 1,
                        lost_frames=0,
                    )
                    if tracked.visible_frames == 1:
                        tracked.first_seen_time = time.time()
                    tracked.last_seen_time = time.time()

                    # 检测状态变化 → 生成事件
                    prev = self._prev_target_status
                    if prev in (TargetStatus.NOT_SEEN, TargetStatus.LOST, TargetStatus.RECENTLY_LOST):
                        if prev == TargetStatus.NOT_SEEN:
                            self._world.add_event("target_found", {
                                "class": best["class"],
                                "confidence": best["confidence"],
                                "offset": (round(tracked.offset_x, 3), round(tracked.offset_y, 3)),
                            })
                        else:
                            self._world.add_event("target_recovered", {
                                "lost_frames": self._world.primary_target.lost_frames if self._world.primary_target else 0,
                            })

                    self._prev_target_status = TargetStatus.VISIBLE
                    self._prev_target_class = best["class"]

                    self._world.update(
                        targets=[tracked],
                        primary_target=tracked,
                        all_detections=all_dets,
                        total_frames=frame_count,
                        yolo_fps=frame_count / max(time.time() - self._start_time, 0.1),
                    )

                else:
                    # 目标不可见
                    if self._world.primary_target and self._world.primary_target.status == TargetStatus.VISIBLE:
                        # 刚丢失
                        lost_target = TrackedObject(
                            class_name=self._world.primary_target.class_name,
                            status=TargetStatus.RECENTLY_LOST,
                            visible_frames=0,
                            lost_frames=1,
                            world_pos=self._world.primary_target.world_pos,
                            depth_meters=self._world.primary_target.depth_meters,
                            distance_meters=self._world.primary_target.distance_meters,
                            last_seen_time=time.time(),
                        )
                        self._world.update(primary_target=lost_target)
                        self._world.add_event("target_lost", {"reason": "YOLO未检测到"})
                        self._prev_target_status = TargetStatus.RECENTLY_LOST
                    elif self._world.primary_target:
                        # 持续丢失
                        self._world.primary_target.lost_frames += 1
                        if self._world.primary_target.lost_frames > 15:
                            self._world.primary_target.status = TargetStatus.LOST

                    self._world.update(
                        targets=[],
                        all_detections=all_dets,
                        total_frames=frame_count,
                        yolo_fps=frame_count / max(time.time() - self._start_time, 0.1),
                    )

            except Exception as e:
                logger.warning(f"PerceptionHub 视觉线程异常: {e}")
                time.sleep(0.2)

    # ── 执行控制指令 ──────────────────────────────────────────────────

    def execute_control(self, approach_distance: float = 5.0) -> dict:
        """获取并执行视觉-控制协调指令。

        这是"控制服务视觉，视觉请求控制"的核心:
        - 目标偏了 → 控制修正航向 (视觉请求控制)
        - 目标居中 → 控制前进靠近 (控制服务视觉: 拿到更清晰图像)
        - 目标丢了 → 控制旋转搜索 (视觉请求控制: 重新获取信息)

        Returns:
            控制执行结果
        """
        cmd = self.get_control_command(approach_distance)

        if cmd["action"] == "hover":
            try:
                self._controller.move_by_velocity(0, 0, 0, duration=0.1, vehicle_name=self._vehicle_name)
            except Exception:
                pass
        elif cmd["action"] == "search":
            # 旋转搜索
            try:
                current_yaw = self._controller.get_heading(self._vehicle_name)
                new_yaw = (current_yaw + 30.0) % 360.0
                self._controller.rotate_to_heading(new_yaw)
            except Exception:
                pass
        elif cmd["action"] in ("approach", "correct"):
            try:
                self._controller.move_by_velocity(
                    cmd["vx"], cmd["vy"], cmd["vz"],
                    duration=0.2, vehicle_name=self._vehicle_name,
                )
            except Exception:
                pass

        return cmd
