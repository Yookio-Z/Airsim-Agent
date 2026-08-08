"""
pymavlink 直接控制层 - 替换 MAVSDK 中间层
参考 dimOS 的 MavlinkConnection 实现，适配 AirSim MCP 架构
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

from pymavlink import mavutil

from src.logging_config import get_logger

logger = get_logger(__name__)


class MavlinkConnection:
    """MAVLink 直接连接，通过 pymavlink 控制 PX4/AirSim"""

    def __init__(
        self,
        connection_string: str = "udp:127.0.0.1:14540",
        outdoor: bool = False,
        max_velocity: float = 5.0,
    ) -> None:
        self.connection_string = connection_string
        self.outdoor = outdoor
        self.max_velocity = max_velocity
        self.mavlink: Any | None = None
        self.connected = False
        self.telemetry: dict[str, Any] = {}

        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._last_update = time.time()
        self._gps_origin: dict[str, float] | None = None

        self._callbacks: list[Callable[[dict[str, Any]], None]] = []
        self.flying_to_target = False

    def connect(self) -> bool:
        """连接到飞控"""
        try:
            logger.info(f"Connecting to {self.connection_string}")
            self.mavlink = mavutil.mavlink_connection(self.connection_string)
            self.mavlink.wait_heartbeat(timeout=30)
            self.connected = True
            logger.info(
                f"Connected to system {self.mavlink.target_system}, "
                f"component {self.mavlink.target_component}"
            )
            self.update_telemetry()
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def update_telemetry(self, timeout: float = 0.1) -> None:
        """轮询更新遥测数据"""
        if not self.connected or self.mavlink is None:
            return

        end_time = time.time() + timeout
        while time.time() < end_time:
            msg = self.mavlink.recv_match(blocking=False)
            if not msg:
                time.sleep(0.001)
                continue

            msg_type = msg.get_type()
            msg_dict = msg.to_dict()

            if msg_type == "GLOBAL_POSITION_INT":
                msg_dict["lat"] = msg_dict.get("lat", 0) / 1e7
                msg_dict["lon"] = msg_dict.get("lon", 0) / 1e7
                msg_dict["alt"] = msg_dict.get("alt", 0) / 1000.0
                msg_dict["relative_alt"] = msg_dict.get("relative_alt", 0) / 1000.0
                msg_dict["vx"] = msg_dict.get("vx", 0) / 100.0
                msg_dict["vy"] = msg_dict.get("vy", 0) / 100.0
                msg_dict["vz"] = msg_dict.get("vz", 0) / 100.0
                msg_dict["hdg"] = msg_dict.get("hdg", 0) / 100.0
                self._update_position(msg_dict)

            elif msg_type == "ATTITUDE":
                self._update_attitude(msg_dict)

            elif msg_type == "HEARTBEAT":
                base_mode = msg_dict.get("base_mode", 0)
                msg_dict["armed"] = bool(
                    base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )

            elif msg_type == "SYS_STATUS":
                msg_dict["voltage_battery"] = msg_dict.get("voltage_battery", 0) / 1000.0
                msg_dict["current_battery"] = msg_dict.get("current_battery", 0) / 100.0

            self.telemetry[msg_type] = msg_dict

        for cb in self._callbacks:
            cb(self.telemetry.copy())

    def _update_position(self, gps_data: dict[str, Any]) -> None:
        """从 GLOBAL_POSITION_INT 更新本地位置"""
        current_time = time.time()
        dt = current_time - self._last_update

        if self.outdoor:
            lat = gps_data.get("lat", 0)
            lon = gps_data.get("lon", 0)
            if lat != 0 and lon != 0:
                if self._gps_origin is None:
                    self._gps_origin = {"lat": lat, "lon": lon}
                R = 6371000.0
                dlat = math.radians(lat - self._gps_origin["lat"])
                dlon = math.radians(lon - self._gps_origin["lon"])
                self._position["x"] = dlat * R
                self._position["y"] = -dlon * R * math.cos(
                    math.radians(self._gps_origin["lat"])
                )
        else:
            vx = gps_data.get("vx", 0)
            vy = gps_data.get("vy", 0)
            if dt > 0:
                self._position["x"] += vx * dt
                self._position["y"] += -vy * dt

        self._position["z"] = gps_data.get("relative_alt", 0)
        self._last_update = current_time

    def _update_attitude(self, attitude: dict[str, Any]) -> None:
        """更新姿态信息"""
        self.telemetry["ATTITUDE"] = attitude

    def _send_position_target(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        yaw_rate: float = 0.0,
        frame: int = mavutil.mavlink.MAV_FRAME_BODY_NED,
        type_mask: int = 0b0000111111000111,
    ) -> None:
        """发送位置/速度目标指令"""
        if not self.connected or self.mavlink is None:
            return
        self.mavlink.mav.set_position_target_local_ned_send(
            0,
            self.mavlink.target_system,
            self.mavlink.target_component,
            frame,
            type_mask,
            0,
            0,
            0,
            vx,
            vy,
            vz,
            0,
            0,
            0,
            0,
            yaw_rate,
        )

    def move_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.0) -> bool:
        """发送速度指令 (NED 坐标系, m/s)"""
        if not self.connected:
            return False
        if duration > 0:
            end_time = time.time() + duration
            while time.time() < end_time:
                self._send_position_target(vx, vy, vz)
                time.sleep(0.1)
            self.stop()
        else:
            self._send_position_target(vx, vy, vz)
        return True

    def move_velocity_body(self, forward: float, right: float, down: float, duration: float = 0.0) -> bool:
        """发送机体坐标系速度指令 (BODY_NED, m/s)"""
        if not self.connected:
            return False
        if duration > 0:
            end_time = time.time() + duration
            while time.time() < end_time:
                self._send_position_target(
                    forward, right, down,
                    frame=mavutil.mavlink.MAV_FRAME_BODY_NED,
                )
                time.sleep(0.1)
            self.stop()
        else:
            self._send_position_target(
                forward, right, down,
                frame=mavutil.mavlink.MAV_FRAME_BODY_NED,
            )
        return True

    def stop(self) -> bool:
        """停止运动"""
        if not self.connected:
            return False
        self._send_position_target(0, 0, 0)
        return True

    def rotate_to(self, target_heading_deg: float, timeout: float = 60.0) -> bool:
        """旋转到指定航向 (度, 0=North, 顺时针+)"""
        if not self.connected:
            return False

        start_time = time.time()
        while time.time() - start_time < timeout:
            gps = self.telemetry.get("GLOBAL_POSITION_INT", {})
            raw_hdg = gps.get("hdg", 0)
            current = raw_hdg if raw_hdg <= 360 else raw_hdg / 100.0

            error = target_heading_deg - current
            if error > 180:
                error -= 360
            elif error < -180:
                error += 360

            if abs(error) < 10:
                return True

            yaw_rate = max(-60.0, min(60.0, error * 0.3))
            if abs(yaw_rate) < 15.0:
                yaw_rate = 15.0 if error > 0 else -15.0

            self._send_position_target(
                yaw_rate=math.radians(yaw_rate),
                type_mask=0b0000011111111111,
            )
            time.sleep(0.1)

        self.stop()
        return False

    def arm(self) -> bool:
        """解锁电机"""
        if not self.connected or self.mavlink is None:
            return False
        self.mavlink.mav.command_long_send(
            self.mavlink.target_system,
            self.mavlink.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        ack = self.mavlink.recv_match(type="COMMAND_ACK", blocking=True, timeout=5)
        if ack and ack.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
            if ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                for _ in range(10):
                    msg = self.mavlink.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
                    if msg and msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                        return True
        return False

    def disarm(self) -> bool:
        """锁定电机"""
        if not self.connected or self.mavlink is None:
            return False
        self.mavlink.mav.command_long_send(
            self.mavlink.target_system,
            self.mavlink.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        return True

    def takeoff(self, altitude: float = 3.0) -> bool:
        """起飞到指定高度"""
        if not self.connected:
            return False
        if not self.set_mode("GUIDED"):
            return False
        if self.mavlink is None:
            return False
        self.mavlink.mav.command_long_send(
            self.mavlink.target_system,
            self.mavlink.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            altitude,
        )
        return True

    def land(self) -> bool:
        """降落"""
        if not self.connected or self.mavlink is None:
            return False
        self.mavlink.mav.command_long_send(
            self.mavlink.target_system,
            self.mavlink.target_component,
            mavutil.mavlink.MAV_CMD_NAV_LAND,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        return True

    def fly_to_gps(self, lat: float, lon: float, alt: float) -> str:
        """飞往 GPS 坐标，阻塞直到到达或超时"""
        if not self.connected:
            return "Failed: Not connected"
        if self.flying_to_target:
            return "Already flying to target"

        self.flying_to_target = True
        if not self.set_mode("GUIDED"):
            self.flying_to_target = False
            return "Failed: Could not set GUIDED mode"

        try:
            acceptance_radius = 5.0
            max_duration = 120
            start_time = time.time()
            loop_count = 0

            while time.time() - start_time < max_duration:
                gps = self.telemetry.get("GLOBAL_POSITION_INT", {})
                if not gps:
                    time.sleep(0.1)
                    continue

                current_lat = gps.get("lat", 0)
                current_lon = gps.get("lon", 0)
                current_alt = gps.get("relative_alt", 0)

                dlat = lat - current_lat
                dlon = lon - current_lon
                dalt = alt - current_alt

                lat_rad = current_lat * math.pi / 180.0
                m_per_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
                m_per_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)

                x_dist = dlat * m_per_lat
                y_dist = dlon * m_per_lon
                distance = math.sqrt(x_dist**2 + y_dist**2 + dalt**2)

                if distance < acceptance_radius:
                    self.stop()
                    self.set_mode("LOITER")
                    self.flying_to_target = False
                    return f"Success: Reached target ({lat:.7f}, {lon:.7f}, {alt:.1f}m)"

                speed = self.max_velocity if distance > 20 else max(0.5, distance / 4.0)
                vx = (x_dist / distance) * speed if distance > 0.1 else 0
                vy = (y_dist / distance) * speed if distance > 0.1 else 0
                vz = (dalt / distance) * speed if distance > 0.1 else 0

                if loop_count == 0:
                    bearing = math.atan2(y_dist, x_dist)
                    target_hdg = math.degrees(bearing)
                    if target_hdg < 0:
                        target_hdg += 360
                    self.rotate_to(target_hdg, timeout=45.0)

                self._send_position_target(vx, vy, vz, frame=mavutil.mavlink.MAV_FRAME_LOCAL_NED)
                loop_count += 1
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"fly_to_gps error: {e}")
            raise
        finally:
            self.flying_to_target = False
            self.set_mode("BRAKE")
            time.sleep(0.5)
            self.set_mode("LOITER")

        return "Failed: Timeout"

    def set_mode(self, mode: str) -> bool:
        """设置飞行模式"""
        if not self.connected or self.mavlink is None:
            return False

        mode_map = {
            "STABILIZE": 0,
            "GUIDED": 4,
            "LOITER": 5,
            "RTL": 6,
            "LAND": 9,
            "POSHOLD": 16,
            "BRAKE": 17,
        }
        if mode not in mode_map:
            logger.error(f"Unknown mode: {mode}")
            return False

        mode_id = mode_map[mode]
        self.mavlink.mav.command_long_send(
            self.mavlink.target_system,
            self.mavlink.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE,
            0,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
            0,
            0,
            0,
            0,
            0,
        )
        ack = self.mavlink.recv_match(type="COMMAND_ACK", blocking=True, timeout=3)
        return ack is not None and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def disconnect(self) -> None:
        """断开连接"""
        if self.mavlink:
            self.mavlink.close()
        self.connected = False
        logger.info("Disconnected from MAVLink")

    def subscribe_telemetry(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """订阅遥测数据回调"""
        self._callbacks.append(callback)

    def get_telemetry(self) -> dict[str, Any]:
        """获取当前遥测快照"""
        for _ in range(5):
            self.update_telemetry(timeout=0.2)
        return self.telemetry.copy()
