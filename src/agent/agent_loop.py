"""Lightweight observe-decide-act loop for advanced UAV tasks."""

from __future__ import annotations

import time
from typing import Any, Callable

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
            )
            state.observations.append(observation)
            self._notify_state(state)

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
                )
                decision = self._guard_decision(command, state, observation, decision, allowed_tools, capabilities)
            decision = self._sanitize_decision(decision, allowed_tools)
            state.decisions.append(decision)
            self._notify_state(state)
            self._event("info", "agent_loop", f"Loop decision {step_index}: {decision.action or 'complete'}", decision.to_dict())

            if decision.is_complete:
                if unresolved_failure:
                    state.status = "failed"
                    state.failure_reason = decision.reflection or decision.reason or "task stopped without recovering from the previous failure"
                else:
                    state.status = "completed"
                    state.summary = decision.reason or decision.reflection or "agent loop completed"
                break
            if not decision.action:
                if decision.needs_replan and replan_count < 2:
                    replan_count += 1
                    last_result = {
                        "ok": False,
                        "data": {"status": "replan_requested", "message": decision.reflection or decision.reason},
                    }
                    self._event("warning", "agent_loop", f"Replan requested ({replan_count}/2)", decision.to_dict())
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
            self._event("info" if result_row.ok else "warning", "tool", f"Loop action {result_row.tool}", result["raw"])

            if not result_row.ok:
                failure_count += 1
                unresolved_failure = True
                state.failure_reason = str(result_row.data.get("message") or f"{result_row.tool} failed")
                if failure_count >= 3:
                    state.status = "failed"
                    break
                self._event(
                    "warning",
                    "agent_loop",
                    f"Action failed; observation/recovery turn allowed ({failure_count}/3)",
                    result_row.to_dict(),
                )
                continue
            if result_row.tool not in {"drone_get_status", "airsim_task_status"}:
                unresolved_failure = False
        else:
            state.status = "blocked"
            state.failure_reason = f"agent loop reached max_steps={max_steps}"
            if state.results:
                state.summary = self._step_limit_summary(state, max_steps)

        if state.status == "created" or state.status == "running":
            state.status = "completed"
        state.finished_at = time.time()
        if not state.summary:
            state.summary = self._summary(state)
        self._notify_state(state)
        return state

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
        has_motion_goal = any(
            term in lower
            for term in [
                "takeoff", "fly", "move", "forward", "backward", "left", "right", "return", "land",
                "\u8d77\u98de", "\u98de\u884c", "\u524d\u98de", "\u5411\u524d", "\u540e\u98de", "\u5de6",
                "\u53f3", "\u8fd4\u822a", "\u8fd4\u56de", "\u964d\u843d",
            ]
        )
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
        return deduped[:32]

    def _allowed_tools(self, tool_cards: list[dict[str, Any]]) -> set[str]:
        card_names = {card.get("name") for card in tool_cards if isinstance(card, dict)}
        return {str(name) for name in card_names if name and str(name) not in self._internal_tools()}

    @staticmethod
    def _internal_tools() -> set[str]:
        return {"memory_store"}

    def _is_visual_request(self, command: str) -> bool:
        text = command.lower()
        terms = [
            "camera", "image", "photo", "picture", "frame", "view", "visible", "see", "look",
            "detect", "search", "find", "locate", "target", "red car", "vehicle",
            "\u6444\u50cf\u5934", "\u753b\u9762", "\u56fe\u50cf", "\u56fe\u7247", "\u62cd\u7167",
            "\u770b\u5230", "\u770b\u4e00\u4e0b", "\u89c6\u89c9", "\u8bc6\u522b", "\u68c0\u6d4b",
            "\u641c\u7d22", "\u5bfb\u627e", "\u76ee\u6807", "\u7ea2\u8272\u8f66", "\u8f66\u8f86",
        ]
        return any(term in text for term in terms)

    def _wants_open_image_analysis(self, command: str) -> bool:
        text = command.lower()
        terms = [
            "what do you see", "what can you see", "describe", "what is in the image",
            "\u770b\u5230\u4e86\u4ec0\u4e48", "\u753b\u9762\u4fe1\u606f", "\u6709\u5565",
            "\u6709\u4ec0\u4e48", "\u770b\u4e00\u4e0b", "\u544a\u8bc9\u6211\u56fe\u7247",
        ]
        return any(term in text for term in terms) and not self._wants_target_confirmation(command)

    def _wants_target_confirmation(self, command: str) -> bool:
        text = command.lower()
        terms = [
            "target", "car", "vehicle", "truck", "bus", "person", "red", "blue", "white",
            "detect", "search", "find", "locate",
            "\u76ee\u6807", "\u8f66", "\u8f66\u8f86", "\u6c7d\u8f66", "\u5361\u8f66",
            "\u884c\u4eba", "\u4eba\u5458", "\u7ea2\u8272", "\u84dd\u8272", "\u767d\u8272", "\u641c\u7d22",
            "\u5bfb\u627e", "\u8bc6\u522b", "\u68c0\u6d4b",
        ]
        return any(term in text for term in terms)

    def _wants_visual_approach(self, command: str) -> bool:
        text = command.lower()
        terms = [
            "fly to", "go to", "move to", "approach", "toward", "towards",
            "\u98de\u5411", "\u98de\u5230", "\u9760\u8fd1", "\u524d\u5f80", "\u79fb\u52a8\u5230",
        ]
        return any(term in text for term in terms)

    def _wants_search(self, command: str) -> bool:
        text = command.lower()
        terms = ["search", "find", "locate", "\u641c\u7d22", "\u5bfb\u627e", "\u627e\u5230", "\u67e5\u627e"]
        return any(term in text for term in terms)

    def _target_description(self, command: str) -> str:
        text = command.strip()
        if text:
            return text[:240]
        return "target"

    def _target_class(self, command: str) -> str:
        text = command.lower()
        if any(term in text for term in ["truck", "\u5361\u8f66", "\u8d27\u8f66"]):
            return "truck"
        if any(term in text for term in ["bus", "\u516c\u4ea4", "\u5df4\u58eb"]):
            return "bus"
        if any(term in text for term in ["person", "pedestrian", "human", "\u884c\u4eba", "\u4eba\u5458"]):
            return "person"
        if any(term in text for term in ["car", "vehicle", "\u6c7d\u8f66", "\u8f66\u8f86", "\u8f66"]):
            return "car"
        return "target"

    def _has_successful_tool(self, state: LoopState, tool: str) -> bool:
        return any(item.get("tool") == tool and bool(item.get("ok")) for item in self._iter_tool_results(state))

    def _has_recent_image(self, state: LoopState) -> bool:
        for result in reversed(state.results):
            if result.ok and self._result_contains_image(result.data):
                return True
        return False

    def _result_contains_image(self, value: Any) -> bool:
        if isinstance(value, dict):
            if any(value.get(key) for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._result_contains_image(item) for item in value.values())
        if isinstance(value, list):
            return any(self._result_contains_image(item) for item in value)
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

    def _iter_nested_tool_results(self, value: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("tool"):
                items.append(value)
            for key in ("tool_results", "results"):
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in reversed(nested):
                        items.extend(self._iter_nested_tool_results(item))
            data = value.get("data")
            if isinstance(data, (dict, list)):
                items.extend(self._iter_nested_tool_results(data))
        elif isinstance(value, list):
            for item in reversed(value):
                items.extend(self._iter_nested_tool_results(item))
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

    def _find_async_descriptor(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().lower()
            task_id = str(value.get("task_id") or "")
            terminal = value.get("terminal")
            if task_id and (terminal is False or status in {"accepted", "started", "pending", "queued", "running", "in_progress"}):
                return value
            for nested in value.values():
                found = self._find_async_descriptor(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in reversed(value):
                found = self._find_async_descriptor(nested)
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

    def _event(self, level: str, source: str, message: str, data: dict[str, Any]) -> None:
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
