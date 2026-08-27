"""决策解析：ReAct 循环决策（native tools / payload 两种来源）与后续动作判定。

拆分自 llm.py（LLMMissionPlanner 方法按职责迁移，行为不变）。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .command_slots import (
    FLIGHT_TERMS,
    NAVIGATION_TERMS,
    extract_command_slots,
    extract_intents,
    extract_target_class,
)
from .llm_protocol import (
    IMAGE_TOKEN_QUOTA,
    ContextBudget,
    TokenMeter,
    estimate_messages,
    function_tool_schema,
    openai_tools_to_anthropic,
    tool_schema_from_spec,
)
from .loop_types import LoopDecision
from .planner import MissionPlan, MissionPlanner, MissionStep
from .llm_clients import (
    _VISION_MODEL_HINTS,
    _CONTEXT_WINDOW_HINTS,
    _NATIVE_TOOL_PROVIDERS,
    infer_model_capabilities,
    LLMConfig,
    LLMConfigStore,
    ModelRegistry,
    _config_value,
    _extract_json,
    _retry_delay,
    _request_with_retry,
    is_context_overflow_error,
    _retryable_stream_error,
    _stream_events_with_first_chunk_retry,
    _stream_timeout_error,
    OpenAIClient,
    AnthropicClient,
    _create_client,
    ToolCallUnsupportedError,
    is_tool_unsupported_error,
    LLMUnavailableError,
)


class LLMPlannerDecisionsMixin:
    def decide_next_step(
        self,
        command: str,
        loop_state: dict[str, Any],
        observation: dict[str, Any],
        tool_cards: list[dict[str, Any]],
        capabilities: dict[str, Any] | None,
        memory: dict[str, Any],
        model_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        require_llm: bool = False,
        skill_guidance: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        fallback_enabled: bool = True,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> LoopDecision:
        """Choose the next action for a lightweight observe-decide-act loop.

        Uses the native function-calling protocol when the selected model
        supports it. The protocol is deliberately single-round and stateless:
        tool results are folded into the next observation JSON instead of being
        appended as tool messages, so native and JSON modes can interleave
        without history-format conflicts. On ToolCallUnsupportedError the
        planner degrades to JSON-schema prompting; other model errors fall back
        to the rule decision unless require_llm is set (or fallback_enabled is
        False, used by sub-agents, in which case errors always raise).
        """

        allowed_tools = self._loop_action_names(tool_cards)
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            if require_llm:
                message = self._chat_unavailable_message(config, "missing_api_key")
                self.last_error = message
                raise LLMUnavailableError(message)
            return self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})

        prompt = system_prompt or self._loop_decision_system_prompt()
        images = self._image_attachments(attachments)
        sections = [
            {"key": "operator_command", "value": command, "priority": "command"},
            {"key": "output_schema", "value": json.dumps(self._loop_decision_schema_hint(), ensure_ascii=False, default=str), "priority": "command"},
            {"key": "observation", "value": json.dumps(self._compact_observation(observation), ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "backend_capabilities", "value": json.dumps(capabilities or {}, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "loop_state", "value": json.dumps(self._compact_loop_state(loop_state), ensure_ascii=False, default=str), "priority": "recent"},
            {"key": "available_tool_cards", "value": json.dumps(self._compact_tool_cards(tool_cards), ensure_ascii=False, default=str), "priority": "tool_cards"},
            {"key": "skill_guidance", "value": json.dumps(self._compact_skill_guidance(skill_guidance or []), ensure_ascii=False, default=str), "priority": "guidance"},
            {"key": "memory_snapshot", "value": json.dumps(self._compact_memory(memory), ensure_ascii=False, default=str), "priority": "memory"},
        ]
        if conversation_context:
            sections.append(
                {
                    "key": "conversation_context",
                    "value": json.dumps(self._compact_conversation(conversation_context), ensure_ascii=False, default=str),
                    "priority": "memory",
                }
            )
        sent_messages: list[list[dict[str, Any]]] = []

        def build_messages(window_scale: float = 1.0) -> list[dict[str, Any]]:
            payload = self._sections_to_payload(self._fit_sections(config, sections, len(images), window_scale))
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": self._content_with_images(
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        attachments or [],
                    ),
                },
            ]
            sent_messages.append(messages)
            return messages

        def fallback(error: Exception) -> LoopDecision:
            if not fallback_enabled:
                raise LLMUnavailableError(f"Agent Loop LLM decision failed: {error}") from error
            decision = self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})
            message = str(error)
            if decision.reflection:
                decision.reflection = f"{decision.reflection} LLM fallback: {message}"
            else:
                decision.reflection = f"LLM fallback: {message}"
            return decision

        native = self._config_native_tools(config)
        openai_tools = self._loop_tool_schemas(tool_cards, tools) if native else []
        if native and openai_tools:
            try:
                decision = self._native_decision(config, build_messages(1.0), openai_tools, allowed_tools)
                self._record_usage(sent_messages[-1] if sent_messages else [], self.last_usage)
                return decision
            except ToolCallUnsupportedError as exc:
                self.last_error = f"native tool calling unavailable; using JSON mode: {exc}"
            except Exception as e:
                if is_context_overflow_error(e):
                    # tighter budget, then the JSON path below as the final fallback
                    try:
                        decision = self._native_decision(config, build_messages(0.5), openai_tools, allowed_tools)
                        self._record_usage(sent_messages[-1] if sent_messages else [], self.last_usage)
                        return decision
                    except ToolCallUnsupportedError as exc:
                        self.last_error = f"native tool calling unavailable; using JSON mode: {exc}"
                    except Exception as e2:
                        self.last_error = str(e2)
                        if require_llm and fallback_enabled:
                            raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e2}") from e2
                        return fallback(e2)
                self.last_error = str(e)
                if require_llm and fallback_enabled:
                    raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e}") from e
                return fallback(e)
        try:
            client = _create_client(config)
            parsed, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
            self.last_error = ""
            self.last_usage = usage
            self._record_usage(sent_messages[-1] if sent_messages else [], usage)
            return self._decision_from_payload(parsed, allowed_tools)
        except Exception as e:
            self.last_error = str(e)
            if require_llm and fallback_enabled:
                raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e}") from e
            return fallback(e)

    def _config_native_tools(self, config: dict[str, Any] | None) -> bool:
        """Per-model gate for the native function-calling path."""
        if not config:
            return False
        explicit = config.get("native_tools")
        if explicit is not None:
            return bool(explicit)
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
        )
        return bool(inferred["native_tools"])

    def _loop_tool_schemas(
        self,
        tool_cards: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Synthesize function-calling schemas from tool cards + runtime specs."""
        specs = {str(t.get("name") or ""): t for t in (tools or []) if isinstance(t, dict)}
        schemas: list[dict[str, Any]] = []
        for card in tool_cards[:40]:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "")
            if not name or name == "memory_store":
                continue
            spec = specs.get(name)
            parameters = spec.get("parameters") if spec else None
            inputs = card.get("inputs") if isinstance(card.get("inputs"), dict) else {}
            schema = tool_schema_from_spec(name, parameters or {}, inputs)
            description = str(card.get("purpose") or (spec or {}).get("description") or name)
            schemas.append(function_tool_schema(name, description, schema))
        return schemas

    def _native_decision(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        openai_tools: list[dict[str, Any]],
        allowed_tools: set[str],
    ) -> LoopDecision:
        client = _create_client(config)
        if str(config.get("api_type") or "openai") == "anthropic":
            tools_payload = openai_tools_to_anthropic(openai_tools)
        else:
            tools_payload = openai_tools
        tool_calls, text, usage = client.chat_tools(messages, tools_payload)
        # surface the model's decision rationale (reasoning_content) for the
        # operator's event stream
        self.last_reasoning = str(getattr(client, "last_reasoning", "") or "")
        self.last_error = ""
        self.last_usage = usage
        return self._decision_from_tool_calls(tool_calls, text, allowed_tools)

    def _decision_from_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        text: str,
        allowed_tools: set[str],
    ) -> LoopDecision:
        """Native protocol: tool calls => actions; no tool calls => complete.

        Unavailable tool calls are skipped (noted in reflection) instead of
        failing the whole turn — one hallucinated name must not kill the run.
        """
        if not tool_calls:
            return LoopDecision(action="", reason=text.strip() or "complete", is_complete=True)
        decisions: list[LoopDecision] = []
        rejected: list[str] = []
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            if name not in allowed_tools:
                rejected.append(name)
                continue
            decisions.append(
                LoopDecision(
                    action=name,
                    params=dict(arguments),
                    reason=text.strip() or f"Call {name}",
                    is_complete=False,
                )
            )
        if not decisions:
            if rejected:
                return LoopDecision(
                    action="",
                    reason=f"Model selected unavailable tools: {', '.join(rejected)}",
                    is_complete=False,
                    reflection="Stopped before executing unavailable tools.",
                )
            return LoopDecision(action="", reason=text.strip() or "complete", is_complete=True)
        main = decisions[0]
        if rejected:
            main.reflection = f"skipped unavailable tool calls: {', '.join(rejected)}"
        main.parallel_actions = [item.to_dict() for item in decisions[1:]]
        return main

    def _loop_decision_system_prompt(self) -> str:
        return (
            "You are the decision layer of a UAV Agent Loop using ReAct / plan-execute-observe. "
            "At each step, choose exactly one next tool action, or mark the task complete after the latest observation proves the goal is handled. "
            "CORRECTION DISCIPLINE: when entering a correction loop, FIRST read the current vehicle status (drone_get_status) and verify from the observation whether the goal action actually failed — never blindly repeat an action that the previous step already reported as completed (e.g. landing completed means the vehicle is on the ground; calling land again is wrong). If the observation shows the goal is already satisfied, mark is_complete=true instead of re-executing. "
            "NEVER declare is_complete=true while any planned motion step (takeoff/hover/move/fly/land/rotate) is still failed or unexecuted from a previous attempt: you must first successfully re-execute that action (fixing its cause first, e.g. climb to the safe altitude before a blocked horizontal move), or prove with fresh drone_get_status telemetry that the target state is genuinely reached. Declaring the task complete while a goal motion is still missing is a hard error and the run will be marked failed. "
            "You may additionally batch up to 2 independent read-only tool calls (drone_get_status, drone_list_vehicles, drone_get_mission_progress, airsim_take_photo, airsim_get_sensors, airsim_get_depth_map, airsim_vlm_analyze_image, airsim_vlm_confirm_target) "
            "in the 'actions' array (JSON mode) or as extra tool calls (native mode). "
            "Never place flight-control tools (arm, disarm, takeoff, land, hover, fly, move, rotate, set_mode, mission upload/start) in that batch — one flight tool per turn. "
            "Use only available_tool_cards. Do not call unavailable tools. "
            "Parse the operator's full instruction yourself, including multi-step Chinese or English commands. Do not rely on keyword routing. "
            "Never bypass safety, never invent telemetry, and do not perform low-level continuous control. "
            "skill_guidance contains Markdown skills that teach how to work; they are not actions. Never output action=skill:* unless that exact action appears in available_tool_cards. "
            "If the operator's command is a knowledge question, explanation request, or non-UAV task that does not require vehicle tools or backend state, mark is_complete=true in the first turn and put a concise Chinese natural-language answer in reason. Do not force a drone tool call for questions unrelated to UAV operation. "
            "If flight_sequence guidance is present and the request is a short ordered workflow such as status -> takeoff -> move -> photo/VLM -> return -> land, use that Markdown to choose the next native tool and keep the sequence concise. "
            "For vague scan/move wording such as '一点距离' or '简单扫描', keep horizontal movement to 1-2 meters, use velocity around 1.0-1.5 m/s, and take off to at least 3 m before horizontal movement. "
            "For open-ended 'what is in the photo' requests, use airsim_vlm_analyze_image; use airsim_vlm_confirm_target only for a named target. "
            "For return-to-start, use the first observed position when available instead of guessing home coordinates. "
            "For square/rectangle/orbit/circle/grid path tasks, prefer one drone_fly_path action with explicit local NED waypoints over many rotate-to-heading plus drone_move_relative turns. Use rotate_to only when camera orientation matters. "
            "For multi-step goals, call one available tool per turn, then use the next observation to decide the following tool. "
            "Prefer status/readback after uncertain results. After a photo or visual sweep produces an image, call airsim_vlm_confirm_target with source=last_image before declaring a target found. "
            "Use memory_snapshot.guidance to prefer historically reliable skills/tools and to avoid repeating known failure patterns. "
            "An async tool is not complete when it returns started/running; wait for the runtime-provided terminal observation before declaring completion. "
            "If the backend lacks a requested capability, stop safely and explain the limitation in reflection. "
            "The reason/reflection fields are displayed live to the operator as public process text; write concise, useful, non-sensitive decision text there. "
            "When is_complete=true, put a concise Chinese task report in reason. Include executed sequence, final state/position, image finding, and any limitation/failure. "
            "Think and reason in Chinese (中文思考). Return JSON only."
        )

    def _loop_decision_schema_hint(self) -> dict[str, Any]:
        return {
            "action": "one available tool name, or empty string when complete",
            "params": {"name": "value"},
            "reason": "brief public Chinese process text explaining what you decided to do next",
            "is_complete": False,
            "needs_replan": False,
            "reflection": "optional brief public reflection on the latest observation/result",
            "actions": [
                {
                    "action": "additional read-only tool name (drone_get_status, airsim_take_photo, sensors, depth, VLM analysis/confirm) — never flight-control tools",
                    "params": {"name": "value"},
                    "reason": "optional short text",
                }
            ],
        }

    def _decision_from_payload(self, payload: dict[str, Any], allowed_tools: set[str]) -> LoopDecision:
        action = str(payload.get("action") or "").strip()
        is_complete = bool(payload.get("is_complete", False))
        if is_complete:
            action = ""
        elif action and action not in allowed_tools:
            return LoopDecision(
                action="",
                reason=f"Model selected unavailable tool: {action}",
                is_complete=False,
                reflection="Stopped before executing an unavailable tool.",
            )
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        decision = LoopDecision(
            action=action,
            params=params,
            reason=str(payload.get("reason") or action or "complete"),
            is_complete=is_complete,
            needs_replan=bool(payload.get("needs_replan", False)),
            reflection=str(payload.get("reflection") or ""),
        )
        raw_actions = payload.get("actions")
        if isinstance(raw_actions, list) and not is_complete:
            extras: list[LoopDecision] = []
            for raw in raw_actions:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("action") or "").strip()
                if not name or name not in allowed_tools or name == action:
                    continue
                raw_params = raw.get("params") or {}
                if not isinstance(raw_params, dict):
                    raw_params = {}
                extras.append(
                    LoopDecision(
                        action=name,
                        params=dict(raw_params),
                        reason=str(raw.get("reason") or ""),
                        is_complete=False,
                        needs_replan=False,
                        reflection=str(raw.get("reflection") or ""),
                    )
                )
            if not action and extras:
                first = extras.pop(0)
                decision = LoopDecision(
                    action=first.action,
                    params=first.params,
                    reason=first.reason or "complete",
                    is_complete=False,
                    reflection=first.reflection,
                )
            decision.parallel_actions = [item.to_dict() for item in extras]
        return decision

    def _loop_action_names(self, tool_cards: list[dict[str, Any]]) -> set[str]:
        return {str(card.get("name")) for card in tool_cards if isinstance(card, dict) and card.get("name")}

    def _loop_has_action(self, loop_state: dict[str, Any], action: str, after_failure: bool = False) -> bool:
        decisions = loop_state.get("decisions") or []
        if not after_failure:
            return any((item or {}).get("action") == action for item in decisions if isinstance(item, dict))
        results = loop_state.get("results") or []
        last_failed_index = 0
        for item in results:
            if isinstance(item, dict) and item.get("ok") is False:
                last_failed_index = int(item.get("step_index") or 0)
        for index, item in enumerate(decisions, 1):
            if isinstance(item, dict) and item.get("action") == action and index > last_failed_index:
                return True
        return False

    def _loop_has_recent_image(self, loop_state: dict[str, Any]) -> bool:
        text = json.dumps(loop_state.get("results") or [], ensure_ascii=False, default=str).lower()
        return any(marker in text for marker in ["image_base64", "image_saved_to", "saved_to", "has_image", "last_image"])

    def _loop_altitude(self, command: str) -> float:
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:m|meter|meters|米|公尺)", command.lower())
        if not match:
            return 3.0
        try:
            return max(1.0, min(30.0, abs(float(match.group(1)))))
        except ValueError:
            return 3.0

    def _loop_target_class(self, command: str) -> str:
        return extract_target_class(command) or "target"
