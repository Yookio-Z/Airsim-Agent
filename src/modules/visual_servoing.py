"""
PID 视觉伺服控制器
参考 dimOS DroneVisualServoingController 设计
输入: 像素误差 (bbox中心 vs 图像中心) → 输出: 速度指令 (vx, vy)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PIDParams:
    Kp: float
    Ki: float
    Kd: float
    output_limits: tuple[float, float]
    integral_limit: Optional[float] = None
    deadband_px: float = 0.0


@dataclass
class ServoingState:
    error_x: float = 0.0
    error_y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    integral_x: float = 0.0
    integral_y: float = 0.0


class VisualServoingController:
    """PID 视觉伺服控制器。

    将目标在图像中的像素偏移转换为无人机速度指令。
    - error_x (水平) → vy (东向速度)
    - error_y (垂直) → vx (前向速度) / vz (垂直速度)

    参考 dimOS 的 PID 参数设计:
      室内: Kp=0.001, Ki=0.0, Kd=0.0001, max=1.0 m/s
      室外: Kp=0.05,  Ki=0.0, Kd=0.0003, max=5.0 m/s
    """

    INDOOR = PIDParams(Kp=0.002, Ki=0.0, Kd=0.0002, output_limits=(-1.0, 1.0), deadband_px=20)
    OUTDOOR = PIDParams(Kp=0.01, Ki=0.0001, Kd=0.0005, output_limits=(-3.0, 3.0), deadband_px=10)

    def __init__(
        self,
        x_params: Optional[PIDParams] = None,
        y_params: Optional[PIDParams] = None,
        max_velocity: float = 1.0,
    ) -> None:
        self.x_params = x_params or self.INDOOR
        self.y_params = y_params or self.INDOOR
        self.max_velocity = max_velocity

        self._integral_x = 0.0
        self._integral_y = 0.0
        self._prev_error_x = 0.0
        self._prev_error_y = 0.0
        self._first_call = True

    def compute(
        self,
        target_x: float,
        target_y: float,
        center_x: float,
        center_y: float,
        dt: float = 0.1,
        lock_altitude: bool = True,
    ) -> tuple[float, float, float]:
        """根据像素误差计算速度指令。

        Args:
            target_x: 目标在图像中的 x 坐标 (像素)
            target_y: 目标在图像中的 y 坐标 (像素)
            center_x: 图像中心 x
            center_y: 图像中心 y
            dt: 时间步长 (秒)
            lock_altitude: 是否锁定高度 (True=vy控制前后, False=vy控制上下)

        Returns:
            (vx, vy, vz) 速度指令 (NED, m/s)
        """
        error_x = target_x - center_x
        error_y = target_y - center_y

        if self._first_call:
            self._prev_error_x = error_x
            self._prev_error_y = error_y
            self._first_call = False

        vx_out = self._pid_step(
            error=-error_y,
            prev_error=self._prev_error_y,
            integral_ref=self._integral_y,
            params=self.y_params,
            dt=dt,
        )

        vy_out = self._pid_step(
            error=error_x,
            prev_error=self._prev_error_x,
            integral_ref=self._integral_x,
            params=self.x_params,
            dt=dt,
        )

        self._integral_y += error_y * dt
        self._integral_x += error_x * dt

        if self.x_params.integral_limit:
            self._integral_x = max(-self.x_params.integral_limit,
                                   min(self.x_params.integral_limit, self._integral_x))
        if self.y_params.integral_limit:
            self._integral_y = max(-self.y_params.integral_limit,
                                   min(self.y_params.integral_limit, self._integral_y))

        self._prev_error_x = error_x
        self._prev_error_y = error_y

        vx_out = max(-self.max_velocity, min(self.max_velocity, vx_out))
        vy_out = max(-self.max_velocity, min(self.max_velocity, vy_out))

        vz_out = 0.0
        if not lock_altitude:
            vz_out = vy_out
            vy_out = 0.0

        return vx_out, vy_out, vz_out

    def _pid_step(
        self,
        error: float,
        prev_error: float,
        integral_ref: float,
        params: PIDParams,
        dt: float,
    ) -> float:
        if abs(error) < params.deadband_px:
            return 0.0

        p_term = params.Kp * error

        i_term = params.Ki * integral_ref

        d_term = params.Kd * (error - prev_error) / dt if dt > 0 else 0.0

        output = p_term + i_term + d_term
        output = max(params.output_limits[0], min(params.output_limits[1], output))

        return output

    def reset(self) -> None:
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._prev_error_x = 0.0
        self._prev_error_y = 0.0
        self._first_call = True

    def get_state(self) -> ServoingState:
        return ServoingState(
            error_x=self._prev_error_x,
            error_y=self._prev_error_y,
            vx=0.0,
            vy=0.0,
            integral_x=self._integral_x,
            integral_y=self._integral_y,
        )
