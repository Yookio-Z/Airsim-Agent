"""消息与事件：消息组装/流式、事件发布、孤儿标记、结果核验与记忆。

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


class RuntimeMessagesMixin:
    def _preapprove_first_high_risk_tool(self, run: RunState) -> dict[str, Any] | None:
        if not run.plan or run.risk_level != "high":
            return None
        runtime = self.tools.status_snapshot()
        capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
        if not capabilities.get("requires_operator_approval"):
            return None
        for step in run.plan.steps:
            risk_level = self._tool_risk_level(step.tool, capabilities, run)
            if risk_level != "high":
                continue
            approved = self._await_tool_approval(
                run,
                step.tool,
                dict(step.params),
                risk_level,
                reason=self._approval_reason(step.tool, dict(step.params)),
            )
            return {
                "approved": approved,
                "tool": step.tool,
                "params": dict(step.params),
                "risk_level": risk_level,
            }
        return None

    def _append_message(
        self,
        role: str,
        content: str,
        run_id: str = "",
        status: str = "complete",
        details: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        now = time.time()
        if role == "assistant" and run_id:
            updated_message: ChatMessage | None = None
            updated_payload: dict[str, Any] | None = None
            with self._lock:
                for existing in reversed(self._messages):
                    if existing.role == "assistant" and existing.run_id == run_id:
                        existing.content = content
                        existing.status = status
                        existing.details = details or existing.details
                        existing.updated_at = now
                        updated_message = existing
                        updated_payload = self._message_public_dict(existing)
                        self._dedupe_assistant_run_messages_locked(run_id, existing.id)
                        break
            if updated_message and updated_payload:
                self._publish("message_update", updated_payload)
                self._persist_current_session()
                return updated_message
        message = ChatMessage(
            id=f"msg_{int(now * 1000)}_{len(self._messages) + 1}",
            role=role,
            content=content,
            attachments=list(attachments or []),
            run_id=run_id,
            status=status,
            details=details or {},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._messages.append(message)
        self._publish("message_create", self._message_public_dict(message))
        self._persist_current_session()
        return message

    def _update_assistant_message(
        self,
        run_id: str,
        content: str,
        status: str,
        details: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        updated = None
        with self._lock:
            # 优先按 run_id 精确匹配
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    # a cancelled run's worker thread may still emit one last
                    # progress update; writing "running" back over the
                    # interrupted marker makes the orphan sweep flag it as an
                    # error later ("任务进程已中断") — freeze it instead
                    if status == "running" and run_id in self._cancelled_request_ids:
                        return
                    message.content = content
                    message.status = status
                    message.details = {**(message.details or {}), **(details or {})}
                    message.updated_at = time.time()
                    updated = self._message_public_dict(message)
                    self._dedupe_assistant_run_messages_locked(run_id, message.id)
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not updated:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        if status == "running" and run_id in self._cancelled_request_ids:
                            return
                        message.content = content
                        message.status = status
                        message.run_id = run_id
                        message.details = {**(message.details or {}), **(details or {})}
                        message.updated_at = time.time()
                        updated = self._message_public_dict(message)
                        self._dedupe_assistant_run_messages_locked(run_id, message.id)
                        break
        if updated:
            self._publish("message_update", updated)
            # persist=False 时跳过磁盘写入，避免 reasoning token 逐个触发全量 IO
            if persist:
                self._persist_current_session()
            return
        self._append_message("assistant", content, run_id=run_id, status=status, details=details)

    def _dedupe_assistant_run_messages_locked(self, run_id: str, keep_id: str) -> bool:
        if not run_id:
            return False
        before = len(self._messages)
        self._messages = [
            message
            for message in self._messages
            if not (
                message.role == "assistant"
                and message.run_id == run_id
                and message.id != keep_id
            )
        ]
        return len(self._messages) != before

    def _mark_orphan_running_messages_locked(self) -> bool:
        active_run_id = self._current.run_id if self._current else ""
        live_statuses = {"running", "queued", "planned", "responding", "awaiting_approval"}
        now = time.time()
        startup_grace_sec = 30.0
        changed = False
        seen_assistant_runs: set[str] = set()
        for message in list(reversed(self._messages)):
            if message.role != "assistant" or not message.run_id:
                continue
            if message.run_id in seen_assistant_runs:
                self._messages.remove(message)
                changed = True
                continue
            seen_assistant_runs.add(message.run_id)
            if message.status in live_statuses and message.run_id != active_run_id:
                message_age = now - max(float(message.updated_at or 0.0), float(message.created_at or 0.0))
                mode = str((message.details or {}).get("mode") or "").lower()
                if mode == "chat" and message.run_id in self._active_chat_requests:
                    continue
                created_in_this_process = float(message.created_at or 0.0) >= self._started_at - 1.0
                if mode == "chat" and created_in_this_process and message_age < 300.0:
                    continue
                # Run IDs still pending in the execute queue (submitted but
                # whose _plan_and_execute thread has not yet set self._current)
                # are alive — not orphans. This closes the race between
                # submit_command creating the message and the thread reaching
                # the self._current assignment after LLM routing.
                if message.run_id in self._pending_run_ids:
                    continue
                if created_in_this_process and message_age < startup_grace_sec:
                    continue
                if not str(message.content or "").strip():
                    message.content = "任务进程已中断或服务已重启，请重新执行该指令。"
                message.status = "error"
                details = dict(message.details or {})
                details["phase"] = "interrupted"
                details["interrupted"] = True
                message.details = details
                message.updated_at = time.time()
                changed = True
        return changed

    def _finalize_assistant_response(self, run: RunState) -> None:
        final_status = run.status
        if final_status == "cancelled" or self._is_run_cancelled(run.run_id):
            run.status = "cancelled"
            run.phase = "cancelled"
            run.finished_at = run.finished_at or time.time()
            run.assistant_message = run.assistant_message or "任务已中断。"
            self._update_assistant_message(run.run_id, run.assistant_message, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        if final_status in {"completed", "planned", "failed", "blocked"} and run.answer_with_llm:
            run.status = "responding"
            run.phase = "responding"
            self._publish_run_update(run)
            self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))

        telemetry = self.tools.status_snapshot().get("drone")
        run.final_telemetry = dict(telemetry or {})
        if not run.verification:
            run.verification = self._verify_run_outcome(run)
        if not run.answer_with_llm:
            answer = self.planner.final_answer_stream(
                command=run.command,
                run_status=final_status,
                plan=run.plan,
                telemetry=telemetry,
                failure_reason=run.failure_reason,
                verification=run.verification,
                model_id=run.model_id or None,
                # LLM-written summary (streamed); final_answer_stream falls
                # back to the template internally when the LLM is unavailable
                force_fallback=False,
                should_stop=lambda: self._is_run_cancelled(run.run_id),
            )
            if self._is_run_cancelled(run.run_id):
                final_status = "cancelled"
                answer = "任务已中断。"
            run.status = final_status
            run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
            run.assistant_message = answer
            self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        buffer: list[str] = []
        reasoning_buffer: list[str] = []

        def on_reasoning(token: str) -> None:
            reasoning_buffer.append(token)
            reasoning = "".join(reasoning_buffer).strip()
            if not reasoning:
                return
            self._append_process(run, "模型推理", reasoning, status="running", kind="reasoning")
            self._update_assistant_message(
                run.run_id,
                "".join(buffer) or self._progress_message(run),
                "running",
                None,
                persist=False,
            )

        def on_token(token: str) -> None:
            buffer.append(token)
            self._append_assistant_delta(run.run_id, token, "".join(buffer), None)

        answer = self.planner.final_answer_stream(
            command=run.command,
            run_status=final_status,
            plan=run.plan,
            telemetry=telemetry,
            failure_reason=run.failure_reason,
            verification=run.verification,
            model_id=run.model_id or None,
            on_token=on_token,
            on_reasoning=on_reasoning,
            force_fallback=not run.answer_with_llm,
            should_stop=lambda: self._is_run_cancelled(run.run_id),
        )
        if not answer and buffer:
            answer = "".join(buffer)
        if reasoning_buffer:
            self._append_process(run, "模型推理", "".join(reasoning_buffer).strip(), status="completed", kind="reasoning")
        if self._is_run_cancelled(run.run_id):
            final_status = "cancelled"
            answer = answer or "任务已中断。"
        run.status = final_status
        run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
        run.assistant_message = answer
        self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
        self._publish_run_update(run)
        self._finalize_task_run(run)
        with self._lock:
            self._cancelled_request_ids.discard(run.run_id)

    def _append_assistant_delta(
        self,
        run_id: str,
        token: str,
        content: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] | None = None
        with self._lock:
            # 已取消的任务：冻结增量写入（与 _update_assistant_message 的
            # 中断竞态保护一致，避免把中断标记覆盖回 running）
            if run_id in self._cancelled_request_ids:
                return
            # 优先按 run_id 精确匹配
            target = None
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    target = message
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not target:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        target = message
                        break
            if target:
                target.content = content
                target.status = "running"
                target.run_id = run_id
                target.details = {**(target.details or {}), **(details or {})}
                target.updated_at = time.time()
                payload = {
                    "id": target.id,
                    "run_id": run_id,
                    "token": token,
                    "content": content,
                    "message": self._message_public_dict(target),
                }
        if payload:
            self._publish("message_delta", payload)

    def _progress_message(self, run: RunState) -> str:
        phase = run.phase or run.status
        if phase == "planning":
            return "正在规划任务并选择可用工具..."
        if phase == "responding":
            return "工具调用已完成，正在整理最终回复..."
        if phase == "verifying":
            return "工具调用已完成，正在回读状态并校验结果..."

        loop_state = run.loop_state if isinstance(run.loop_state, dict) else {}
        decisions = loop_state.get("decisions") if isinstance(loop_state, dict) else []
        results = loop_state.get("results") if isinstance(loop_state, dict) else []
        if isinstance(decisions, list) and isinstance(results, list) and len(decisions) > len(results):
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."
        if isinstance(results, list) and results:
            latest_result = results[-1] if isinstance(results[-1], dict) else {}
            result_tool = str(latest_result.get("tool") or "")
            if result_tool:
                return f"已完成：{self._tool_action_label(result_tool)}，正在处理结果..."
        if isinstance(decisions, list) and decisions:
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."

        if run.plan and run.current_step:
            for step in run.plan.steps:
                if step.id == run.current_step:
                    if step.tool == "memory_store":
                        return "正在整理最终结果..."
                    label = step.label or self._tool_action_label(step.tool)
                    return f"正在执行：{label}..."
        if run.plan and run.plan.steps:
            current = next((step for step in run.plan.steps if step.status == "running"), None)
            if not current:
                current = next((step for step in run.plan.steps if step.status in {"pending", "planned"}), None)
            if current:
                if current.tool == "memory_store":
                    return "正在整理最终结果..."
                label = current.label or self._tool_action_label(current.tool)
                return f"正在执行：{label}..."

        if phase == "executing":
            return "正在执行任务，请稍候..."
        return "正在处理任务，请稍候..."

    def _message_details(self, run: RunState) -> dict[str, Any]:
        return {
            "mode": run.mode,
            "phase": run.phase,
            "run_status": run.status,
            "progress": round(run.progress, 1),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "plan": self._sanitize_for_frontend(run.plan.to_dict()) if run.plan else None,
            "failure_reason": run.failure_reason,
            "task_level": run.task_level,
            "route_strategy": run.route_strategy,
            "route_reason": run.route_reason,
            "loop_state": self._sanitize_for_frontend(run.loop_state),
            "verification": self._sanitize_for_frontend(run.verification),
            "agent_state": self._sanitize_for_frontend(run.agent_state),
            "thought_trace": self._sanitize_for_frontend(list(run.thought_trace)),
            "process_trace": self._sanitize_for_frontend(list(run.process_trace)),
        }

    def _run_public_dict(self, run: RunState) -> dict[str, Any]:
        return self._sanitize_for_frontend(run.to_dict())

    def _message_public_dict(self, message: ChatMessage) -> dict[str, Any]:
        return self._sanitize_for_frontend(message.to_dict())

    def _sanitize_for_frontend(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text == "image_base64":
                    image_text = str(item or "")
                    sanitized["image_base64_omitted"] = True
                    sanitized["image_base64_bytes"] = len(image_text)
                    continue
                if key_text in {"data_url", "image_data_url"}:
                    image_text = str(item or "")
                    sanitized[f"{key_text}_omitted"] = True
                    sanitized[f"{key_text}_bytes"] = len(image_text)
                    continue
                sanitized[key_text] = self._sanitize_for_frontend(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_for_frontend(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_for_frontend(item) for item in value]
        if isinstance(value, str) and len(value) > 12000:
            return f"{value[:12000]}... [omitted {len(value) - 12000} chars]"
        return value

    def _append_event(
        self,
        level: str,
        source: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(time.time(), level, source, message, data or {})
        with self._lock:
            self._events.append(event)
            self._events = self._events[-200:]
            terminal = {"completed", "planned", "failed", "blocked", "cancelled"}
            default_run_id = (
                self._current.run_id
                if self._current and self._current.status not in terminal
                else ""
            )
        self._record_task_event(event, default_run_id=default_run_id)
        self._publish("runtime_event", event.to_dict())
        return event

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "type": event_type,
            "payload": payload,
            "time": time.time(),
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(envelope)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(envelope)
                except Exception:
                    pass

    def _publish_run_update(self, run: RunState) -> None:
        self._update_task_run(run)
        self._publish("run_update", self._run_public_dict(run))

    def _start_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.start_run(run, session_id=self._current_session_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _update_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.update_run(run)
        except Exception:
            pass

    def _finalize_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.finalize_run(run)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_event(self, event: RuntimeEvent, default_run_id: str = "") -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_event(event.to_dict(), default_run_id=default_run_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_tool_result(self, run: RunState, step: MissionStep, result: ToolCallResult) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_tool_result(run, step, result)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _capture_start_telemetry(self, run: RunState) -> None:
        if isinstance(run.start_telemetry, dict) and isinstance(run.start_telemetry.get("position_ned"), dict):
            self._remember_task_start(run, run.start_telemetry)
            return
        result = self.tools.execute("drone_get_status", {}, dry_run=False)
        if result.ok and isinstance(result.data, dict):
            run.start_telemetry = dict(result.data)
            self._remember_task_start(run, run.start_telemetry)
            self._append_event("info", "verifier", "任务起点状态已回读", result.to_dict())
        else:
            self._append_event("warning", "verifier", "任务起点状态回读失败", result.to_dict())

    def _verify_run_outcome(self, run: RunState) -> dict[str, Any]:
        if not run.execute or run.status == "planned":
            return {
                "status": "not_executed",
                "level": "info",
                "summary": "当前仅完成规划，未执行仿真动作，因此不进行任务后位置校验。",
            }

        start = run.start_telemetry or {}
        end = run.final_telemetry or {}
        start_pos = start.get("position_ned") if isinstance(start, dict) else None
        end_pos = end.get("position_ned") if isinstance(end, dict) else None
        checks: list[dict[str, Any]] = []

        result: dict[str, Any] = {
            "status": "unknown",
            "level": "info",
            "summary": "已回读任务后状态。",
            "start_position_ned": start_pos or {},
            "final_position_ned": end_pos or {},
            "final_flying": end.get("flying") if isinstance(end, dict) else None,
            "final_landed_state": end.get("landed_state") if isinstance(end, dict) else None,
            "checks": checks,
        }

        if isinstance(start_pos, dict) and isinstance(end_pos, dict):
            dx = self._float(end_pos.get("x")) - self._float(start_pos.get("x"))
            dy = self._float(end_pos.get("y")) - self._float(start_pos.get("y"))
            dz = self._float(end_pos.get("z")) - self._float(start_pos.get("z"))
            result["delta_ned"] = {"x": round(dx, 3), "y": round(dy, 3), "z": round(dz, 3)}
            result["delta_xy_m"] = round((dx * dx + dy * dy) ** 0.5, 3)
            result["delta_3d_m"] = round((dx * dx + dy * dy + dz * dz) ** 0.5, 3)

        lower = run.command.lower()
        steps = list(run.plan.steps if run.plan else [])
        wants_land = any(k in lower for k in ["land", "降落", "落地"])
        final_landing_expected = wants_land or any(step.tool == "drone_land" for step in steps)

        def later_has_position_goal(index: int) -> bool:
            later_tools = {step.tool for step in steps[index + 1 :]}
            return bool(later_tools & {"drone_fly_to", "drone_move_relative", "drone_upload_mission", "drone_start_mission"})

        def later_lands(index: int) -> bool:
            return any(step.tool == "drone_land" for step in steps[index + 1 :])

        if final_landing_expected:
            # land 工具内部已做最长 45s 的落地确认（并行轮询每架）——工具
            # 成功即视为落地达标；落地后 AirSim 的遥测枚举/位置有滞后，
            # 仅凭 end telemetry 会误判"未达目标"而空转纠错循环
            land_step_ok = any(
                step.tool == "drone_land" and step.status == "completed"
                for step in steps
            )
            landed = bool(
                end.get("flying") is False
                or end.get("landed_state") == "landed"
                or land_step_ok
            )
            checks.append({
                "name": "landed_state",
                "ok": landed,
                "severity": "hard",
                "expected": "flying=false 或 landed_state=landed",
                "actual": {"flying": end.get("flying"), "landed_state": end.get("landed_state")},
            })

        takeoff_steps = [step for step in steps if step.tool == "drone_takeoff"]
        if takeoff_steps and isinstance(end, dict) and not final_landing_expected:
            expected_altitude = max(self._float(step.params.get("altitude"), 3.0) for step in takeoff_steps)
            ned_altitude = abs(self._float(end_pos.get("z"))) if isinstance(end_pos, dict) else 0.0
            gps = end.get("gps") if isinstance(end.get("gps"), dict) else {}
            gps_altitude = abs(self._float(gps.get("alt"))) if isinstance(gps, dict) else 0.0
            actual_altitude = max(ned_altitude, gps_altitude)
            min_altitude = max(0.5, expected_altitude * 0.85, expected_altitude - 0.5)
            flying = bool(end.get("flying") is True or actual_altitude >= 0.5)
            checks.append({
                "name": "takeoff_altitude",
                "ok": flying and actual_altitude >= min_altitude,
                "severity": "hard",
                "expected": {"altitude_m": round(expected_altitude, 3), "min_observed_m": round(min_altitude, 3)},
                "actual": {
                    "altitude_m": round(actual_altitude, 3),
                    "flying": end.get("flying"),
                    "armed": end.get("armed"),
                    "mode": end.get("mode"),
                },
            })

        for index, step in enumerate(steps):
            if step.tool == "drone_move_relative" and isinstance(start_pos, dict) and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                expected_xy = (self._float(step.params.get("forward_m")) ** 2 + self._float(step.params.get("right_m")) ** 2) ** 0.5
                actual_xy = float(result.get("delta_xy_m", 0.0))
                tolerance = max(1.0, expected_xy * 0.45)
                error = abs(actual_xy - expected_xy)
                hard_tolerance = max(3.0, expected_xy * 1.5)
                ok = error <= tolerance
                checks.append({
                    "name": "relative_xy_distance",
                    "ok": ok,
                    "severity": "hard" if error > hard_tolerance else "soft",
                    "expected": round(expected_xy, 3),
                    "actual": round(actual_xy, 3),
                    "tolerance": round(tolerance, 3),
                    "error_m": round(error, 3),
                })
            elif step.tool == "drone_fly_to" and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                target = step.params
                dx = self._float(end_pos.get("x")) - self._float(target.get("x"))
                dy = self._float(end_pos.get("y")) - self._float(target.get("y"))
                dz = self._float(end_pos.get("z")) - self._float(target.get("z"))
                err_xy = (dx * dx + dy * dy) ** 0.5
                ignore_z = later_lands(index) or final_landing_expected
                err = err_xy if ignore_z else (dx * dx + dy * dy + dz * dz) ** 0.5
                tolerance = 2.0
                hard_tolerance = 6.0
                checks.append({
                    "name": "absolute_position_target",
                    "ok": err <= tolerance,
                    "severity": "hard" if err > hard_tolerance else "soft",
                    "expected": {"x": target.get("x"), "y": target.get("y"), "z": target.get("z")},
                    "actual": end_pos,
                    "error_m": round(err, 3),
                    "xy_error_m": round(err_xy, 3),
                    "z_ignored_after_land": ignore_z,
                    "tolerance": tolerance,
                })

        if isinstance(end, dict) and ("has_collided" in end or "collision" in end):
            collision_value = end.get("has_collided")
            if collision_value is None and isinstance(end.get("collision"), dict):
                collision_value = end["collision"].get("has_collided")
            checks.append({
                "name": "collision_free",
                "ok": collision_value is not True,
                "severity": "hard",
                "expected": False,
                "actual": collision_value,
            })

        wants_search = any(k in lower for k in ["search", "find", "locate", "搜索", "寻找", "查找", "目标"])
        search_steps = [step for step in steps if step.tool in {"skill:search", "airsim_search_target", "airsim_vlm_confirm_target"}]
        if wants_search or search_steps:
            search_statuses = {
                str(value).strip().lower()
                for step in search_steps
                for value in self._collect_field_values(step.result, "status")
            }
            found_markers = {"candidate_found", "target_found", "found", "locked", "target_confirmed"}
            failed_markers = {"not_found", "target_not_confirmed", "failed", "cancelled", "canceled", "error", "blocked"}
            search_ok = (
                bool(search_steps)
                and bool(search_statuses & (found_markers | {"completed"}))
                and not bool(search_statuses & failed_markers)
            )
            if search_statuses & found_markers:
                search_ok = True
            checks.append({
                "name": "target_search_outcome",
                "ok": search_ok,
                "severity": "hard",
                "expected": "search reaches a terminal non-failure outcome",
                "actual": sorted(search_statuses),
            })

        wants_track = any(k in lower for k in ["track", "follow", "追踪", "跟踪", "跟随"])
        tracking_steps = [step for step in steps if step.tool == "airsim_track_object"]
        if wants_track or tracking_steps:
            tracking_statuses = {
                str(value).strip().lower()
                for step in tracking_steps
                for value in self._collect_field_values(step.result, "status")
            }
            tracking_ok = bool(tracking_steps) and "completed" in tracking_statuses and not bool(
                tracking_statuses & {"failed", "cancelled", "canceled", "error", "blocked"}
            )
            checks.append({
                "name": "tracking_outcome",
                "ok": tracking_ok,
                "severity": "hard",
                "expected": "tracking task completed",
                "actual": sorted(tracking_statuses),
            })

        if checks:
            failed = [check for check in checks if not check.get("ok")]
            hard_failed = [check for check in failed if check.get("severity") == "hard"]
            result["status"] = "failed" if hard_failed else ("passed_with_warnings" if failed else "passed")
            result["level"] = "failed" if hard_failed else ("warning" if failed else "ok")
            if hard_failed:
                result["summary"] = "任务执行后关键状态未达到目标。"
            elif failed:
                result["summary"] = "任务执行后状态已回读，未发现阻断性失败。"
            else:
                result["summary"] = "任务执行后状态与目标一致。"
        else:
            result["status"] = "observed"
        return result

    def _float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _collect_field_values(self, value: Any, field_name: str) -> list[Any]:
        values: list[Any] = []
        if isinstance(value, dict):
            if field_name in value:
                values.append(value.get(field_name))
            for nested in value.values():
                values.extend(self._collect_field_values(nested, field_name))
        elif isinstance(value, list):
            for nested in value:
                values.extend(self._collect_field_values(nested, field_name))
        return values

    def _remember_task_start(self, run: RunState, telemetry: dict[str, Any] | None) -> None:
        if run.start_position_recorded or not isinstance(telemetry, dict):
            return
        position = telemetry.get("position_ned")
        if not isinstance(position, dict):
            return
        heading = telemetry.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_task_start(run.run_id, run.command, position, heading_float)
        self.memory.remember_position(position, heading_float, source="task_start")
        run.start_position_recorded = True

    def _remember_position_from_payload(self, payload: dict[str, Any] | None, source: str) -> None:
        if not isinstance(payload, dict):
            return
        position = payload.get("position_ned") or payload.get("target_position_ned")
        if not isinstance(position, dict):
            return
        heading = payload.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_position(position, heading_float, source=source)

    def _chat_readonly_tools(self) -> list[dict[str, Any]]:
        """Read-only query tools exposed to chat mode (function-calling
        schemas). The whitelist is the safety boundary: chat can pull live
        status data but can never arm/move/land a vehicle."""
        allowed = {"drone_get_status", "drone_list_vehicles"}
        schemas: list[dict[str, Any]] = []
        try:
            for spec in self.tools.list_tools():
                name = str(spec.get("name") or "")
                if name not in allowed:
                    continue
                schemas.append(
                    function_tool_schema(
                        name,
                        str(spec.get("description") or name),
                        tool_schema_from_spec(name, spec.get("parameters") or {}, {}),
                    )
                )
        except Exception:
            return []
        return schemas

    def _refresh_chat_state(self, agent_state: dict[str, Any]) -> dict[str, Any]:
        """Refresh the read-only vehicle state once before answering a chat
        question when the snapshot is busy or stale.

        Chat mode does not execute control tools, but it must not answer from
        fabricated/outdated numbers either — a single read-only status + list
        call gives the model real telemetry to reason about.
        """
        try:
            runtime = self.tools.status_snapshot()
        except Exception:
            return agent_state
        if not runtime.get("connected") or runtime.get("stale_connection"):
            return agent_state
        busy = bool(runtime.get("busy"))
        has_vehicle = bool((agent_state or {}).get("vehicle") or (runtime.get("vehicles")))
        if not busy and has_vehicle:
            return agent_state
        result = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
        if not result.ok:
            return agent_state
        try:
            fresh_runtime = self.tools.status_snapshot()
        except Exception:
            fresh_runtime = runtime
        fresh = self._agent_state_context(fresh_runtime)
        if fresh:
            agent_state = fresh
        return agent_state

    def _plan_reasoning_sink(self, run_id: str, command: str) -> Callable[[str], None]:
        """Throttled reasoning-token sink for streamed planning.

        Reasoning streams into the message's ``reasoning_text`` details field
        （前端思考块：默认折叠、标题行滚动最新一句、展开看全文——dsh 插件
        同款交互）。正文 content 不被推理占据，规划完成后直接呈现结果。"""
        buffer: list[str] = []
        emitted: list[str] = []
        last_flush: list[float] = [0.0]

        def flush() -> None:
            if not buffer:
                return
            text = "".join(buffer)
            buffer.clear()
            if not text:
                return
            emitted.append(text)
            full = self._strip_plan_json_draft("".join(emitted))
            sink.full_text = full  # type: ignore[attr-defined]
            self._append_event("info", "model_reasoning", text[-1500:], {"run_id": run_id, "command": command[:60]})
            self._update_assistant_message(
                run_id,
                "思考中…",
                "running",
                {"mode": "execute", "phase": "planning", "reasoning_text": full[-8000:]},
                persist=False,
            )

        def sink(token: str) -> None:
            buffer.append(token)
            now = time.time()
            if now - last_flush[0] >= 0.4:
                last_flush[0] = now
                flush()

        # attach the final flush so the wrapper can drain the tail
        sink.final_flush = flush  # type: ignore[attr-defined]
        sink.full_text = ""
        return sink

    def _safety_snapshot(self) -> dict[str, Any]:
        constraints = self.tools.safety.constraints
        return {
            "max_altitude_m": constraints.max_altitude,
            "min_altitude_m": constraints.min_altitude,
            "max_velocity_ms": constraints.max_velocity,
            "geofence_radius_m": constraints.max_distance_from_home,
            "home_position_ned": list(constraints.home_position),
            "no_fly_zones": constraints.no_fly_zones,
            "hard_rules": [
                "NED z must be negative in the air",
                "danger-level safety validation blocks execution",
                "emergency stop may override every action",
                "long-running search/tracking tools must return task_id and be polled",
            ],
        }

    def _is_conflicting(self, command: str) -> bool:
        lower = command.lower()
        if not command.strip():
            return True
        landish = any(k in lower for k in ["land", "降落", "落地"])
        takeoffish = any(k in lower for k in ["takeoff", "起飞", "升空"])
        if landish and takeoffish and len(lower) < 20:
            return True
        return False
