"""飞行控制：起降、单机运动、派发/编队、状态/航向查询。

拆分自 airsim_controller.py（AirSimController 方法按职责迁移，行为不变）。
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Optional, Any, Callable

import airsim

from .flight_controller import FlightController, DroneStatus, ConnectionInfo
from ..logging_config import get_logger

logger = get_logger(__name__)


class AirSimFlightMixin:
    def arm(self, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"arm failed: {e}")
            return False

    def disarm(self, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for disarm."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(self._client.armDisarm, False, name)
                self._armed.discard(name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"disarm failed: {e}")
            return False

    def _takeoff_target_z(self, altitude: float) -> float:
        """起飞目标 z：相对地面（AGL）而非 NED 原点。

        本环境 NED 原点可能位于地面以下数米，-altitude 会把"起飞 3m"
        变成实际爬升十几米；有 kinematics 地面标定时按 AGL 换算。
        目标额外下压 1m 抵消位置控制器的悬停死区（实测到位差 ~1.2m）。
        """
        altitude = max(0.5, abs(float(altitude)))
        ground_z = self._ground_z_kin_value()
        target_alt = altitude + 1.0
        if ground_z is not None:
            return ground_z - target_alt
        return -target_alt

    def _dispatch_climb(self, name: str, target_z: float, altitude: float, timeout: float = 10.0) -> None:
        """给单机派发爬升指令。已在空中的机绝不能再发 takeoffAsync——
        本机版本对飞行中的飞机调 takeoffAsync 会触发异常状态（直接降落地面）；
        地面机（即使已解锁）必须先 takeoffAsync。"""
        try:
            status = self.get_status(name).to_dict()
        except Exception:
            status = {}
        if not status.get("flying"):
            self._rpc(
                lambda n=name: self._client.takeoffAsync(timeout_sec=max(30, altitude * 5), vehicle_name=n),
                timeout=timeout,
            )
            # 给 takeoffAsync 留出状态机初始化窗口再派发爬升，避免紧接的
            # moveToZ 抢占 takeoff 导致起飞只拉起一半就停摆
            time.sleep(0.3)
        self._rpc(
            lambda n=name: self._client.moveToZAsync(
                target_z,
                velocity=1.5,
                timeout_sec=max(25, altitude * 8),
                vehicle_name=n,
            ),
            timeout=timeout,
        )

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            altitude = max(0.5, abs(float(altitude)))
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            target_z = self._takeoff_target_z(altitude)
            # 多机并行：先把全部起飞/爬升任务发给模拟器（AirSim 并发执行），
            # 再统一等待到位——逐架 join 会把 N 架的起飞时间串行相加
            for name in names:
                self._dispatch_climb(name, target_z, altitude)
            for name in names:
                if not self._wait_until_airborne_at_altitude(name, altitude, timeout=max(35, altitude * 10)):
                    # 指令可能被瞬时 RPC 超时丢弃（多客户端并发时时有发生）：
                    # 重发一次爬升再等一轮才判定失败
                    logger.warning(f"takeoff[{name}]: 首轮未到高度, 重发爬升指令")
                    self._dispatch_climb(name, target_z, altitude)
                    if not self._wait_until_airborne_at_altitude(name, altitude, timeout=max(35, altitude * 10)):
                        self.last_error = f"AirSim takeoff command returned but {name} did not reach {altitude:.1f}m"
                        return False
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"takeoff failed: {e}")
            return False

    def land(self, vehicle_name: str = "") -> bool:
        """降落并锁定（阻塞直到全部目标机确认触地）。

        本机 AirSim 版本 landAsync 静默无效、landed_state 触地后仍停留在
        Flying，二者均不可用；改用引导压地：moveToPosition 持续指向地面，
        触地由 kinematics 高度/速度（或 landed_state==0）判定，随后上锁。
        多机并发：每机一个监视线程，互不阻塞。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for landing."
            return False
        names = self._resolve_vehicles(vehicle_name)
        results: dict[str, bool] = {}
        threads: list[threading.Thread] = []
        for name in names:
            t = threading.Thread(
                target=self._guided_land_worker,
                args=(name, results),
                daemon=True,
                name=f"guided_land_{name}",
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        failed = [n for n in names if not results.get(n)]
        if failed:
            self.last_error = f"landing not confirmed for: {', '.join(failed)}"
            logger.warning(self.last_error)
            return False
        return True

    def _guided_land_worker(self, name: str, results: dict[str, bool] | None = None, timeout: float = 180.0) -> bool:
        """单机引导降落：确保 API 控制权 → 压地循环 → 触地 → 上锁并复核。

        上锁必须被模拟器接受且复核不再处于 armed 才判定成功——上锁失败
        绝不能报"已降落锁定"（SimpleFlight 状态机异常时 armDisarm 会拒绝，
        无人机仍悬停在空中，系统却已宣告完成）。
        """
        ok = False
        try:
            try:
                self._rpc(self._client.enableApiControl, True, name)
                self._control_enabled.add(name)
            except Exception:
                pass
            ok = self._guided_land_loop(name, timeout)
        except Exception as e:
            logger.error(f"guided_land[{name}]: 异常: {e}")
            ok = False

        if ok:
            ok = self._disarm_verified(name)

        if ok:
            self._landed_vehicles.add(name)
            logger.info(f"guided_land[{name}]: 已触地并确认上锁")
        else:
            logger.warning(f"guided_land[{name}]: 未完成（触地/上锁未确认）")
        if results is not None:
            results[name] = ok
        return ok

    def _disarm_verified(self, name: str) -> bool:
        """上锁并复核：armDisarm(False) 必须被接受，随后遥测不再报告 armed。

        失败时用 enableApiControl 重断言 + hover 复位状态机重试一次。
        """
        for attempt in range(2):
            try:
                self._rpc(self._client.armDisarm, False, name)
                self._armed.discard(name)
                time.sleep(0.8)
                status = self.get_status(name).to_dict()
                if status.get("armed") is False or name not in self._armed:
                    return True
                logger.warning(f"guided_land[{name}]: 上锁后仍 armed, 重试 #{attempt + 1}")
            except Exception as e:
                logger.warning(f"guided_land[{name}]: 上锁失败: {e}")
            # 复位状态机后重试
            try:
                self._rpc(self._client.enableApiControl, True, name)
                self._rpc(lambda n=name: self._client.hoverAsync(vehicle_name=n), timeout=10.0)
                time.sleep(1.0)
            except Exception:
                pass
        self.last_error = f"{name or '默认机'} 触地但上锁未确认（模拟器拒绝 armDisarm）"
        return False

    def _guided_land_loop(self, name: str, timeout: float = 150.0) -> bool:
        """引导压地循环。触地判定（任一满足，宁可不确认也绝不在空中误判）：
        1. kinematics 高度接近标定地面且速度低（主判定，需地面标定）；
        2. 模拟器原始状态报 Landed(0) 且速度低（该状态可能卡 1，但报 0 可信）；
        3. 无地面标定时：撞击检测——simGetCollisionInfo 的时间戳出现新撞击
           （无 reset 接口，只能用时间戳区分新旧撞击）。
        下降速度分级：高中速 ≤0.5 m/s，低空 ≤0.35，贴地 ≤0.25。
        """
        deadline = time.time() + timeout
        last_cmd_ts = 0.0
        last_progress_log = 0.0
        # 无标定路径：撞击检测基线
        collision_baseline: float | None = None
        step_target_pz: float | None = None
        step_achieve_since: float | None = None
        while time.time() < deadline:
            if self._stop_requested():
                return False
            try:
                status = self.get_status(name).to_dict()
            except Exception:
                time.sleep(0.5)
                continue
            if status.get("connection_error"):
                time.sleep(0.5)
                continue
            pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
            vel = status.get("velocity_ned") if isinstance(status.get("velocity_ned"), dict) else {}
            px = float(pos.get("x", 0.0) or 0.0)
            py = float(pos.get("y", 0.0) or 0.0)
            pz = float(pos.get("z", 0.0) or 0.0)
            speed = math.sqrt(
                float(vel.get("vx", 0.0) or 0.0) ** 2
                + float(vel.get("vy", 0.0) or 0.0) ** 2
                + float(vel.get("vz", 0.0) or 0.0) ** 2
            )
            ground_z = self._ground_z_kin_value()
            # AGL = 地面 - 当前位置（NED 向上为负；地面上方 agl>0）
            agl = (ground_z - pz) if ground_z is not None else None
            now = time.time()
            if now - last_progress_log > 10.0:
                last_progress_log = now
                agl_text = f"{agl:.2f}m" if agl is not None else "未知(未标定)"
                logger.info(
                    f"guided_land[{name}]: 下降中 agl={agl_text} speed={speed:.2f}m/s "
                    f"raw_landed={status.get('landed_state_raw')}"
                )

            # 触地判定 1：接近标定地面且低速（命令目标略低于地面以压实）
            if agl is not None and agl < 0.5 and speed < 0.4:
                return True
            # 触地判定 2：模拟器原始状态报告已落地且低速（报 0 时可信等
            # 同于物理在地面，其 z 是精确地面，顺带回填标定）
            raw_landed = status.get("landed_state_raw")
            if raw_landed == 0 and speed < 0.4:
                self._retro_record_ground_z(name, pz)
                return True
            # 触地判定 3（仅无标定）：步进下压，撞击时间戳出现新撞击 → 触地
            if ground_z is None:
                try:
                    ci = self._rpc(self._client.simGetCollisionInfo, vehicle_name=name, timeout=5.0)
                    ts = float(getattr(ci, "time_stamp", 0.0) or 0.0)
                    if collision_baseline is None:
                        collision_baseline = ts
                    elif ts > collision_baseline and speed < 0.6:
                        logger.info(
                            f"guided_land[{name}]: 撞击检测触地 (碰撞 ts {ts:.0f} > 基线 {collision_baseline:.0f})"
                        )
                        self._retro_record_ground_z(name, pz)
                        return True
                except Exception:
                    pass
                # 步进完成判定：达到当前步进目标后才允许发下一步
                if step_target_pz is not None and pz >= step_target_pz - 0.3:
                    if step_achieve_since is None:
                        step_achieve_since = now
                    elif now - step_achieve_since > 1.0:
                        step_target_pz = None
                        step_achieve_since = None
                else:
                    step_achieve_since = None

            # 下压指令：每 1.2s 重发（持续压向地面；目标略低于地面）
            if now - last_cmd_ts >= 1.2:
                if agl is not None:
                    # 压点在地面下方 0.2m，抵消位置控制器 ~0.4m 悬停死区
                    target_z = ground_z + 0.2
                    # 分级减速：高中速 ≤0.5，低空 ≤0.35，贴地 ≤0.25
                    if agl < 0.8:
                        descend_speed = 0.25
                    elif agl < 1.5:
                        descend_speed = 0.35
                    else:
                        descend_speed = 0.5
                else:
                    if step_target_pz is None:
                        step_target_pz = pz + 1.2
                        step_achieve_since = None
                    target_z = step_target_pz
                    descend_speed = 0.4
                try:
                    self._rpc(
                        lambda n=name: self._client.moveToPositionAsync(
                            px, py, target_z, descend_speed, timeout_sec=10.0, vehicle_name=n
                        ),
                        timeout=8.0,
                    )
                    last_cmd_ts = now
                except Exception as e:
                    logger.warning(f"guided_land[{name}]: 下压指令异常: {e}")
            time.sleep(0.35)
        return False

    def _retro_record_ground_z(self, name: str, touchdown_z: float) -> None:
        """触地后回填 kinematics 地面标定（无标定/标定过期时让后续降落直接可用）。"""
        samples = type(self)._ground_z_kin_samples
        if not math.isfinite(float(touchdown_z)) or abs(float(touchdown_z)) > 200.0:
            return
        if len(samples) < 5:
            samples.append(round(float(touchdown_z), 3))
        type(self)._ground_z_kin = sum(samples) / len(samples) if samples else None

    def _wait_until_airborne_at_altitude(self, vehicle_name: str, altitude: float, timeout: float = 4.0) -> bool:
        deadline = time.time() + max(0.0, timeout)
        required_altitude = max(0.4, altitude * 0.75)
        ground_z = self._ground_z_kin_value()
        while time.time() < deadline:
            try:
                state = self._rpc(self._client.getMultirotorState, vehicle_name)
                pos = state.kinematics_estimated.position
                # AGL：有 kinematics 地面标定时按地面算（原点可能在地面以下）
                if ground_z is not None:
                    current_altitude = max(0.0, ground_z - float(pos.z_val))
                else:
                    current_altitude = max(0.0, -float(pos.z_val))
                if state.landed_state == 1 and current_altitude >= required_altitude:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def hover(self, vehicle_name: str = "") -> bool:
        """Send hover to the vehicle(s) and return immediately.

        The hover command is fire-and-forget: AirSim executes it regardless of
        whether we wait, and not waiting keeps emergency-stop paths from queueing
        behind slow RPC calls (formation shutdown hovers every drone).
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for hover."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(
                    lambda n=name: self._client.hoverAsync(vehicle_name=n),
                )
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"hover failed: {e}")
            return False

    def move_to_position(self, x: float, y: float, z: float, velocity: float = 2.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            dist = (x**2 + y**2 + z**2) ** 0.5
            # 本机版本有效飞行速度常远低于设定值（任务按 timeout_sec 中途停止），
            # 超时按保守速度放大；等待用纯遥测轮询（task.join 会提前返回），
            # 任务停摆未到位时重发指令
            flight_timeout = max(45, dist / 0.8 + 30.0)
            for name in names:
                arrived = False
                for attempt in range(3):
                    self._rpc(
                        lambda n=name: self._client.moveToPositionAsync(
                            x, y, z, velocity,
                            timeout_sec=flight_timeout,
                            vehicle_name=n,
                        ),
                        timeout=15.0,
                    )
                    if self._wait_move_arrival(name, (x, y, z), tolerance=1.5, timeout=flight_timeout):
                        arrived = True
                        break
                    if self._stop_requested():
                        return False
                    logger.warning(
                        f"move_to_position[{name}]: 任务停摆未到位, 重发 #{attempt + 1}"
                    )
                if not arrived:
                    pos = self.get_status(name).to_dict().get("position_ned") or {}
                    err = math.sqrt(
                        (float(pos.get("x", 0.0) or 0.0) - x) ** 2
                        + (float(pos.get("y", 0.0) or 0.0) - y) ** 2
                        + (float(pos.get("z", 0.0) or 0.0) - z) ** 2
                    )
                    self.last_error = f"{name or '默认机'} 未到达目标位置 (距目标 {err:.1f}m)"
                    logger.warning(self.last_error)
                    return False
            time.sleep(1.0)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_to_position failed: {e}")
            return False

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            total_dist = sum(
                ((waypoints[i].get("x",0)-waypoints[i-1].get("x",0))**2 +
                 (waypoints[i].get("y",0)-waypoints[i-1].get("y",0))**2 +
                 (waypoints[i].get("z",0)-waypoints[i-1].get("z",0))**2) ** 0.5
                for i in range(1, len(waypoints))
            ) if len(waypoints) > 1 else 0
            # 本机版本有效飞行速度常远低于设定值，任务会按 timeout_sec 中途停止；
            # 超时按保守速度(0.8m/s)放大；等待用纯遥测轮询（task.join 会提前
            # 返回），任务停摆未到位时从最近航点起重发剩余段
            flight_timeout = max(60, total_dist / 0.8 + 30.0)
            final_wp = waypoints[-1]
            target = (
                float(final_wp.get("x", 0.0)),
                float(final_wp.get("y", 0.0)),
                float(final_wp.get("z", 0.0)),
            )
            for name in names:
                current_path = [dict(wp) for wp in waypoints]
                arrived = False
                for attempt in range(3):
                    airsim_path = [
                        airsim.Vector3r(wp.get("x", 0), wp.get("y", 0), wp.get("z", 0))
                        for wp in current_path
                    ]
                    self._rpc(
                        lambda n=name: self._client.moveOnPathAsync(
                            airsim_path, velocity,
                            timeout_sec=flight_timeout,
                            vehicle_name=n,
                        ),
                        timeout=15.0,
                    )
                    if self._wait_move_arrival(name, target, tolerance=1.5, timeout=flight_timeout):
                        arrived = True
                        break
                    if self._stop_requested():
                        return False
                    # 从距当前位置最近的航点起重发剩余段（避免飞回已过的航点）
                    pos = self.get_status(name).to_dict().get("position_ned") or {}
                    px = float(pos.get("x", 0.0) or 0.0)
                    py = float(pos.get("y", 0.0) or 0.0)
                    pz = float(pos.get("z", 0.0) or 0.0)
                    dists = [
                        math.sqrt(
                            (float(wp.get("x", 0.0)) - px) ** 2
                            + (float(wp.get("y", 0.0)) - py) ** 2
                            + (float(wp.get("z", 0.0)) - pz) ** 2
                        )
                        for wp in current_path
                    ]
                    k = dists.index(min(dists))
                    current_path = current_path[k:]
                    logger.warning(
                        f"move_on_path[{name}]: 任务停摆未到位, 从第 {k + 1} 点起重发 #{attempt + 1}"
                    )
                if not arrived:
                    self.last_error = f"{name or '默认机'} 未到达航线终点"
                    logger.warning(self.last_error)
                    return False
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_on_path failed: {e}")
            return False

    def _wait_move_arrival(
        self,
        name: str,
        target: tuple[float, float, float],
        tolerance: float = 1.5,
        timeout: float = 120.0,
    ) -> bool:
        """轮询等待移动任务到位（纯遥测判定，不依赖 task.join）。

        msgpackrpc 客户端以 timeout_value=5 创建时，task.join() 会在 ~6s
        提前返回（客户端超时），绝不能用 join 判断任务完成。
        到位 → True；任务停摆（低速悬停但未到位，模拟器侧任务已结束）→ False；
        超时 → False；外部急停/取消 → hover 抢占并 False。
        """
        deadline = time.time() + max(5.0, timeout)
        tx, ty, tz = target
        slow_since: float | None = None
        while time.time() < deadline:
            if self._stop_requested():
                try:
                    self._client.hoverAsync(vehicle_name=name)
                except Exception:
                    pass
                self.last_error = "interrupted by emergency stop / cancel"
                return False
            try:
                status = self.get_status(name).to_dict()
            except Exception:
                time.sleep(0.5)
                continue
            if status.get("connection_error"):
                time.sleep(0.5)
                continue
            pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
            vel = status.get("velocity_ned") if isinstance(status.get("velocity_ned"), dict) else {}
            px = float(pos.get("x", 0.0) or 0.0)
            py = float(pos.get("y", 0.0) or 0.0)
            pz = float(pos.get("z", 0.0) or 0.0)
            err = math.sqrt((px - tx) ** 2 + (py - ty) ** 2 + (pz - tz) ** 2)
            if err < tolerance:
                time.sleep(0.5)
                return True
            speed = math.sqrt(
                float(vel.get("vx", 0.0) or 0.0) ** 2
                + float(vel.get("vy", 0.0) or 0.0) ** 2
                + float(vel.get("vz", 0.0) or 0.0) ** 2
            )
            now = time.time()
            if speed < 0.15:
                if slow_since is None:
                    slow_since = now
                elif now - slow_since > 4.0:
                    # 低速悬停且未到位：模拟器侧任务已结束
                    return False
            else:
                slow_since = None
            time.sleep(0.5)
        return False

    def set_stop_provider(self, stop_provider: Callable[[], bool] | None = None) -> None:
        """Wire an external stop/cancel signal into blocking flight commands."""
        self._stop_provider = stop_provider

    def _stop_requested(self) -> bool:
        provider = getattr(self, "_stop_provider", None)
        try:
            return bool(provider and provider())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 非阻塞派发（fire-and-forget）：多机同时起飞/执行各自航线用。
    # AirSim 的 Async 指令在模拟器侧执行，RPC 派发后立即返回，不 join。
    # ------------------------------------------------------------------

    def dispatch_takeoff(self, altitude: float, vehicle_name: str = "") -> bool:
        """派发起飞（不等待完成）。多机并发起飞的基础。

        注意：部分 AirSim 版本在"降落→解锁→再起飞"循环后 takeoffAsync 只翻转
        状态不实际爬升，因此与阻塞版 takeoff() 一致，补一段 moveToZ 真正爬升。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            altitude = max(0.5, abs(float(altitude)))
            names = self._resolve_vehicles(vehicle_name)
            target_z = self._takeoff_target_z(altitude)
            for name in names:
                self._ensure_control(name)
                self._dispatch_climb(name, target_z, altitude)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"dispatch_takeoff failed: {e}")
            return False

    def dispatch_move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        """派发航线（不等待完成）。每架机各自执行自己的路径。

        本机版本任务可能按 timeout_sec 中途停止（有效速度远低于设定值），
        每机附一个后台监视线程：未到终点时从最近航点起重发剩余段，
        到位后把 _shared_dispatched_paths 标记为 done。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if not waypoints:
            self.last_error = "waypoints is empty"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            total_dist = sum(
                ((waypoints[i].get("x",0)-waypoints[i-1].get("x",0))**2 +
                 (waypoints[i].get("y",0)-waypoints[i-1].get("y",0))**2 +
                 (waypoints[i].get("z",0)-waypoints[i-1].get("z",0))**2) ** 0.5
                for i in range(1, len(waypoints))
            ) if len(waypoints) > 1 else 0
            # 保守速度 0.8m/s 估算（有效速度可能远低于设定值）
            flight_timeout = max(60, total_dist / 0.8 + 30.0)
            for name in names:
                airsim_path = [
                    airsim.Vector3r(wp.get("x", 0), wp.get("y", 0), wp.get("z", 0))
                    for wp in waypoints
                ]
                self._rpc(
                    lambda n=name: self._client.moveOnPathAsync(
                        airsim_path, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ),
                    timeout=10.0,
                )
                # 记录派发，供完成跟踪（update_flight_task_progress）使用
                type(self)._shared_dispatched_paths[name] = {
                    "waypoints": [dict(wp) for wp in waypoints],
                    "velocity": float(velocity),
                    "dispatched_at": time.time(),
                    "done": False,
                    "near_count": 0,
                }
                threading.Thread(
                    target=self._dispatch_path_monitor,
                    args=(name, [dict(wp) for wp in waypoints], float(velocity), flight_timeout),
                    daemon=True,
                    name=f"path_monitor_{name}",
                ).start()
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"dispatch_move_on_path failed: {e}")
            return False

    def _dispatch_path_monitor(self, name: str, waypoints: list[dict], velocity: float, flight_timeout: float) -> None:
        """航线到位监视线程：任务超时停摆时从最近航点起重发剩余段。

        判定到位：距最后航点 <1.5m。总窗口 = flight_timeout × 2 + 120s，
        超窗放弃（保留未 done 状态，由上层进度跟踪如实上报）。
        """
        deadline = time.time() + flight_timeout * 2 + 120.0
        final_wp = waypoints[-1]
        current_path = [dict(wp) for wp in waypoints]
        last_dispatch = time.time()
        reissued = 0
        while time.time() < deadline:
            if self._stop_requested():
                return
            info = type(self)._shared_dispatched_paths.get(name)
            if info and info.get("done"):
                return
            time.sleep(2.0)
            try:
                status = self.get_status(name).to_dict()
            except Exception:
                continue
            if status.get("connection_error"):
                continue
            pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
            px = float(pos.get("x", 0.0) or 0.0)
            py = float(pos.get("y", 0.0) or 0.0)
            pz = float(pos.get("z", 0.0) or 0.0)
            err = math.sqrt(
                (px - float(final_wp.get("x", 0.0))) ** 2
                + (py - float(final_wp.get("y", 0.0))) ** 2
                + (pz - float(final_wp.get("z", 0.0))) ** 2
            )
            if err < 1.5:
                if info is not None:
                    info["done"] = True
                    info["done_at"] = time.time()
                logger.info(f"path_monitor[{name}]: 航线完成 (距终点 {err:.2f}m)")
                return
            # 任务停摆（超时悬停）→ 从最近航点起重发剩余段
            vel = status.get("velocity_ned") if isinstance(status.get("velocity_ned"), dict) else {}
            speed = math.sqrt(
                float(vel.get("vx", 0.0) or 0.0) ** 2
                + float(vel.get("vy", 0.0) or 0.0) ** 2
                + float(vel.get("vz", 0.0) or 0.0) ** 2
            )
            if speed > 0.5 or time.time() - last_dispatch < 15.0:
                continue
            if reissued >= 3:
                logger.warning(f"path_monitor[{name}]: 重发 {reissued} 次仍未到位，放弃")
                return
            dists = [
                math.sqrt(
                    (float(wp.get("x", 0.0)) - px) ** 2
                    + (float(wp.get("y", 0.0)) - py) ** 2
                    + (float(wp.get("z", 0.0)) - pz) ** 2
                )
                for wp in current_path
            ]
            k = dists.index(min(dists))
            remaining = current_path[k:]
            try:
                airsim_path = [
                    airsim.Vector3r(wp.get("x", 0), wp.get("y", 0), wp.get("z", 0))
                    for wp in remaining
                ]
                self._rpc(
                    lambda n=name: self._client.moveOnPathAsync(
                        airsim_path, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ),
                    timeout=10.0,
                )
                reissued += 1
                last_dispatch = time.time()
                logger.warning(
                    f"path_monitor[{name}]: 停摆未到位 (距终点 {err:.1f}m), 从第 {k + 1} 点起重发 #{reissued}"
                )
            except Exception as e:
                logger.warning(f"path_monitor[{name}]: 重发异常: {e}")
                last_dispatch = time.time()

    def is_flying(self, vehicle_name: str = "") -> bool:
        status = self.get_status(vehicle_name)
        return bool(status.flying)

    def dispatch_land(self, vehicle_name: str = "") -> bool:
        """派发降落（fire-and-forget）。多机同时降落用，完成验证由调用方轮询。

        引导压地方案（landAsync 在本机版本静默无效），每机一个后台线程。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        names = self._resolve_vehicles(vehicle_name)
        for name in names:
            threading.Thread(
                target=self._guided_land_worker,
                args=(name, None),
                daemon=True,
                name=f"guided_land_{name}",
            ).start()
        return True

    def dispatch_return_and_land(
        self, x: float, y: float, z: float, velocity: float = 3.0, vehicle_name: str = ""
    ) -> bool:
        """派发返航：引导循环飞回 (x,y,z)，到位后自动降落并锁定。

        返航语义 = 到达初始点 + 降落 + 上锁，全程异步，不阻塞调用方。
        不用 moveOnPath：任务结束悬停后直接派发长航线首段会与悬停状态竞争，
        改用引导循环——监视线程每 1.2s 重发一次 moveToPosition(初始点)，
        飞机必然收敛到家；降落阶段用引导压地（本机版本 landAsync 无效）。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        names = self._resolve_vehicles(vehicle_name)
        for name in names:
            self._ensure_control(name)
            monitor = threading.Thread(
                target=self._return_land_monitor,
                args=(name, float(x), float(y), float(z), float(velocity)),
                daemon=True,
                name=f"return_land_{name}",
            )
            monitor.start()
        return True

    def _return_land_monitor(self, name: str, x: float, y: float, z: float, velocity: float = 3.0) -> None:
        """引导式返航：循环重发 moveToPosition(初始点)，到位后引导压地并上锁。

        每轮重发会取消上一条同目标指令——目标不变，对飞行无害。
        地面待飞机第一轮指令即从地面爬升飞向初始点。单机总超时 180s。
        """
        deadline = time.time() + 180.0
        arrived = False
        logger.info(f"return_land_monitor[{name}]: 引导返航启动, 目标=({x:.2f}, {y:.2f}, {z:.2f})")
        while time.time() < deadline:
            if self._stop_requested():
                logger.info(f"return_land_monitor[{name}]: 收到停止信号退出")
                return
            try:
                status = self.get_status(name).to_dict()
                pos = status.get("position_ned") or {}
                vel = status.get("velocity_ned") or {}
                px = float(pos.get("x", 0.0) or 0.0)
                py = float(pos.get("y", 0.0) or 0.0)
                dist_xy = math.hypot(px - x, py - y)
                speed = math.sqrt(
                    float(vel.get("vx", 0.0) or 0.0) ** 2
                    + float(vel.get("vy", 0.0) or 0.0) ** 2
                    + float(vel.get("vz", 0.0) or 0.0) ** 2
                )
                # 到位判定用水平距离：飞机先飞到自家初始点正上方（巡航高度），
                # 再垂直降落在初始点上。1.2m 容忍覆盖 GPS↔kinematics 帧 ~0.6m 残差
                # + 位置控制悬停余量。竖直高度也在范围内时立即转入降落阶段
                # （避免在目标点上方 5m 反复盘旋）。
                if dist_xy < 1.2 and speed < 0.5:
                    arrived = True
                    logger.info(f"return_land_monitor[{name}]: 到位 (水平 {dist_xy:.2f}m, 速度 {speed:.2f}m/s), 准备降落")
                    break
                # 引导指令：持续指向初始点
                self._rpc(
                    lambda n=name: self._client.moveToPositionAsync(
                        x, y, z, max(0.5, velocity),
                        timeout_sec=20.0,
                        vehicle_name=n,
                    ),
                    timeout=10.0,
                )
            except Exception as e:
                logger.warning(f"return_land_monitor: {name} 引导指令异常: {e}")
            time.sleep(1.2)
        if not arrived:
            logger.warning(f"return_land_monitor[{name}]: 未在超时前到位，跳过自动降落")
            return
        try:
            # 精对准：到位后再补一条终点指令，收掉最后 ~1m 的水平偏差
            try:
                self._rpc(
                    lambda n=name: self._client.moveToPositionAsync(
                        x, y, z, 1.0, timeout_sec=15.0, vehicle_name=n
                    ),
                    timeout=10.0,
                )
                time.sleep(2.5)
            except Exception:
                pass
            # 引导压地 + 触地上锁（本机版本 landAsync 无效，不能用 landAsync）
            if self._guided_land_worker(name, timeout=150.0):
                logger.info(f"return_land_monitor[{name}]: 已返航降落并锁定")
            else:
                logger.error(f"return_land_monitor[{name}]: 返航到位但降落未确认")
        except Exception as e:
            logger.error(f"return_land_monitor: {name} 降落/锁定失败: {e}")

    def update_flight_task_progress(self, vehicles: list[dict]) -> dict[str, dict[str, Any]]:
        """用最新逐车遥测更新已派发航线的执行状态（纯计算，无 RPC）。

        判定完成：连续多次距最后航点 < 1.2m 且速度接近 0（悬停到位）。
        返回 {vehicle_name: {state, waypoint_count, dist_to_target, speed}}。
        """
        for vehicle in vehicles or []:
            if not isinstance(vehicle, dict):
                continue
            name = str(vehicle.get("vehicle_name") or "")
            info = type(self)._shared_dispatched_paths.get(name)
            if not info or info.get("done"):
                continue
            pos = vehicle.get("position_ned") if isinstance(vehicle.get("position_ned"), dict) else {}
            vel = vehicle.get("velocity_ned") if isinstance(vehicle.get("velocity_ned"), dict) else {}
            final = info["waypoints"][-1]
            dist = math.sqrt(
                (float(pos.get("x", 0.0) or 0.0) - float(final.get("x", 0.0))) ** 2
                + (float(pos.get("y", 0.0) or 0.0) - float(final.get("y", 0.0))) ** 2
                + (float(pos.get("z", 0.0) or 0.0) - float(final.get("z", 0.0))) ** 2
            )
            speed = math.sqrt(
                float(vel.get("vx", 0.0) or 0.0) ** 2
                + float(vel.get("vy", 0.0) or 0.0) ** 2
                + float(vel.get("vz", 0.0) or 0.0) ** 2
            )
            if dist < 1.2 and speed < 0.5:
                info["near_count"] = int(info.get("near_count", 0)) + 1
            else:
                info["near_count"] = 0
            # 飞机已落地(降落/被返航接管) → 路径任务自然结束,不留滞留记录
            grounded = (not vehicle.get("flying")) and (not vehicle.get("armed")) and speed < 0.5
            if info["near_count"] >= 3 or grounded:
                info["done"] = True
                info["done_at"] = time.time()
            info["last_dist"] = round(dist, 2)
            info["last_speed"] = round(speed, 2)

        return {
            name: {
                "state": "done" if info.get("done") else "flying",
                "waypoint_count": len(info["waypoints"]),
                "dist_to_target": info.get("last_dist"),
                "speed": info.get("last_speed"),
                "dispatched_at": info.get("dispatched_at"),
            }
            for name, info in type(self)._shared_dispatched_paths.items()
        }

    def wait_until_flying(self, vehicle_name: str = "", timeout: float = 15.0) -> bool:
        """轮询等待某机进入飞行状态（起飞派发后确认用）。"""
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if self._stop_requested():
                return False
            try:
                if self.is_flying(vehicle_name):
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def move_by_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(self._client.moveByVelocityAsync, vx, vy, vz, duration or 0.1, vehicle_name=name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_by_velocity failed: {e}")
            return False

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        if not self._ensure_connected():
            return DroneStatus(extra={"connection_error": "AirSim not connected"})

        names = self._resolve_vehicles(vehicle_name)
        if not names:
            return DroneStatus()

        name = names[0]
        try:
            # tight RPC timeout: get_status is called on every UI poll per
            # vehicle; a stuck AirSim response must not block the single RPC
            # worker for the default 30s recovery window on every poll
            state = self._rpc(self._client.getMultirotorState, name, timeout=4.0)
            pos = state.kinematics_estimated.position
            vel = state.kinematics_estimated.linear_velocity
            ori = state.kinematics_estimated.orientation
            landed = state.landed_state

            position = {"x": round(pos.x_val, 3), "y": round(pos.y_val, 3), "z": round(pos.z_val, 3)}
            world_position = None
            try:
                world_pose = self._rpc(self._client.simGetObjectPose, name)
                wpos = world_pose.position
                world_position = {"x": round(wpos.x_val, 3), "y": round(wpos.y_val, 3), "z": round(wpos.z_val, 3)}
            except Exception:
                pass

            # 逐机位置可信度判定（不依赖任何会话假设）：
            # 本机 AirSim 版本中，飞过的机 kinematics 逐机正确（悬停也正确），
            # 从未飞过的地面机 kinematics 是废数（可能共享首机读数或偏离出生点），
            # 而这时原始 GPS 恰好逐机正确。逐机规则：
            #   - kinematics 在自家出生点附近 → 用 kinematics（地面/已飞均正确）
            #   - 否则若原始 GPS 在出生点附近 → 该机从未有效飞行，用原始 GPS
            #   - 两者都不在出生点 → 用 kinematics（飞行/悬停中，逐机正确）
            raw_gps = None
            try:
                gnss = self._rpc(self._client.getGpsData, vehicle_name=name, timeout=4.0).gnss
                geo = gnss.geo_point
                if geo and abs(float(geo.latitude)) > 0.001 and abs(float(geo.longitude)) > 0.001:
                    raw_gps = {
                        "lat": round(float(geo.latitude), 7),
                        "lon": round(float(geo.longitude), 7),
                        "alt": round(float(geo.altitude), 2),
                    }
            except Exception:
                raw_gps = None
            speed = math.sqrt(vel.x_val ** 2 + vel.y_val ** 2 + vel.z_val ** 2)
            spawn = self._settings_spawns.get(name)
            raw_ned = None
            if raw_gps is not None:
                try:
                    raw_ned = self.gps_to_ned(
                        float(raw_gps["lat"]), float(raw_gps["lon"]), float(raw_gps["alt"] or 0.0)
                    )
                except (TypeError, ValueError):
                    raw_ned = None
            use_raw_gps = False
            if spawn is not None and raw_ned is not None:
                kin_ok = math.hypot(pos.x_val - spawn[0], pos.y_val - spawn[1]) < 1.2
                gps_ok = math.hypot(raw_ned["x"] - spawn[0], raw_ned["y"] - spawn[1]) < 1.2
                use_raw_gps = (not kin_ok) and gps_ok
            if use_raw_gps:
                position = {"x": raw_ned["x"], "y": raw_ned["y"], "z": raw_ned["z"]}
                gps_data = raw_gps
            else:
                derived_gps = self.ned_to_gps(pos.x_val, pos.y_val, pos.z_val)
                gps_data = derived_gps if derived_gps is not None else raw_gps

            _, pitch, yaw = self._quat_to_euler(ori)
            heading_deg = math.degrees(yaw)
            if heading_deg < 0:
                heading_deg += 360.0
            # AirSim 落地后 getMultirotorState 的遥测不可靠（landed_state 可能
            # 滞后为 Flying、位置 z 残留甚至翻转）。land 成功后控制器记录落地
            # 事实，直到下一次 arm 前都按"已着陆"上报，保证状态自洽。
            landed_confirmed = name in getattr(self, "_landed_vehicles", set())
            speed = math.sqrt(vel.x_val ** 2 + vel.y_val ** 2 + vel.z_val ** 2)
            # 悬停时速度≈0 但明显高于地面——只看速度会把悬停误判成落地，
            # 导致返航被 "vehicle is not airborne" 拒绝。速度或相对高度任一满足即算飞行。
            # 相对高度基于 kinematics 帧标定地面（GPS 帧与 kinematics 帧有 ~2m 偏差）。
            ground_ref = self._ground_z_kin_value()
            if ground_ref is None:
                ground_ref = self._ue_ned_ground_z if self._ue_ned_ground_z is not None else 0.0
            airborne_by_altitude = pos.z_val < (ground_ref - 0.3)
            flying = (landed == 1) and not landed_confirmed and (speed > 0.3 or airborne_by_altitude)
            if landed_confirmed:
                position = {"x": float(position.get("x", 0.0) or 0.0), "y": float(position.get("y", 0.0) or 0.0), "z": 0.0}
            extra = {
                "heading_deg": round(heading_deg, 1),
                "landed_state": "flying" if flying else "landed",
                # 原始模拟器落地状态(0=Landed)：本版本触地后可能卡在 1(Flying)，
                # 但报 0 时可信，供触地判定直接使用
                "landed_state_raw": int(landed),
                "has_collided": getattr(state, 'collision', None) and state.collision.has_collided,
                "api_control_enabled": name in self._control_enabled,
                "vehicle_types": self._settings_vehicle_types,
                "external_flight_controller": self._uses_external_px4_controller(name),
            }
            if world_position is not None:
                extra["world_position"] = world_position

            status_dict = DroneStatus(
                position_ned=position,
                velocity_ned={
                    "vx": round(vel.x_val, 3),
                    "vy": round(vel.y_val, 3),
                    "vz": round(vel.z_val, 3),
                },
                attitude_rad={
                    "roll": round(math.atan2(2 * (ori.w_val * ori.x_val + ori.y_val * ori.z_val),
                                             1 - 2 * (ori.x_val ** 2 + ori.y_val ** 2)), 4),
                    "pitch": round(math.asin(max(-1, min(1, 2 * (ori.w_val * ori.y_val - ori.z_val * ori.x_val)))), 4),
                    "yaw": round(yaw, 4),
                },
                armed=name in self._armed,
                flying=flying,
                mode="airsim_simpleflight",
                gps=gps_data,
                extra=extra,
            )
            self._record_home_position(name, status_dict.to_dict(), landed)
            home = self.home_position(name)
            if home is not None:
                status_dict.extra["home_position_ned"] = dict(home)
            return status_dict
        except Exception as e:
            logger.error(f"get_status failed: {e}")
            self._connected = False
            self._client = None
            return DroneStatus(extra={"connection_error": str(e)})

    def list_vehicles(self, refresh: bool = False) -> list[str]:
        """Return the known vehicle list.

        Default reads the locally cached list populated at connect time —
        the vehicle composition does not change while AirSim runs, and a
        live RPC here would queue behind the single RPC worker on every
        250ms UI poll (a stuck AirSim response then blocks ALL telemetry
        until the 30s runtime reset, which shows up as the vehicle panel
        flickering). Pass ``refresh=True`` for a force re-read.
        """
        if refresh:
            if not self._ensure_connected():
                return self._vehicles or []
            try:
                fresh = self._rpc(self._client.listVehicles, timeout=5.0)
                if fresh:
                    self._vehicles = list(fresh)
            except Exception:
                pass
        if not self.is_connected:
            return []  # keep the pre-cache semantics for tool-layer callers
        return self._vehicles or [""]

    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        logger.warning("AirSim does not support flight modes")
        return False

    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for yaw control."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc_call(
                    lambda n=name: self._client.rotateToYawAsync(
                        heading_deg, timeout_sec=30, vehicle_name=n
                    ).join(),
                    timeout=40.0,
                )
            return True
        except Exception as e:
            logger.error(f"rotate_to_heading failed: {e}")
            return False

    def get_heading(self, vehicle_name: str = "") -> float:
        if not self._ensure_connected():
            return 0.0
        try:
            names = self._resolve_vehicles(vehicle_name)
            name = names[0]
            state = self._rpc(self._client.getMultirotorState, vehicle_name=name)
            q = state.kinematics_estimated.orientation
            _, _, yaw_rad = self._quat_to_euler(q)
            yaw_deg = math.degrees(yaw_rad) % 360.0
            return round(yaw_deg, 1)
        except Exception as e:
            logger.error(f"get_heading failed: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _quat_to_euler(q) -> tuple:
        sinr_cosp = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
        cosr_cosp = 1.0 - 2.0 * (q.x_val ** 2 + q.y_val ** 2)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
        pitch = math.asin(max(-1.0, min(1.0, sinp)))

        siny_cosp = 2.0 * (q.w_val * q.z_val + q.x_val * q.y_val)
        cosy_cosp = 1.0 - 2.0 * (q.y_val ** 2 + q.z_val ** 2)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return roll, pitch, yaw

    def world_to_local(self, vehicle_name: str, x: float, y: float, z: float) -> tuple:
        try:
            offset = self.get_world_offset(vehicle_name)
            return (x - offset["dx"], y - offset["dy"], z - offset["dz"])
        except Exception:
            return (x, y, z)

    def get_world_offset(self, vehicle_name: str) -> dict:
        try:
            ned = self._rpc(self._client.getMultirotorState, vehicle_name).kinematics_estimated.position
            world = self._rpc(self._client.simGetObjectPose, vehicle_name).position
            return {"dx": world.x_val - ned.x_val, "dy": world.y_val - ned.y_val, "dz": world.z_val - ned.z_val}
        except Exception:
            return {"dx": 0, "dy": 0, "dz": 0}
