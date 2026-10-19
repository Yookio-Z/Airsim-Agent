"""Minimal ground-station service facade over the current ToolRuntime.

The first implementation deliberately wraps the existing runtime instead of
replacing controllers. This gives UI and Agent code a shared GCS boundary while
leaving the AirSim path stable and keeping room for PX4 mission upload, ROS2,
and real-vehicle adapters behind the same managers.
"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .managers import ManagerResult
from .mission import GeoPoint, MissionItem, MissionPlanDraft
from .state import GroundStationState, LinkState, MissionState, SafetyState, VehicleTelemetry

if TYPE_CHECKING:  # 只用于类型标注：避免 gcs → agent → gcs 的循环导入
    from src.agent.tool_executor import ToolCallResult, ToolRuntime


SupervisorStatusProvider = Callable[[], dict[str, Any]]
CurrentRunProvider = Callable[[], dict[str, Any] | None]


class ToolTelemetryManager:
    """Telemetry manager backed by ToolRuntime.status_snapshot().

    **面板/HUD 遥测的唯一读口**（2026-10-15）：界面能看到的一切遥测——HUD 帧、
    整体状态载荷、链路汇总、机队清单、遥测录制——都从这里取同一份快照。
    运行时**内部**的决策读（看门狗、审批、循环观察）仍直读工具层：那是运行时的
    事，不是面板数据，混进来只会让"谁在读遥测"更难查。
    """

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

    def snapshot(self) -> dict[str, Any]:
        """一次工具运行时快照：面板遥测的唯一来源。"""
        return self._tools.status_snapshot()

    def get_state(self) -> GroundStationState:
        snapshot = self.snapshot()
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
        backend = str(self._telemetry.snapshot().get("backend") or "")
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

    def reconnect(self) -> ManagerResult:
        """重连当前链路（用上一次的连接参数）。"""
        result = self._tools.reconnect()
        return _manager_result_from_tool(result)

    def disconnect(self, link_id: str = "") -> ManagerResult:
        result = self._tools.execute("drone_disconnect", {}, dry_run=False, allow_reconnect=False)
        return _manager_result_from_tool(result)

    def status(self) -> LinkState:
        return self._telemetry.get_state().link


class ToolVehicleManager:
    """机队清单：读遥测快照里的载具列表（**不额外打工具调用**）。

    界面每 250ms 轮询一次状态，所以这里刻意不执行工具——旧实现每次都调
    drone_get_status，等于每次轮询都往飞控发一次读请求。快照里没有 vehicles
    时返回空列表：单机后端就是这个样子，界面把"没选"理解为"默认那一台"。
    """

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager) -> None:
        self._tools = tools
        self._telemetry = telemetry

    def list_vehicles(self) -> list[dict[str, Any]]:
        snapshot = self._telemetry.snapshot()
        vehicles = snapshot.get("vehicles")
        entries: list[dict[str, Any]] = []
        for item in vehicles or []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry.setdefault("vehicle_id", str(entry.get("vehicle_name") or ""))
            entries.append(entry)
        return entries


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
    """设备动作的唯一入口：安全校验 → 运行时治理执行。

    操作员的两条直接入口（``POST /api/control`` 与 ``POST /api/tool``）与 Agent 的
    飞行步骤最终都走 ``_execute_agent_tool`` 这条治理入口，所以"手动点一下"和
    "Agent 自己飞"共享同一套守卫（执行槽占用、编队冲突、真机审批、后端切换检查），
    这里额外加的是 gcs 自己的急停门与安全校验。
    """

    # 设备级动作：会被本管理器接管（任务级动作如暂停/取消/急停属于 supervisor）。
    DEVICE_TOOLS = (
        "drone_arm",
        "drone_disarm",
        "drone_takeoff",
        "drone_land",
        "drone_hover",
        "drone_set_mode",
        "drone_fly_to",
        "drone_move_relative",
        "drone_fly_path",
        "drone_rotate_to",
    )

    def __init__(
        self,
        tools: ToolRuntime,
        telemetry: ToolTelemetryManager,
        safety: ToolSafetyManager,
        executor: Callable[..., Any] | None = None,
    ) -> None:
        self._tools = tools
        self._telemetry = telemetry
        self._safety = safety
        # 默认退回工具层；运行时注入 _execute_agent_tool（治理入口）。
        self._executor = executor

    def execute_tool(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        vehicle_id: str = "",
    ) -> Any:
        """一次设备动作：校验通过后交给治理入口执行，返回 ToolCallResult。

        校验失败也返回 ToolCallResult（ok=False），调用方不必区分两种返回类型。
        """
        from src.agent.tool_executor import ToolCallResult

        payload = dict(params or {})
        if vehicle_id:
            payload["vehicle_name"] = vehicle_id
        state = self._telemetry.get_state()
        state.safety = self._safety.state()
        validation = self._safety.validate_command(tool, payload, state)
        if not validation.ok:
            return ToolCallResult(
                tool,
                payload,
                False,
                {"status": "error", "message": validation.message, **dict(validation.data or {})},
                0.0,
                0.0,
                error_code="SAFETY_BLOCKED",
            )
        executor = self._executor
        if callable(executor):
            return executor(tool, payload, dry_run=False)
        return self._tools.execute(tool, payload, dry_run=False)


class ToolMissionManager:
    """Local mission manager with AirSim path execution fallback."""

    def __init__(self, tools: ToolRuntime, telemetry: ToolTelemetryManager, safety: ToolSafetyManager) -> None:
        self._tools = tools
        self._telemetry = telemetry
        self._safety = safety
        self._draft: MissionPlanDraft | None = None
        self._state = MissionState()
        # 后台任务注册表（方案乙：多机非阻塞由编排层承担）
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.RLock()

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
                {
                    "waypoints_json": json.dumps([item.to_dict() for item in self._draft.items]),
                    # PX4 等原生 mission 后端按车上传（空串 = 默认机）
                    "vehicle_name": self._draft_vehicle_name(),
                },
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
            result = self._tools.execute(
                "drone_clear_mission",
                {"vehicle_name": self._draft_vehicle_name()},
                dry_run=False,
            )
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

        # 急停期间不得启航。ToolRuntime.execute 的 blocked_by_supervisor 默认
        # False，而这条路径以前既不传它也不查急停状态，于是急停后点"启航"仍会
        # 启动已上传的航线——MAVLink 的 _start_mission_one 还会先自动解锁、再切
        # MISSION 模式。上传本身是惰性的，所以门放在上传之后、任何启动动作之前。
        safety_state = self._safety.state() if self._safety is not None else None
        if safety_state is not None and safety_state.emergency_stop:
            return ManagerResult(
                False,
                "emergency stop is active; release it before starting the mission",
                {"level": "danger", "mission": self._state.to_dict()},
            )

        if self._state.details.get("execution_mode") == "local_path_fallback":
            waypoints, velocity = self._draft_to_local_path(self._draft)
            if not waypoints:
                return ManagerResult(False, "mission cannot be converted to local NED path", {"mission": self._draft.to_dict()})
            return self._execute_local_path(waypoints, velocity)

        if "drone_start_mission" in _tool_names(self._tools):
            result = self._tools.execute(
                "drone_start_mission",
                {"vehicle_name": self._draft_vehicle_name()},
                dry_run=False,
            )
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
                "waypoints": json.dumps(waypoints),
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

    def start_multi(self, assignments: list[dict[str, Any]] | None) -> ManagerResult:
        """多机各自航线：提交后台任务后立即返回，飞行在后台按机并发执行。

        方案乙（2026-10-15）：不再使用 dispatch_* 工具（已删除）。"非阻塞"
        语义由编排层承担——每架机一个后台工作线程调用阻塞工具
        （drone_takeoff / drone_fly_path）；控制器内部已支持多机并发
        （MAVLink 用线程本地目标，AirSim 用每机客户端），因此两套后端行为一致。

        assignments: [{"vehicle": "Drone1", "items": [MissionItem...]}, ...]
        """
        if not isinstance(assignments, list) or not assignments:
            return ManagerResult(False, "no mission assignments", {})
        state = self._telemetry.get_state()
        state.safety = self._safety.state()
        if state.safety.emergency_stop:
            return ManagerResult(False, "mission blocked by emergency stop", self._state.to_dict())
        validation = self._safety.validate_command("drone_fly_path", {}, state)
        if not validation.ok:
            return validation

        # 1. 每机航点 → 本地 NED 路径（AirSim 航点本身就是 local_ned，直接转换）
        plans: list[dict[str, Any]] = []
        for entry in assignments:
            if not isinstance(entry, dict):
                continue
            vehicle = str(entry.get("vehicle") or "").strip()
            raw_items = [it for it in (entry.get("items") or []) if isinstance(it, dict)]
            if not raw_items:
                continue
            mission_items = [MissionItem.from_dict(it) for it in raw_items]
            draft = MissionPlanDraft(vehicle=vehicle, items=mission_items)
            waypoints, velocity = self._draft_to_local_path(draft)
            if not waypoints:
                label = vehicle or "默认机"
                return ManagerResult(False, f"{label} 的航线无法转换为本地 NED 路径（缺少坐标）", {})
            plans.append({
                "vehicle": vehicle,
                "waypoints": waypoints,
                "velocity": velocity,
                "takeoff_altitude": self._mission_takeoff_altitude(draft) or 3.0,
            })
        if not plans:
            return ManagerResult(False, "没有非空的航线任务", {})

        # 2. 提交后台任务：立即返回，飞行在后台并发执行（进度见 progress().details.jobs）
        job_id = self._submit_multi_job(plans)
        summary = "、".join(f"{p['vehicle'] or '默认机'}({len(p['waypoints'])}点)" for p in plans)
        message = f"多机任务已提交后台执行: {summary}"
        self._state.running = True
        self._state.message = message
        self._state.details = {"execution_mode": "multi_vehicle_background", "job_id": job_id}
        return ManagerResult(
            True, message,
            {"job_id": job_id, "assignments": plans, "mission": self._state.to_dict()},
        )

    def _submit_multi_job(self, plans: list[dict[str, Any]]) -> str:
        """每架机一个后台工作线程：确保空中 → 飞自己的航线（阻塞调用）。

        急停：工具调用携带 blocked_by_supervisor；控制器的移动循环本身也会在
        急停时中断（AirSim 的 _wait_move_arrival / MAVLink 的逐航点循环）。
        """
        job_id = f"multi_{int(time.time() * 1000)}"
        record: dict[str, Any] = {
            "job_id": job_id,
            "kind": "multi_mission",
            "status": "running",
            "created_at": time.time(),
            "vehicles": {},
        }
        with self._jobs_lock:
            self._jobs[job_id] = record

        def worker(plan: dict[str, Any]) -> None:
            vehicle = str(plan.get("vehicle") or "")
            label = vehicle or "默认机"
            stop = bool(self._safety.state().emergency_stop) if self._safety is not None else False
            if stop:
                record["vehicles"][label] = {"ok": False, "phase": "blocked", "message": "emergency stop active"}
                return
            try:
                snapshot = self._telemetry.get_state()
                telemetry = None
                if vehicle:
                    telemetry = next((v for v in snapshot.vehicles if v.vehicle_id == vehicle), None)
                else:
                    telemetry = snapshot.vehicle
                if telemetry is None or not (telemetry.flying and telemetry.armed):
                    takeoff = self._tools.execute(
                        "drone_takeoff",
                        {"altitude": plan["takeoff_altitude"], "vehicle_name": vehicle},
                        dry_run=False,
                        blocked_by_supervisor=stop,
                    )
                    if not takeoff.ok:
                        record["vehicles"][label] = {
                            "ok": False,
                            "phase": "takeoff",
                            "message": str((takeoff.data or {}).get("message") or "takeoff failed"),
                        }
                        return
                result = self._tools.execute(
                    "drone_fly_path",
                    {
                        "waypoints": json.dumps(plan["waypoints"]),
                        "velocity": plan["velocity"],
                        "vehicle_name": vehicle,
                    },
                    dry_run=False,
                    blocked_by_supervisor=stop,
                )
                record["vehicles"][label] = {
                    "ok": bool(result.ok),
                    "phase": "path",
                    "waypoint_count": len(plan["waypoints"]),
                    "message": str((result.data or {}).get("message") or ""),
                }
            except Exception as exc:  # 单机异常不影响其他机
                record["vehicles"][label] = {"ok": False, "phase": "error", "message": str(exc)}

        def run() -> None:
            threads = [
                threading.Thread(target=worker, args=(plan,), daemon=True, name=f"gcs-multi-{job_id}-{index}")
                for index, plan in enumerate(plans)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            outcome = record["vehicles"]
            record["status"] = "completed" if outcome and all(v.get("ok") for v in outcome.values()) else "failed"
            record["finished_at"] = time.time()

        threading.Thread(target=run, daemon=True, name=f"gcs-{job_id}").start()
        return job_id

    def _execute_local_path(self, waypoints: list[dict[str, float]], velocity: float) -> ManagerResult:
        if self._draft is None:
            return ManagerResult(False, "no mission draft", {})
        state = self._telemetry.get_state()
        if state.safety.emergency_stop:
            return ManagerResult(False, "mission blocked by emergency stop", self._state.to_dict())

        takeoff_altitude = self._mission_takeoff_altitude(self._draft)
        target_vehicle = self._draft_vehicle_name()
        vehicle = state.vehicle
        if target_vehicle:
            # 起飞判断必须针对任务目标机：默认机在空中不代表目标机不在地面
            for entry in state.vehicles:
                if entry.vehicle_id == target_vehicle:
                    vehicle = entry
                    break
        needs_takeoff = bool(takeoff_altitude and (vehicle is None or not vehicle.flying or not vehicle.armed))
        takeoff_result: ManagerResult | None = None
        if needs_takeoff:
            if "drone_takeoff" not in _tool_names(self._tools):
                return ManagerResult(False, "local guided mission requires takeoff but drone_takeoff is unavailable", self._state.to_dict())
            raw_takeoff = self._tools.execute(
                "drone_takeoff",
                {"altitude": takeoff_altitude, "vehicle_name": target_vehicle},
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
            {
                "waypoints": json.dumps(waypoints),
                "velocity": velocity,
                "vehicle_name": target_vehicle,
            },
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
        params = {"vehicle_name": self._draft_vehicle_name()} if self._draft_vehicle_name() else {}
        result = self._tools.execute("drone_hover", params, dry_run=False)
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
        with self._jobs_lock:
            jobs = [dict(job) for job in self._jobs.values()][-3:]
        if jobs:
            self._state.details = {**self._state.details, "jobs": jobs}
        return self._state

    def _draft_to_local_path(self, draft: MissionPlanDraft) -> tuple[list[dict[str, float]], float]:
        waypoints: list[dict[str, float]] = []
        velocities: list[float] = []
        # global 航点（PX4 帧）转本地 NED 时，偏移基准必须是该计划目标机自己
        # 的位置——用默认车会把多机任务里其余各机的航点整体偏移
        vehicle_geo = self._current_vehicle_geo_home(draft.vehicle)
        vehicle_local = self._current_vehicle_local_position(draft.vehicle)
        home = draft.home or vehicle_geo
        for item in draft.items:
            # 只转换几何航点：takeoff/land/rtl 是动作指令（分别派发
            # drone_takeoff/drone_land/drone_rtl），不是坐标——尤其 UI 自
            # 动插入的 takeoff 项带 x:0,y:0，泄漏进路径会让每架机先飞到原
            # 点（Drone1 出生点）再执行自己的航线。
            if item.type not in ("waypoint", ""):
                continue
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

    def _current_vehicle_geo_home(self, vehicle_id: str = "") -> GeoPoint | None:
        """目标车（空 = 默认车）的 GPS 位置，global 航点转本地 NED 的基准。"""
        try:
            state = self._telemetry.get_state()
            target = None
            if vehicle_id:
                target = next((v for v in state.vehicles if v.vehicle_id == vehicle_id), None)
            if target is None:
                target = state.vehicle
            gps = target.gps if target else None
            if isinstance(gps, dict) and gps.get("lat") is not None and gps.get("lon") is not None:
                return GeoPoint(
                    lat=float(gps["lat"]),
                    lon=float(gps["lon"]),
                    alt_m=float(gps.get("alt", 0.0) or 0.0),
                )
        except Exception:
            return None
        return None

    def _current_vehicle_local_position(self, vehicle_id: str = "") -> dict[str, float] | None:
        """目标车（空 = 默认车）的本地 NED 位置，global 航点转本地路径的基准。"""
        try:
            state = self._telemetry.get_state()
            target = None
            if vehicle_id:
                target = next((v for v in state.vehicles if v.vehicle_id == vehicle_id), None)
            if target is None:
                target = state.vehicle
            pos = target.position_ned if target else None
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
        governed_executor: Callable[..., Any] | None = None,
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
        self.command = ToolCommandManager(
            tools, self.telemetry, self.safety, executor=governed_executor
        )
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
