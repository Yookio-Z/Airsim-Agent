"""执行与轨迹渲染：_run_plan 主循环、执行时间线/思考/过程分发、幂等跳过。

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


class RuntimeExecuteMixin:
    def _simulate_plan(self, run: RunState) -> None:
        total = max(1, len(run.plan.steps if run.plan else []))
        if not run.plan:
            return
        run.phase = "planning"
        for index, step in enumerate(run.plan.steps, 1):
            result = self.tools.execute(step.tool, step.params, dry_run=True)
            step.status = "planned" if result.ok else "blocked"
            step.result = result.data
            step.safety = result.safety
            run.progress = index / total * 100
        run.status = "planned"
        run.phase = "planned"
        run.finished_at = time.time()

    def _begin_execution_trace(self, run: RunState, content: str = "") -> None:
        if not run.plan:
            return
        run.phase = "executing" if run.execute else "planning"
        if content:
            self._append_thought(run, "思考", content)
            self._append_process(run, "理解任务", content, status="completed")
        else:
            overview = self._thought_overview(run)
            self._append_thought(run, "理解任务", overview)
            self._append_process(run, "理解任务", overview, status="completed")
        tools = " → ".join(step.tool for step in run.plan.steps if step.tool and step.tool != "memory_store")
        if tools:
            self._append_thought(run, "工具选择", tools)
            self._append_process(run, "选择工具", tools, status="completed")
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._publish_run_update(run)
        self._frontend_render_grace()

    def _update_execution_trace_for_step(
        self,
        run: RunState,
        step: MissionStep,
        index: int,
        total: int,
    ) -> None:
        if step.tool == "memory_store":
            return
        self._append_thought(
            run,
            f"调用 {step.tool}",
            f"{index}/{total} · {step.label}",
            status="running",
        )
        self._append_process(
            run,
            self._tool_action_label(step.tool),
            f"{index}/{total} · {step.label}",
            status="running",
            tool=step.tool,
            params=step.params,
            kind="tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._frontend_render_grace(0.08)

    def _update_execution_trace_after_step(
        self,
        run: RunState,
        step: MissionStep,
        ok: bool,
    ) -> None:
        if step.tool == "memory_store":
            return
        message = ""
        if isinstance(step.result, dict):
            message = str(step.result.get("message") or step.result.get("status") or "")
        label = self._tool_action_label(step.tool)
        self._append_thought(
            run,
            f"{step.tool} {'完成' if ok else '失败'}",
            message,
            status="completed" if ok else "failed",
        )
        self._append_process(
            run,
            label,
            f"{label} → {message}" if message else ("完成" if ok else "失败"),
            status="completed" if ok else "failed",
            tool=step.tool,
            params=step.params,
            kind="tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )

    def _append_thought(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
    ) -> None:
        run.thought_trace.append(
            {
                "timestamp": time.time(),
                "title": title,
                "body": body,
                "status": status,
            }
        )

    def _append_process(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
        tool: str = "",
        params: dict[str, Any] | None = None,
        kind: str = "",
    ) -> None:
        body = self._compact_process_text(body)
        item_kind = kind or ("tool" if tool else "reasoning")
        if status in {"running", "completed", "failed", "blocked"}:
            for item in reversed(run.process_trace):
                same_item = item.get("tool") == tool if tool else item.get("title") == title
                if same_item and item.get("status") == "running":
                    item.update(
                        {
                            "timestamp": time.time(),
                            "title": title,
                            "body": body,
                            "status": status,
                            "params": dict(params or {}),
                            "kind": item_kind,
                        }
                    )
                    return
        run.process_trace.append(
            {
                "timestamp": time.time(),
                "title": title,
                "body": body,
                "status": status,
                "tool": tool,
                "params": dict(params or {}),
                "kind": item_kind,
            }
        )
        run.process_trace = run.process_trace[-80:]

    @staticmethod
    def _compact_process_text(text: str, limit: int = 6000) -> str:
        value = str(text or "")
        if len(value) <= limit:
            return value
        return "...\n" + value[-limit:]

    def _tool_action_label(self, tool: str) -> str:
        labels = {
            "drone_connect": "Connect flight link",
            "drone_disconnect": "Disconnect flight link",
            "drone_list_vehicles": "List vehicles",
            "drone_get_status": "Read vehicle status",
            "drone_arm": "Arm motors",
            "drone_disarm": "Disarm motors",
            "drone_takeoff": "Take off",
            "drone_land": "Land",
            "drone_hover": "Hold position",
            "drone_fly_to": "Fly to local coordinate",
            "drone_move_relative": "Move in body frame",
            "drone_fly_velocity": "Fly by velocity",
            "drone_fly_path": "Fly waypoint path",
            "drone_upload_mission": "Upload mission",
            "drone_download_mission": "Download mission",
            "drone_clear_mission": "Clear mission",
            "drone_start_mission": "Start mission",
            "drone_get_mission_progress": "Read mission progress",
            "drone_rotate_to": "Rotate heading",
            "drone_set_mode": "Set flight mode",
            "airsim_take_photo": "Capture image",
            "airsim_get_sensors": "Read sensors",
            "airsim_get_depth_map": "Read depth map",
            "airsim_detect_objects": "Detect objects",
            "airsim_vlm_analyze_image": "Analyze camera image",
            "airsim_vlm_confirm_target": "Confirm visual target",
            "provider_bridge_health": "Check provider bridge",
            "provider_obstacle_summary": "Read obstacle provider",
            "provider_validate_motion": "Validate motion provider",
            "airsim_task_status": "Read legacy task status",
            "airsim_task_cancel": "Cancel legacy task",
            "memory_store": "Store mission memory",
        }
        return labels.get(tool, tool.replace("_", " "))

    @staticmethod
    def _strip_plan_json_draft(text: str) -> str:
        """截掉思考文本末尾的规划/决策 JSON 草稿。

        模型思考流的结尾常带一段它正在起草的计划 JSON（{"intent": ...}）
        或纠错决策 JSON（{"action": ...}），思考块只保留自然语言推理——
        从第一段可识别的 JSON 草稿起截断。
        """
        value = str(text or "")
        m = re.search(
            r'\{\s*"(intent|summary|steps|assumptions|risk_level|task_level|execution_mode'
            r'|action|reason|is_complete|needs_replan|reflection|goal|tool|params|target)"\s*:',
            value,
        )
        if m:
            return value[: m.start()].rstrip()
        return value

    def _thought_overview(self, run: RunState) -> str:
        if not run.plan:
            return run.route_reason or "正在整理任务上下文。"
        reasoning = (run.plan.reasoning or "").strip()
        if reasoning:
            return reasoning
        if run.route_strategy == "direct" and run.plan.steps:
            tool = run.plan.steps[0].tool
            return f"这是一个明确的单步飞控意图，我直接选择 {tool}，随后用遥测回读确认结果。"
        if run.route_strategy == "template":
            return "这是结构清晰的飞行任务，我先形成可审计的工具序列，再逐步执行并校验状态。"
        if run.route_strategy == "plan_execute":
            return "这是短序列飞行任务，我采用一次性规划执行：LLM 先给出完整工具序列，runtime 逐步执行并校验，失败时再进入 Agent Loop 纠错。"
        if run.route_reason:
            return run.route_reason
        return run.plan.summary or "正在整理任务上下文。"

    def _frontend_render_grace(self, seconds: float = 0.15) -> None:
        with self._lock:
            has_subscribers = bool(self._subscribers)
        if has_subscribers:
            time.sleep(max(0.0, seconds))

    def _run_plan(self, run: RunState, finalize: bool = True, remember: bool = True) -> None:
        if not run.plan:
            return
        run.status = "running"
        run.phase = "executing"
        total = max(1, len(run.plan.steps))
        ok_count = 0
        preapproved = self._preapprove_first_high_risk_tool(run)
        if not (preapproved and preapproved.get("approved") is False):
            self._capture_start_telemetry(run)

        for index, step in enumerate(run.plan.steps, 1):
            if preapproved and preapproved.get("approved") is False:
                break
            while self.supervisor.should_pause() and not self.supervisor.is_emergency_stopped():
                run.status = "paused"
                run.phase = "paused"
                run.current_step = step.id
                time.sleep(0.2)

            if self.supervisor.is_emergency_stopped():
                run.status = "blocked"
                run.phase = "blocked"
                run.failure_reason = "emergency stop"
                break

            run.status = "running"
            run.phase = "executing"
            run.current_step = step.id
            step.status = "running"
            self._publish_run_update(run)
            self._append_event(
                "info",
                step.layer,
                f"执行步骤 {step.id}: {step.label}",
                {"tool": step.tool, "params": step.params},
            )
            self._update_execution_trace_for_step(run, step, index, total)

            result = self._maybe_skip_idempotent_step(step)
            if result is None:
                already_approved = bool(
                    preapproved
                    and preapproved.get("approved") is True
                    and preapproved.get("tool") == step.tool
                    and preapproved.get("params") == dict(step.params)
                )
                result = self._execute_agent_tool(
                    step.tool,
                    step.params,
                    dry_run=False,
                    run=run,
                    approval_already_granted=already_approved,
                )
            step.result = result.data
            step.safety = result.safety
            step.status = "completed" if result.ok else "failed"
            self._record_task_tool_result(run, step, result)
            self.memory.remember_tool_call(step.tool, result.ok)
            if not run.start_position_recorded:
                self._remember_task_start(run, result.data)
            self._remember_position_from_payload(result.data, source=step.tool)
            run.progress = index / total * 100
            self._publish_run_update(run)
            self._update_execution_trace_after_step(run, step, result.ok)

            if result.ok:
                ok_count += 1
                self._append_event("info", "tool", f"{step.tool} 完成", result.to_dict())
            else:
                run.status = "failed"
                run.phase = "failed"
                run.failure_reason = result.data.get("message", f"{step.tool} failed")
                self._publish_run_update(run)
                self._append_event("danger", "tool", f"{step.tool} 失败", result.to_dict())
                if step.tool not in {"drone_land", "drone_hover"}:
                    self.tools.execute("drone_hover", {}, dry_run=False)
                break

            run.progress = index / total * 100

        if run.status == "running":
            run.status = "completed"
            run.phase = "verifying"
            run.progress = 100.0

        run.finished_at = time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.agent_state = self._agent_state_context()
        self._append_thought(run, "校验结果", "正在回读最终状态并核对任务目标。", status="running")
        self._append_process(run, "回读与校验", "正在回读最终状态并核对任务目标。", status="running", kind="verify")
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        self._publish_run_update(run)
        run.verification = self._verify_run_outcome(run)
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.phase = "failed"
            run.failure_reason = run.verification.get("summary", "任务后状态校验失败")
            self._append_thought(run, "校验未通过", run.failure_reason, status="failed")
            self._append_process(run, "回读与校验", run.failure_reason, status="failed", kind="verify")
            self._append_event("warning", "verifier", "任务后状态校验失败", run.verification)
        elif run.verification:
            self._append_thought(run, "校验完成", str(run.verification.get("summary") or ""), status="completed")
            self._append_process(run, "回读与校验", str(run.verification.get("summary") or ""), status="completed", kind="verify")
            self._append_event("info", "verifier", "任务后状态校验完成", run.verification)
        if run.status == "completed":
            run.phase = "completed"
        if run.status == "completed":
            self._append_event("info", "memory", "任务闭环完成，写入经验")
        if remember:
            self._remember_plan_run(run, total=total, ok_count=ok_count)
        if finalize:
            self._finalize_assistant_response(run)

    def _remember_plan_run(self, run: RunState, total: int, ok_count: int) -> None:
        self.memory.remember_mission(
            {
                "run_id": run.run_id,
                "command": run.command,
                "intent": run.intent,
                "status": run.status,
                "summary": run.summary,
                "duration_sec": round((run.finished_at or time.time()) - run.started_at, 2),
                "steps_total": total,
                "steps_ok": ok_count,
                "failure_reason": run.failure_reason,
                "route_strategy": run.route_strategy,
                "tool_sequence": [step.tool for step in (run.plan.steps if run.plan else [])],
                "verification_status": run.verification.get("status", ""),
            }
        )

    def _maybe_skip_idempotent_step(self, step: MissionStep) -> ToolCallResult | None:
        """Skip already-satisfied setup steps in deterministic plans."""
        runtime = self.tools.status_snapshot()
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        message = ""
        if step.tool == "drone_connect" and connected:
            message = "already connected"
        elif step.tool == "drone_arm" and bool(drone.get("armed")):
            message = "already armed"
        elif step.tool == "drone_takeoff" and self._is_takeoff_already_satisfied(drone, step.params):
            message = "already airborne near requested altitude"
        else:
            return None

        now = time.time()
        return ToolCallResult(
            tool=step.tool,
            params=dict(step.params),
            ok=True,
            data={
                "status": "ok",
                "message": f"{message}; skipped duplicate {step.tool}",
                "skipped": True,
                "drone": drone,
            },
            started_at=now,
            finished_at=now,
        )

    def _is_takeoff_already_satisfied(self, drone: dict[str, Any], params: dict[str, Any]) -> bool:
        if not isinstance(drone, dict):
            return False
        altitude = self._vehicle_altitude_m(drone)
        try:
            target = abs(float(params.get("altitude", 3.0) or 3.0))
        except (TypeError, ValueError):
            target = 3.0
        if altitude is None:
            return bool(drone.get("flying"))
        target = max(0.5, target)
        minimum = max(0.5, min(target * 0.85, target - 0.3 if target > 1.0 else target * 0.85))
        return bool(drone.get("flying")) and altitude >= minimum

    def _vehicle_altitude_m(self, drone: dict[str, Any]) -> float | None:
        for key in ("altitude_m", "altitude"):
            value = drone.get(key)
            if value is None:
                continue
            try:
                return abs(float(value))
            except (TypeError, ValueError):
                pass
        pos = drone.get("position_ned")
        if isinstance(pos, dict) and pos.get("z") is not None:
            try:
                return abs(float(pos.get("z")))
            except (TypeError, ValueError):
                return None
        return None
