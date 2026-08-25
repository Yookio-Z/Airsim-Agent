"""Core atomic tools for vehicle link, telemetry, commands, and missions."""

from __future__ import annotations

import json
import math
from typing import Callable

from src.modules.flight_controller import FlightController


def register_core_tools(
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
        """
        kwargs: dict = {}
        if url:
            kwargs["url"] = url
            if fallback_url:
                kwargs["fallback_url"] = fallback_url
            if remote_host:
                kwargs["remote_host"] = remote_host
            if remote_port:
                kwargs["remote_port"] = int(remote_port)
            kwargs["real_vehicle"] = bool(real_vehicle)
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
    def drone_list_vehicles() -> str:
        """List vehicles known to the active backend."""
        # LLM-facing tool: low call frequency, so a fresh re-read is fine
        # (the UI keeps its own cached copy via the local list_vehicles());
        # backends without the refresh parameter fall back to their default
        try:
            vehicles = controller.list_vehicles(refresh=True)
        except TypeError:
            vehicles = controller.list_vehicles()
        return fmt_result(
            {
                "status": "ok",
                "backend": controller.backend_name,
                "vehicle_count": len(vehicles),
                "vehicles": vehicles,
            }
        )

    @mcp.tool()
    def drone_get_firmware_info(force: bool = False) -> str:
        """Read MAVLink AUTOPILOT_VERSION firmware and board metadata."""
        method = getattr(controller, "get_firmware_info", None)
        if not callable(method):
            return fmt_result({
                "status": "error",
                "backend": controller.backend_name,
                "message": "firmware information is not supported by this backend",
            })
        data = method(force=bool(force))
        if not isinstance(data, dict):
            data = {"result": data}
        return fmt_result({
            "status": data.get("status", "ok"),
            "backend": controller.backend_name,
            **data,
        })

    @mcp.tool()
    def drone_get_parameters(query: str = "", limit: int = 50, refresh: bool = False) -> str:
        """Read PX4 MAVLink parameters from the cached PARAM_VALUE list.

        Args:
            query: Optional case-insensitive name/value/type filter, for example
                "BAT", "SYS_AUTOSTART", or "COM_".
            limit: Maximum number of matching parameters to return.
            refresh: Request a new PARAM_REQUEST_LIST download before returning.
        """
        method = getattr(controller, "get_parameters", None)
        if not callable(method):
            return fmt_result({
                "status": "error",
                "backend": controller.backend_name,
                "message": "parameter download is not supported by this backend",
                "parameters": [],
            })
        try:
            clean_limit = max(0, min(int(limit), 200))
        except (TypeError, ValueError):
            clean_limit = 50
        data = method(
            refresh=bool(refresh),
            query=str(query or ""),
            limit=clean_limit,
            offset=0,
            timeout=20.0,
        )
        if not isinstance(data, dict):
            data = {"result": data}
        return fmt_result({
            "status": data.get("status", "ok"),
            "backend": controller.backend_name,
            **data,
        })

    @mcp.tool()
    def drone_arm(vehicle_name: str = "") -> str:
        """Arm the vehicle motors (default vehicle; "all" arms every vehicle)."""
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
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": "landing complete" if ok else "landing failed",
            "vehicles": targets,
        }
        if not ok:
            _, detail = action_error("landing failed")
            if detail:
                payload["error_detail"] = detail
        payload.update(status_payload())
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
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.move_to_position(x, y, z, velocity, name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"target reached ({x}, {y}, {z})" if ok else "position command failed",
            "vehicles": targets,
        }
        payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_fly_velocity(vx: float, vy: float, vz: float, duration: float = 0.0, vehicle_name: str = "") -> str:
        """Command local NED velocity for a duration (default vehicle; "all" for every vehicle).

        Duration 0 sends one backend update when supported.
        """
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: controller.move_by_velocity(vx, vy, vz, duration, name))
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"velocity command sent ({vx}, {vy}, {vz})" if ok else "velocity command failed",
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
    def drone_fly_path(waypoints_json: str, velocity: float = 2.0, vehicle_name: str = "") -> str:
        """Fly a local NED waypoint path (default vehicle; "all" for every vehicle).

        `waypoints_json` must be a JSON array of objects with x, y, and z.
        """
        try:
            waypoints = json.loads(waypoints_json)
        except json.JSONDecodeError as exc:
            return fmt_result({"status": "error", "message": f"invalid waypoint JSON: {exc}"})

        if not isinstance(waypoints, list) or not waypoints:
            return fmt_result({"status": "error", "message": "waypoints must be a non-empty list"})

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
    def drone_dispatch_takeoff(altitude: float = 3.0, vehicle_name: str = "") -> str:
        """Dispatch a non-blocking takeoff (fire-and-forget; returns immediately).

        多机并发起飞用：先对每架车 dispatch_takeoff，等各机 airborne 后再逐机
        dispatch_path。需要等待起飞完成才能继续时，用 drone_takeoff（阻塞版）。
        """
        dispatch = getattr(controller, "dispatch_takeoff", None)
        if not callable(dispatch):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "dispatch takeoff is not supported by this backend",
                }
            )
        altitude = max(0.5, abs(float(altitude or 3.0)))
        ok, targets = _run_for_vehicles(vehicle_name, lambda name: dispatch(altitude, name))
        return fmt_result(
            {
                "status": "ok" if ok else "error",
                "backend": controller.backend_name,
                "message": f"takeoff dispatched ({altitude}m, non-blocking)" if ok else "takeoff dispatch failed",
                "vehicles": targets,
            }
        )

    @mcp.tool()
    def drone_dispatch_path(waypoints_json: str, velocity: float = 2.0, vehicle_name: str = "") -> str:
        """Dispatch a non-blocking waypoint path (fire-and-forget; returns immediately).

        多机各自执行各自航线用：每架车派发自己的路径后立即返回，不等待完成。
        需要确认路径飞完再继续时，用 drone_fly_path（阻塞版）。
        `waypoints_json` must be a JSON array of objects with x, y, and z.
        """
        dispatch = getattr(controller, "dispatch_move_on_path", None)
        if not callable(dispatch):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "dispatch path is not supported by this backend",
                }
            )
        try:
            waypoints = json.loads(waypoints_json)
        except json.JSONDecodeError as exc:
            return fmt_result({"status": "error", "message": f"invalid waypoint JSON: {exc}"})

        if not isinstance(waypoints, list) or not waypoints:
            return fmt_result({"status": "error", "message": "waypoints must be a non-empty list"})

        ok, targets = _run_for_vehicles(
            vehicle_name,
            lambda name: dispatch([dict(wp) for wp in waypoints if isinstance(wp, dict)], velocity, name),
        )
        payload = {
            "status": "ok" if ok else "error",
            "backend": controller.backend_name,
            "message": f"path dispatched ({len(waypoints)} waypoints, non-blocking)" if ok else "path dispatch failed",
            "vehicles": targets,
        }
        if not ok:
            payload.update(status_payload())
        return fmt_result(payload)

    @mcp.tool()
    def drone_upload_mission(waypoints_json: str) -> str:
        """Upload a backend mission.

        The payload can be an array of MissionItem objects or a MissionPlanDraft
        object with `items` or `waypoints`.
        """
        method = getattr(controller, "upload_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission upload is not supported by this backend",
                }
            )
        try:
            payload = json.loads(waypoints_json)
        except json.JSONDecodeError as exc:
            return fmt_result({"status": "error", "message": f"invalid mission JSON: {exc}"})
        if isinstance(payload, dict):
            waypoints = payload.get("items") or payload.get("waypoints") or []
        else:
            waypoints = payload
        if not isinstance(waypoints, list):
            return fmt_result({"status": "error", "message": "mission items must be a list"})
        return fmt_result(dict(method([item for item in waypoints if isinstance(item, dict)])))

    @mcp.tool()
    def drone_download_mission() -> str:
        """Download the current backend mission."""
        method = getattr(controller, "download_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission download is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))

    @mcp.tool()
    def drone_clear_mission() -> str:
        """Clear the current backend mission."""
        method = getattr(controller, "clear_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission clear is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))

    @mcp.tool()
    def drone_start_mission() -> str:
        """Start the mission already uploaded to the backend."""
        method = getattr(controller, "start_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission start is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))

    @mcp.tool()
    def drone_get_mission_progress() -> str:
        """Read current mission progress from the backend."""
        method = getattr(controller, "get_mission_progress", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission progress is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))

    @mcp.tool()
    def drone_get_status(vehicle_name: str = "") -> str:
        """Read vehicle status. Without vehicle_name this returns EVERY
        vehicle in one call (preferred: one query covers the whole fleet);
        with a specific name it reads that vehicle only."""
        if not vehicle_name:
            try:
                names = [str(n) for n in (controller.list_vehicles() or []) if str(n)]
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
        return fmt_result(
            {
                "status": "ok",
                "backend": controller.backend_name,
                "vehicle_name": vehicle_name or getattr(status, "vehicle_name", ""),
                **status.to_dict(),
            }
        )

    @mcp.tool()
    def drone_set_mode(mode: str) -> str:
        """Set the flight mode when supported by the active backend."""
        ok = controller.set_mode(mode)
        return fmt_result(
            {
                "status": "ok" if ok else "error",
                "backend": controller.backend_name,
                "message": f"mode set to {mode}" if ok else f"failed to set mode {mode}",
            }
        )

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
