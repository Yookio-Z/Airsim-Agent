"""状态快照与 vehicle 信息/参数/Setup 输出。

拆分自 tool_executor.py（ToolRuntime 方法按职责迁移，行为不变）。
"""

from __future__ import annotations

import inspect
import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .backends import BackendProfile, BackendRegistry, create_builtin_backend_registry
from .llm_protocol import validate_json_schema
from src.modules.formation import FLIGHT_ACTIONS, FormationController
from src.modules.safety_validator import FlightConstraint, SafetyValidator
from src.tools.manifest import manifest_metadata, list_tool_manifest


class ToolRuntimeSnapshotMixin:
    def _public_backend_profile(self) -> dict[str, Any] | None:
        if self.backend_profile is None:
            return None
        profile = self.backend_profile.to_public_dict()
        profile["capabilities"] = self._camera_capabilities(profile.get("capabilities") or {})
        if self.backend_id == "px4_mavlink" and self._real_vehicle:
            capabilities = dict(profile.get("capabilities") or {})
            capabilities.update({
                "real_vehicle": True,
                "simulated_vehicle": False,
                "requires_operator_approval": True,
            })
            profile["capabilities"] = capabilities
        return profile

    def _operation_contract(self, drone_status: dict[str, Any] | None = None) -> dict[str, Any]:
        """Describe the exact command and mission channel selected for this backend."""
        drone = drone_status or {}
        connected = bool(getattr(self.controller, "is_connected", False)) if self.controller is not None else False
        real_vehicle = bool((self._public_backend_profile() or {}).get("capabilities", {}).get("real_vehicle"))
        map_position_valid = bool(drone.get("map_position_valid", not real_vehicle))
        if self.backend_id == "px4_mavlink":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "real_px4" if real_vehicle else "px4_sitl",
                "command_channel": "MAVLink",
                "mission_channel": "PX4 native mission protocol",
                "mission_frame": "global_relative_alt",
                "return_channel": "PX4 native RTL mode",
                "position_source": drone.get("position_source") or "MAVLink telemetry",
                "map_position_valid": map_position_valid,
                "global_mission_ready": connected and map_position_valid,
            }
        if self.backend_id == "px4_ros2":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "px4_via_ros2",
                "command_channel": "ROS2 Provider Gateway",
                "mission_channel": "ROS2 offboard local path",
                "mission_frame": "local_ned",
                "return_channel": "ROS2 gateway PX4 RTL mode",
                "position_source": drone.get("position_source") or "PX4 ROS2 odometry",
                "map_position_valid": bool(drone.get("map_position_valid", False)),
                "global_mission_ready": False,
            }
        return {
            "backend": self.backend_id,
            "vehicle_kind": "simulation",
            "command_channel": "AirSim RPC",
            "mission_channel": "AirSim local path",
            "mission_frame": "local_ned",
            "return_channel": "AirSim local home path",
            "position_source": "AirSim NED + configured geodetic origin",
            "map_position_valid": connected,
            "global_mission_ready": connected,
        }

    def status_snapshot(self) -> dict[str, Any]:
        # Try a non-blocking lock so long connect/reconnect operations do not block status polling.
        if not self._lock.acquire(blocking=False):
            live_snapshot = self._busy_status_snapshot()
            if live_snapshot is not None:
                return live_snapshot
            if self._last_status_snapshot:
                cached = dict(self._last_status_snapshot)
                cached["busy"] = True
                return cached
            return {
                "ready": self.available,
                "init_error": self.init_error,
                "connected": False,
                "stale_connection": True,
                "busy": True,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": [],
                "backends": self.backend_registry.list_public(),
                "drone": None,
                "operation_contract": self._operation_contract(),
            }
        try:
            ready = self.ensure_ready()
            connected = False
            drone_status: dict[str, Any] | None = None
            stale_connection = False
            if ready and self.controller is not None:
                connected = bool(getattr(self.controller, "is_connected", False))
                if connected:
                    try:
                        drone_status = self.controller.get_status().to_dict()
                    except Exception as e:
                        drone_status = {"error": str(e)}
                        stale_connection = True
                    connected = bool(getattr(self.controller, "is_connected", False))
                    if self._status_is_stale(drone_status):
                        stale_connection = True
                        connected = False
            vehicles_status = self._vehicles_status(connected)
            # 已派发航线（fire-and-forget）的完成跟踪，供 UI 提示"飞行结束"
            flight_tasks: dict[str, Any] = {}
            update_tasks = getattr(self.controller, "update_flight_task_progress", None)
            if callable(update_tasks):
                try:
                    flight_tasks = update_tasks(vehicles_status) or {}
                except Exception:
                    flight_tasks = {}
            snapshot = {
                "ready": ready,
                "init_error": self.init_error,
                "connected": connected,
                "stale_connection": stale_connection,
                "busy": False,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": self.list_tool_cards() if ready else [],
                "backends": self.backend_registry.list_public(),
                "drone": drone_status,
                "vehicles": vehicles_status,
                "flight_tasks": flight_tasks,
                "operation_contract": self._operation_contract(drone_status),
            }
            self._last_status_snapshot = snapshot
            return dict(snapshot)
        finally:
            self._lock.release()

    def _vehicles_status(self, connected: bool) -> list[dict[str, Any]]:
        """Per-vehicle compact status for multi-vehicle backends (AirSim).

        Single-vehicle backends report one entry matching ``drone``; backends
        without per-vehicle status fall back to the default drone payload.
        """
        controller = self.controller
        if not connected or controller is None:
            return []
        try:
            names = list(controller.list_vehicles() or [])
        except Exception:
            names = []
        if not names:
            return []
        vehicles: list[dict[str, Any]] = []
        for name in names:
            try:
                status = controller.get_status(name).to_dict()
            except Exception as exc:
                status = {"vehicle_name": name, "error": str(exc)}
            status.setdefault("vehicle_name", name)
            vehicles.append(status)
        return vehicles

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return active vehicle link and firmware metadata for settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            connection_info = getattr(controller, "get_connection_info", None)
            if callable(connection_info):
                payload["connection"] = connection_info()
            if connected:
                firmware_info = getattr(controller, "get_firmware_info", None)
                if callable(firmware_info):
                    payload["firmware"] = firmware_info(force=bool(refresh))
                parameter_status = getattr(controller, "get_parameter_status", None)
                if callable(parameter_status):
                    payload["parameters"] = parameter_status()
            return payload
        finally:
            self._lock.release()

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return active vehicle parameters for the settings UI and Agent."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "parameters": [],
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "parameters": [],
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_parameters = getattr(controller, "get_parameters", None)
            if not callable(get_parameters):
                payload["status"] = "error"
                payload["message"] = "parameter download is not supported by this backend"
                return payload
            data = get_parameters(
                refresh=bool(refresh),
                timeout=float(timeout),
                query=str(query or ""),
                limit=int(limit),
                offset=int(offset),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one PX4 parameter through the active MAVLink backend."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            set_parameter = getattr(controller, "set_parameter", None)
            if not callable(set_parameter):
                payload["status"] = "error"
                payload["message"] = "parameter write is not supported by this backend"
                return payload
            data = set_parameter(
                name=str(name or ""),
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=float(timeout or 3.0),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return QGC-style read-only PX4 setup diagnostics for the settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_setup_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle setup diagnostics are not supported by this backend"
                return payload
            data = get_snapshot(include_history=include_history, history_limit=history_limit)
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return lightweight live vehicle telemetry for high-frequency settings UI updates."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_telemetry_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle live telemetry is not supported by this backend"
                return payload
            data = get_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def _busy_status_snapshot(self) -> dict[str, Any] | None:
        """Best-effort live telemetry while a long-running control tool owns the runtime lock.

        Long local waypoint execution can hold ToolRuntime._lock for many seconds. Returning
        only the last cached snapshot during that window makes the frontend marker jump from
        the initial frame to the final frame. This method reads telemetry only; it never sends
        flight-control commands. MAVLink backends can expose get_cached_status() so this read
        does not consume command ACK / mission-transfer messages while control is active.
        """
        controller = self.controller
        if controller is None:
            return None

        ready = self.available
        connected = bool(getattr(controller, "is_connected", False))
        if not ready or not connected:
            return None

        stale_connection = False
        try:
            get_cached_status = getattr(controller, "get_cached_status", None)
            if callable(get_cached_status):
                drone_status = get_cached_status().to_dict()
            else:
                drone_status = controller.get_status().to_dict()
        except Exception as exc:
            drone_status = {"error": str(exc)}
            stale_connection = True

        connected = bool(getattr(controller, "is_connected", False))
        if self._status_is_stale(drone_status):
            stale_connection = True
            connected = False

        cached = self._last_status_snapshot or {}
        # vehicles: reuse the cached list only — a fresh per-vehicle status
        # read is an RPC that would queue behind the tool call currently
        # holding the lock. Without the cached list the frontend vehicle
        # panel empties whenever any long tool call runs, flickering between
        # "3 drones" and "none".
        vehicles_cached = cached.get("vehicles") or []
        flight_tasks_busy: dict[str, Any] = {}
        update_tasks_busy = getattr(controller, "update_flight_task_progress", None)
        if callable(update_tasks_busy):
            try:
                flight_tasks_busy = update_tasks_busy(vehicles_cached) or {}
            except Exception:
                flight_tasks_busy = {}
        snapshot = {
            "ready": ready,
            "init_error": self.init_error,
            "connected": connected,
            "stale_connection": stale_connection,
            "busy": True,
            "backend": self.backend_id,
            "backend_profile": cached.get("backend_profile") or self._public_backend_profile(),
            "tool_cards": cached.get("tool_cards") or [],
            "backends": cached.get("backends") or self.backend_registry.list_public(),
            "drone": drone_status,
            "vehicles": vehicles_cached,
            "flight_tasks": flight_tasks_busy,
        }
        self._last_status_snapshot = snapshot
        return dict(snapshot)
