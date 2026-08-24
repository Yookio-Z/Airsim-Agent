"""Minimal ground-station service facade over the current ToolRuntime.

The first implementation deliberately wraps the existing runtime instead of
replacing controllers. This gives UI and Agent code a shared GCS boundary while
leaving the AirSim path stable and keeping room for PX4 mission upload, ROS2,
and real-vehicle adapters behind the same managers.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from src.agent.tool_executor import ToolCallResult, ToolRuntime

from .managers import ManagerResult
from .mission import GeoPoint, MissionPlanDraft
from .state import GroundStationState, LinkState, MissionState, SafetyState, VehicleTelemetry


SupervisorStatusProvider = Callable[[], dict[str, Any]]
CurrentRunProvider = Callable[[], dict[str, Any] | None]


class ToolTelemetryManager:
    """Telemetry manager backed by ToolRuntime.status_snapshot()."""

    def __init__(
        self,
        tools: ToolRuntime,
        supervisor_status_provider: SupervisorStatusProvider | None = None,
        current_run_provider: CurrentRunProvider | None = None,
        mission_state_provider: Callable[[], MissionState] | None = None,
    ) -> None:
        self._tools = tools
        self._supervisor_status_provider = supervisor_status_provider
        self._current_run_provider = current_run_provider
        self._mission_state_provider = mission_state_provider

    def get_vehicle(self, vehicle_id: str = "") -> VehicleTelemetry | None:
        state = self.get_state()
        if not state.vehicle:
            return None
        if vehicle_id and state.vehicle.vehicle_id and state.vehicle.vehicle_id != vehicle_id:
            return None
        return state.vehicle

    def get_state(self) -> GroundStationState:
        snapshot = self._tools.status_snapshot()
        supervisor = self._supervisor_status_provider() if self._supervisor_status_provider else {}
        current_run = self._current_run_provider() if self._current_run_provider else None
        state = GroundStationState.from_tool_runtime(snapshot, supervisor=supervisor, current_run=current_run)
        if self._mission_state_provider:
            state.mission = self._mission_state_provider()
        return state

    def refresh(self) -> GroundStationState:
        return self.get_state()


class ToolLinkManager:
    """Link manager for the active backend connection."""

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager) -> None:
        self._tools = tools
        self._telemetry = telemetry

    def list_links(self) -> list[LinkState]:
        return [self.status()]

    def connect(self, transport: str = "", endpoint: str = "", **params: Any) -> ManagerResult:
        payload = dict(params)
        backend = str(self._tools.status_snapshot().get("backend") or "")
        transport_key = transport.strip().lower()

        if endpoint:
            if backend == "px4_mavlink" or transport_key in {"mavlink", "udp", "tcp", "serial", "px4"}:
                payload["url"] = endpoint
            else:
                host, port = _split_host_port(endpoint)
                if host:
                    payload["ip"] = host
                if port:
                    payload["port"] = port

        result = self._tools.execute("drone_connect", payload, dry_run=False, allow_reconnect=False)
        return _manager_result_from_tool(result)

    def disconnect(self, link_id: str = "") -> ManagerResult:
        result = self._tools.execute("drone_disconnect", {}, dry_run=False, allow_reconnect=False)
        return _manager_result_from_tool(result)

    def status(self) -> LinkState:
        return self._telemetry.get_state().link


class ToolVehicleManager:
    """Vehicle discovery backed by the core drone_list_vehicles tool."""

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager) -> None:
        self._tools = tools
        self._telemetry = telemetry
        self._active_vehicle_id = ""

    def list_vehicles(self) -> list[dict[str, Any]]:
        result = self._tools.execute("drone_list_vehicles", {}, dry_run=False)
        vehicles = result.data.get("vehicles") if result.ok else []
        return [dict(v) for v in vehicles] if isinstance(vehicles, list) else []

    def active_vehicle_id(self) -> str:
        if self._active_vehicle_id:
            return self._active_vehicle_id
        vehicle = self._telemetry.get_vehicle()
        return vehicle.vehicle_id if vehicle else ""

    def set_active_vehicle(self, vehicle_id: str) -> ManagerResult:
        self._active_vehicle_id = vehicle_id.strip()
        return ManagerResult(True, "active vehicle selected", {"vehicle_id": self._active_vehicle_id})

    def remove_vehicle(self, vehicle_id: str) -> ManagerResult:
        if vehicle_id == self._active_vehicle_id:
            self._active_vehicle_id = ""
        return ManagerResult(True, "vehicle removed from local selection", {"vehicle_id": vehicle_id})


class ToolSafetyManager:
    """Safety manager that reuses ToolRuntime validation and supervisor state."""

    _ALLOWED_WHILE_STOPPED = {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect"}

    def __init__(
        self,
        tools: ToolRuntime,
        supervisor: Any | None = None,
        supervisor_status_provider: SupervisorStatusProvider | None = None,
    ) -> None:
        self._tools = tools
        self._supervisor = supervisor
        self._supervisor_status_provider = supervisor_status_provider

    def state(self) -> SafetyState:
        supervisor = self._supervisor_status_provider() if self._supervisor_status_provider else {}
        constraints = self._constraint_details()
        return SafetyState(
            emergency_stop=bool(supervisor.get("emergency_stop", False)),
            paused=bool(supervisor.get("paused", False)),
            details={"supervisor": supervisor, "constraints": constraints},
        )

    def validate_command(self, command: str, params: dict[str, Any], state: GroundStationState) -> ManagerResult:
        if state.safety.emergency_stop and command not in self._ALLOWED_WHILE_STOPPED:
            return ManagerResult(False, "emergency stop is active", {"level": "danger"})

        result = self._tools.validate(command, dict(params or {}))
        level = str(result.get("level") or "safe")
        ok = level != "danger"
        message = "command accepted" if ok else "command blocked by safety layer"
        return ManagerResult(ok, message, result)

    def validate_mission(self, draft: MissionPlanDraft, state: GroundStationState) -> ManagerResult:
        warnings = draft.recalculate().validation_warnings
        if not state.capabilities.get("flight_control", False):
            warnings = [*warnings, "backend_without_flight_control"]
        ok = not warnings and not state.safety.emergency_stop
        message = "mission accepted" if ok else "mission blocked by safety layer"
        return ManagerResult(ok, message, {"warnings": warnings, "mission": draft.to_dict()})

    def emergency_stop(self) -> ManagerResult:
        if self._supervisor and hasattr(self._supervisor, "emergency_stop"):
            self._supervisor.emergency_stop()
        return ManagerResult(True, "emergency stop active", self.state().to_dict())

    def reset_emergency(self) -> ManagerResult:
        if self._supervisor and hasattr(self._supervisor, "reset_emergency"):
            self._supervisor.reset_emergency()
        return ManagerResult(True, "emergency stop reset", self.state().to_dict())

    def _constraint_details(self) -> dict[str, Any]:
        constraints = getattr(getattr(self._tools, "safety", None), "constraints", None)
        if constraints is None:
            return {}
        return {
            "min_altitude": getattr(constraints, "min_altitude", None),
            "max_altitude": getattr(constraints, "max_altitude", None),
            "max_velocity": getattr(constraints, "max_velocity", None),
            "geofence_radius": getattr(constraints, "max_distance_from_home", None),
        }


class ToolCommandManager:
    """High-level vehicle commands routed through safety validation."""

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager, safety: ToolSafetyManager) -> None:
        self._tools = tools
        self._telemetry = telemetry
        self._safety = safety

    def arm(self, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_arm", {}, vehicle_id=vehicle_id)

    def disarm(self, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_disarm", {}, vehicle_id=vehicle_id)

    def takeoff(self, altitude_m: float, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_takeoff", {"altitude": altitude_m}, vehicle_id=vehicle_id)

    def land(self, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_land", {}, vehicle_id=vehicle_id)

    def hold(self, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_hover", {}, vehicle_id=vehicle_id)

    def rtl(self, vehicle_id: str = "") -> ManagerResult:
        return self.set_mode("RTL", vehicle_id=vehicle_id)

    def set_mode(self, mode: str, vehicle_id: str = "") -> ManagerResult:
        return self._execute("drone_set_mode", {"mode": mode}, vehicle_id=vehicle_id)

    def goto(self, item: dict[str, Any], vehicle_id: str = "") -> ManagerResult:
        payload = {
            "x": float(item.get("x", 0.0)),
            "y": float(item.get("y", 0.0)),
            "z": float(item.get("z", -3.0)),
            "velocity": float(item.get("velocity", item.get("speed_mps", 2.0)) or 2.0),
        }
        return self._execute("drone_fly_to", payload, vehicle_id=vehicle_id)

    def _execute(self, tool: str, params: dict[str, Any], vehicle_id: str = "") -> ManagerResult:
        if vehicle_id:
            params = {**dict(params), "vehicle_name": vehicle_id}
        state = self._telemetry.get_state()
        state.safety = self._safety.state()
        validation = self._safety.validate_command(tool, params, state)
        if not validation.ok:
            return validation
        result = self._tools.execute(
            tool,
            params,
            dry_run=False,
            blocked_by_supervisor=state.safety.emergency_stop,
        )
        return _manager_result_from_tool(result)


class ToolMissionManager:
    """Local mission manager with AirSim path execution fallback."""

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager, safety: ToolSafetyManager) -> None:
        self._tools = tools
        self._telemetry = telemetry
        self._safety = safety
        self._draft: MissionPlanDraft | None = None
        self._state = MissionState()

    def get_draft(self) -> MissionPlanDraft | None:
        return self._draft

    def set_draft(self, draft: MissionPlanDraft) -> ManagerResult:
        self._draft = draft.recalculate()
        self._state.draft = self._draft
        self._state.uploaded = False
        self._state.running = False
        self._state.progress = 0.0
        self._state.details = {}
        self._state.total_items = len(self._draft.items)
        self._state.message = "local mission draft updated"
        return ManagerResult(True, self._state.message, {"mission": self._draft.to_dict()})

    def validate(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        candidate = draft or self._draft
        if candidate is None:
            return ManagerResult(False, "no mission draft", {})
        state = self._telemetry.get_state()
        state.safety = self._safety.state()
        return self._safety.validate_mission(candidate, state)

    def upload(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        if draft is not None:
            set_result = self.set_draft(draft)
            if not set_result.ok:
                return set_result
        if self._draft is None:
            return ManagerResult(False, "no mission draft", {})

        validation = self.validate(self._draft)
        if not validation.ok:
            return validation

        if "drone_upload_mission" in _tool_names(self._tools):
            result = self._tools.execute(
                "drone_upload_mission",
                {"waypoints_json": json.dumps([item.to_dict() for item in self._draft.items])},
                dry_run=False,
            )
            manager_result = _manager_result_from_tool(result)
            self._state.uploaded = manager_result.ok
            self._state.message = manager_result.message
            self._state.details = {
                "execution_mode": "native_mission",
                "native_upload_result": manager_result.to_dict(),
            }
            state = self._telemetry.get_state()
            real_vehicle = bool(state.capabilities.get("real_vehicle"))
            if not manager_result.ok and not real_vehicle and "drone_fly_path" in _tool_names(self._tools):
                waypoints, velocity = self._draft_to_local_path(self._draft)
                if waypoints:
                    self._state.uploaded = True
                    self._state.running = False
                    self._state.message = "PX4 native mission upload rejected; staged for local guided execution"
                    self._state.details = {
                        "execution_mode": "local_path_fallback",
                        "native_upload_result": manager_result.to_dict(),
                        "local_waypoints": waypoints,
                        "velocity": velocity,
                    }
                    return ManagerResult(True, self._state.message, self._state.to_dict())
            return manager_result

        self._state.uploaded = True
        self._state.running = False
        self._state.message = "local mission staged; backend upload is not implemented"
        return ManagerResult(True, self._state.message, {"mission": self._draft.to_dict()})

    def download(self) -> MissionPlanDraft | None:
        if "drone_download_mission" not in _tool_names(self._tools):
            return self._draft
        result = self._tools.execute("drone_download_mission", {}, dry_run=False)
        mission = result.data.get("mission") if result.ok else None
        if isinstance(mission, dict):
            self._draft = MissionPlanDraft.from_dict(mission)
            self._state.draft = self._draft
            self._state.total_items = len(self._draft.items)
        return self._draft

    def clear(self) -> ManagerResult:
        self._draft = None
        self._state = MissionState(message="local mission cleared")
        if "drone_clear_mission" in _tool_names(self._tools):
            result = self._tools.execute("drone_clear_mission", {}, dry_run=False)
            manager_result = _manager_result_from_tool(result)
            if manager_result.ok:
                return manager_result
            message = manager_result.message.lower()
            if "not supported" in message or "not implemented" in message:
                return ManagerResult(
                    True,
                    "local mission cleared; active backend has no native mission store",
                    {
                        "native_clear_result": manager_result.to_dict(),
                        "mission": self._state.to_dict(),
                    },
                )
            return manager_result
        return ManagerResult(True, "local mission cleared", {})

    def start(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        if draft is not None:
            upload_result = self.upload(draft)
            if not upload_result.ok:
                return upload_result
        if self._draft is None:
            return ManagerResult(False, "no mission draft", {})
        if not self._state.uploaded:
            upload_result = self.upload(self._draft)
            if not upload_result.ok:
                return upload_result

        if self._state.details.get("execution_mode") == "local_path_fallback":
            waypoints, velocity = self._draft_to_local_path(self._draft)
            if not waypoints:
                return ManagerResult(False, "mission cannot be converted to local NED path", {"mission": self._draft.to_dict()})
            return self._execute_local_path(waypoints, velocity)

        if "drone_start_mission" in _tool_names(self._tools):
            result = self._tools.execute("drone_start_mission", {}, dry_run=False)
            manager_result = _manager_result_from_tool(result)
            self._state.running = manager_result.ok
            self._state.message = manager_result.message
            return manager_result

        waypoints, velocity = self._draft_to_local_path(self._draft)
        if not waypoints:
            return ManagerResult(False, "mission cannot be converted to local NED path", {"mission": self._draft.to_dict()})

        state = self._telemetry.get_state()
        result = self._tools.execute(
            "drone_fly_path",
            {
                "waypoints_json": json.dumps(waypoints),
                "velocity": velocity,
                # 面板选定的目标机（空串 = 后端默认机）；多机后端必须传递，
                # 否则任务永远落在第一架车上
                "vehicle_name": self._draft_vehicle_name(),
            },
            dry_run=False,
            blocked_by_supervisor=state.safety.emergency_stop,
        )
        manager_result = _manager_result_from_tool(result)
        self._state.running = False
        self._state.progress = 1.0 if manager_result.ok else self._state.progress
        self._state.message = manager_result.message
        return manager_result

    def _draft_vehicle_name(self) -> str:
        """Target vehicle from the draft ("" = backend default vehicle)."""
        return str((self._draft.vehicle or "").strip()) if self._draft else ""

    def _execute_local_path(self, waypoints: list[dict[str, float]], velocity: float) -> ManagerResult:
        if self._draft is None:
            return ManagerResult(False, "no mission draft", {})
        state = self._telemetry.get_state()
        if state.safety.emergency_stop:
            return ManagerResult(False, "mission blocked by emergency stop", self._state.to_dict())

        takeoff_altitude = self._mission_takeoff_altitude(self._draft)
        vehicle = state.vehicle
        needs_takeoff = bool(takeoff_altitude and (vehicle is None or not vehicle.flying or not vehicle.armed))
        takeoff_result: ManagerResult | None = None
        if needs_takeoff:
            if "drone_takeoff" not in _tool_names(self._tools):
                return ManagerResult(False, "local guided mission requires takeoff but drone_takeoff is unavailable", self._state.to_dict())
            raw_takeoff = self._tools.execute(
                "drone_takeoff",
                {"altitude": takeoff_altitude},
                dry_run=False,
                blocked_by_supervisor=state.safety.emergency_stop,
            )
            takeoff_result = _manager_result_from_tool(raw_takeoff)
            if not takeoff_result.ok:
                self._state.running = False
                self._state.message = f"local guided takeoff failed: {takeoff_result.message}"
                self._state.details = {
                    **self._state.details,
                    "local_takeoff_result": takeoff_result.to_dict(),
                    "local_waypoints": waypoints,
                    "velocity": velocity,
                }
                return ManagerResult(False, self._state.message, self._state.to_dict())

        state = self._telemetry.get_state()
        result = self._tools.execute(
            "drone_fly_path",
            {"waypoints_json": json.dumps(waypoints), "velocity": velocity},
            dry_run=False,
            blocked_by_supervisor=state.safety.emergency_stop,
        )
        manager_result = _manager_result_from_tool(result)
        self._state.running = False
        self._state.progress = 1.0 if manager_result.ok else self._state.progress
        self._state.message = manager_result.message
        self._state.details = {
            **self._state.details,
            "local_takeoff_result": takeoff_result.to_dict() if takeoff_result else None,
            "local_start_result": manager_result.to_dict(),
            "local_waypoints": waypoints,
            "velocity": velocity,
        }
        return manager_result

    def pause(self) -> ManagerResult:
        result = self._tools.execute("drone_hover", {}, dry_run=False)
        manager_result = _manager_result_from_tool(result)
        self._state.running = False
        self._state.message = manager_result.message
        return manager_result

    def resume(self) -> ManagerResult:
        return ManagerResult(False, "mission resume is not implemented for staged local missions", self._state.to_dict())

    def progress(self) -> MissionState:
        if self._draft:
            self._state.draft = self._draft
            self._state.total_items = len(self._draft.items)
        return self._state

    def _draft_to_local_path(self, draft: MissionPlanDraft) -> tuple[list[dict[str, float]], float]:
        waypoints: list[dict[str, float]] = []
        velocities: list[float] = []
        vehicle_geo = self._current_vehicle_geo_home()
        vehicle_local = self._current_vehicle_local_position()
        home = draft.home or vehicle_geo
        for item in draft.items:
            if item.is_global() and vehicle_geo and vehicle_local:
                relative = item.to_local_ned(vehicle_geo)
                if relative is None:
                    return [], 0.0
                waypoints.append({
                    "x": float(vehicle_local.get("x", 0.0) or 0.0) + relative.x,
                    "y": float(vehicle_local.get("y", 0.0) or 0.0) + relative.y,
                    "z": relative.z,
                })
            else:
                local = item.to_local_ned(home)
                if local is None:
                    return [], 0.0
                waypoints.append(local.to_dict())
            if item.speed_mps:
                velocities.append(float(item.speed_mps))
        velocity = velocities[0] if velocities else 2.0
        return waypoints, velocity

    def _mission_takeoff_altitude(self, draft: MissionPlanDraft) -> float | None:
        for item in draft.items:
            if item.type != "takeoff":
                continue
            if item.alt_m is not None:
                return max(0.5, abs(float(item.alt_m)))
            if item.z is not None:
                return max(0.5, abs(float(item.z)))
            return 3.0
        return None

    def _current_vehicle_geo_home(self) -> GeoPoint | None:
        try:
            state = self._telemetry.get_state()
            gps = state.vehicle.gps if state.vehicle else None
            if isinstance(gps, dict) and gps.get("lat") is not None and gps.get("lon") is not None:
                return GeoPoint(
                    lat=float(gps["lat"]),
                    lon=float(gps["lon"]),
                    alt_m=float(gps.get("alt", 0.0) or 0.0),
                )
        except Exception:
            return None
        return None

    def _current_vehicle_local_position(self) -> dict[str, float] | None:
        try:
            state = self._telemetry.get_state()
            pos = state.vehicle.position_ned if state.vehicle else None
            if isinstance(pos, dict):
                return {
                    "x": float(pos.get("x", 0.0) or 0.0),
                    "y": float(pos.get("y", 0.0) or 0.0),
                    "z": float(pos.get("z", 0.0) or 0.0),
                }
        except Exception:
            return None
        return None


class GroundStationServices:
    """Container for the initial GCS manager facade."""

    def __init__(
        self,
        tools: ToolRuntime,
        supervisor: Any | None = None,
        current_run_provider: CurrentRunProvider | None = None,
    ) -> None:
        supervisor_status_provider = supervisor.get_status if supervisor and hasattr(supervisor, "get_status") else None
        self.telemetry = ToolTelemetryManager(
            tools,
            supervisor_status_provider=supervisor_status_provider,
            current_run_provider=current_run_provider,
            mission_state_provider=lambda: self.mission.progress(),
        )
        self.link = ToolLinkManager(tools, self.telemetry)
        self.vehicle = ToolVehicleManager(tools, self.telemetry)
        self.safety = ToolSafetyManager(
            tools,
            supervisor=supervisor,
            supervisor_status_provider=supervisor_status_provider,
        )
        self.command = ToolCommandManager(tools, self.telemetry, self.safety)
        self.mission = ToolMissionManager(tools, self.telemetry, self.safety)

    def state(self) -> GroundStationState:
        return self.telemetry.get_state()


def _manager_result_from_tool(result: ToolCallResult) -> ManagerResult:
    message = result.data.get("message") or result.data.get("status") or ("ok" if result.ok else "failed")
    return ManagerResult(result.ok, str(message), result.to_dict())


def _tool_names(tools: ToolRuntime) -> set[str]:
    names: set[str] = set()
    for item in tools.list_tools():
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = getattr(item, "name", "")
        if name:
            names.add(str(name))
    return names


def _split_host_port(endpoint: str) -> tuple[str, int | None]:
    if ":" not in endpoint:
        return endpoint, None
    host, port_text = endpoint.rsplit(":", 1)
    try:
        return host, int(port_text)
    except ValueError:
        return endpoint, None
