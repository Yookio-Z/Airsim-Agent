"""飞行工具组：链路/遥测/飞行指令（后端中立的 drone_* 原子工具）。

- 注册入口：``register_flight_tools(collector, controller, fmt)``
- 调用方：``src/agent/tool_executor.py`` 的 ``ToolRuntime.ensure_ready()``（每个后端都注册）
- 相邻文件：任务工具在 ``mission.py``（GCS 专用）、传感器在 ``sensors.py``（仅 AirSim）、
  看图/测距在 ``perception_axis.py``（相机链路）、ROS2 provider 在 ``providers.py``。
"""

from __future__ import annotations

import json
import math
from typing import Callable

from src.modules.backends.base import FlightController


def register_flight_tools(
    mcp,
    controller: FlightController,
    fmt_result: Callable[[dict], str],
) -> None:
    """Register backend-neutral core tools against a FlightController."""

    def _resolve_target_vehicles(vehicle_name: str) -> list[str]:
        """Map tool-level vehicle_name onto concrete vehicle names.

        ""   -> the default vehicle only (never an implicit broadcast): the
                sole vehicle when the backend has one, else the first listed.
        "all" -> every vehicle reported by the backend.
        name  -> that exact vehicle.
        """
        try:
            vehicles = list(controller.list_vehicles() or [])
        except Exception:
            vehicles = []
        if not vehicle_name:
            if len(vehicles) <= 1:
                return vehicles or [""]
            return [vehicles[0]]
        if str(vehicle_name).strip().lower() == "all":
            return vehicles or [""]
        return [str(vehicle_name)]

    def _run_for_vehicles(vehicle_name: str, action) -> tuple[bool, list[str]]:
        """Run action(name) for each resolved vehicle; all must succeed."""
        targets = _resolve_target_vehicles(vehicle_name)
        ok = True
        for name in targets:
            if not action(name):
                ok = False
                break
        return ok, targets

    def finite_float(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def heading_deg_from_status(status) -> float:
        heading = finite_float(getattr(status, "extra", {}).get("heading_deg"))
        if heading is not None:
            return heading % 360.0
        attitude = getattr(status, "attitude_rad", None)
        yaw = finite_float(attitude.get("yaw") if isinstance(attitude, dict) else None)
        if yaw is not None:
            return math.degrees(yaw) % 360.0
        return 0.0

    def status_payload() -> dict:
        try:
            status = controller.get_status()
            payload = status.to_dict()
            payload.setdefault("heading_deg", round(heading_deg_from_status(status), 3))
            return payload
        except Exception as exc:
            return {"telemetry_error": str(exc)}

    def action_error(default_message: str) -> tuple[str, str]:
        detail = str(getattr(controller, "last_error", "") or "").strip()
        if not detail:
            return default_message, ""
        return f"{default_message}: {detail}", detail

    @mcp.tool()
    def drone_connect(
        ip: str = "127.0.0.1",
        port: int = 41452,
        url: str = "",
        fallback_url: str = "",
        remote_host: str = "",
        remote_port: int = 0,
        real_vehicle: bool = False,
    ) -> str:
        """Connect to the active vehicle backend.

        AirSim example: drone_connect(ip="127.0.0.1", port=41452)
        MAVLink example: drone_connect(url="udp:127.0.0.1:14550")

        real_vehicle=true 会打开真机审批门：改变位置/模式/解锁的动作必须人工
        确认。接真机时务必显式传 true——它决定这套门是否生效。
        """
        kwargs: dict = {}
        # real_vehicle 两条路径都要带。只在 url 分支传的话，调用方用 ip/port
        # 连真机时标志会丢掉，审批门静默失效。
        kwargs["real_vehicle"] = bool(real_vehicle)
        if url:
            kwargs["url"] = url
            if fallback_url:
                kwargs["fallback_url"] = fallback_url
            if remote_host:
                kwargs["remote_host"] = remote_host
            if remote_port:
                kwargs["remote_port"] = int(remote_port)
        else:
            kwargs["ip"] = ip
            kwargs["port"] = port
        info = controller.connect(**kwargs)
        return fmt_result(info.to_dict())

    @mcp.tool()
    def drone_disconnect() -> str:
        """Disconnect from the active vehicle backend."""
        controller.disconnect()
        return fmt_result({"status": "disconnected", "backend": controller.backend_name})

    @mcp.tool()
    def drone_arm(vehicle_name: str = "") -> str:
        """Arm the vehicle motors (default vehicle; "all" arms every vehicle)."""
        # 幂等：已经解锁就别再发一次解锁指令。计划里写死的"先解锁"在飞机已经
        # 解锁时纯属空转，有些飞控还会对重复解锁报错，让整份计划在第一步就翻车。
        if not vehicle_name:
            status = status_payload()
            if status.get("armed"):
                return fmt_result({
                    "status": "ok",
                    "backend": controller.backend_name,
                    "message": "already armed（已解锁，跳过重复解锁）",
                    "skipped": True,
                    "vehicles": [],
                })
        ok, targets = _run_for_vehicles(vehicle_name, controller.arm)
        message, detail = action_error("arm failed") if not ok else ("motors armed", "")
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": message,
            "vehicles": targets,
        }
        if detail:
            payload["error_detail"] = detail
        return fmt_result(payload)

    @mcp.tool()
    def drone_disarm(vehicle_name: str = "") -> str:
        """Disarm the vehicle motors (default vehicle; "all" disarms every vehicle)."""
        ok, targets = _run_for_vehicles(vehicle_name, controller.disarm)
        message, detail = action_error("disarm failed") if not ok else ("motors disarmed", "")
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": message,
            "vehicles": targets,
        }
        if detail:
            payload["error_detail"] = detail
        return fmt_result(payload)

    @mcp.tool()
    def drone_takeoff(altitude: float = 3.0, vehicle_name: str = "") -> str:
        """Take off to the requested positive altitude in meters (default vehicle; "all" for every vehicle)."""
        # 幂等（也是安全项）：已经在空中时再执行一次起飞会让飞机意外爬升。
        # 计划模板里写死的"先起飞"遇到已经在空中的飞机必须变成空操作。
        if not vehicle_name:
            status = status_payload()
            pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
            alt = abs(float((pos or {}).get("z", 0.0) or 0.0))
            # 只认"明确在空中"：高度字段在拿不到真实遥测时会给出占位值，靠它判断
            # 会把地面上的飞机当成在空中、把起飞整个跳过。
            if status.get("flying") is True or status.get("landed_state") in (2, "flying"):
                return fmt_result({
                    "status": "ok",
                    "backend": controller.backend_name,
                    "message": f"already airborne（当前高度约 {alt:.1f}m，跳过重复起飞）",
                    "skipped": True,
                    "vehicles": [],
                    **status,
                })
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.takeoff(altitude, name))
        message, detail = action_error("takeoff failed") if not ok else (f"takeoff complete ({altitude}m)", "")
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": message,
            "vehicles": targets,
        }
        if detail:
            payload["error_detail"] = detail
        payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_land(vehicle_name: str = "") -> str:
        """Land the vehicle (default vehicle; "all" for every vehicle)."""
        ok, targets = _run_for_vehicles(vehicle_name, controller.land)
        status = status_payload()
        pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
        alt = abs(float((pos or {}).get("z", 0.0) or 0.0))
        grounded = (not status.get("flying")) and (not status.get("armed") or alt < 0.5)
        # 降落是"指令已下达"而非"已落地"：下落需要时间，这里如实区分，
        # 避免上层在飞机仍在 2~3m 下沉时就认定任务收尾完成。
        if ok:
            message = "landing complete" if grounded else "landing commanded (descending)"
        else:
            message = "landing failed"
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": message,
            "vehicles": targets,
        }
        if not ok:
            _, detail = action_error("landing failed")
            if detail:
                payload["error_detail"] = detail
        payload.update(status)
        return fmt_result(payload)

    @mcp.tool()
    def drone_hover(vehicle_name: str = "") -> str:
        """Hold the current position or stop motion (default vehicle; "all" for every vehicle)."""
        ok, targets = _run_for_vehicles(vehicle_name, controller.hover)
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": "holding position" if ok else "hover failed",
            "vehicles": targets,
        }
        payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_fly_to(x: float, y: float, z: float, velocity: float = 2.0, vehicle_name: str = "") -> str:
        """Fly to an absolute local NED coordinate (default vehicle; "all" for every vehicle).

        X is north meters, Y is east meters, Z is down meters. Negative Z means
        altitude above the local origin.
        """
        fx, fy, fz = finite_float(x), finite_float(y), finite_float(z)
        if fx is None or fy is None or fz is None:
            return fmt_result({
                "status": "error",
                "backend": controller.backend_name,
                "message": "invalid parameters: x/y/z must be finite numbers (got null)",
                "received": {"x": x, "y": y, "z": z},
            })
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.move_to_position(fx, fy, fz, velocity, name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"target reached ({fx}, {fy}, {fz})" if ok else "position command failed",
            "vehicles": targets,
        }
        payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_move_relative(
        forward_m: float = 0.0,
        right_m: float = 0.0,
        up_m: float = 0.0,
        velocity: float = 2.0,
        vehicle_name: str = "",
    ) -> str:
        """Move relative to the current vehicle heading (default vehicle; "all" for every vehicle)."""
        f_forward = finite_float(forward_m)
        f_right = finite_float(right_m)
        f_up = finite_float(up_m)
        f_velocity = finite_float(velocity)
        if None in (f_forward, f_right, f_up, f_velocity):
            return fmt_result({
                "status": "error",
                "backend": controller.backend_name,
                "message": (
                    "invalid parameters: forward_m/right_m/up_m/velocity must be finite numbers"
                ),
                "received": {
                    "forward_m": forward_m, "right_m": right_m,
                    "up_m": up_m, "velocity": velocity,
                },
            })
        forward_m, right_m, up_m, velocity = f_forward, f_right, f_up, f_velocity
        targets = _resolve_target_vehicles(vehicle_name)
        ok = True
        pos: dict = {}
        heading_deg = 0.0
        target_position: dict = {}
        for name in targets:
            status = controller.get_status(name)
            pos = status.position_ned or {"x": 0.0, "y": 0.0, "z": 0.0}
            heading_deg = heading_deg_from_status(status)
            heading_rad = math.radians(heading_deg)

            dx = math.cos(heading_rad) * forward_m + math.cos(heading_rad + math.pi / 2) * right_m
            dy = math.sin(heading_rad) * forward_m + math.sin(heading_rad + math.pi / 2) * right_m
            target_x = float(pos.get("x", 0.0)) + dx
            target_y = float(pos.get("y", 0.0)) + dy
            target_z = float(pos.get("z", 0.0)) - up_m
            target_position = {"x": round(target_x, 3), "y": round(target_y, 3), "z": round(target_z, 3)}

            if not controller.move_to_position(target_x, target_y, target_z, velocity, name):
                ok = False
                break
        return fmt_result(
            {
                "status": "ok" if ok else "error",
                "backend": controller.backend_name,
                "message": (
                    f"relative move complete: forward={forward_m}, right={right_m}, up={up_m}; "
                    f"target_ned=({target_x:.2f}, {target_y:.2f}, {target_z:.2f})"
                )
                if ok
                else "relative move failed",
                "vehicles": targets,
                "start_position_ned": pos,
                "heading_deg": heading_deg,
                "target_position_ned": target_position,
            }
        )

    @mcp.tool()
    def drone_fly_path(waypoints: str, velocity: float = 2.0, vehicle_name: str = "") -> str:
        """Fly a local NED waypoint path (default vehicle; "all" for every vehicle).

        `waypoints` 两种写法（快速调用用第一种）：
        - 紧凑串 "x,y,z;x,y,z"：分号分段、逗号分轴，例如 "10,0,-5;20,0,-5;30,0,-5"；
        - JSON 数组：[{"x":10,"y":0,"z":-5}, ...]（GCS/程序化调用用）。
        """
        text = str(waypoints or "").strip()
        if not text:
            return fmt_result({"status": "error", "message": "waypoints must not be empty"})
        if text[0] in "[{":
            try:
                waypoints = json.loads(text)
            except json.JSONDecodeError as exc:
                return fmt_result({"status": "error", "message": f"invalid waypoint JSON: {exc}"})
        else:
            parsed: list[dict] = []
            for index, segment in enumerate(text.split(";")):
                parts = [part.strip() for part in segment.split(",")]
                if len(parts) != 3 or not segment.strip():
                    return fmt_result({
                        "status": "error",
                        "message": f"waypoint #{index} 需要 'x,y,z'（或用 JSON 数组整段传入）: {segment!r}",
                    })
                try:
                    parsed.append({"x": float(parts[0]), "y": float(parts[1]), "z": float(parts[2])})
                except ValueError:
                    return fmt_result({
                        "status": "error",
                        "message": f"waypoint #{index} 的坐标不是数字: {segment!r}",
                    })
            waypoints = parsed

        if not isinstance(waypoints, list) or not waypoints:
            return fmt_result({"status": "error", "message": "waypoints must be a non-empty list"})

        f_velocity = finite_float(velocity)
        if f_velocity is None:
            return fmt_result({
                "status": "error",
                "message": f"invalid parameters: velocity must be a finite number (got {velocity!r})",
            })
        velocity = f_velocity
        # 航点值同样必须是有限数：NaN 会让 move_on_path 内部的到达判断永远为假，
        # 飞机在"未到达"状态下无限等待；Inf 则被当成合法目标点直接下发。
        cleaned_waypoints = []
        for index, waypoint in enumerate(waypoints):
            if not isinstance(waypoint, dict):
                return fmt_result({
                    "status": "error",
                    "message": f"waypoint #{index} must be an object with x, y, z",
                })
            coords = {}
            for axis in ("x", "y", "z"):
                value = finite_float(waypoint.get(axis))
                if value is None:
                    return fmt_result({
                        "status": "error",
                        "message": (
                            f"waypoint #{index} has a non-finite {axis}: "
                            f"{waypoint.get(axis)!r}"
                        ),
                    })
                coords[axis] = value
            cleaned_waypoints.append({**waypoint, **coords})
        waypoints = cleaned_waypoints

        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.move_on_path(waypoints, velocity, name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"path complete ({len(waypoints)} waypoints)" if ok else "path command failed",
            "vehicles": targets,
        }
        get_last_path_error = getattr(controller, "get_last_path_error", None)
        if not ok and callable(get_last_path_error):
            payload["path_error"] = get_last_path_error()
            payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_get_status(vehicle_name: str = "") -> str:
        """Read vehicle status. Without vehicle_name this returns EVERY
        vehicle in one call (preferred: one query covers the whole fleet);
        with a specific name it reads that vehicle only."""
        if not vehicle_name:
            # 机队清单就来自这里（原 drone_list_vehicles 已合并进本工具）：
            # 先强制重读一次载具列表，后端不支持 refresh 参数时退回缓存读取。
            try:
                names = [str(n) for n in (controller.list_vehicles(refresh=True) or []) if str(n)]
            except TypeError:
                try:
                    names = [str(n) for n in (controller.list_vehicles() or []) if str(n)]
                except Exception:
                    names = []
            except Exception:
                names = []
            if len(names) > 1:
                vehicles = []
                for name in names:
                    try:
                        vehicles.append({"vehicle_name": name, **controller.get_status(name).to_dict()})
                    except Exception as exc:
                        vehicles.append({"vehicle_name": name, "status": "error", "message": str(exc)[:160]})
                return fmt_result(
                    {
                        "status": "ok",
                        "backend": controller.backend_name,
                        "vehicle_count": len(vehicles),
                        "vehicles": vehicles,
                        **(vehicles[0] if vehicles else {}),
                    }
                )
        try:
            status = controller.get_status(vehicle_name)
        except TypeError:
            # backends whose get_status has no vehicle_name parameter
            status = controller.get_status()
        payload = {
            "status": "ok",
            "backend": controller.backend_name,
            "vehicle_name": vehicle_name or getattr(status, "vehicle_name", ""),
            **status.to_dict(),
        }
        # 控制器用 extra.connection_error 表示"这次读取失败了"，但 DroneStatus
        # .to_dict() 里没有 status 字段，而上面是硬编码 "ok"，于是链路已断时这个
        # 工具仍然报成功：执行器不会重连、AgentLoop 的连接熔断永不触发，而位置
        # 读数只是 {0,0,0} 的占位值，却被下游当成真实遥测。
        connection_error = str(payload.get("connection_error") or "").strip()
        if connection_error:
            payload["status"] = "error"
            payload["message"] = f"遥测读取失败: {connection_error}"
        return fmt_result(payload)

    @mcp.tool()
    def drone_set_mode(mode: str, vehicle_name: str = "") -> str:
        """Set the flight mode when supported by the active backend."""
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.set_mode(mode, vehicle_name=name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"mode set to {mode}" if ok else f"failed to set mode {mode}",
            "vehicles": targets,
        }
        payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_rotate_to(heading_deg: float, vehicle_name: str = "") -> str:
        """Rotate to an absolute heading in degrees. 0 is north (default vehicle; "all" for every vehicle)."""
        target_heading = float(heading_deg) % 360.0
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.rotate_to_heading(target_heading, vehicle_name=name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"rotated to {target_heading:g} deg" if ok else "rotation failed",
            "target_heading_deg": round(target_heading, 3),
            "vehicles": targets,
        }
        payload.update(status_payload())
        return fmt_result(payload)
