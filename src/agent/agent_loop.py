"""Lightweight observe-decide-act loop for advanced UAV tasks."""

from __future__ import annotations

import time
from typing import Any, Callable

from .command_slots import MOTION_TERMS, extract_intents, extract_target_class
from .llm import LLMMissionPlanner
from .loop_types import LoopActionResult, LoopDecision, LoopObservation, LoopState
from .memory import AgentMemory
from .planner import MissionPlan
from .skill_registry import SkillRegistry
from .tool_executor import ToolCallResult, ToolRuntime


LoopEventCallback = Callable[[str, str, str, dict[str, Any]], None]
LoopStopCheck = Callable[[], bool]
LoopPauseCheck = Callable[[], bool]
AgentToolExecutor = Callable[[str, dict[str, Any], bool], ToolCallResult]
LoopStateCallback = Callable[[LoopState], None]

# 计划中"可验证目标达成"的运动类工具：LLM 声明任务完成前，这些动作
# 必须在该循环内留下一次成功执行记录（或被状态回读证明目标已达成）。
_PLANNED_MOTION_TOOLS = {
    "drone_takeoff",
    "drone_hover",
    "drone_move_relative",
    "drone_fly_to",
    "drone_fly_path",
    "drone_land",
    "drone_rotate_to",
}


class AgentLoop:
    """Small ReAct-style loop that stays close to the existing tool runtime."""

    def __init__(
        self,
        tools: ToolRuntime,
        planner: LLMMissionPlanner,
        memory: AgentMemory,
        on_event: LoopEventCallback | None = None,
        should_stop: LoopStopCheck | None = None,
        should_pause: LoopPauseCheck | None = None,
        skills: SkillRegistry | None = None,
        execute_tool: AgentToolExecutor | None = None,
        on_state: LoopStateCallback | None = None,
        async_timeout: float = 120.0,
        async_poll_interval: float = 1.0,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.skills = skills or SkillRegistry()
        self.on_event = on_event
        self.should_stop = should_stop
        self.should_pause = should_pause
        self.execute_tool = execute_tool
        self.on_state = on_state
        self.async_timeout = max(1.0, float(async_timeout))
        self.async_poll_interval = max(0.05, float(async_poll_interval))

    def run(
        self,
        run_id: str,
        command: str,
        capabilities: dict[str, Any],
        tool_cards: list[dict[str, Any]],
        initial_plan: MissionPlan | None = None,
        model_id: str | None = None,
        max_steps: int = 10,
        execute: bool = True,
        attachments: list[dict[str, Any]] | None = None,
        require_llm: bool = False,
        conversation_context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        fallback_enabled: bool = True,
    ) -> LoopState:
        state = LoopState(
            run_id=run_id,
            command=command,
            status="running",
            original_plan=initial_plan.to_dict() if initial_plan else None,
            max_steps=max_steps,
        )
        memory_snapshot = self.memory.snapshot()
        guidance_loader = getattr(self.skills, "guidance_cards", None)
        skill_guidance = guidance_loader(command, capabilities, memory_snapshot) if callable(guidance_loader) else []
        decision_cards = self._decision_cards(command, [], tool_cards, capabilities)
        allowed_tools = self._allowed_tools(decision_cards)
        last_result: dict[str, Any] | None = None
        failure_count = 0
        unresolved_failure = False
        replan_count = 0
        verify_corrected = False

        for step_index in range(1, max_steps + 1):
            if self._should_stop():
                state.status = "blocked"
                state.failure_reason = "supervisor emergency stop"
                break
            self._wait_if_paused()

            observation = LoopObservation(
                step_index=step_index,
                world_state=self.tools.status_snapshot(),
                last_action_result=last_result,
                elapsed_since_start=time.time() - state.started_at,
                frame_age_s=self._latest_image_age(state),
            )
            state.observations.append(observation)
            self._notify_state(state)
            self._event("info", "agent_loop", f"Observation {step_index}", observation.to_dict(), kind="observation")

            decision = None if require_llm else self._preemptive_guard_decision(command, state, observation, allowed_tools, capabilities)
            if decision is None:
                decision = self.planner.decide_next_step(
                    command=command,
                    loop_state=state.to_dict(),
                    observation=observation.to_dict(),
                    tool_cards=decision_cards,
                    capabilities=capabilities,
                    memory=memory_snapshot,
                    model_id=model_id,
                    attachments=attachments or [],
                    require_llm=require_llm,
                    skill_guidance=skill_guidance,
                    tools=self._tool_specs(),
                    system_prompt=system_prompt,
                    fallback_enabled=fallback_enabled,
                    conversation_context=conversation_context,
                )
                decision = self._guard_decision(command, state, observation, decision, allowed_tools, capabilities)
            decision = self._sanitize_decision(decision, allowed_tools)
            state.decisions.append(decision)
            self._notify_state(state)
            reasoning_text = str(getattr(self.planner, "last_reasoning", "") or "").strip()
            # 决策理由（decision.reason/reflection，中文）也是"LLM 的思考"——
            # 与 reasoning_content 一起进思考块，保证展开必有内容
            decision_rationale = str(decision.reason or decision.reflection or "").strip()
            combined_reasoning = "\n".join(part for part in (reasoning_text, decision_rationale) if part)
            if combined_reasoning:
                self._event("info", "model_reasoning", combined_reasoning[:1500], {"step": step_index})
            self._event("info", "agent_loop", f"Loop decision {step_index}: {decision.action or 'complete'}", decision.to_dict(), kind="loop.decision")

            if decision.is_complete:
                goal = self._run_goal(initial_plan)
                verification = self._verify_completion(goal, state, observation)
                if verification["criteria"]:
                    if not verification["satisfied"] and not verify_corrected:
                        corrective = self._corrective_decision(verification, state, allowed_tools)
                        if corrective is not None:
                            verify_corrected = True
                            corrective = self._sanitize_decision(corrective, allowed_tools)
                            self._event(
                                "warning",
                                "verification",
                                f"完成判据未满足，先执行纠正动作: {corrective.action or '状态回读'}",
                                verification,
                                kind="verification",
                            )
                            decision = corrective
                            # record the corrective decision in the audit trail
                            state.decisions.append(corrective)
                            self._notify_state(state)
                        else:
                            state.verification_status = "failed"
                            self._event("warning", "verification", "完成判据无法满足且无纠正路径", verification, kind="verification")
                    elif not verification["satisfied"]:
                        state.verification_status = "failed"
                        self._event("warning", "verification", "完成判据仍未满足（已执行一次纠正）", verification, kind="verification")
                    else:
                        state.verification_status = "ok"
            if decision.is_complete:
                if unresolved_failure:
                    state.status = "failed"
                    state.failure_reason = decision.reflection or decision.reason or "task stopped without recovering from the previous failure"
                else:
                    state.status = "completed"
                    # a recovered earlier failure must never leak into the
                    # final state: "completed" carries no failure reason
                    state.failure_reason = ""
                    state.summary = decision.reason or decision.reflection or "agent loop completed"
                break
            if not decision.action:
                if decision.needs_replan and replan_count < 2:
                    replan_count += 1
                    last_result = {
                        "ok": False,
                        "data": {"status": "replan_requested", "message": decision.reflection or decision.reason},
                    }
                    self._event("warning", "agent_loop", f"Replan requested ({replan_count}/2)", decision.to_dict(), kind="replan")
                    continue
                state.status = "failed"
                state.failure_reason = decision.reflection or decision.reason or "agent loop produced no executable action"
                break

            result = self._execute_action(decision, dry_run=not execute)
            result = self._settle_async_result(result, dry_run=not execute)
            result_row = LoopActionResult(
                step_index=step_index,
                tool=result["tool"],
                params=result["params"],
                ok=bool(result["ok"]),
                data=result["data"],
                safety=result.get("safety"),
                duration_ms=result["duration_ms"],
            )
            state.results.append(result_row)
            self._notify_state(state)
            self.memory.remember_tool_call(result_row.tool, result_row.ok)
            last_result = result_row.to_dict()
            self._event("info" if result_row.ok else "warning", "tool", f"Loop action {result_row.tool}", result["raw"], kind="tool.result")

            if not result_row.ok:
                failure_count += 1
                unresolved_failure = True
                state.failure_reason = str(result_row.data.get("message") or f"{result_row.tool} failed")
                # 连接熔断：后端断连/超时后继续决策只会烧 token（重连、拍照、
                # 再重连的无限循环）。连续两次连接类失败直接终止任务，让操作
                # 员先恢复链路。
                error_code = str(result_row.data.get("error_code") or "")
                message_l = state.failure_reason.lower()
                connection_failure = error_code in {"NOT_CONNECTED", "CONNECTION", "TIMEOUT", "LINK_STALE"} or any(
                    term in message_l for term in ("not connected", "connection", "timed out", "timeout", "未连接", "连接")
                )
                if connection_failure:
                    # 一次连接类失败即熔断：后端断连后继续决策只会空转烧
                    # token（重连、拍图、再重连的循环），立即终止并提醒
                    # 操作员检查 AirSim/飞控。
                    state.status = "failed"
                    state.failure_reason = (
                        "检测到后端连接断开，任务已终止。"
                        "AirSim/飞控服务可能已断开，请检查服务后在连接面板重新连接，再重新下发任务。"
                    )
                    self._event(
                        "danger",
                        "agent_loop",
                        "连接断开，任务已终止（请检查 AirSim/飞控服务）",
                        {"tool": result_row.tool, "message": state.failure_reason[:160]},
                    )
                    break
                if failure_count >= 3:
                    state.status = "failed"
                    break
                self._event(
                    "warning",
                    "agent_loop",
                    f"Action failed; observation/recovery turn allowed ({failure_count}/3)",
                    result_row.to_dict(),
                    kind="tool.result",
                )
                continue
            if result_row.tool not in {"drone_get_status", "airsim_task_status"}:
                unresolved_failure = False

            if decision.parallel_actions and state.status != "failed":
                batch_state = self._execute_batch_actions(
                    decision.parallel_actions,
                    command,
                    state,
                    observation,
                    allowed_tools,
                    capabilities,
                    step_index,
                    execute,
                    failure_count,
                    unresolved_failure,
                )
                failure_count = batch_state["failure_count"]
                unresolved_failure = batch_state["unresolved_failure"]
                if state.status == "failed":
                    break
        else:
            state.status = "blocked"
            state.failure_reason = f"agent loop reached max_steps={max_steps}"
            if state.results:
                state.summary = self._step_limit_summary(state, max_steps)
                # smolagents provide_final_answer pattern: let the model review
                # the attempt and produce an operator-facing report instead of
                # a template line. Only when the LLM is already in use.
                if require_llm:
                    model_summary = self._llm_attempt_summary(state, model_id)
                    if model_summary:
                        state.summary = model_summary

        if state.status == "created" or state.status == "running":
            state.status = "completed"
        state.finished_at = time.time()
        if not state.summary:
            state.summary = self._summary(state)
        self._notify_state(state)
        return state

    def _llm_attempt_summary(self, state: LoopState, model_id: str | None) -> str:
        """Model-generated final report for a step-budget-exhausted loop.

        Returns "" on any failure so the caller keeps the local template
        summary — the summary is an enhancement, never a failure path.
        """
        summarize = getattr(self.planner, "summarize_attempt", None)
        if not callable(summarize):
            return ""
        try:
            return summarize(state.command, state.to_dict(), model_id=model_id or None)
        except Exception:
            return ""

    def _sanitize_decision(self, decision: LoopDecision, allowed_tools: set[str]) -> LoopDecision:
        if decision.is_complete:
            return decision
        if not decision.action:
            return decision
        if decision.action in allowed_tools:
            decision.params = dict(decision.params or {})
            return decision
        return LoopDecision(
            action="",
            reason=f"Tool '{decision.action}' is not available for the current backend.",
            is_complete=False,
            reflection="Unavailable tools are treated as execution failures, not successful completion.",
        )

    def _guard_decision(
        self,
        command: str,
        state: LoopState,
        observation: LoopObservation,
        decision: LoopDecision,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision:
        if decision.action in self._internal_tools():
            decision = LoopDecision(
                action="",
                reason=f"{decision.action} is handled by runtime memory, not by the Agent Loop.",
                is_complete=False,
                reflection="Internal memory writes are not valid loop actions.",
            )

        if not self._is_visual_request(command):
            return decision

        has_capture_tool = bool(capabilities.get("image_capture")) or "airsim_take_photo" in allowed_tools
        if not has_capture_tool:
            if not self._wants_open_image_analysis(command):
                return decision
            return LoopDecision(
                action="",
                reason="The active backend has no image capture tool for this visual request.",
                is_complete=False,
                reflection="Cannot answer or act on camera imagery without an image source.",
            )

        has_image = self._has_recent_image(state)
        needs_guard_action = not decision.action or decision.is_complete or decision.action not in allowed_tools
        if (
            not has_image
            and "skill:visual_observe" in allowed_tools
            and needs_guard_action
        ):
            params = {"question": command, "image_type": "scene"}
            if self._wants_target_confirmation(command):
                params["target_description"] = self._target_description(command)
            return LoopDecision(
                "skill:visual_observe",
                params,
                "Use the visual observation skill to capture and analyze the current camera frame.",
            )
        if not has_image and "airsim_take_photo" in allowed_tools and needs_guard_action:
            return LoopDecision(
                "airsim_take_photo",
                {"image_type": "scene", "auto_save": False, "max_retries": 1, "timeout_sec": 8.0},
                "Capture the current camera frame before answering the visual request.",
            )

        wants_open_analysis = self._wants_open_image_analysis(command)
        wants_target = self._wants_target_confirmation(command)
        has_analysis = self._has_successful_tool(state, "airsim_vlm_analyze_image")
        has_confirm = self._has_successful_tool(state, "airsim_vlm_confirm_target")

        if has_image and wants_open_analysis and not has_analysis and "airsim_vlm_analyze_image" in allowed_tools and needs_guard_action:
            return LoopDecision(
                "airsim_vlm_analyze_image",
                {"question": command, "source": "last_image"},
                "Analyze the captured camera frame with the selected multimodal model.",
            )

        if has_image and wants_target and not has_confirm and "airsim_vlm_confirm_target" in allowed_tools and needs_guard_action:
            return LoopDecision(
                "airsim_vlm_confirm_target",
                {"target_description": self._target_description(command), "source": "last_image"},
                "Confirm the requested target in the captured frame before any movement.",
            )

        if wants_target and has_confirm and self._wants_search(command):
            search = self._search_decision_after_confirmation(command, state, allowed_tools)
            if search is not None:
                return search

        if self._wants_visual_approach(command) and has_confirm:
            approach = self._approach_decision_from_confirmation(command, state, allowed_tools)
            if approach is not None:
                return approach

        if (wants_open_analysis and has_analysis) or (wants_target and has_confirm and not self._wants_visual_approach(command)):
            if not decision.action or decision.is_complete:
                return LoopDecision(
                    action="",
                    reason="Visual analysis/confirmation has completed.",
                    is_complete=True,
                    reflection="The final answer can use the latest VLM result.",
                )
        return decision

    def _preemptive_guard_decision(
        self,
        command: str,
        state: LoopState,
        observation: LoopObservation,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision | None:
        if not self._is_visual_request(command):
            return None
        lower = command.lower()
        has_motion_goal = any(term in lower for term in MOTION_TERMS)
        visual_target_approach = self._wants_visual_approach(command) and self._wants_target_confirmation(command)
        flight_progress = any(
            self._has_successful_tool(state, tool)
            for tool in ("drone_takeoff", "drone_move_relative", "drone_fly_to", "drone_rotate_to")
        )
        if has_motion_goal and not visual_target_approach and not flight_progress:
            return None
        neutral = LoopDecision(action="", reason="", is_complete=False)
        guarded = self._guard_decision(command, state, observation, neutral, allowed_tools, capabilities)
        if guarded.action or guarded.is_complete or guarded.reflection:
            return guarded
        return None

    def _decision_cards(
        self,
        command: str,
        skill_cards: list[dict[str, Any]],
        atomic_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cards = [card for card in skill_cards if card.get("name") not in self._internal_tools()]
        for card in atomic_cards:
            if isinstance(card, dict) and card.get("name") not in self._internal_tools():
                cards.append(card)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            name = str(card.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(card)
        # Agent-level cards (memory/subtask) always keep a slot even when the
        # regular tool set overflows the decision-card budget.
        agent_names = {"memory_recall", "memory_remember", "agent_subtask"}
        agent_cards = [card for card in deduped if card.get("name") in agent_names]
        regular = [card for card in deduped if card.get("name") not in agent_names]
        return (regular[: max(0, 32 - len(agent_cards))] + agent_cards)[:32]

    def _allowed_tools(self, tool_cards: list[dict[str, Any]]) -> set[str]:
        card_names = {card.get("name") for card in tool_cards if isinstance(card, dict)}
        return {str(name) for name in card_names if name and str(name) not in self._internal_tools()}

    @staticmethod
    def _internal_tools() -> set[str]:
        return {"memory_store"}

    def _is_visual_request(self, command: str) -> bool:
        return extract_intents(command)["visual"]

    def _wants_open_image_analysis(self, command: str) -> bool:
        return extract_intents(command)["open_image_analysis"]

    def _wants_target_confirmation(self, command: str) -> bool:
        return extract_intents(command)["target_confirmation"]

    def _wants_visual_approach(self, command: str) -> bool:
        return extract_intents(command)["visual_approach"]

    def _wants_search(self, command: str) -> bool:
        return extract_intents(command)["search"]

    def _target_description(self, command: str) -> str:
        text = command.strip()
        if text:
            return text[:240]
        return "target"

    def _target_class(self, command: str) -> str:
        return extract_target_class(command) or "target"

    def _has_successful_tool(self, state: LoopState, tool: str) -> bool:
        return any(item.get("tool") == tool and bool(item.get("ok")) for item in self._iter_tool_results(state))

    def _has_recent_image(self, state: LoopState) -> bool:
        now = time.time()
        for result in reversed(state.results):
            if (
                result.ok
                and (now - result.timestamp) <= self._FRAME_MAX_AGE_S
                and self._result_contains_image(result.data)
            ):
                return True
        return False

    def _latest_image_age(self, state: LoopState) -> float | None:
        """Seconds since the newest image-bearing result (None if no image)."""
        now = time.time()
        ages = [
            now - result.timestamp
            for result in reversed(state.results)
            if result.ok and self._result_contains_image(result.data)
        ]
        return min(ages) if ages else None

    def _result_contains_image(self, value: Any, _depth: int = 0) -> bool:
        if _depth > 24:
            return False
        if isinstance(value, dict):
            if any(value.get(key) for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._result_contains_image(item, _depth + 1) for item in value.values())
        if isinstance(value, list):
            return any(self._result_contains_image(item, _depth + 1) for item in value)
        return False

    def _latest_result_data(self, state: LoopState, tool: str) -> dict[str, Any]:
        for item in self._iter_tool_results(state):
            if item.get("tool") != tool:
                continue
            data = item.get("data")
            if isinstance(data, dict):
                return data
        return {}

    def _iter_tool_results(self, state: LoopState) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for result in reversed(state.results):
            items.append({"tool": result.tool, "ok": result.ok, "data": result.data})
            items.extend(self._iter_nested_tool_results(result.data))
        return items

    def _iter_nested_tool_results(self, value: Any, _depth: int = 0) -> list[dict[str, Any]]:
        if _depth > 24:
            return []
        items: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("tool"):
                items.append(value)
            for key in ("tool_results", "results"):
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in reversed(nested):
                        items.extend(self._iter_nested_tool_results(item, _depth + 1))
            data = value.get("data")
            if isinstance(data, (dict, list)):
                items.extend(self._iter_nested_tool_results(data, _depth + 1))
        elif isinstance(value, list):
            for item in reversed(value):
                items.extend(self._iter_nested_tool_results(item, _depth + 1))
        return items

    def _approach_decision_from_confirmation(
        self,
        command: str,
        state: LoopState,
        allowed_tools: set[str],
    ) -> LoopDecision | None:
        confirmation = self._latest_result_data(state, "airsim_vlm_confirm_target")
        if not confirmation.get("target_found"):
            return LoopDecision(
                action="",
                reason="Target was not confirmed in the camera frame, so movement toward it is blocked.",
                is_complete=False,
                reflection=str(confirmation.get("message") or confirmation.get("summary_zh") or "target not confirmed"),
            )
        if "airsim_get_depth_map" in allowed_tools and not self._has_successful_tool(state, "airsim_get_depth_map"):
            return LoopDecision(
                "airsim_get_depth_map",
                {"camera_name": "0", "return_vis": False},
                "Read depth data before deciding whether target-relative movement is safe.",
            )
        return LoopDecision(
            action="",
            reason="Target is visible, but no safe 3D target position or visual approach tool is available.",
            is_complete=False,
            reflection=(
                "The Agent will not fly toward a 2D image target without depth, a projected world position, "
                "or an approved visual approach tool."
            ),
        )

    def _search_decision_after_confirmation(
        self,
        command: str,
        state: LoopState,
        allowed_tools: set[str],
    ) -> LoopDecision | None:
        confirmation = self._latest_result_data(state, "airsim_vlm_confirm_target")
        if confirmation.get("target_found"):
            return None
        target = self._target_class(command)
        if "skill:search" in allowed_tools and not self._has_successful_tool(state, "skill:search"):
            return LoopDecision(
                "skill:search",
                {"target_class": target, "search_altitude": 3.0, "search_radius": 25.0, "scene_description": command},
                "The target is not visible in the current frame; continue with the search skill.",
            )
        return None

    def _tool_specs(self) -> list[dict[str, Any]] | None:
        """Runtime tool specs (annotations/defaults) for schema synthesis."""
        try:
            return self.tools.list_tools() if hasattr(self.tools, "list_tools") else None
        except Exception:
            return None

    def _is_parallel_safe(self, name: str) -> bool:
        """Batch actions are restricted to read-only tools so a batch can never
        smuggle a flight-control call past the one-flight-tool-per-turn rule.
        airsim_task_cancel is deliberately excluded: cancelling an in-flight
        task is a control-side effect and stays single-turn."""
        read_only = getattr(self.tools, "READ_ONLY_TOOLS", set())
        if name in read_only:
            return True
        if name in {"airsim_task_status", "memory_store", "memory_recall", "memory_remember"}:
            return True
        if name == "skill:visual_observe":
            return True
        return False

    # Task-contract verification: machine-checkable completion criteria that
    # gate model-declared completion ("LLM 提议 + 确定性验证").
    _CORRECTIVE_TOOLS = {
        "status_ok": "drone_get_status",
        "landed": "drone_get_status",
        "flying_at": "drone_get_status",
        "position_reached": "drone_get_status",
        "photo_taken": "airsim_take_photo",
        "mission_progress_complete": "drone_get_mission_progress",
        "target_confirmed": "airsim_vlm_confirm_target",
        "formation_stable": "formation_command",
    }
    # Frames older than this are treated as "no recent image" by the guards.
    _FRAME_MAX_AGE_S = 60.0

    @staticmethod
    def _run_goal(initial_plan: MissionPlan | None) -> dict[str, Any]:
        if initial_plan is None:
            return {}
        plan_dict = initial_plan.to_dict() if hasattr(initial_plan, "to_dict") else {}
        goal = dict(plan_dict.get("goal") or {})
        criteria = list(goal.get("success_criteria") or [])
        # 自动补完成判据：LLM 经常不提供 success_criteria，导致"声明完成即
        # 完成"畅行无阻（例如向前移动被安全层拦截后仍被标记 completed）。
        # 计划中含有运动步骤时，注入"这些动作必须成功执行过"的机器校验判据。
        if not any(c.get("metric") == "planned_motion_steps_ok" for c in criteria):
            planned_motion = [s.tool for s in (initial_plan.steps or []) if getattr(s, "tool", "") in _PLANNED_MOTION_TOOLS]
            if planned_motion:
                criteria.append({
                    "metric": "planned_motion_steps_ok",
                    "tools": list(dict.fromkeys(planned_motion)),
                    "detail": "计划中的运动步骤必须成功执行：{}".format(
                        ", ".join(dict.fromkeys(planned_motion))
                    ),
                })
        if criteria:
            goal["success_criteria"] = criteria
        return goal

    def _verify_completion(self, goal: dict[str, Any], state: LoopState, observation: LoopObservation) -> dict[str, Any]:
        criteria = (goal or {}).get("success_criteria") or []
        if not isinstance(criteria, list) or not criteria:
            return {"satisfied": True, "criteria": [], "results": []}
        results = [self._verify_criterion(criterion, state, observation) for criterion in criteria]
        # Unevaluated criteria (missing telemetry) do not fail the run; they are
        # recorded as warnings per the design ("无法评估 → 接受完成 + warning").
        failed = [item for item in results if not item["satisfied"] and item.get("evaluated", True)]
        unevaluated = [item for item in results if not item.get("evaluated", True)]
        satisfied = not failed
        reason = "; ".join(f"{item['metric']}: {item['detail']}" for item in failed)
        if unevaluated:
            note = "; ".join(f"{item['metric']}: {item['detail']}" for item in unevaluated)
            reason = f"{reason}; unevaluated: {note}".strip("; ")
        return {"satisfied": satisfied, "criteria": criteria, "results": results, "reason": reason, "unevaluated": unevaluated}

    def _verify_criterion(self, criterion: dict[str, Any], state: LoopState, observation: LoopObservation) -> dict[str, Any]:
        metric = str(criterion.get("metric") or "")
        base: dict[str, Any] = {"metric": metric, "satisfied": False, "detail": "", "evaluated": True}
        if metric == "status_ok":
            failed = [result for result in state.results if not result.ok]
            # "无失败工具" plus at least one executed action: an empty loop that
            # declares completion without doing anything must not pass the gate.
            base["satisfied"] = bool(state.results) and not failed
            base["detail"] = f"results={len(state.results)} failed={len(failed)}"
        elif metric == "planned_motion_steps_ok":
            # 计划中的每个运动步骤必须至少留下一次成功执行记录；被安全层
            # 拦截/失败的动作用 fresh 状态回读证明目标已达成也无法通过——
            # 必须真正补做成功（LLM 提示词里已声明此纪律）。
            tools = list(criterion.get("tools") or [])
            ok_tools = {str(result.tool) for result in state.results if result.ok}
            missing = [t for t in tools if t not in ok_tools]
            base.update(
                satisfied=not missing,
                detail=f"missing={','.join(missing)}" if missing else "all planned motion steps succeeded",
            )
        elif metric == "photo_taken":
            ok = self._has_successful_tool(state, "airsim_take_photo") or any(
                result.ok and self._result_contains_image(result.data) for result in state.results
            )
            base.update(satisfied=ok, detail="photo captured" if ok else "no successful photo in results")
        elif metric == "target_confirmed":
            found = self._target_found_any(state)
            explicitly_not_found = self._target_not_found_explicit(state)
            looked = self._has_successful_tool(state, "skill:search") or self._has_successful_tool(state, "airsim_take_photo") or self._has_successful_tool(state, "airsim_vlm_confirm_target")
            satisfied = bool(found) or (explicitly_not_found and looked)
            base.update(satisfied=satisfied, detail=f"found={found} not_found={explicitly_not_found}")
        elif metric == "landed":
            flying = self._observation_flying(observation)
            if flying is None:
                # no telemetry: criterion cannot be evaluated, do not fail the run
                base.update(satisfied=True, evaluated=False, detail="no flight-state telemetry")
            else:
                base.update(satisfied=flying is False, detail=f"flying={flying}")
        elif metric == "flying_at":
            current = self._observation_altitude(observation)
            altitude = criterion.get("altitude")
            tolerance = float(criterion.get("tolerance") or 1.0)
            if current is None:
                base.update(satisfied=True, evaluated=False, detail="no altitude telemetry")
            else:
                satisfied = altitude is not None and abs(current - float(altitude)) <= tolerance
                base.update(satisfied=satisfied, detail=f"current={current} target={altitude}±{tolerance}")
        elif metric == "position_reached":
            pos = self._observation_position(observation)
            tolerance = float(criterion.get("tolerance") or 1.5)
            if pos is None:
                base.update(satisfied=True, evaluated=False, detail="no position telemetry")
            else:
                dx = abs(pos[0] - float(criterion.get("x") or 0.0))
                dy = abs(pos[1] - float(criterion.get("y") or 0.0))
                dz = abs(pos[2] - float(criterion.get("z") or 0.0))
                satisfied = dx <= tolerance and dy <= tolerance and dz <= tolerance
                base.update(satisfied=satisfied, detail=f"current={pos} target=({criterion.get('x')},{criterion.get('y')},{criterion.get('z')})±{tolerance}")
        elif metric == "mission_progress_complete":
            complete = any(self._progress_complete(result.data) for result in state.results if result.ok)
            base.update(satisfied=complete, detail="mission progress complete" if complete else "mission progress not complete")
        elif metric == "formation_stable":
            # only the LATEST formation_command result counts — a stale stable
            # flag from an earlier status must not satisfy the criterion
            last_formation = None
            for result in reversed(state.results):
                if result.tool == "formation_command":
                    last_formation = result
                    break
            stable = bool(
                last_formation
                and last_formation.ok
                and self._nested_bool(last_formation.data, "stable") is True
            )
            base.update(satisfied=stable, detail="formation stable" if stable else "formation not stable / no status result")
        return base

    def _corrective_decision(self, verification: dict[str, Any], state: LoopState, allowed_tools: set[str]) -> LoopDecision | None:
        """One corrective action for the first unsatisfied criterion that has a
        re-verification path. Returns None when nothing can be re-checked."""
        for criterion, item in zip(verification.get("criteria") or [], verification.get("results") or []):
            if item.get("satisfied"):
                continue
            tool = self._CORRECTIVE_TOOLS.get(str(item.get("metric") or ""))
            if tool is None or tool not in allowed_tools:
                continue
            if tool == "airsim_vlm_confirm_target":
                if not self._has_recent_image(state):
                    if "airsim_take_photo" in allowed_tools:
                        return LoopDecision(
                            "airsim_take_photo",
                            {"image_type": "scene", "auto_save": False},
                            "Capture a fresh frame before re-verifying the target.",
                            is_complete=False,
                        )
                    continue
                return LoopDecision(
                    "airsim_vlm_confirm_target",
                    {"target_description": str(criterion.get("target") or state.command[:160]), "source": "last_image"},
                    "Re-confirm the target before accepting completion.",
                    is_complete=False,
                )
            if tool == "formation_command":
                return LoopDecision(
                    "formation_command",
                    {"action": "status"},
                    "Re-poll formation status before accepting completion.",
                    is_complete=False,
                )
            return LoopDecision(
                tool,
                {},
                f"Re-read state to verify completion criterion '{item.get('metric')}'.",
                is_complete=False,
            )
        return None

    def _target_found_any(self, state: LoopState) -> bool:
        for item in self._iter_tool_results(state):
            if self._nested_bool(item.get("data"), "target_found") is True:
                return True
        return False

    def _target_not_found_explicit(self, state: LoopState) -> bool:
        for item in self._iter_tool_results(state):
            data = item.get("data") or {}
            if self._nested_bool(data, "target_found") is False:
                return True
            if self._nested_value(data, "status") == "target_not_confirmed":
                return True
        return False

    @staticmethod
    def _nested_bool(value: Any, key: str) -> bool | None:
        found = AgentLoop._nested_value(value, key)
        return bool(found) if isinstance(found, bool) else None

    @staticmethod
    def _nested_value(value: Any, key: str, _depth: int = 0) -> Any:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            if key in value:
                return value[key]
            for item in value.values():
                found = AgentLoop._nested_value(item, key, _depth + 1)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = AgentLoop._nested_value(item, key, _depth + 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _progress_complete(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        progress = data.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool) and progress >= 99.0:
            return True
        percent = data.get("percent")
        if isinstance(percent, (int, float)) and not isinstance(percent, bool) and percent >= 99.0:
            return True
        status = str(data.get("status") or "").lower()
        return any(marker in status for marker in ("complete", "finished", "done"))

    @staticmethod
    def _observation_drone(observation: LoopObservation) -> dict[str, Any]:
        world = observation.world_state if hasattr(observation, "world_state") else {}
        drone = world.get("drone") if isinstance(world, dict) else {}
        return drone if isinstance(drone, dict) else {}

    @staticmethod
    def _observation_flying(observation: LoopObservation) -> bool | None:
        flying = AgentLoop._observation_drone(observation).get("flying")
        return bool(flying) if flying is not None else None

    @staticmethod
    def _observation_altitude(observation: LoopObservation) -> float | None:
        drone = AgentLoop._observation_drone(observation)
        pos = drone.get("position_ned")
        if isinstance(pos, dict):
            try:
                z = float(pos.get("z") or 0.0)
                return abs(z)
            except (TypeError, ValueError):
                pass
        try:
            altitude = float(drone.get("altitude_m") or 0.0)
            return abs(altitude) if altitude else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _observation_position(observation: LoopObservation) -> tuple[float, float, float] | None:
        pos = AgentLoop._observation_drone(observation).get("position_ned")
        if not isinstance(pos, dict):
            return None
        try:
            return (float(pos.get("x") or 0.0), float(pos.get("y") or 0.0), float(pos.get("z") or 0.0))
        except (TypeError, ValueError):
            return None

    def _execute_batch_actions(
        self,
        raw_actions: list[dict[str, Any]],
        command: str,
        state: LoopState,
        observation: LoopObservation,
        allowed_tools: set[str],
        capabilities: dict[str, Any],
        step_index: int,
        execute: bool,
        failure_count: int,
        unresolved_failure: bool,
    ) -> dict[str, Any]:
        """Execute read-only batch actions one by one, each through the same
        sanitize + guard pipeline as the main action. Returns the updated
        failure counters so the caller keeps the 3-strike window semantics."""
        for raw_action in raw_actions:
            if state.status == "failed":
                break
            if not isinstance(raw_action, dict):
                continue
            sub = LoopDecision(
                action=str(raw_action.get("action") or ""),
                params=dict(raw_action.get("params") or {}),
                reason=str(raw_action.get("reason") or ""),
                is_complete=bool(raw_action.get("is_complete")),
                needs_replan=bool(raw_action.get("needs_replan")),
                reflection=str(raw_action.get("reflection") or ""),
            )
            if not sub.action or sub.is_complete:
                continue
            sub = self._sanitize_decision(sub, allowed_tools)
            if not sub.action:
                self._event("warning", "agent_loop", f"Batch action skipped: {raw_action.get('action')}", sub.to_dict(), kind="loop.decision")
                continue
            if not self._is_parallel_safe(sub.action):
                self._event(
                    "warning",
                    "agent_loop",
                    f"Batch action rejected (flight-control tools are one per turn): {sub.action}",
                    sub.to_dict(),
                    kind="loop.decision",
                )
                continue
            guarded = self._guard_decision(command, state, observation, sub, allowed_tools, capabilities)
            if guarded is not None:
                sub = guarded
            sub = self._sanitize_decision(sub, allowed_tools)
            if sub.is_complete or not sub.action:
                continue
            batch_result = self._execute_action(sub, dry_run=not execute)
            batch_result = self._settle_async_result(batch_result, dry_run=not execute)
            batch_row = LoopActionResult(
                step_index=step_index,
                tool=batch_result["tool"],
                params=batch_result["params"],
                ok=bool(batch_result["ok"]),
                data=batch_result["data"],
                safety=batch_result.get("safety"),
                duration_ms=batch_result["duration_ms"],
            )
            state.results.append(batch_row)
            self._notify_state(state)
            self.memory.remember_tool_call(batch_row.tool, batch_row.ok)
            self._event(
                "info" if batch_row.ok else "warning",
                "tool",
                f"Batch action {batch_row.tool}",
                batch_result["raw"],
                kind="tool.result",
            )
            if not batch_row.ok:
                failure_count += 1
                unresolved_failure = True
                state.failure_reason = str(batch_row.data.get("message") or f"{batch_row.tool} failed")
                self._event(
                    "warning",
                    "agent_loop",
                    f"Batch action failed ({failure_count}/3)",
                    batch_row.to_dict(),
                    kind="tool.result",
                )
                if failure_count >= 3:
                    state.status = "failed"
                    break
            elif batch_row.tool not in {"drone_get_status", "airsim_task_status"}:
                unresolved_failure = False
        return {"failure_count": failure_count, "unresolved_failure": unresolved_failure}

    def _execute_action(self, decision: LoopDecision, dry_run: bool) -> dict[str, Any]:
        started = time.time()
        if decision.action.startswith("skill:"):
            skill_result = self.skills.execute(
                decision.action,
                decision.params,
                self.tools,
                dry_run=dry_run,
                execute_tool=self._call_tool,
            )
            finished = time.time()
            return {
                "tool": skill_result.skill,
                "params": dict(decision.params),
                "ok": skill_result.ok,
                "data": skill_result.to_dict(),
                "safety": None,
                "duration_ms": round((finished - started) * 1000, 1),
                "raw": skill_result.to_dict(),
            }
        tool_result = self._call_tool(decision.action, decision.params, dry_run)
        return {
            "tool": tool_result.tool,
            "params": tool_result.params,
            "ok": tool_result.ok,
            "data": tool_result.data,
            "safety": tool_result.safety,
            "duration_ms": round((tool_result.finished_at - tool_result.started_at) * 1000, 1),
            "raw": tool_result.to_dict(),
        }

    def _call_tool(self, name: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
        if self.execute_tool:
            return self.execute_tool(name, params, dry_run)
        return self.tools.execute(
            name,
            params,
            dry_run=dry_run,
            blocked_by_supervisor=self._should_stop(),
        )

    def _settle_async_result(self, result: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run or not result.get("ok"):
            return result
        descriptor = self._find_async_descriptor(result.get("raw") or result.get("data") or {})
        if not descriptor:
            return result
        task_id = str(descriptor.get("task_id") or "")
        if not task_id:
            return result

        accepted = dict(result.get("data") or {})
        deadline = time.monotonic() + self.async_timeout
        self._event("info", "async_task", f"Waiting for background task {task_id}", {"task_id": task_id})
        while time.monotonic() < deadline:
            if self._should_stop():
                self._cancel_async_task(task_id)
                result["ok"] = False
                result["data"] = {"status": "blocked", "task_id": task_id, "message": "background task cancelled by emergency stop"}
                return result
            self._wait_if_paused()
            status_result = self._call_tool("airsim_task_status", {"task_id": task_id}, False)
            polled = True
            # Backend coupling guard: airsim_task_status is an AirSim-backend
            # tool. If the active backend does not register it (PX4 backends
            # never return async descriptors today, but a future tool could),
            # polling would spin the whole timeout for nothing — accept the
            # original result and note the gap instead.
            if status_result.error_code == "UNKNOWN_TOOL":
                result["data"] = {
                    "status": "accepted",
                    "task_id": task_id,
                    "task": status_result.data,
                    "accepted_result": accepted,
                    "message": "background task accepted; task_status tool is not available on this backend",
                }
                self._event("warning", "async_task", f"Task {task_id} accepted, but airsim_task_status is unavailable on this backend", {"task_id": task_id})
                return result
            status = str((status_result.data or {}).get("status") or "").strip().lower()
            self._event(
                "info" if status_result.ok else "warning",
                "async_task",
                f"Background task {task_id}: {status or 'unknown'}",
                status_result.to_dict(),
            )
            if status in {"completed", "failed", "cancelled", "canceled", "error", "blocked"} or status_result.terminal:
                result["ok"] = status_result.ok and status == "completed"
                result["data"] = {
                    "status": status or ("completed" if status_result.ok else "failed"),
                    "task_id": task_id,
                    "task": status_result.data,
                    "accepted_result": accepted,
                }
                result["raw"] = status_result.to_dict()
                return result
            time.sleep(self.async_poll_interval)

        self._cancel_async_task(task_id)
        result["ok"] = False
        result["data"] = {
            "status": "failed",
            "task_id": task_id,
            "message": f"background task timed out after {self.async_timeout:.0f}s and was cancelled",
            "accepted_result": accepted,
        }
        return result

    def _cancel_async_task(self, task_id: str) -> None:
        try:
            self._call_tool("airsim_task_cancel", {"task_id": task_id}, False)
        except Exception:
            pass

    def _find_async_descriptor(self, value: Any, _depth: int = 0) -> dict[str, Any] | None:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().lower()
            task_id = str(value.get("task_id") or "")
            terminal = value.get("terminal")
            if task_id and (terminal is False or status in {"accepted", "started", "pending", "queued", "running", "in_progress"}):
                return value
            for nested in value.values():
                found = self._find_async_descriptor(nested, _depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in reversed(value):
                found = self._find_async_descriptor(nested, _depth + 1)
                if found:
                    return found
        return None

    def _wait_if_paused(self) -> None:
        if not self.should_pause:
            return
        while self.should_pause() and not self._should_stop():
            time.sleep(0.2)

    def _should_stop(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def _event(self, level: str, source: str, message: str, data: dict[str, Any], kind: str = "") -> None:
        if kind:
            data = {**data, "kind": kind}
        if self.on_event:
            self.on_event(level, source, message, data)

    def _notify_state(self, state: LoopState) -> None:
        if self.on_state:
            self.on_state(state)

    def _summary(self, state: LoopState) -> str:
        ok_count = sum(1 for result in state.results if result.ok)
        if state.status == "completed":
            return f"Agent loop completed with {ok_count} successful action(s)."
        if state.failure_reason.startswith("agent loop reached max_steps=") and state.results:
            return self._step_limit_summary(state, state.max_steps)
        return state.failure_reason or f"Agent loop stopped after {len(state.results)} action(s)."

    def _step_limit_summary(self, state: LoopState, max_steps: int) -> str:
        ok_tools = [result.tool for result in state.results if result.ok]
        failed_tools = [result.tool for result in state.results if not result.ok]
        suffix = f"，失败工具：{', '.join(failed_tools[-3:])}" if failed_tools else ""
        return (
            f"Agent Loop 达到 {max_steps} 步上限，已完成 {len(ok_tools)} 个工具调用"
            f"{suffix}。最终报告应基于已收集的工具结果和遥测说明完成项、未完成项与当前状态。"
        )
