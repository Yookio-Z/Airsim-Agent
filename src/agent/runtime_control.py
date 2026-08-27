"""手动控制：_manual_land/_manual_return_home、control 入口、取消与中断判定。

拆分自 runtime.py（AgentRuntime 方法按职责迁移，行为不变）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.autonomy.supervisor import ExecutionSupervisor
from src.gcs import GroundStationServices
from src.modules.mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from src.modules.formation import FLIGHT_ACTIONS as FORMATION_FLIGHT_ACTIONS
from src.replay.session import ReplaySession, list_replay_sessions, read_replay_session

from .agent_loop import AgentLoop
from .llm import LLMMissionPlanner, LLMUnavailableError
from .loop_types import LoopState
from .memory import AgentMemory
from .planner import MissionPlan, MissionPlanner, MissionStep
from .run_log import RunLog, RunLogStore
from .skill_registry import SkillRegistry
from .sub_agent import SubAgentRunner
from .task_runs import TaskRunStore
from .tool_cards import TOOL_CARDS
from .tool_executor import TOOL_OUTPUT_SCHEMAS, ToolCallResult, ToolRuntime
from .llm_protocol import function_tool_schema, tool_schema_from_spec, validate_json_schema
from src.config import config

from .runtime_types import (
    ChatMessage,
    RunState,
    RuntimeEvent,
    ToolApprovalRequest,
)
from .runtime_settings import (
    AIRSIM_SETTINGS_TEMPLATES,
    ATTACHMENTS_DIR,
    REPO_ROOT,
    SESSIONS_DIR,
    SETTINGS_PATH,
    SKILLS_OVERRIDES_PATH,
    _application_settings,
    _build_connect_params,
    _camera_settings,
    _connection_settings,
    _default_application_settings,
    _default_camera_settings,
    _default_connection_settings,
    _load_settings,
    _save_settings,
    _select_connection_for_backend,
)


class RuntimeControlMixin:
    def _manual_land(self, targets: list[str] | None = None) -> dict[str, Any]:
        """降落目标机（空 = 全部载具）：并发派发降落，逐机验证落地后上锁。

        只有当目标列表里的每一台都确认落地才算完成，避免"降了一台就报完成"。
        """
        runtime = self.tools.status_snapshot()
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        if not connected:
            return {"ok": False, "error": "flight controller link is offline or stale"}
        vehicles = [v for v in (runtime.get("vehicles") or []) if isinstance(v, dict) and not v.get("error")]
        by_name = {str(v.get("vehicle_name") or ""): v for v in vehicles}
        names = [str(t).strip() for t in (targets or []) if str(t).strip()] or list(by_name.keys()) or [""]

        to_land: list[str] = []
        to_disarm: list[str] = []
        results: list[dict[str, Any]] = []
        for name in names:
            v = by_name.get(name)
            if v is None and len(names) == 1:
                v = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else None
            flying = bool((v or {}).get("flying"))
            armed = bool((v or {}).get("armed"))
            if flying:
                to_land.append(name)
            elif armed:
                to_disarm.append(name)
            else:
                results.append({"vehicle": name or "默认机", "state": "already_grounded_disarmed", "ok": True})

        controller = getattr(self.tools, "controller", None)
        dispatch_land = getattr(controller, "dispatch_land", None) if controller is not None else None
        for name in to_land:
            if callable(dispatch_land):
                if not dispatch_land(name):
                    return {"ok": False, "error": f"{name or '默认机'} 降落派发失败", "vehicles": results}
            else:
                r = self.tools.execute(
                    "drone_land", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                if not r.ok:
                    return {"ok": False, "error": f"{name or '默认机'} 降落指令失败", "vehicles": results}

        # 轮询验证：目标列表里每台都必须确认落地。
        # 地面 NED z 随地形/出生点变化（3~6m 都有），用该机 home 的 z 作地面基准。
        controller = getattr(self.tools, "controller", None)
        ground_z = {}
        home_read = getattr(controller, "home_position", None) if controller is not None else None
        for name in to_land:
            if callable(home_read):
                try:
                    home = home_read(name)
                except Exception:
                    home = None
                if isinstance(home, dict):
                    ground_z[name] = float(home.get("z", 0.0))
        pending = set(to_land)
        deadline = time.time() + 120.0
        while pending and time.time() < deadline:
            time.sleep(1.5)
            snap = self.tools.status_snapshot()
            for v in snap.get("vehicles") or []:
                n = str(v.get("vehicle_name") or "")
                if n in pending:
                    pos = v.get("position_ned") if isinstance(v.get("position_ned"), dict) else {}
                    z = float(pos.get("z", 0.0) or 0.0)
                    near_ground = True
                    if n in ground_z:
                        near_ground = abs(z - ground_z[n]) < 1.5
                    if not v.get("flying") and near_ground:
                        pending.discard(n)

        landed = [n for n in to_land if n not in pending]
        for name in to_disarm:
            r = self.tools.execute(
                "drone_disarm", {"vehicle_name": name} if name else {},
                dry_run=False, blocked_by_supervisor=False,
            )
            results.append({"vehicle": name or "默认机", "state": "grounded_disarmed", "ok": bool(r.ok)})
        for name in landed:
            r = self.tools.execute(
                "drone_disarm", {"vehicle_name": name} if name else {},
                dry_run=False, blocked_by_supervisor=False,
            )
            results.append({"vehicle": name or "默认机", "state": "landed_disarmed", "ok": bool(r.ok)})

        ok = not pending and all(r.get("ok") for r in results)
        if pending:
            message = f"降落超时未确认: {', '.join(sorted(pending))}"
        elif len(results) == 1:
            message = "已降落并锁定"
        else:
            message = f"全部降落并锁定: {len(results)} 台"
        self._append_event("warning", "tool", "手动降落", {"vehicles": results, "message": message})
        return {"ok": ok, "message": message, "vehicles": results}

    def _manual_return_home(self, targets: list[str] | None = None) -> dict[str, Any]:
        if self.tools.formation_active():
            return {
                "ok": False,
                "error": "a formation/coverage mission is active; use formation_command(action=land_all) or hover_all before return home",
            }
        runtime = self.tools.status_snapshot()
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        if not connected:
            return {"ok": False, "error": "flight controller link is offline or stale"}

        min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
        max_altitude = float(getattr(self.tools.safety.constraints, "max_altitude", 50.0) or 50.0)
        controller = getattr(self.tools, "controller", None)

        vehicles = [v for v in (runtime.get("vehicles") or []) if isinstance(v, dict) and not v.get("error")]
        target_set = {str(t).strip() for t in (targets or []) if str(t).strip()}
        # 返航目标：在空中的 + 待飞(解锁但在地面,如任务中碰撞落地)的都纳入;
        # 已锁定在地面(disarmed)的无需返航
        flying = [
            v for v in vehicles
            if (v.get("flying") or v.get("armed"))
            and (not target_set or str(v.get("vehicle_name") or "") in target_set)
        ]

        if not flying:
            if not drone or drone.get("error"):
                return {"ok": False, "error": str(drone.get("error") or "vehicle status unavailable")}
            armed = bool(drone.get("armed"))
            pos = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            altitude = abs(self._ned_value(pos, "z", 0.0) or 0.0)
            if not armed and altitude < min_altitude:
                self._append_event("info", "tool", "无人机已在地面，无需返航", {"drone": drone})
                return {"ok": True, "message": "vehicle already on ground", "drone": drone}
            return {"ok": False, "error": "vehicle is not airborne; return_home command was not sent", "drone": drone}

        # 每架在空中的机各自返航到自己的初始点位（首次记录的地面位置）。
        # 多机并发：非阻塞派发，互不等待。
        memory = self.memory.snapshot()
        session = memory.get("session") if isinstance(memory, dict) else {}
        memory_start = session.get("last_task_start_position_ned") if isinstance(session, dict) else None
        speed = max(1.0, min(3.0, float(getattr(self.tools.safety.constraints, "max_velocity", 3.0) or 3.0)))

        results: list[dict[str, Any]] = []
        for vehicle in flying:
            name = str(vehicle.get("vehicle_name") or "")
            label = name or "默认机"
            pos = vehicle.get("position_ned") if isinstance(vehicle.get("position_ned"), dict) else {}
            status_flying = bool(vehicle.get("flying"))
            current_x = self._ned_value(pos, "x", 0.0) or 0.0
            current_y = self._ned_value(pos, "y", 0.0) or 0.0
            current_z = self._ned_value(pos, "z", 0.0) or 0.0
            altitude = abs(current_z)
            if current_z < -min_altitude:
                target_z = -min(max(altitude, min_altitude), max_altitude)
            else:
                target_z = -min(max(3.0, min_altitude), max_altitude)

            home = None
            if controller is not None and hasattr(controller, "home_position"):
                try:
                    home = controller.home_position(name)
                except Exception:
                    home = None

            if isinstance(home, dict) and home.get("x") is not None:
                target = {
                    "x": round(float(home.get("x", 0.0)), 3),
                    "y": round(float(home.get("y", 0.0)), 3),
                    "z": round(float(target_z), 3),
                }
                target_source = "vehicle_initial_position"
            elif len(flying) == 1:
                # 无该机记录时的单机回退：上次任务起点 / 安全 home
                target_x = self._ned_value(memory_start, "x")
                target_y = self._ned_value(memory_start, "y")
                if target_x is None or target_y is None:
                    home_x, home_y = self.tools.safety.constraints.home_position
                    target_x, target_y = float(home_x), float(home_y)
                    target_source = "safety_home_position"
                else:
                    target_source = "last_task_start_position_ned"
                target = {"x": round(float(target_x), 3), "y": round(float(target_y), 3), "z": round(float(target_z), 3)}
            else:
                results.append({
                    "vehicle": name, "ok": False,
                    "message": "no recorded initial position for this vehicle",
                })
                continue

            horizontal_error = math.hypot(current_x - target["x"], current_y - target["y"])
            if horizontal_error < 0.6 and status_flying:
                # 空中且已在初始点上方 → 直接降落并锁定
                land_result = self.tools.execute(
                    "drone_land", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                disarm_result = None
                if land_result.ok:
                    disarm_result = self.tools.execute(
                        "drone_disarm", {"vehicle_name": name} if name else {},
                        dry_run=False, blocked_by_supervisor=False,
                    )
                ok_near = land_result.ok and (disarm_result is None or disarm_result.ok)
                results.append({
                    "vehicle": name, "ok": bool(ok_near),
                    "message": "already at return point; landed and disarmed",
                    "target_position_ned": target, "target_source": target_source,
                })
                continue

            # 返航完整语义：飞回初始点 → 到位后自动降落 → 上锁（后台监视线程完成）
            move_result = self.tools.execute(
                "drone_dispatch_return_land",
                {
                    **target,
                    "velocity": speed,
                    "vehicle_name": name,
                },
                dry_run=False,
                blocked_by_supervisor=False,
            )
            results.append({
                "vehicle": name,
                "ok": bool(move_result.ok),
                "message": move_result.data.get("message", "return + land dispatched"),
                "target_position_ned": target,
                "target_source": target_source,
            })

        ok = all(r.get("ok") for r in results) and bool(results)
        if len(results) == 1:
            message = "返航已派发：到位后将自动降落锁定" if ok else str(results[0].get("message") or "return home failed")
        else:
            names = "、".join(str(r.get("vehicle") or "默认机") for r in results)
            message = f"返航已派发（到位后各自降落锁定）: {names}" if ok else f"部分返航派发失败: {names}"
        self._append_event(
            "info" if ok else "warning",
            "tool",
            "手动返航" if len(results) == 1 else "多机手动返航",
            {"vehicles": results},
        )
        return {"ok": ok, "message": message, "vehicles": results}

    def control(self, action: str, expected_backend: str = "", vehicles: list[Any] | None = None) -> dict[str, Any]:
        action = action.strip().lower()
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        # 目标机列表（空 = 全部载具）；来自 UI 多选 chips
        target_vehicles = [str(v).strip() for v in (vehicles or []) if str(v).strip()]
        if action in {"cancel", "stop", "interrupt"}:
            return self._cancel_active_work()
        if action == "pause":
            self.supervisor.pause()
            if self._current and self._current.status == "running":
                self._current.status = "paused"
                self._current.phase = "paused"
            self._append_event("warning", "safety", "任务已暂停")
            return {"ok": True}
        if action == "resume":
            self.supervisor.resume()
            if self._current and self._current.status == "paused":
                self._current.status = "running"
                self._current.phase = "executing"
            self._append_event("info", "safety", "任务已恢复")
            return {"ok": True}
        if action == "emergency_stop":
            self.supervisor.emergency_stop()
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            # hover every formation drone too — the single-vehicle hover only
            # covers the default vehicle
            formation_stopped = self._close_formation("emergency_stop")
            self._append_event(
                "danger",
                "safety",
                "急停已触发，尝试悬停",
                {**result.to_dict(), "formation_stopped": formation_stopped},
            )
            if self._current:
                self._current.status = "blocked"
                self._current.phase = "blocked"
                self._current.failure_reason = "emergency stop"
                self._current.finished_at = time.time()
            return {"ok": result.ok, "result": result.to_dict(), "formation_stopped": formation_stopped}
        if action == "reset_emergency":
            self.supervisor.reset_emergency()
            self._append_event("info", "safety", "急停状态已复位")
            return {"ok": True}
        if action == "hover":
            results = []
            for name in target_vehicles or [""]:
                result = self.tools.execute(
                    "drone_hover", {"vehicle_name": name} if name else {},
                    dry_run=False, blocked_by_supervisor=False,
                )
                results.append(result.to_dict())
            ok = all(r["ok"] for r in results)
            self._append_event("info", "tool", "手动悬停", {"vehicles": results})
            return {"ok": ok, "result": {"vehicles": results}}
        if action == "land":
            result = self._manual_land(target_vehicles)
            self._append_event("warning", "tool", "手动降落", result)
            return {"ok": result.get("ok", False), "result": result}
        if action in {"return_home", "rtl"}:
            cancel_result = self._cancel_active_work() if self._active_run_is_interruptible() else None
            runtime = self.tools.status_snapshot()
            backend = str(runtime.get("backend") or "")
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if backend in {"px4_mavlink", "px4_ros2"} and capabilities.get("mode_control"):
                rtl_result = self.tools.execute(
                    "drone_set_mode",
                    {"mode": "RTL"},
                    dry_run=False,
                    blocked_by_supervisor=False,
                )
                result = {
                    "ok": rtl_result.ok,
                    "message": "PX4 native RTL requested" if rtl_result.ok else "PX4 rejected native RTL",
                    "control_channel": "MAVLink native mode" if backend == "px4_mavlink" else "ROS2 gateway native mode",
                    "result": rtl_result.to_dict(),
                }
            else:
                result = self._manual_return_home(target_vehicles)
                result["control_channel"] = "local NED guided path"
            if cancel_result:
                result["cancelled_active_task"] = cancel_result
            return result
        if action in {"connect", "reconnect"}:
            result = self.tools.reconnect()
            self._append_event("info", "tool", "Reconnect AirSim", result.to_dict())
            return {"ok": result.ok, "result": result.to_dict()}
        if action == "clear_events":
            with self._lock:
                self._events.clear()
            return {"ok": True}
        return {"ok": False, "error": f"unknown control action: {action}"}

    def _backend_mismatch(self, expected_backend: str = "") -> dict[str, Any] | None:
        expected = str(expected_backend or "").strip()
        active = str(self.tools.backend_id or "").strip()
        if expected and expected != active:
            return {
                "ok": False,
                "error": f"active backend changed from {expected} to {active}; command was not sent",
                "expected_backend": expected,
                "active_backend": active,
            }
        return None

    def _cancel_active_work(self) -> dict[str, Any]:
        self._cancel_requested.set()
        run_to_publish: RunState | None = None
        previous_phase = ""
        cancelled_ids: list[str] = []
        with self._lock:
            if self._current and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}:
                previous_phase = self._current.phase
                self._current.status = "cancelled"
                self._current.phase = "cancelled"
                self._current.failure_reason = "operator cancelled task"
                self._current.finished_at = time.time()
                self._current.progress = 100.0
                self._current.assistant_message = "任务已中断。"
                self._cancelled_request_ids.add(self._current.run_id)
                cancelled_ids.append(self._current.run_id)
                run_to_publish = self._current
            for message in self._messages:
                if message.role == "assistant" and message.status == "running" and message.run_id:
                    self._cancelled_request_ids.add(message.run_id)
                    cancelled_ids.append(message.run_id)
                    if not str(message.content or "").strip():
                        message.content = "已中断当前回复。"
                    message.status = "complete"
                    details = dict(message.details or {})
                    details["phase"] = "cancelled"
                    details["cancelled"] = True
                    message.details = details
                    message.updated_at = time.time()

        hover_result = None
        if run_to_publish and run_to_publish.mode == "execute" and previous_phase != "responding":
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if capabilities.get("flight_control"):
                hover_result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)

        if run_to_publish:
            self._update_assistant_message(
                run_to_publish.run_id,
                "任务已中断。",
                "complete",
                self._message_details(run_to_publish),
            )
            self._publish_run_update(run_to_publish)
        self._append_event(
            "warning",
            "agent",
            "已发送中断请求",
            {"run_ids": sorted(set(cancelled_ids)), "hover_result": hover_result.to_dict() if hover_result else None},
        )
        return {"ok": True, "cancelled": sorted(set(cancelled_ids)), "hover": hover_result.to_dict() if hover_result else None}

    def _is_run_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return self._cancel_requested.is_set() or run_id in self._cancelled_request_ids
