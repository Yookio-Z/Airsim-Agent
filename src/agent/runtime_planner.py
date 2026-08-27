"""plan-execute 规划与执行编排：LLM 规划/降级、工具审批、路由判定、纠正循环、事件格式化。

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


# Plan-Execute ⇄ ReAct collaboration:
# - OBSERVATION_TOOLS: steps whose outcome must be seen before the next step
#   can be chosen (photo/VLM/detect/depth).
# - MOTION_TOOLS: steps that change vehicle state.
# A fixed sequence with observation -> motion is structurally dependent on
# mid-execution observations, so it routes to the ReAct loop before executing.
CORRECTION_ATTEMPTS_MAX = 2
OBSERVATION_TOOLS = {
    "airsim_take_photo",
    "airsim_detect_objects",
    "airsim_vlm_analyze_image",
    "airsim_vlm_confirm_target",
    "airsim_get_depth_map",
    "airsim_get_sensors",
}
MOTION_TOOLS = {
    "drone_arm",
    "drone_takeoff",
    "drone_fly_to",
    "drone_move_relative",
    "drone_fly_path",
    "drone_rotate_to",
    "drone_land",
    "drone_hover",
}
# Failures that re-running cannot fix: link/connection problems mean the
# backend itself is unreachable, so a ReAct correction round is pointless.
CONNECTION_FAILURE_TERMS = (
    "connect",
    "connection refused",
    "connect timed out",
    "connection timed out",
    "connect timeout",
    "unreachable",
    "no backend",
    "链接失败",
    "连接失败",
    "无法连接",
)



class RuntimePlannerMixin:
    def _plan_and_execute(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str = "",
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        release_execution_slot: bool = False,
    ) -> None:
        if execute:
            self._execution_thread_id = threading.get_ident()
        replay_session = None
        if execute and run_id:
            replay_session = self._start_replay_session(
                run_id,
                {"run_id": run_id, "command": command, "mode": "execute"},
            )
        if run_id:
            with self._lock:
                self._run_log = RunLog(run_id)
            tool_runtime = self.tools.status_snapshot()
            self._run_log.write(
                "run.start",
                {
                    "command": command,
                    "mode": "execute" if execute else "plan",
                    "model_id": model_id or "",
                    "backend": str(tool_runtime.get("backend") or ""),
                    "attachments": len(attachments or []),
                },
            )
        else:
            with self._lock:
                self._run_log = None
        try:
            tool_runtime = self.tools.status_snapshot()
            agent_state = agent_state or self._agent_state_context(tool_runtime)
            backend_profile = tool_runtime.get("backend_profile") or {}
            capabilities = backend_profile.get("capabilities") or {}
            memory_snapshot = self.memory.snapshot()
            if run_id:
                self._update_assistant_message(
                    run_id,
                    "正在理解指令并准备执行计划...",
                    "running",
                    {
                        "mode": "execute" if execute else "plan",
                        "phase": "planning",
                        "agent_state": agent_state,
                        "process_trace": [
                            {
                                "timestamp": time.time(),
                                "title": "理解指令",
                                "body": "正在解析任务意图并生成可执行的工具序列；模型不可用时不会降级发出飞控指令。",
                                "status": "running",
                            }
                        ],
                    },
                )
            skill_guidance = self.skills.guidance_cards(command, capabilities, memory_snapshot)
            if skill_guidance:
                agent_state = self._agent_state_with_skill_guidance(agent_state, skill_guidance)
            # Primary path: Plan-Execute. The LLM plans once and the runtime
            # executes/verifies the sequence — simple commands finish after a
            # few deterministic steps without an agent loop, and failures or
            # observation-dependent tasks enter the correction loop.
            route = {
                "level": "plan_execute",
                "strategy": "plan_execute",
                "reason": "Plan-Execute primary path: LLM plans once, runtime executes and verifies; correction loop only on failure",
                "risk_level": "elevated" if capabilities.get("flight_control") else "safe",
            }
            self._append_event("info", "planner", "Plan-Execute 主路径启动", route)
            self._execute_plan_execute_route(
                command,
                execute,
                telemetry,
                model_id,
                route,
                capabilities,
                tool_runtime,
                memory_snapshot,
                run_id,
                agent_state,
                attachments=attachments or [],
            )
            return
        except Exception as e:
            failed_run = None
            hover_result = None
            if run_id:
                with self._lock:
                    if self._current and self._current.run_id == run_id:
                        self._current.status = "failed"
                        self._current.phase = "failed"
                        self._current.failure_reason = str(e)
                        self._current.finished_at = time.time()
                        failed_run = self._current
            if execute:
                hover_result = self._attempt_failure_hover(failed_run, str(e))
            if run_id:
                message = f"任务处理失败: {str(e)}"
                if hover_result:
                    message += " 已尝试执行安全悬停。"
                details = (
                    self._message_details(failed_run)
                    if failed_run
                    else {
                        "mode": "execute" if execute else "plan",
                        "phase": "failed",
                        "agent_state": agent_state or {},
                    }
                )
                if hover_result:
                    details["failure_safety_hover"] = hover_result
                self._update_assistant_message(
                    run_id,
                    message,
                    "error",
                    details,
                )
                if failed_run:
                    self._publish_run_update(failed_run)
                    try:
                        self._finalize_task_run(failed_run)
                    except Exception:
                        pass
            else:
                self._append_message("assistant", f"任务处理失败: {str(e)}", status="error")
            self._append_event("danger", "planner", "任务处理失败", {"error": str(e)})
        finally:
            if replay_session is not None:
                self._stop_replay_session()
            self._close_run_log(run_id, execute)
            if execute and self._execution_thread_id == threading.get_ident():
                self._execution_thread_id = 0
            if release_execution_slot and self._execution_slot.locked():
                self._execution_slot.release()
            if run_id:
                with self._lock:
                    self._cancelled_request_ids.discard(run_id)
                    self._pending_run_ids.discard(run_id)

    def _close_formation(self, reason: str) -> bool:
        """Hover all formation drones and stop the control thread.

        Called on run end, backend switches, and emergency stop so the swarm
        never keeps flying without an owner. Returns True when a mission was
        actually active.
        """
        try:
            return self.tools.formation_shutdown(reason)
        except Exception:
            return False

    def _close_run_log(self, run_id: str, execute: bool) -> None:
        """Write the terminal run.end event, drop the active log reference,
        and store one bounded transcript row in long-term memory."""
        with self._lock:
            run_log = self._run_log
            current = self._current
            if run_log is None:
                return
            self._run_log = None
        payload: dict[str, Any] = {"status": "planned" if not execute else "stopped", "command": ""}
        if current is not None and current.run_id == run_id:
            payload = {
                "status": current.status,
                "command": current.command,
                "summary": current.summary or "",
                "failure_reason": current.failure_reason or "",
                "verification_status": str((current.verification or {}).get("level") or ""),
                "finished_at": current.finished_at or time.time(),
                "phase": current.phase or "",
            }
            try:
                tools = [
                    str(row.get("tool") or "")
                    for row in ((current.loop_state or {}).get("results") or [])
                    if isinstance(row, dict)
                ]
                self.memory.remember_transcript(
                    run_id,
                    current.command,
                    current.status,
                    current.summary or "",
                    tools,
                    current.failure_reason or "",
                )
            except Exception:
                pass
            if "native tool calling unavailable" in (self.planner.last_error or ""):
                run_log.write("protocol.degraded", {"reason": self.planner.last_error[:300]})
        # a run ending with an active formation/coverage mission must not leave
        # the swarm flying without an owner
        if self._close_formation("run_end"):
            run_log.write("formation.shutdown", {"reason": "run_end", "phase": payload.get("phase", "")})
        run_log.write("run.end", payload)

    def _try_llm_plan(
        self,
        *,
        command: str,
        telemetry: dict[str, Any] | None,
        model_id: str,
        run_id: str,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        agent_state: dict[str, Any],
        attachments: list[dict[str, Any]],
        reasoning_sink: Callable[[str], None] | None = None,
    ) -> MissionPlan | None:
        planner_tool_cards = self._planner_tool_cards(
            command,
            tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            capabilities,
            memory_snapshot,
        )
        plan = self.planner.plan(
            command=command,
            tools=self.tools.list_tools(),
            safety=self._safety_snapshot(),
            telemetry=telemetry,
            memory=memory_snapshot,
            model_id=model_id or None,
            backend=str(tool_runtime.get("backend") or (tool_runtime.get("backend_profile") or {}).get("id") or ""),
            capabilities=capabilities,
            tool_cards=planner_tool_cards,
            agent_state=agent_state,
            conversation_context=self._recent_chat_context(),
            attachments=attachments,
            on_reasoning=reasoning_sink,
        )
        if run_id:
            plan.run_id = run_id
        run_log = self._run_log
        if run_log is not None:
            run_log.write(
                "plan",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "intent": plan.intent,
                    "summary": plan.summary,
                    "goal": plan.goal,
                    "steps": [
                        {"id": step.id, "label": step.label, "tool": step.tool, "params": step.params, "layer": step.layer}
                        for step in plan.steps
                    ],
                },
            )
        if str(plan.planner_source).startswith("rules"):
            self._append_event(
                "warning",
                "planner",
                "LLM 规划不可用，回退到规则路径",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "risk_notes": list(plan.risk_notes),
                },
            )
            return None
        return plan

    @staticmethod
    def _approval_reason(tool: str, params: dict[str, Any]) -> str:
        """Approval reason including the target vehicle(s) so the operator
        sees exactly what will be controlled (multi-vehicle aware)."""
        vehicle = str((params or {}).get("vehicle_name") or "")
        base = f"governed high-risk tool call: {tool}"
        if tool == "formation_command":
            action = str((params or {}).get("action") or "")
            ids = str((params or {}).get("vehicle_ids") or "")
            detail = f"action={action}"
            if ids:
                detail += f", vehicles={ids}"
            return f"{base} ({detail})"
        if not vehicle:
            return base
        return f"{base} (vehicle={vehicle})"

    def _await_tool_approval(
        self,
        run: RunState,
        tool: str,
        params: dict[str, Any],
        risk_level: str,
        reason: str = "",
    ) -> bool:
        """Block until the operator approves/rejects one governed tool call.

        Returns True if approved, False if rejected or timed out. Updates
        ``run.status`` / ``run.failure_reason`` accordingly.
        """
        req = ToolApprovalRequest(
            run_id=run.run_id,
            command=run.command,
            tool=tool,
            params=dict(params),
            risk_level=risk_level,
            reason=reason or f"high-risk tool: {tool}",
        )
        with self._lock:
            self._pending_approvals[run.run_id] = req
            run.status = "awaiting_approval"
            run.phase = "awaiting_approval"
        self._append_event(
            "warning",
            "safety",
            f"等待操作员确认: {tool}",
            {
                "run_id": run.run_id,
                "approval": req.to_dict(),
                "message": "真机环境高风险操作，需操作员审批后方可执行",
            },
        )
        self._publish_run_update(run)
        self._publish("approval_required", {"approval": req.to_dict()})

        # Block until decision or timeout. Poll every 1s so emergency_stop can interrupt.
        deadline = req.created_at + req.timeout_seconds
        while True:
            if self.supervisor.is_emergency_stopped():
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = "emergency stop during approval"
                    run.finished_at = time.time()
                self._append_event("danger", "safety", "审批期间触发急停，任务取消", {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            remaining = deadline - time.time()
            if remaining <= 0:
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = "approval timeout"
                    run.finished_at = time.time()
                self._append_event("warning", "safety", "审批超时，任务取消", {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            if req.event.wait(timeout=1.0):
                break

        approved = bool(req.approved)
        with self._lock:
            if approved:
                run.status = "running"
                run.phase = "executing"
                self._append_event("info", "safety", f"操作员已确认，开始执行: {tool}", {"run_id": run.run_id})
            else:
                run.status = "cancelled"
                run.phase = "cancelled"
                run.failure_reason = "operator rejected"
                run.finished_at = time.time()
                self._append_event("warning", "safety", "操作员拒绝，任务取消", {"run_id": run.run_id})
        self._cleanup_approval(run.run_id)
        self._publish_run_update(run)
        return approved

    def _cleanup_approval(self, run_id: str) -> None:
        with self._lock:
            self._pending_approvals.pop(run_id, None)

    def approve_run(self, run_id: str) -> dict[str, Any]:
        """Operator approves a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = True
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "approved"}

    def reject_run(self, run_id: str) -> dict[str, Any]:
        """Operator rejects a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = False
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "rejected"}

    # ── Replay 录制 ──

    @staticmethod
    def _plan_has_observation_dependency(plan: MissionPlan | None) -> bool:
        """A fixed sequence fails when an observation step precedes a motion
        step: the later move depends on what the observation shows (photo ->
        decide -> move), so it must run in the ReAct loop instead."""
        steps = list(plan.steps) if plan else []
        for index, step in enumerate(steps):
            if step.tool in OBSERVATION_TOOLS:
                if any(s.tool in MOTION_TOOLS for s in steps[index + 1 :]):
                    return True
        return False

    @staticmethod
    def _plan_requires_agent_loop(plan: MissionPlan | None) -> bool:
        """Choose Plan-Execute vs ReAct. The planner may declare agent_loop
        explicitly (visual search, tracking, conditional tasks); otherwise a
        fixed sequence with observation -> motion steps is detected
        structurally — no natural-language classification involved."""
        if plan is None:
            return False
        return plan.execution_mode == "agent_loop" or RuntimePlannerMixin._plan_has_observation_dependency(plan)

    def _correction_command(self, run: RunState) -> str:
        """Structured failure context for the ReAct correction loop: the LLM
        needs the failed step, tool output, verification summary, and current
        position to choose a meaningful corrective action."""
        parts = [f"继续完成原始任务并修正失败步骤。原始任务：{run.command}"]
        if run.failure_reason:
            parts.append(f"失败原因：{run.failure_reason}")
        verification = run.verification or {}
        if verification.get("summary"):
            parts.append(f"校验摘要：{verification.get('summary')}")
        failed_step = next(
            (s for s in (run.plan.steps if run.plan else []) if s.status == "failed"),
            None,
        )
        if failed_step is not None:
            detail = failed_step.result if isinstance(failed_step.result, dict) else {}
            message = str(detail.get("message") or detail.get("error") or "")
            parts.append(f"失败步骤：{failed_step.id} {failed_step.tool}{'：' + message if message else ''}")
        final = run.final_telemetry or {}
        position = final.get("position_ned") if isinstance(final, dict) else None
        if isinstance(position, dict) and any(position.get(k) is not None for k in ("x", "y", "z")):
            parts.append(
                f"当前 NED 位置：N {position.get('x')} / E {position.get('y')} / D {position.get('z')}"
            )
        return "；".join(parts)

    @staticmethod
    def _agent_loop_primary_command(run: RunState) -> str:
        """Command for a plan routed to ReAct before execution: the fixed
        sequence cannot express the task, so the loop decides per step."""
        return (
            f"按已生成的计划逐步执行。原始任务：{run.command}\n"
            "计划依赖中间观察结果（拍照/识别/确认后决策），请逐步执行："
            "每次先观察最新状态和工具返回，再选择下一步工具，直到任务完成。"
        )

    @staticmethod
    def _agent_state_with_skill_guidance(
        agent_state: dict[str, Any],
        skill_guidance: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not skill_guidance:
            return agent_state
        enriched = dict(agent_state or {})
        enriched["skill_guidance"] = [
            {
                "name": card.get("name", ""),
                "display_name": card.get("display_name", ""),
                "description": card.get("description", ""),
                "when_to_use": card.get("when_to_use", ""),
                "required_capabilities": list(card.get("required_capabilities") or []),
                "subtools": list(card.get("subtools") or []),
                "markdown": card.get("markdown", ""),
                "executable": False,
            }
            for card in skill_guidance[:3]
        ]
        return enriched


    def _execute_plan_execute_route(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str,
        route: dict[str, Any],
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skill_guidance: list[dict[str, Any]] | None = None,
    ) -> None:
        run_id = run_id or f"run_{int(time.time() * 1000)}"
        skill_guidance = skill_guidance or self.skills.guidance_cards(command, capabilities, memory_snapshot)
        agent_state = self._agent_state_with_skill_guidance(
            agent_state or self._agent_state_context(tool_runtime),
            skill_guidance,
        )
        reasoning_sink = self._plan_reasoning_sink(run_id, command)
        plan = self._try_llm_plan(
            command=command,
            telemetry=telemetry,
            model_id=model_id,
            run_id=run_id,
            capabilities=capabilities,
            tool_runtime=tool_runtime,
            memory_snapshot=memory_snapshot,
            agent_state={**agent_state, "planner_mode": "plan_execute"},
            attachments=attachments or [],
            reasoning_sink=reasoning_sink,
        )
        final_flush = getattr(reasoning_sink, "final_flush", None)
        if callable(final_flush):
            final_flush()
        reasoning_full = str(getattr(reasoning_sink, "full_text", "") or "").strip()
        if plan is None:
            if execute:
                # LLM 失效时的安全原则：不自动退化为规则规划继续飞行。
                # 规则规划覆盖不了模型级任务理解，自动执行会把 LLM 失效的
                # 影响面扩大到真实飞控；改为失败 + 安全悬停（由
                # _plan_and_execute 的异常路径执行 _attempt_failure_hover）。
                self._append_event(
                    "danger",
                    "planner",
                    "LLM 规划不可用，已停止执行以保护无人机",
                    {"command": command, "phase": "planning"},
                )
                raise LLMUnavailableError("LLM 规划不可用，已停止执行以保护无人机。请检查模型配置后重试。")
            plan = self.rule_planner.plan(command, capabilities=capabilities)
            plan.run_id = run_id
            plan.planner_source = "rules_plan_execute_fallback"
            plan.assumptions.append("仅规划预览：LLM 不可用，使用本地规则规划器生成只读预览。")
        else:
            plan.assumptions.append("采用 Plan-Execute：LLM 一次性规划，runtime 串行执行并校验；失败时进入 Agent Loop 纠错。")

        run = RunState(
            run_id=run_id,
            command=command,
            intent=plan.intent,
            summary=plan.summary,
            status="queued" if execute else "planned",
            mode="execute" if execute else "plan",
            phase="planning",
            execute=execute,
            model_id=model_id,
            plan=plan,
            task_level=route["level"],
            route_strategy=route["strategy"],
            route_reason=route["reason"],
            risk_level=route["risk_level"],
            answer_with_llm=False,
            start_telemetry=dict(telemetry or {}),
            agent_state=agent_state,
        )
        with self._lock:
            self._current = run
            self._pending_run_ids.discard(run_id)
        self._start_task_run(run)
        self._append_event(
            "info",
            "planner",
            "Plan-Execute route selected",
            {"run_id": run.run_id, "execute": execute, "planner_source": plan.planner_source, **route},
        )
        # 思考块内容组合（保证展开必有内容）：
        #   1. LLM 规划理由 plan.reasoning（中文）；模型省略时用任务理解兜底
        #   2. 执行计划概览（步骤序列）
        #   3. 思考词元流（reasoning_content，模型开启思考时才有）
        reasoning_parts: list[str] = []
        plan_reasoning = str(getattr(plan, "reasoning", "") or "").strip()
        if not plan_reasoning:
            plan_reasoning = f"任务理解：{plan.summary}"
        # 模型有时把 reasoning 字段写成整个 plan JSON 草稿——解析取其中的
        # reasoning/summary 文本，避免思考块里出现一大段 JSON
        if plan_reasoning.lstrip().startswith("{"):
            extracted = ""
            try:
                parsed_reasoning = json.loads(plan_reasoning)
                if isinstance(parsed_reasoning, dict):
                    extracted = str(parsed_reasoning.get("reasoning") or parsed_reasoning.get("summary") or "").strip()
            except Exception:
                pass
            if not extracted:
                # 截断/不合法的 JSON 草稿：正则直接抠 "reasoning" 字段值
                m = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', plan_reasoning)
                if m:
                    extracted = (
                        m.group(1)
                        .replace("\\n", "\n")
                        .replace("\\t", " ")
                        .replace('\\"', '"')
                    )
            if extracted:
                plan_reasoning = extracted
        reasoning_parts.append(plan_reasoning)
        step_tools = [step.tool for step in (plan.steps or []) if step.tool and step.tool != "memory_store"]
        if step_tools:
            reasoning_parts.append("执行计划（" + str(len(step_tools)) + " 步）：" + " → ".join(step_tools))
        if reasoning_full:
            # 思考流末尾常带模型起草的计划 JSON 草稿，截掉只留自然语言推理
            reasoning_parts.append(self._strip_plan_json_draft(reasoning_full))
        reasoning_full = "\n\n".join(reasoning_parts)
        if reasoning_full:
            # 规划推理全文归档到 run 上（此前写在消息 details 里会被
            # _begin_execution_trace 的 process_trace 覆盖而丢失）：
            # thought_trace 存全文供回看，process_trace 首条进时间线折叠块；
            # 并同步进消息 details.reasoning_text（前端思考块数据源）
            self._append_thought(run, "模型思考", reasoning_full)
            run.process_trace.insert(
                0,
                {
                    "timestamp": time.time(),
                    "title": "模型思考",
                    "body": reasoning_full[:8000],
                    "status": "completed",
                    "kind": "reasoning",
                },
            )
            self._update_assistant_message(
                run.run_id,
                "正在执行计划...",
                "running",
                {"mode": "execute", "phase": "planning", "reasoning_text": reasoning_full[:8000]},
                persist=False,
            )
        if execute:
            self._begin_execution_trace(run, "任务适合一次性规划执行：先生成完整工具序列，再由 runtime 逐步执行、回读和校验。")
            # skill guidance is injected into the planner prompt as background
            # knowledge — it is NOT a tool call, so it must not be displayed
            # as if a skill had been invoked
            if self._plan_requires_agent_loop(run.plan):
                # The plan depends on mid-execution observations (photo ->
                # decide -> move) or the planner declared agent_loop: a fixed
                # sequence would fail, so ReAct runs as the primary path.
                run.route_strategy = "agent_loop"
                self._append_event(
                    "info",
                    "planner",
                    "计划依赖中间观察，转入 Agent Loop 逐步执行",
                    {"run_id": run.run_id, "execution_mode": run.plan.execution_mode if run.plan else "auto"},
                )
                self._run_correction_loop(
                    run,
                    capabilities=capabilities,
                    tool_runtime=tool_runtime,
                    model_id=model_id,
                    attachments=attachments or [],
                    label="Agent Loop",
                    command_override=self._agent_loop_primary_command(run),
                )
            else:
                self._run_plan(run, finalize=False, remember=False)
                while self._should_enter_correction_loop(run):
                    run.correction_attempts += 1
                    self._append_event(
                        "warning",
                        "planner",
                        f"计划执行失败，进入 Agent Loop 纠错（{run.correction_attempts}/{CORRECTION_ATTEMPTS_MAX}）",
                        {"run_id": run.run_id, "failure_reason": run.failure_reason},
                    )
                    self._run_correction_loop(
                        run,
                        capabilities=capabilities,
                        tool_runtime=tool_runtime,
                        model_id=model_id,
                        attachments=attachments or [],
                    )
                total = len(run.plan.steps if run.plan else [])
                ok_count = sum(1 for step in (run.plan.steps if run.plan else []) if step.status == "completed")
                self._remember_plan_run(run, total=max(1, total), ok_count=ok_count)
            self._finalize_assistant_response(run)
        else:
            self._simulate_plan(run)
            self._finalize_assistant_response(run)

    def _should_enter_correction_loop(self, run: RunState) -> bool:
        if not run.execute or self._is_run_cancelled(run.run_id):
            return False
        if run.route_strategy != "plan_execute":
            return False
        if run.correction_attempts >= CORRECTION_ATTEMPTS_MAX:
            return False
        reason = (run.failure_reason or "").lower()
        if any(term in reason for term in ["operator", "approval", "emergency stop", "急停", "操作员"]):
            return False
        # Link-level failures cannot be fixed by re-deciding the plan.
        if any(term in reason for term in CONNECTION_FAILURE_TERMS):
            return False
        return run.status in {"failed", "blocked"} or run.verification.get("level") == "failed"

    def _run_correction_loop(
        self,
        run: RunState,
        *,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        model_id: str,
        attachments: list[dict[str, Any]],
        label: str = "纠错 Loop",
        command_override: str | None = None,
    ) -> None:
        self._append_process(
            run,
            label,
            "一次性计划未完全达成，进入 Agent Loop 回读当前状态并选择修正动作。"
            if label == "纠错 Loop"
            else "任务需要观察-响应循环，进入 Agent Loop 逐步执行。",
            status="running",
            kind="reasoning",
        )
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        correction_command = command_override or self._correction_command(run)
        loop = self.agent_loop.run(
            run_id=run.run_id,
            command=correction_command,
            capabilities=capabilities,
            tool_cards=tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            initial_plan=run.plan,
            model_id=model_id or None,
            max_steps=6,
            execute=True,
            attachments=attachments,
            require_llm=True,
            conversation_context=self._recent_chat_context(),
        )
        correction_plan = self._plan_from_loop_state(loop)
        if run.plan:
            offset = len(run.plan.steps)
            for index, step in enumerate(correction_plan.steps, 1):
                step.id = f"s{offset + index:02d}"
                run.plan.steps.append(step)
        else:
            run.plan = correction_plan
        run.loop_state = loop.to_dict()
        run.summary = loop.summary or run.summary
        run.status = loop.status if loop.status in {"completed", "failed", "blocked"} else "completed"
        # 目标未达成兜底：原计划中的运动步骤（起飞/移动/降落等）失败且纠错
        # 阶段没有留下同工具的成功记录时，一律不得标记 completed——LLM 有时会
        # 在目标动作仍缺失时误判"任务目标已满足"，此处用机器检查拦截。
        failed_motion = [
            step.tool for step in (run.plan.steps or [])
            if step.status == "failed" and step.tool in MOTION_TOOLS
        ]
        recovered_motion = {
            step.tool for step in (correction_plan.steps or [])
            if step.status == "completed" and step.tool in failed_motion
        }
        unresolved_motion = [tool for tool in failed_motion if tool not in recovered_motion]
        if run.status == "completed" and unresolved_motion:
            run.status = "failed"
            run.verification = {
                "level": "failed",
                "summary": (
                    f"任务目标未完全达成：以下动作在计划执行中失败，"
                    f"且纠错阶段未成功补做：{', '.join(dict.fromkeys(unresolved_motion))}"
                ),
            }
        # a recovered earlier failure must never leak into a completed run:
        # the frontend renders the error badge from failure_reason
        run.failure_reason = "" if run.status == "completed" else (run.failure_reason or loop.failure_reason)
        run.finished_at = loop.finished_at or time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.verification = self._verify_run_outcome(run)
        # Loop-level task-contract verification (machine-checked completion
        # criteria) feeds the same failed-verification gate as the plan path.
        if loop.verification_status == "failed" and run.verification.get("level") != "failed":
            run.verification = {
                "level": "failed",
                "summary": f"完成判据未满足：{loop.summary or loop.failure_reason or '任务目标未达成'}",
            }
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.failure_reason = run.verification.get("summary", "纠错后任务校验仍未通过")
        run.phase = run.status if run.status in {"completed", "failed", "blocked"} else "completed"
        self._append_process(
            run,
            label,
            loop.summary or run.failure_reason or f"{label} 已结束。",
            status="completed" if run.status == "completed" else "failed",
            kind="reasoning",
        )
        self._publish_run_update(run)

    def _plan_from_loop_state(self, loop: LoopState, planned: bool = False) -> MissionPlan:
        """Rebuild a plan from the loop's audit trail.

        Decisions and results are paired by tool name with consumption order,
        so corrective decisions and batch results are never lost from the
        rebuilt plan; leftover results (e.g. batch extras) become their own
        steps at the end.
        """
        steps: list[MissionStep] = []
        consumed: set[int] = set()

        def status_for(result: Any) -> str:
            if result is None:
                return "pending"
            return "planned" if planned and result.ok else ("completed" if result.ok else "failed")

        for decision in loop.decisions:
            if decision.is_complete or not decision.action:
                continue
            result = None
            for ridx, row in enumerate(loop.results):
                if ridx in consumed:
                    continue
                if row.tool == decision.action:
                    result = row
                    consumed.add(ridx)
                    break
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=decision.reason or decision.action,
                    tool=decision.action,
                    params=dict(decision.params or {}),
                    layer="agent_loop",
                    status=status_for(result),
                    result=result.data if result else None,
                )
            )
        for ridx, row in enumerate(loop.results):
            if ridx in consumed:
                continue
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=row.tool,
                    tool=row.tool,
                    params=dict(row.params or {}),
                    layer="agent_loop",
                    status="completed" if row.ok else "failed",
                    result=row.data,
                )
            )
        return MissionPlan(
            run_id=loop.run_id,
            command=loop.command,
            intent="agent_loop",
            summary=loop.summary or "Agent Loop task",
            steps=steps,
            planner_source="agent_loop",
            reasoning="Loop decisions are stored in loop_state.decisions.",
            risk_notes=[loop.failure_reason] if loop.failure_reason else [],
        )

    def _planner_tool_cards(
        self,
        command: str,
        atomic_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
        memory_snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a small LLM-facing action surface.

        The model should reason over skills first. Atomic tools remain visible
        only when they are safe/read-only, needed for visual grounding, or the
        active backend has no suitable skill for the requested capability.
        """
        skill_names: set[str] = set()
        atomic_by_name = {
            str(card.get("name")): card
            for card in atomic_cards
            if isinstance(card, dict) and card.get("name")
        }
        allowed_atomic = self._allowed_planner_atomic_tools(command, skill_names, capabilities)
        cards: list[dict[str, Any]] = []
        for name in sorted(allowed_atomic):
            card = atomic_by_name.get(name)
            if card:
                cards.append(card)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            name = str(card.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(card)
        # Agent-level cards (memory/subtask) always keep a slot.
        agent_names = {"memory_recall", "memory_remember", "agent_subtask"}
        agent_cards = [card for card in deduped if card.get("name") in agent_names]
        regular = [card for card in deduped if card.get("name") not in agent_names]
        return (regular[: max(0, 18 - len(agent_cards))] + agent_cards)[:18]

    def _allowed_planner_atomic_tools(
        self,
        command: str,
        skill_names: set[str],
        capabilities: dict[str, Any],
    ) -> set[str]:
        text = (command or "").lower()
        allowed = {"drone_connect", "drone_get_status"}
        visual_terms = (
            "camera", "image", "photo", "see", "look", "detect", "search", "find", "target",
            "摄像头", "画面", "图像", "图片", "照片", "拍照", "看到", "看一下", "看看", "识别", "检测", "搜索", "寻找", "目标",
        )
        mission_terms = ("mission", "waypoint", "航点", "航线", "任务", "上传", "下载", "进度", "清空", "启动")
        landing_terms = ("land", "rtl", "return", "降落", "返航", "返回")
        hover_terms = ("hover", "hold", "pause", "悬停", "保持", "暂停")
        path_terms = ("path", "route", "orbit", "circle", "scan", "patrol", "绕圈", "转圈", "盘旋", "扫描", "巡航", "巡检", "半径")

        if any(term in text for term in visual_terms):
            allowed.update({
                "airsim_take_photo",
                "airsim_vlm_analyze_image",
                "airsim_vlm_confirm_target",
                "airsim_get_depth_map",
                "airsim_task_status",
                "airsim_task_cancel",
            })
            if "skill:visual_observe" not in skill_names:
                allowed.update({"airsim_take_photo", "airsim_vlm_analyze_image", "airsim_vlm_confirm_target"})

        if any(term in text for term in mission_terms):
            allowed.update({
                "drone_download_mission",
                "drone_get_mission_progress",
                "drone_upload_mission",
                "drone_start_mission",
                "drone_clear_mission",
            })

        if any(term in text for term in landing_terms):
            allowed.add("drone_land")
        if any(term in text for term in hover_terms):
            allowed.add("drone_hover")
        if any(term in text for term in path_terms):
            allowed.add("drone_fly_path")
        formation_terms = (
            "formation", "swarm", "编队", "队形", "coverage", "覆盖", "区域扫描", "网格扫描", "分区扫描",
        )
        if any(term in text for term in formation_terms):
            allowed.add("formation_command")

        if "skill:navigation" not in skill_names:
            allowed.update({"drone_arm", "drone_takeoff", "drone_fly_to", "drone_move_relative", "drone_hover"})
        if "skill:return_home" not in skill_names and capabilities.get("flight_control"):
            allowed.update({"drone_fly_to", "drone_land"})
        return allowed

    def _on_agent_loop_state(self, loop: LoopState) -> None:
        with self._lock:
            run = self._current
            if not run or run.run_id != loop.run_id:
                return
            if run.status == "cancelled":
                self._publish_run_update(run)
                return
            previous_decision_count = len((run.loop_state or {}).get("decisions") or [])
            previous_result_count = len((run.loop_state or {}).get("results") or [])
            previous_observation_count = len((run.loop_state or {}).get("observations") or [])
            run.loop_state = loop.to_dict()
            decision_count = len(loop.decisions)
            result_count = len(loop.results)
            observation_count = len(loop.observations)
            run.current_step = f"loop-{decision_count}" if decision_count else "observe"
            run.progress = min(95.0, decision_count / max(1, loop.max_steps) * 100.0)
            if run.execute and run.status not in {"paused", "awaiting_approval", "cancelled", "blocked"}:
                run.status = "running"
                run.phase = "executing"
            if observation_count > previous_observation_count and decision_count == previous_decision_count:
                self._append_process(
                    run,
                    "模型决策",
                    "正在根据最新遥测、工具结果和任务目标选择下一步动作。",
                    status="running",
                    kind="reasoning",
                )
            if decision_count > previous_decision_count:
                decision = loop.decisions[-1]
                decision_text = self._loop_decision_public_text(decision)
                run.thought_trace.append({
                    "timestamp": time.time(),
                    "title": f"循环决策 {decision_count}",
                    "body": decision_text or decision.action or "检查任务是否完成",
                    "status": "completed" if decision.is_complete else "running",
                })
                run.thought_trace = run.thought_trace[-30:]
                if decision.is_complete:
                    self._append_process(
                        run,
                        "模型决策",
                        "任务目标已满足，正在整理最终报告。",
                        status="completed",
                        kind="reasoning",
                    )
                if decision_text:
                    self._append_process(
                        run,
                        "模型总结" if decision.is_complete else "模型决策",
                        decision_text,
                        status="completed",
                        kind="reasoning",
                    )
                if decision.action:
                    self._append_process(
                        run,
                        decision.action,
                        self._format_tool_call_body(decision.params),
                        status="running",
                        tool=decision.action,
                        params=decision.params,
                        kind="tool",
                    )
            if result_count > previous_result_count and loop.results:
                result = loop.results[-1]
                self._append_process(
                    run,
                    result.tool,
                    self._format_loop_result_body(result.data),
                    status="completed" if result.ok else "failed",
                    tool=result.tool,
                    params=result.params,
                    kind="tool",
                )
        self._publish_run_update(run)
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))

    @staticmethod
    def _loop_decision_public_text(decision: Any) -> str:
        parts: list[str] = []
        reason = str(getattr(decision, "reason", "") or "").strip()
        reflection = str(getattr(decision, "reflection", "") or "").strip()
        if reason:
            parts.append(reason)
        if reflection and reflection != reason:
            parts.append(reflection)
        return "\n".join(parts).strip()

    @staticmethod
    def _format_tool_call_body(params: dict[str, Any] | None) -> str:
        if not params:
            return "准备调用工具。"
        try:
            payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            payload = str(params)
        return f"参数 {payload}"

    @staticmethod
    def _format_loop_result_body(data: dict[str, Any] | None) -> str:
        if not isinstance(data, dict):
            return ""
        message = str(data.get("message") or data.get("summary_zh") or data.get("status") or "").strip()
        tool_results = data.get("tool_results")
        if isinstance(tool_results, list) and tool_results:
            parts: list[str] = []
            for item in tool_results[:12]:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool") or item.get("name") or "tool")
                ok = "ok" if item.get("ok") is True else ("failed" if item.get("ok") is False else "")
                nested = item.get("data") if isinstance(item.get("data"), dict) else {}
                detail = str(nested.get("message") or nested.get("summary_zh") or nested.get("status") or "").strip()
                label = f"{tool} {ok}".strip()
                parts.append(f"{label}: {detail}" if detail else label)
            summary = " → ".join(parts)
            if len(tool_results) > 12:
                summary += f" → +{len(tool_results) - 12} more"
            return f"{message}\n{summary}".strip() if message else summary
        return message

    def _on_agent_event(self, level: str, source: str, message: str, data: dict[str, Any]) -> None:
        self._append_event(level, source, message, data)
        # ReAct 每步决策的推理（reasoning_content）追加进当前消息的
        # reasoning_text——前端思考块一个折叠块看全程思考
        if source == "model_reasoning" and self._current is not None:
            run = self._current
            with self._lock:
                if not isinstance(run.agent_state, dict):
                    run.agent_state = {}
                prev = str(run.agent_state.get("_reasoning_text") or "")
                run.agent_state["_reasoning_text"] = (prev + "\n" + message).strip()[:12000]
                # JSON 草稿跨多个事件分块到达，累积原文、组装时统一截断
                full = self._strip_plan_json_draft(run.agent_state["_reasoning_text"])
                target_message = next(
                    (m for m in reversed(self._messages) if m.run_id == run.run_id and m.role == "assistant"),
                    None,
                )
            if target_message is not None:
                det = target_message.details or {}
                self._update_assistant_message(
                    run.run_id,
                    target_message.content or "",
                    "running" if target_message.status == "running" else target_message.status,
                    {"mode": det.get("mode", "execute"), "phase": det.get("phase", "executing"),
                     "reasoning_text": full},
                    persist=False,
                )
        with self._lock:
            run_log = self._run_log
        if run_log is not None:
            kind = str(data.get("kind") or "")
            if kind == "loop.decision":
                run_log.write("loop.decision", data)
            elif kind == "tool.result":
                run_log.write("tool.result", data)
            elif kind == "observation":
                run_log.write("observation", data)
            elif kind == "replan":
                run_log.write("replan", data)
            elif kind == "verification":
                run_log.write("verification", data)
            elif kind == "async.poll":
                run_log.write(
                    "async.poll",
                    {
                        "task_id": str(data.get("task_id") or ""),
                        "status": str(data.get("status") or ""),
                    },
                )
        if source != "async_task":
            return
        with self._lock:
            run = self._current
            if not run:
                return
            run.agent_state = dict(run.agent_state or {})
            run.agent_state["active_operation"] = {
                "message": message,
                "task_id": str(data.get("task_id") or (data.get("data") or {}).get("task_id") or ""),
                "status": str((data.get("data") or {}).get("status") or data.get("status") or "running"),
                "updated_at": time.time(),
            }
        self._publish_run_update(run)

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _ned_value(self, position: dict[str, Any] | None, key: str, default: float | None = None) -> float | None:
        if not isinstance(position, dict):
            return default
        value = self._finite_float(position.get(key))
        return value if value is not None else default

    def _active_run_is_interruptible(self) -> bool:
        with self._lock:
            return bool(
                self._current
                and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}
            )

    def _attempt_failure_hover(self, run: RunState | None, reason: str) -> dict[str, Any] | None:
        try:
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if not capabilities.get("flight_control"):
                return None
            drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
            position = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            z = self._finite_float(position.get("z")) or 0.0
            min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
            active_airframe = bool(drone.get("flying") or drone.get("armed") or abs(z) >= min_altitude)
            if not active_airframe:
                return None
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            payload = result.to_dict()
            self._append_event(
                "warning" if result.ok else "danger",
                "tool",
                "任务异常后安全悬停",
                {"reason": reason, "hover": payload},
            )
            if run is not None:
                self._append_process(
                    run,
                    "异常安全悬停",
                    "Agent 决策中断，已发送悬停保位指令。"
                    if result.ok
                    else f"Agent 决策中断，悬停保位失败：{result.data.get('message', '')}",
                    status="completed" if result.ok else "failed",
                    tool="drone_hover",
                    params={},
                    kind="tool",
                )
            return payload
        except Exception as exc:
            self._append_event("warning", "tool", "任务异常后安全悬停失败", {"reason": reason, "error": str(exc)})
            return {"ok": False, "error": str(exc)}
