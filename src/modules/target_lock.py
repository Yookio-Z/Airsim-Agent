"""
TargetLock - 目标锁定模块

核心设计: 视觉与控制并行，移动中不丢失视觉，视觉中不忽略移动。

架构:
  ┌─────────────────────────────────────────────────────┐
  │  视觉线程 (VisionThread)                            │
  │  VideoStream → YOLO检测 → 更新TargetState          │
  │  持续运行，每帧检测，实时更新目标位置/可见性/偏差    │
  └──────────────────────┬──────────────────────────────┘
                         │ TargetState (线程安全)
  ┌──────────────────────▼──────────────────────────────┐
  │  控制线程 (ControlThread)                            │
  │  读取TargetState → PID视觉伺服 → 发送速度指令       │
  │  目标可见: 视觉伺服靠近                              │
  │  目标丢失: 悬停等待恢复 / 旋转搜索恢复              │
  └─────────────────────────────────────────────────────┘

参考:
  - PEACE WorldModelService: 线程安全状态聚合
  - dimOS DroneVisualServoingController: PID视觉伺服
  - AirSimVideoStream: 后台取帧 + 回调机制
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from src.modules.video_stream import AirSimVideoStream
from src.modules.visual_servoing import VisualServoingController
from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TargetState:
    """目标锁定状态（线程安全，由视觉线程写入，控制线程读取）。

    这是视觉线程和控制线程之间的共享状态。
    视觉线程每帧更新，控制线程每步读取。
    """
    # 目标可见性
    visible: bool = False
    confidence: float = 0.0
    detection_class: str = ""

    # 目标在画面中的位置 (像素)
    bbox: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    center_px: tuple[float, float] = (0.0, 0.0)  # (cx, cy)

    # 图像尺寸
    image_size: tuple[int, int] = (0, 0)  # (w, h)

    # 像素偏差 (归一化 -1~1, 相对图像中心)
    offset_x: float = 0.0  # 正=目标偏右
    offset_y: float = 0.0  # 正=目标偏下

    # 3D 世界坐标 (NED, 来自深度投影)
    world_pos: Optional[dict] = None  # {"x": ..., "y": ..., "z": ...}
    depth_meters: float = 0.0
    distance_meters: float = 0.0

    # 统计
    frame_count: int = 0
    lost_count: int = 0  # 连续丢失帧数
    last_update_time: float = 0.0

    # 锁
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **kwargs) -> None:
        """线程安全更新状态。"""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k) and k != "_lock":
                    setattr(self, k, v)
            self.last_update_time = time.time()

    def snapshot(self) -> dict:
        """线程安全读取快照。"""
        with self._lock:
            return {
                "visible": self.visible,
                "confidence": self.confidence,
                "detection_class": self.detection_class,
                "bbox": list(self.bbox),
                "center_px": self.center_px,
                "image_size": self.image_size,
                "offset_x": self.offset_x,
                "offset_y": self.offset_y,
                "world_pos": dict(self.world_pos) if self.world_pos else None,
                "depth_meters": self.depth_meters,
                "distance_meters": self.distance_meters,
                "frame_count": self.frame_count,
                "lost_count": self.lost_count,
                "last_update_time": self.last_update_time,
            }


class TargetLock:
    """目标锁定器: 视觉与控制并行。

    核心思想:
      1. 视觉线程持续运行YOLO检测，维护TargetState
      2. 控制循环读取TargetState，用PID视觉伺服发送速度指令
      3. 移动中不丢失视觉，视觉中不忽略移动

    使用方式:
      lock = TargetLock(controller, vehicle_name, camera_name, "car")
      lock.start()  # 启动视觉线程

      # 控制循环 (由调用方驱动)
      while not arrived:
          state = lock.get_state()
          if state["visible"]:
              lock.approach_step(approach_distance=5.0)  # 视觉伺服靠近一步
          else:
              lock.search_step()  # 旋转搜索恢复
          time.sleep(0.1)

      lock.stop()  # 停止视觉线程
    """

    def __init__(
        self,
        controller,
        vehicle_name: str,
        camera_name: str,
        target_class: str,
        confidence: float = 0.25,
        servoing_params: str = "outdoor",
    ) -> None:
        self._controller = controller
        self._vehicle_name = vehicle_name
        self._camera_name = camera_name
        self._target_class = target_class
        self._confidence = confidence

        # 视觉伺服 PID 控制器
        if servoing_params == "indoor":
            self._servo = VisualServoingController(
                x_params=VisualServoingController.INDOOR,
                y_params=VisualServoingController.INDOOR,
                max_velocity=1.0,
            )
        else:
            self._servo = VisualServoingController(
                x_params=VisualServoingController.OUTDOOR,
                y_params=VisualServoingController.OUTDOOR,
                max_velocity=3.0,
            )

        # 共享状态
        self._state = TargetState()

        # 视频流
        self._stream: Optional[AirSimVideoStream] = None

        # YOLO 模型 (延迟加载)
        self._model = None
        self._model_classes = None

        # 运行状态
        self._running = False
        self._vision_thread: Optional[threading.Thread] = None

    @property
    def state(self) -> TargetState:
        return self._state

    def get_state(self) -> dict:
        """获取当前目标状态快照（线程安全）。"""
        return self._state.snapshot()

    def is_target_visible(self) -> bool:
        """目标是否可见。"""
        return self._state.visible

    def get_lost_count(self) -> int:
        """连续丢失帧数。"""
        return self._state.lost_count

    # ── 生命周期 ──────────────────────────────────────────────────────

    def start(self) -> None:
        """启动视觉线程。"""
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
        self._vision_thread = threading.Thread(target=self._vision_loop, daemon=True)
        self._vision_thread.start()

        logger.info(f"TargetLock: 启动 target={self._target_class}")

    def stop(self) -> None:
        """停止视觉线程和视频流。"""
        self._running = False

        if self._vision_thread and self._vision_thread.is_alive():
            self._vision_thread.join(timeout=3.0)

        if self._stream:
            self._stream.stop()
            self._stream = None

        # 停止移动
        try:
            self._controller.move_by_velocity(0, 0, 0, duration=0.1, vehicle_name=self._vehicle_name)
        except Exception:
            pass

        logger.info("TargetLock: 停止")

    # ── 视觉线程 ──────────────────────────────────────────────────────

    def _vision_loop(self) -> None:
        """后台视觉线程: 持续 YOLO 检测，更新 TargetState。"""
        from src.modules.yolo_detection import run_yolo_detection

        while self._running:
            try:
                frame = self._stream.get_latest_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue

                h, w = frame.shape[:2]
                img_cx, img_cy = w / 2, h / 2

                # YOLO 检测
                detections = run_yolo_detection(
                    self._model, frame, self._target_class, self._confidence
                )

                if detections:
                    # 取置信度最高的检测
                    best = max(detections, key=lambda d: d["confidence"])
                    cx, cy = best["center"]

                    self._state.update(
                        visible=True,
                        confidence=best["confidence"],
                        detection_class=best["class"],
                        bbox=best["bbox"],
                        center_px=(cx, cy),
                        image_size=(w, h),
                        offset_x=(cx - img_cx) / img_cx,  # 归一化 -1~1
                        offset_y=(cy - img_cy) / img_cy,
                        frame_count=self._state.frame_count + 1,
                        lost_count=0,
                    )
                else:
                    # 目标丢失
                    self._state.update(
                        visible=False,
                        frame_count=self._state.frame_count + 1,
                        lost_count=self._state.lost_count + 1,
                    )

            except Exception as e:
                logger.warning(f"TargetLock 视觉线程异常: {e}")
                time.sleep(0.2)

    # ── 控制方法 (由调用方驱动) ──────────────────────────────────────

    def approach_step(
        self,
        approach_distance: float = 5.0,
        forward_speed: float = 2.0,
    ) -> dict:
        """视觉伺服靠近一步。

        读取当前 TargetState，用 PID 计算速度指令并发送。
        目标可见时: 视觉伺服 (让目标居中 + 前进)
        目标丢失时: 悬停

        Args:
            approach_distance: 目标距离小于此值时停止靠近
            forward_speed: 前进速度上限

        Returns:
            {"action": "approach"|"hover"|"arrived", "state": ...}
        """
        snap = self.get_state()

        if not snap["visible"]:
            # 目标丢失，悬停
            try:
                self._controller.move_by_velocity(0, 0, 0, duration=0.1, vehicle_name=self._vehicle_name)
            except Exception:
                pass
            return {"action": "hover", "state": snap, "reason": "目标丢失，悬停等待"}

        # 检查是否已到达
        if snap["distance_meters"] > 0 and snap["distance_meters"] <= approach_distance:
            try:
                self._controller.move_by_velocity(0, 0, 0, duration=0.1, vehicle_name=self._vehicle_name)
            except Exception:
                pass
            return {"action": "arrived", "state": snap, "reason": f"已到达 {snap['distance_meters']:.1f}m"}

        # PID 视觉伺服
        w, h = snap["image_size"]
        if w == 0 or h == 0:
            return {"action": "hover", "state": snap, "reason": "图像尺寸无效"}

        cx, cy = snap["center_px"]
        vx, vy, vz = self._servo.compute(
            target_x=cx,
            target_y=cy,
            center_x=w / 2,
            center_y=h / 2,
            dt=0.2,
            lock_altitude=True,
        )

        # 添加前进分量 (目标在画面中心附近时前进)
        # offset 越小 (目标越居中)，前进速度越大
        offset_mag = (snap["offset_x"] ** 2 + snap["offset_y"] ** 2) ** 0.5
        if offset_mag < 0.3:
            # 目标较居中，前进
            vx += forward_speed * (1.0 - offset_mag / 0.3)

        # 限速
        speed = (vx ** 2 + vy ** 2 + vz ** 2) ** 0.5
        max_speed = 3.0
        if speed > max_speed:
            scale = max_speed / speed
            vx *= scale
            vy *= scale
            vz *= scale

        # 发送速度指令
        try:
            self._controller.move_by_velocity(
                vx, vy, vz, duration=0.2, vehicle_name=self._vehicle_name
            )
        except Exception as e:
            logger.warning(f"TargetLock 速度指令失败: {e}")

        return {
            "action": "approach",
            "state": snap,
            "velocity": {"vx": round(vx, 2), "vy": round(vy, 2), "vz": round(vz, 2)},
            "offset": {"x": round(snap["offset_x"], 3), "y": round(snap["offset_y"], 3)},
        }

    def search_step(self, yaw_step: float = 30.0) -> dict:
        """目标丢失时旋转搜索恢复一步。

        Args:
            yaw_step: 每步旋转角度 (度)

        Returns:
            {"action": "search", "state": ...}
        """
        snap = self.get_state()

        if snap["visible"]:
            return {"action": "found", "state": snap, "reason": "目标已恢复可见"}

        try:
            current_yaw = self._controller.get_heading(self._vehicle_name)
            new_yaw = (current_yaw + yaw_step) % 360.0
            self._controller.rotate_to_heading(new_yaw)
        except Exception as e:
            logger.warning(f"TargetLock 旋转搜索失败: {e}")

        return {"action": "search", "state": snap, "yaw_step": yaw_step}

    def orbit_step(
        self,
        target_x: float,
        target_y: float,
        orbit_radius: float = 5.0,
        orbit_speed: float = 1.0,
    ) -> dict:
        """环绕目标一步 (保持目标在画面中)。

        绕目标做圆周运动，同时用视觉伺服保持目标居中。

        Args:
            target_x: 目标X (NED)
            target_y: 目标Y (NED)
            orbit_radius: 环绕半径
            orbit_speed: 环绕角速度 (度/步)

        Returns:
            {"action": "orbit", "state": ...}
        """
        snap = self.get_state()

        try:
            status = self._controller.get_status(self._vehicle_name)
            drone_x = status.position_ned["x"]
            drone_y = status.position_ned["y"]
        except Exception:
            return {"action": "error", "state": snap, "reason": "获取位置失败"}

        # 计算环绕速度 (垂直于到目标方向)
        dx = drone_x - target_x
        dy = drone_y - target_y
        dist = max((dx ** 2 + dy ** 2) ** 0.5, 0.1)

        # 切向速度 (垂直于径向)
        tangent_vx = -dy / dist * orbit_speed
        tangent_vy = dx / dist * orbit_speed

        # 径向修正 (保持环绕半径)
        radial_error = dist - orbit_radius
        radial_vx = -dx / dist * radial_error * 0.5
        radial_vy = -dy / dist * radial_error * 0.5

        # 视觉修正 (保持目标居中)
        visual_vx = 0.0
        visual_vy = 0.0
        if snap["visible"]:
            w, h = snap["image_size"]
            if w > 0 and h > 0:
                cx, cy = snap["center_px"]
                vx_s, vy_s, _ = self._servo.compute(
                    target_x=cx, target_y=cy,
                    center_x=w / 2, center_y=h / 2,
                    dt=0.2, lock_altitude=True,
                )
                visual_vx = vx_s * 0.3
                visual_vy = vy_s * 0.3

        # 合成速度
        final_vx = tangent_vx + radial_vx + visual_vx
        final_vy = tangent_vy + radial_vy + visual_vy

        # 限速
        speed = (final_vx ** 2 + final_vy ** 2) ** 0.5
        if speed > 3.0:
            scale = 3.0 / speed
            final_vx *= scale
            final_vy *= scale

        try:
            self._controller.move_by_velocity(
                final_vx, final_vy, 0, duration=0.3, vehicle_name=self._vehicle_name
            )
        except Exception as e:
            logger.warning(f"TargetLock 环绕速度指令失败: {e}")

        return {
            "action": "orbit",
            "state": snap,
            "velocity": {"vx": round(final_vx, 2), "vy": round(final_vy, 2)},
            "orbit_dist": round(dist, 2),
        }
