"""工具执行：设置/遥测快照、后端连接激活、_execute_agent_tool、护栏与 VLM 工具。

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


class RuntimeToolsMixin:
    def connection_settings(self) -> dict[str, Any]:
        """Return current connection settings (QGC Links style)."""
        settings = _connection_settings()
        try:
            settings["detected_mavlink_links"] = [
                candidate.to_dict()
                for candidate in discover_serial_mavlink_candidates()
            ]
        except Exception:
            settings["detected_mavlink_links"] = []
        try:
            settings["vehicle_info"] = self.tools.vehicle_info(refresh=False)
        except Exception as exc:
            settings["vehicle_info"] = {"status": "error", "message": str(exc)}
        return settings

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return current active vehicle connection and firmware metadata."""
        try:
            info = self.tools.vehicle_info(refresh=refresh)
            return {"ok": info.get("status") != "error", "vehicle_info": info}
        except Exception as exc:
            return {"ok": False, "vehicle_info": {"status": "error", "message": str(exc)}}

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return current active vehicle parameter cache/query results."""
        try:
            data = self.tools.vehicle_parameters(
                refresh=refresh,
                query=query,
                limit=limit,
                offset=offset,
                timeout=timeout,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "parameter_info": data}
        except Exception as exc:
            return {"ok": False, "parameter_info": {"status": "error", "message": str(exc), "parameters": []}}

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one active vehicle parameter through MAVLink."""
        try:
            data = self.tools.set_vehicle_parameter(
                name=name,
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=timeout,
            )
            return {"ok": data.get("status") == "ok", "parameter_write": data}
        except Exception as exc:
            return {"ok": False, "parameter_write": {"status": "error", "message": str(exc)}}

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return current active vehicle setup diagnostics and telemetry histories."""
        try:
            data = self.tools.vehicle_setup_snapshot(
                include_history=include_history,
                history_limit=history_limit,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_setup": data}
        except Exception as exc:
            return {"ok": False, "vehicle_setup": {"status": "error", "message": str(exc), "history": {}}}

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return current active vehicle lightweight telemetry and histories."""
        try:
            data = self.tools.vehicle_telemetry_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_telemetry": data}
        except Exception as exc:
            return {"ok": False, "vehicle_telemetry": {"status": "error", "message": str(exc), "history": {}}}

    def camera_settings(self) -> dict[str, Any]:
        """Return current UI camera source settings."""
        return _camera_settings()

    def application_settings(self) -> dict[str, Any]:
        """Return operator-facing application preferences."""
        return _application_settings()

    def save_application_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist validated application preferences."""
        try:
            settings = _load_settings()
            current = _application_settings(settings)
            incoming = payload if isinstance(payload, dict) else {}
            for group in current:
                update = incoming.get(group)
                if isinstance(update, dict):
                    current[group].update(update)
            settings["application"] = _application_settings({"application": current})
            _save_settings(settings)
            return {"ok": True, "application": settings["application"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_connection_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the connection list / active id / auto-connect flag."""
        try:
            settings = _load_settings()
            if payload is None:
                payload = {}
            settings["connections"] = {
                "auto_connect": bool(payload.get("auto_connect", True)),
                "active_connection_id": str(payload.get("active_connection_id", "")),
                "connections": list(payload.get("connections") or []),
            }
            _save_settings(settings)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_camera_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the camera source settings used by the UI camera viewer."""
        try:
            settings = _load_settings()
            current = _camera_settings(settings)
            if payload is None:
                payload = {}
            payload_dict = dict(payload)
            merged = _camera_settings({"camera": {**current, **payload_dict}})
            persisted = dict(merged)
            if "host" not in payload_dict:
                persisted.pop("host", None)
            if "port" not in payload_dict:
                persisted.pop("port", None)
            settings["camera"] = persisted
            _save_settings(settings)
            return {"ok": True, "camera": merged}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── AirSim settings.json 模板（通信模式一键切换） ──

    def airsim_settings_info(self) -> dict[str, Any]:
        """List AirSim settings.json templates (with full content) + target path.

        AirSim's communication mode is decided by the settings.json:
          * SimpleFlight           -> airsim backend (direct RPC control)
          * PX4Multirotor + UDP    -> px4_mavlink backend (local/WSL PX4 SITL)
          * PX4Multirotor + TCP    -> px4_ros2 backend (Jetson/edge PX4 SITL)
        """
        templates_dir = REPO_ROOT / "config" / "airsim_settings"
        templates: list[dict[str, Any]] = []
        for template_id, meta in AIRSIM_SETTINGS_TEMPLATES.items():
            path = templates_dir / meta["file"]
            templates.append(
                {
                    "id": template_id,
                    "label": meta["label"],
                    "description": meta["description"],
                    "backend": meta["backend"],
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                    "content": path.read_text(encoding="utf-8") if path.is_file() else "",
                }
            )
        target = self._airsim_settings_path()
        return {
            "templates": templates,
            "target_path": str(target),
            "target_exists": target.is_file(),
        }

    @classmethod
    def _airsim_settings_path(cls) -> Path:
        """Resolve the AirSim settings.json location automatically.

        Nothing is hard-coded: %USERPROFILE% resolves from the current
        Windows user (Path.home()), so moving to another machine just works.
        The AIRSIM_SETTINGS_PATH environment variable remains available for
        deployment-level overrides (e.g. a custom AirSim install location);
        it is not exposed in the UI.
        """
        env_path = os.environ.get("AIRSIM_SETTINGS_PATH", "").strip()
        if env_path:
            return Path(env_path)
        candidates = [
            Path.home() / "Documents" / "AirSim" / "settings.json",
            Path.home() / "AirSim" / "settings.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def apply_airsim_settings_template(self, template_id: str) -> dict[str, Any]:
        """Backup the current settings.json and write the requested template.

        Never destructive: the existing file (if any) is copied to
        settings.json.bak-<timestamp> before writing.
        """
        meta = AIRSIM_SETTINGS_TEMPLATES.get(str(template_id or ""))
        if meta is None:
            return {"ok": False, "error": f"unknown template: {template_id}"}
        template_path = REPO_ROOT / "config" / "airsim_settings" / meta["file"]
        if not template_path.is_file():
            return {"ok": False, "error": f"template file missing: {template_path.name}"}
        target = self._airsim_settings_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup_path = None
            if target.is_file():
                backup_path = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
                backup_path.write_bytes(target.read_bytes())
            target.write_bytes(template_path.read_bytes())
            return {
                "ok": True,
                "template": template_id,
                "target_path": str(target),
                "backup_path": str(backup_path) if backup_path else None,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_backend(
        self,
        backend_id: str,
        connect_params: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Switch backend and reconnect, persisting the choice to disk.

        Args:
            backend_id: Target backend identifier.
            connect_params: Optional connection overrides. For PX4 MAVLink
                pass ``{"url": "udp:127.0.0.1:14540"}``. For AirSim pass
                ``{"ip": "127.0.0.1", "port": 41452}``.
            connection_id: Optional link id from the QGC Links panel. When
                provided, ``connect_params`` are taken from the stored link
                definition unless explicitly overridden.
        """
        with self._lock:
            self._backend_generation += 1
        # Resolve params: explicit overrides > stored link definition > defaults.
        resolved_params = dict(connect_params) if connect_params else {}
        if connection_id:
            conn_section = _connection_settings()
            connection = next(
                (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
                None,
            )
            if connection:
                _, built_params = _build_connect_params(connection)
                # Connection definition takes precedence; explicit overrides are fallback defaults.
                resolved_params = {**(dict(connect_params) if connect_params else {}), **built_params}

        switch = self.tools.set_backend(backend_id)
        if not switch.ok:
            return {"ok": False, "error": switch.data.get("message", "backend switch failed")}

        # Persist a minimal backend record for backwards compatibility.
        try:
            settings = _load_settings()
            settings["backend"] = self.tools.backend_id
            if resolved_params:
                settings["connect_params"] = resolved_params
            _save_settings(settings)
        except Exception:
            pass

        ip = str(resolved_params.get("ip", "127.0.0.1"))
        port = int(resolved_params.get("port", 41452))
        url = str(resolved_params.get("url", ""))
        fallback_url = str(resolved_params.get("fallback_url", ""))
        remote_host = str(resolved_params.get("remote_host", ""))
        remote_port = int(resolved_params.get("remote_port", 0) or 0)
        real_vehicle = bool(resolved_params.get("real_vehicle", False))
        result = self.tools.reconnect(
            ip=ip,
            port=port,
            url=url,
            fallback_url=fallback_url,
            remote_host=remote_host,
            remote_port=remote_port,
            real_vehicle=real_vehicle,
        )
        self._append_event(
            "info" if result.ok else "warning",
            "tool",
            f"Backend {self.tools.backend_id} reconnect",
            result.to_dict(),
        )
        return {
            "ok": result.ok,
            "backend": self.tools.backend_id,
            "switch": switch.to_dict(),
            "result": result.to_dict(),
        }

    def activate_connection(self, connection_id: str) -> dict[str, Any]:
        """Connect/disconnect a QGC Links entry.

        If the requested link is already the active one and currently connected,
        disconnect. Otherwise switch backend and reconnect with the link params.
        """
        if not connection_id:
            return {"ok": False, "error": "connection_id required"}

        conn_section = _connection_settings()
        connection = next(
            (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
            None,
        )
        if not connection:
            return {"ok": False, "error": f"unknown connection {connection_id}"}

        backend_id, connect_params = _build_connect_params(connection)
        tool_runtime = self.tools.status_snapshot()
        active_id = str(conn_section.get("active_connection_id") or "")
        already_active = tool_runtime.get("backend") == backend_id
        currently_connected = bool(tool_runtime.get("connected")) and not tool_runtime.get("stale_connection")

        if already_active and currently_connected and active_id == connection_id:
            result = self.tools.execute("drone_disconnect", {})
            ok = result.ok
            if ok:
                settings = _load_settings()
                settings["connections"] = settings.get("connections") or {}
                settings["connections"]["active_connection_id"] = ""
                _save_settings(settings)
            self._append_event(
                "info" if ok else "warning",
                "tool",
                "Disconnect link",
                result.to_dict(),
            )
            return {"ok": ok, "action": "disconnect", "result": result.to_dict()}

        result = self.set_backend(backend_id, connect_params=connect_params, connection_id=connection_id)
        if result.get("ok"):
            settings = _load_settings()
            settings["connections"] = settings.get("connections") or {}
            settings["connections"]["active_connection_id"] = connection_id
            _save_settings(settings)
        return result

    def _auto_connect_from_settings(self, expected_generation: int = 0, expected_backend_id: str = "") -> None:
        """Load persisted active link and reconnect on startup."""
        try:
            time.sleep(0.05)
            with self._lock:
                if (
                    self._backend_generation != expected_generation
                    or self.tools.backend_id != (expected_backend_id or self.tools.backend_id)
                ):
                    self._append_event(
                        "info",
                        "system",
                        "Auto-connect skipped because backend changed during startup",
                        {
                            "expected_generation": expected_generation,
                            "current_generation": self._backend_generation,
                            "expected_backend": expected_backend_id,
                            "current_backend": self.tools.backend_id,
                        },
                    )
                    return
            conn_section = _connection_settings()
            if not conn_section.get("auto_connect"):
                return
            expected_backend = self.tools.backend_id
            active_id, connection = _select_connection_for_backend(conn_section, expected_backend)
            if not connection:
                return
            backend_id, connect_params = _build_connect_params(connection)
            if active_id != conn_section.get("active_connection_id"):
                settings = _load_settings()
                settings["connections"] = settings.get("connections") or {}
                settings["connections"]["active_connection_id"] = active_id
                _save_settings(settings)
            self._append_event(
                "info",
                "system",
                f"Auto-connecting to {connection.get('name', active_id)}",
                {"backend": backend_id, "connect_params": connect_params},
            )
            self.set_backend(backend_id, connect_params=connect_params, connection_id=active_id)
        except Exception as exc:
            self._append_event("warning", "system", f"Auto-connect failed: {exc}", {})

    def _execute_agent_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
        approval_already_granted: bool = False,
    ) -> ToolCallResult:
        """Single governed entry point used by plans, loops, skills, and the tool API."""
        params = dict(params or {})
        caller_owns_run = self._execution_thread_id == threading.get_ident()
        with self._lock:
            run = run or (self._current if caller_owns_run else None)

        manual_safety_tools = {"drone_hover", "drone_land", "airsim_task_cancel"}
        read_only_tools = set(self.tools.READ_ONLY_TOOLS) | {"airsim_task_status"}
        if self._execution_slot.locked() and not caller_owns_run and tool not in read_only_tools | manual_safety_tools:
            return self._blocked_tool_result(tool, params, "an Agent execution is active; use pause/hover/land or wait for completion")

        # Formation conflict guard: while the deterministic formation/coverage
        # control loop is commanding vehicles, single-vehicle flight tools are
        # blocked so two control paths can never fight over the same drone.
        # Hover/land/status/connect stay available as safe recovery actions.
        formation_active = getattr(self.tools, "formation_active", None)
        if (
            callable(formation_active)
            and formation_active()
            and tool in self.tools.CONTROL_TOOLS
            and tool not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect", "airsim_task_cancel"}
        ):
            return self._blocked_tool_result(
                tool,
                params,
                "a formation/coverage mission is active; use formation_command(action=hover_all) or land_all before single-vehicle control",
            )

        if tool.startswith("skill:"):
            return self._execute_skill_tool(tool, params, dry_run=dry_run, run=run)
        if tool == "agent_subtask":
            return self._execute_sub_agent_tool(params, dry_run=dry_run, run=run)
        if tool == "memory_recall":
            return self._execute_memory_recall(params, dry_run=dry_run)
        if tool == "memory_remember":
            return self._execute_memory_remember(params, dry_run=dry_run)
        if tool == "airsim_vlm_confirm_target":
            return self._execute_vlm_confirm_tool(params, dry_run=dry_run, run=run)
        if tool == "airsim_vlm_analyze_image":
            return self._execute_vlm_analyze_tool(params, dry_run=dry_run, run=run)
        if dry_run:
            return self.tools.execute(tool, params, dry_run=True)

        runtime = self.tools.status_snapshot()
        profile = runtime.get("backend_profile") or {}
        capabilities = profile.get("capabilities") or {}
        risk_level = self._tool_risk_level(tool, capabilities, run, params)
        requires_approval = bool(capabilities.get("requires_operator_approval"))
        if risk_level == "high" and requires_approval and not approval_already_granted:
            if run is None:
                return self._blocked_tool_result(
                    tool,
                    params,
                    "high-risk real-vehicle tools must be submitted through Execute mode and approved",
                )
            approved = self._await_tool_approval(
                run,
                tool,
                params,
                risk_level,
                reason=self._approval_reason(tool, params),
            )
            if not approved:
                return self._blocked_tool_result(tool, params, run.failure_reason or "operator approval rejected")

        low_altitude_block = self._low_altitude_motion_guard(tool, params, runtime, capabilities)
        if low_altitude_block:
            return low_altitude_block

        obstacle_block = self._obstacle_guard(tool, params, capabilities)
        if obstacle_block:
            return obstacle_block

        result = self.tools.execute(
            tool,
            params,
            dry_run=False,
            blocked_by_supervisor=self.supervisor.is_emergency_stopped(),
        )
        self._remember_visual_frame_from_payload(result.data, source=tool, params=params)
        return result

    def _low_altitude_motion_guard(
        self,
        tool: str,
        params: dict[str, Any],
        runtime: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        if tool != "drone_move_relative" or not capabilities.get("flight_control"):
            return None
        try:
            forward = abs(float(params.get("forward_m", 0.0) or 0.0))
            right = abs(float(params.get("right_m", 0.0) or 0.0))
            up = float(params.get("up_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if (forward * forward + right * right) ** 0.5 < 0.15:
            return None
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        altitude = self._vehicle_altitude_m(drone)
        if altitude is None:
            return None
        projected_altitude = altitude + max(0.0, up)
        if bool(drone.get("flying")) and projected_altitude >= 1.5:
            return None
        return self._blocked_tool_result(
            tool,
            params,
            (
                "horizontal relative movement is blocked below safe altitude; "
                f"current altitude is {altitude:.2f} m. Take off to at least 3 m before moving horizontally."
            ),
        )

    def _execute_memory_recall(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        query = str(params.get("query") or "").strip()
        limit = 5
        try:
            limit = max(1, min(10, int(params.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        results = [] if dry_run else self.memory.recall(query, limit=limit)
        data = {"status": "planned" if dry_run else "ok", "query": query, "count": len(results), "results": results}
        return ToolCallResult("memory_recall", dict(params), True, data, started, time.time())

    def _execute_memory_remember(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        key = str(params.get("key") or "").strip()
        value = str(params.get("value") or "").strip()
        if not key:
            return ToolCallResult(
                "memory_remember",
                dict(params),
                False,
                {"status": "error", "message": "memory_remember requires a non-empty 'key'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        tags = params.get("tags")
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",") if item.strip()]
        if not dry_run:
            self.memory.remember_fact(key, value, tags)
        data = {"status": "planned" if dry_run else "ok", "key": key, "message": f"fact '{key}' stored"}
        return ToolCallResult("memory_remember", dict(params), True, data, started, time.time())

    def _execute_sub_agent_tool(self, params: dict[str, Any], dry_run: bool = False, run: RunState | None = None) -> ToolCallResult:
        """Delegate an open-ended subtask to a bounded sub-agent.

        Runs synchronously in the caller's thread, so the execution slot and
        approval context are shared with the parent loop. The sub-agent gets
        its own run log and step budget; the parent only receives the report.
        """
        started = time.time()
        goal = str(params.get("goal") or "").strip()
        if not goal:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                False,
                {"status": "error", "message": "agent_subtask requires a non-empty 'goal'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        max_steps = 6
        try:
            max_steps = max(2, min(12, int(params.get("max_steps") or 6)))
        except (TypeError, ValueError):
            max_steps = 6
        model_id = str(params.get("model_id") or "").strip() or (run.model_id if run else "") or ""
        if dry_run:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                True,
                {"status": "planned", "goal": goal, "message": "dry run only"},
                started,
                time.time(),
            )
        tool_runtime = self.tools.status_snapshot()
        capabilities = ((tool_runtime.get("backend_profile") or {}).get("capabilities")) or {}
        tool_cards = self.tools.list_tool_cards()
        parent_run_id = run.run_id if run else f"run_{int(time.time() * 1000)}"
        runner = SubAgentRunner(
            tools=self.tools,
            planner=self.planner,
            memory=self.memory,
            execute_tool=lambda name, sub_params, sub_dry: self._execute_agent_tool(name, sub_params, dry_run=sub_dry, run=run),
            should_stop=lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set(),
            should_pause=self.supervisor.should_pause,
            on_ui_event=self._on_agent_event,
            on_ui_state=self._on_agent_loop_state,
        )
        report = runner.run(
            parent_run_id,
            goal,
            constraints=str(params.get("constraints") or ""),
            tool_cards=tool_cards,
            capabilities=capabilities,
            model_id=model_id or None,
            max_steps=max_steps,
        )
        ok = report.get("status") == "completed"
        error_code = "" if ok else ("BLOCKED" if report.get("status") == "blocked" else "TOOL_ERROR")
        return ToolCallResult(
            "agent_subtask",
            dict(params),
            ok,
            {"status": "ok" if ok else "failed", **report},
            started,
            time.time(),
            error_code=error_code,
        )

    def _execute_skill_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()

        def governed(subtool: str, subparams: dict[str, Any], sub_dry_run: bool) -> ToolCallResult:
            return self._execute_agent_tool(subtool, subparams, dry_run=sub_dry_run, run=run)

        result = self.skills.execute(
            tool,
            params,
            self.tools,
            dry_run=dry_run,
            execute_tool=governed,
        )
        data = result.to_dict()
        self._remember_visual_frame_from_payload(data, source=tool, params=params)
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=result.ok,
            data=data,
            started_at=started,
            finished_at=time.time(),
        )

    def _tool_risk_level(
        self,
        tool: str,
        capabilities: dict[str, Any],
        run: RunState | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        card = TOOL_CARDS.get(tool)
        card_risk = str(card.risk if card else "low")
        if run and run.route_strategy == "direct" and run.plan and any(step.tool == tool for step in run.plan.steps):
            route_risk = {
                "safe": "low",
                "elevated": "medium",
                "high": "high",
            }.get(str(run.risk_level), str(run.risk_level))
            risk = self._max_risk(route_risk, card_risk)
        else:
            risk = card_risk
        if tool == "drone_land" and capabilities.get("real_vehicle"):
            return "high"
        if tool == "formation_command" and capabilities.get("real_vehicle"):
            action = str((params or {}).get("action") or "status")
            if action in {"set_drones", "set_formation"} or action in FORMATION_FLIGHT_ACTIONS:
                return "high"
        return risk if risk in {"low", "medium", "high"} else "medium"

    def _max_risk(self, first: str, second: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        first = first if first in order else "medium"
        second = second if second in order else "medium"
        return first if order[first] >= order[second] else second

    def _obstacle_guard(
        self,
        tool: str,
        params: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        guarded_tools = {"drone_move_relative"}
        if tool not in guarded_tools or not capabilities.get("obstacle_avoidance"):
            return None

        collector = getattr(self.tools, "collector", None)
        available_tools = getattr(collector, "tools", {}) if collector else {}
        if "provider_validate_motion" not in available_tools:
            return None

        validation_params = {
            "forward_m": float(params.get("forward_m", 0.0) or 0.0),
            "right_m": float(params.get("right_m", 0.0) or 0.0),
            "up_m": float(params.get("up_m", 0.0) or 0.0),
            "velocity": float(params.get("velocity", 1.0) or 1.0),
            "max_age_sec": 1.0,
        }
        check = self.tools.execute(
            "provider_validate_motion",
            validation_params,
            dry_run=False,
            allow_reconnect=False,
        )
        if check.ok:
            return None

        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={
                "status": "blocked",
                "message": (
                    "motion blocked by obstacle provider: "
                    f"{check.data.get('message') or check.data.get('status') or 'unsafe motion'}"
                ),
                "provider_check": check.to_dict(),
            },
            started_at=now,
            finished_at=now,
        )

    def _blocked_tool_result(self, tool: str, params: dict[str, Any], message: str) -> ToolCallResult:
        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={"status": "blocked", "message": message},
            started_at=now,
            finished_at=now,
            error_code="BLOCKED",
        )

    def _execute_vlm_confirm_tool(
        self,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()
        target = str(
            params.get("target_description")
            or params.get("target_class")
            or params.get("verify_target_class")
            or (run.command if run else "")
            or "目标"
        ).strip()
        if dry_run:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                True,
                {"status": "planned", "message": f"dry run only: confirm target '{target}' with VLM"},
                started,
                time.time(),
            )

        image_base64 = str(params.get("image_base64") or "").strip()
        source = str(params.get("source") or "last_image").strip().lower()
        context = dict(params.get("context") or {}) if isinstance(params.get("context"), dict) else {}
        with self._lock:
            last_frame = dict(self._last_visual_frame)
        if not image_base64 and source in {"", "last_image", "latest", "latest_image"}:
            image_base64 = str(last_frame.get("image_base64") or "")
            context.setdefault("image_source", last_frame.get("source_tool", "last_image"))
            context.setdefault("image_saved_to", last_frame.get("image_saved_to", ""))
            context.setdefault("visual_metadata", last_frame.get("metadata", {}))
        if not image_base64:
            image_path = str(params.get("image_path") or params.get("saved_to") or last_frame.get("image_saved_to") or "")
            image_base64 = self._read_image_base64(image_path)
            if image_path:
                context.setdefault("image_saved_to", image_path)
        if not image_base64:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {
                    "status": "error",
                    "message": "no image available for VLM confirmation; capture/search an image first or pass image_base64",
                },
                started,
                time.time(),
            )

        try:
            confirmation = self.planner.confirm_target_in_image(
                target_description=target,
                image_base64=image_base64,
                context={
                    **context,
                    "agent_state": self._agent_state_context(),
                    "operator_command": run.command if run else "",
                },
                model_id=(run.model_id if run and run.model_id else str(params.get("model_id") or "") or None),
            )
            data = {
                **confirmation,
                "message": confirmation.get("summary_zh", ""),
                "source": context.get("image_source") or source or "last_image",
                "image_saved_to": context.get("image_saved_to", ""),
            }
            if not dry_run:
                violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS.get("airsim_vlm_confirm_target"))
                if violations:
                    return ToolCallResult(
                        "airsim_vlm_confirm_target",
                        params,
                        False,
                        {**data, "status": "error", "validation_errors": violations, "message": "VLM confirmation output failed shape validation"},
                        started,
                        time.time(),
                        error_code="INVALID_TOOL_OUTPUT",
                    )
            return ToolCallResult("airsim_vlm_confirm_target", params, True, data, started, time.time())
        except LLMUnavailableError as exc:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {"status": "error", "message": str(exc), "target_description": target},
                started,
                time.time(),
            )
        except Exception as exc:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {"status": "error", "message": f"VLM target confirmation failed: {exc}", "target_description": target},
                started,
                time.time(),
            )

    def _execute_vlm_analyze_tool(
        self,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()
        question = str(params.get("question") or params.get("prompt") or (run.command if run else "") or "").strip()
        if dry_run:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                True,
                {"status": "planned", "message": "dry run only: analyze latest image with VLM"},
                started,
                time.time(),
            )

        image_base64, context = self._image_for_vlm(params)
        if not image_base64:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {
                    "status": "error",
                    "message": "no image available for VLM analysis; capture/search an image first or pass image_base64",
                },
                started,
                time.time(),
            )

        try:
            analysis = self.planner.analyze_image(
                question=question,
                image_base64=image_base64,
                context={
                    **context,
                    "agent_state": self._agent_state_context(),
                    "operator_command": run.command if run else "",
                },
                model_id=(run.model_id if run and run.model_id else str(params.get("model_id") or "") or None),
            )
            data = {
                **analysis,
                "source": context.get("image_source") or str(params.get("source") or "last_image"),
                "image_saved_to": context.get("image_saved_to", ""),
            }
            if not dry_run:
                violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS.get("airsim_vlm_analyze_image"))
                if violations:
                    return ToolCallResult(
                        "airsim_vlm_analyze_image",
                        params,
                        False,
                        {**data, "status": "error", "validation_errors": violations, "message": "VLM analysis output failed shape validation"},
                        started,
                        time.time(),
                        error_code="INVALID_TOOL_OUTPUT",
                    )
            return ToolCallResult("airsim_vlm_analyze_image", params, True, data, started, time.time())
        except LLMUnavailableError as exc:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {"status": "error", "message": str(exc), "question": question},
                started,
                time.time(),
            )
        except Exception as exc:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {"status": "error", "message": f"VLM image analysis failed: {exc}", "question": question},
                started,
                time.time(),
            )

    def _image_for_vlm(self, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        image_base64 = str(params.get("image_base64") or "").strip()
        source = str(params.get("source") or "last_image").strip().lower()
        context = dict(params.get("context") or {}) if isinstance(params.get("context"), dict) else {}
        with self._lock:
            last_frame = dict(self._last_visual_frame)
        if not image_base64 and source in {"", "last_image", "latest", "latest_image"}:
            image_base64 = str(last_frame.get("image_base64") or "")
            context.setdefault("image_source", last_frame.get("source_tool", "last_image"))
            context.setdefault("image_saved_to", last_frame.get("image_saved_to", ""))
            context.setdefault("visual_metadata", last_frame.get("metadata", {}))
        if not image_base64:
            image_path = str(params.get("image_path") or params.get("saved_to") or last_frame.get("image_saved_to") or "")
            image_base64 = self._read_image_base64(image_path)
            if image_path:
                context.setdefault("image_saved_to", image_path)
        return image_base64, context

    def _remember_visual_frame_from_payload(
        self,
        payload: dict[str, Any] | None,
        source: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return
        image_base64 = self._find_visual_value(payload, {"image_base64"})
        image_saved_to = self._find_visual_value(payload, {"image_saved_to", "saved_to", "approach_image_saved_to"})
        if not image_base64 and image_saved_to:
            image_base64 = self._read_image_base64(str(image_saved_to))
        if not image_base64:
            return
        metadata = self._visual_metadata(payload)
        with self._lock:
            self._last_visual_frame = {
                "source_tool": source,
                "params": dict(params or {}),
                "image_base64": str(image_base64),
                "image_saved_to": str(image_saved_to or ""),
                "metadata": metadata,
                "updated_at": time.time(),
            }

    def _find_visual_value(self, value: Any, keys: set[str], _depth: int = 0) -> Any:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item:
                    return item
            for nested in value.values():
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in reversed(value):
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        return None

    def _read_image_base64(self, image_path: str) -> str:
        if not image_path:
            return ""
        try:
            path = Path(image_path).expanduser()
            if not path.exists() or not path.is_file():
                return ""
            if path.stat().st_size > 12 * 1024 * 1024:
                return ""
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            return ""

    def _visual_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {
            "status",
            "message",
            "target",
            "target_class",
            "target_description",
            "vehicle",
            "camera",
            "image_type",
            "selected_view",
            "current_position",
            "search_progress",
            "detections",
            "target_world_position",
            "target_depth_meters",
            "target_distance_meters",
            "task_id",
        }
        metadata = {key: payload.get(key) for key in keep_keys if key in payload}
        task = payload.get("task")
        if isinstance(task, dict):
            result = task.get("result")
            if isinstance(result, dict):
                metadata["task_result"] = {
                    key: result.get(key)
                    for key in keep_keys
                    if key in result and key not in {"image_base64"}
                }
        return metadata

    def execute_tool(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        result = self._execute_agent_tool(tool, params or {}, dry_run=dry_run)
        self.memory.remember_tool_call(tool, result.ok)
        self._remember_position_from_payload(result.data, source=tool)
        self._append_event("info" if result.ok else "warning", "tool", f"工具调用: {tool}", result.to_dict())
        return {"ok": result.ok, "result": result.to_dict()}

    def capture_camera_frame(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capture a UI stream frame without polluting task history or tool statistics."""
        result = self._execute_agent_tool("airsim_take_photo", params or {}, dry_run=False)
        return {"ok": result.ok, "result": result.to_dict()}

    def camera_preview_frame(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight camera preview frame for the frontend."""
        return self.tools.capture_camera_preview(params or {})

    # ------------------------------------------------------------------
    # P6: GCS MissionManager facade. UI and Agent both call these methods
    # so mission data flows through a single backend-neutral boundary.
    # ------------------------------------------------------------------
