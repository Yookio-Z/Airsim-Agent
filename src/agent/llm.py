"""LLM planner with multi-provider model registry for the AirSim VLA agent.

客户端层拆至 llm_clients.py，LLMMissionPlanner 拆为 decisions/compact/payload 三个 Mixin；本文件保留组合类与全部符号 re-export。"""
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
from .llm_planner_decisions import LLMPlannerDecisionsMixin
from .llm_planner_compact import LLMPlannerCompactMixin
from .llm_planner_payload import LLMPlannerPayloadMixin


class LLMMissionPlanner(
    LLMPlannerDecisionsMixin,
    LLMPlannerCompactMixin,
    LLMPlannerPayloadMixin,
):
    """LLM-first planner with rule-based fallback and per-request model selection."""

    def __init__(self, config: LLMConfig | None = None, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.legacy_config = config
        self.fallback = MissionPlanner()
        self.last_error = ""
        self.last_usage: dict[str, Any] = {}
        self.token_meter = TokenMeter()

    def reload_config(self) -> None:
        self.registry = ModelRegistry()

    def _resolve_config(self, model_id: str | None = None) -> dict[str, Any] | None:
        if model_id:
            model = self.registry.get(model_id)
            if model:
                return model
        if self.legacy_config and self.legacy_config.enabled:
            return {
                "id": self.legacy_config.provider,
                "name": self.legacy_config.provider,
                "provider": self.legacy_config.provider,
                "model": self.legacy_config.model,
                "base_url": self.legacy_config.base_url,
                "api_key": self.legacy_config.api_key,
                "api_type": "openai",
                "timeout_sec": self.legacy_config.timeout_sec,
                "max_tokens": self.legacy_config.max_tokens,
                "temperature": self.legacy_config.temperature,
            }
        return self.registry.get_default()

    def _enabled(self, config: dict[str, Any] | None) -> bool:
        return bool(config and config.get("api_key"))

    def _chat_json_with_retries(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        *,
        attempts: int = 3,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Call ``chat_json`` with retries for recoverable outputs.

        Slow models often return an empty/truncated response first; a short
        backoff (0.35s) then retries into the same still-running window and
        fails a second time. Use a longer, per-attempt backoff so the retry
        lands after the model has actually finished generating."""
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                return client.chat_json(messages)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable_llm_error(exc):
                    raise
                time.sleep(1.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _retryable_llm_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            term in message
            for term in (
                "empty content",
                "unexpected_eof",
                "eof occurred",
                "connection reset",
                "temporarily unavailable",
                "timeout",
                # JSON 解析失败通常是输出被截断或格式偏差，重试有望成功
                "json parse failed",
                "jsondecodeerror",
                "unterminated string",
                "expecting value",
                "expecting property name",
            )
        )

    def _chat_with_overflow_recovery(
        self,
        client: Any,
        build_messages: Callable[[float], list[dict[str, Any]]],
        call: Callable[[Any, list[dict[str, Any]]], tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Call the LLM, retrying once with a halved context budget when the
        provider reports a context-length overflow.

        ``call`` receives ``(client, messages)`` — the same shape as
        ``client.chat_json(messages)`` / ``client.chat_tools(...)`` so a
        bound method like ``self._chat_json_with_retries`` (which takes
        ``client`` first) can be passed directly.
        """
        try:
            return call(client, build_messages(1.0))
        except Exception as exc:
            if not is_context_overflow_error(exc):
                raise
            self.last_error = f"context overflow; retrying with a tighter budget: {exc}"
            return call(client, build_messages(0.5))

    def _stream_plan(
        self,
        client: Any,
        build_messages: Callable[[float], list[dict[str, Any]]],
        on_reasoning: Callable[[str], None],
        window_scale: float = 1.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Plan through the streaming endpoint so reasoning tokens are
        delivered to the operator while the model thinks.

        Falls back to nothing itself: empty content / parse errors raise so
        callers can retry or degrade to the non-streaming path.
        """
        content: list[str] = []
        for event in _stream_events_with_first_chunk_retry(client, build_messages(window_scale)):
            token = str(event.get("token") or "")
            if not token:
                continue
            if event.get("type") == "reasoning":
                on_reasoning(token)
            else:
                content.append(token)
        text = "".join(content).strip()
        if not text:
            raise RuntimeError("LLM returned empty content")
        try:
            parsed = json.loads(_extract_json(text))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM JSON parse failed: {e}: {text[:300]}") from e
        return parsed, {}

    def supports_multimodal(self, model_id: str | None = None) -> bool:
        config = self._resolve_config(model_id)
        return self._enabled(config) and self._config_supports_multimodal(config)

    @staticmethod
    def _config_supports_multimodal(config: dict[str, Any] | None) -> bool:
        if not config:
            return False
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
        )
        return bool(inferred["multimodal"])

    def status(self) -> dict[str, Any]:
        default = self.registry.get_default()
        data: dict[str, Any] = {"models": self.registry.list_public()}
        if default:
            data.update({
                "provider": default.get("provider", ""),
                "base_url": default.get("base_url", ""),
                "model": default.get("model", ""),
                "enabled": self._enabled(default),
                "api_type": default.get("api_type", "openai"),
            })
        else:
            data.update({"provider": "", "base_url": "", "model": "", "enabled": False, "api_type": "openai"})
        data["last_error"] = self.last_error
        data["last_usage"] = self.last_usage
        return data

    def plan(
        self,
        command: str,
        tools: list[dict[str, Any]],
        safety: dict[str, Any],
        telemetry: dict[str, Any] | None,
        memory: dict[str, Any],
        model_id: str | None = None,
        backend: str = "",
        capabilities: dict[str, Any] | None = None,
        tool_cards: list[dict[str, Any]] | None = None,
        agent_state: dict[str, Any] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> MissionPlan:
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)

        available_tools = self._compact_tools(tools)
        capability_payload = capabilities or {}
        card_payload = self._compact_tool_cards(tool_cards or [])
        images = self._image_attachments(attachments)
        sections = [
            {"key": "operator_command", "value": command, "priority": "command"},
            {"key": "output_schema", "value": json.dumps(self._schema_hint(), ensure_ascii=False, default=str), "priority": "command"},
            {"key": "backend", "value": str(backend or ""), "priority": "observation"},
            {"key": "backend_capabilities", "value": json.dumps(capability_payload, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "safety_constraints", "value": json.dumps(safety, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "current_telemetry", "value": json.dumps(telemetry or {}, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "agent_state", "value": json.dumps(agent_state or {}, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "available_tools", "value": json.dumps(available_tools, ensure_ascii=False, default=str), "priority": "tool_cards"},
            {"key": "available_tool_cards", "value": json.dumps(card_payload, ensure_ascii=False, default=str), "priority": "tool_cards"},
            {
                "key": "skill_guidance",
                "value": json.dumps(self._compact_skill_guidance((agent_state or {}).get("skill_guidance") or []), ensure_ascii=False, default=str),
                "priority": "guidance",
            },
            {"key": "memory_snapshot", "value": json.dumps(self._compact_memory(memory), ensure_ascii=False, default=str), "priority": "memory"},
            {
                "key": "conversation_context",
                "value": json.dumps(self._compact_conversation(conversation_context or []), ensure_ascii=False, default=str),
                "priority": "memory",
            },
        ]
        sent_messages: list[list[dict[str, Any]]] = []

        def build_messages(window_scale: float = 1.0) -> list[dict[str, Any]]:
            payload_parts = self._sections_to_payload(self._fit_sections(config, sections, len(images), window_scale))
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": self._content_with_images(
                        json.dumps(payload_parts, ensure_ascii=False, indent=2, default=str),
                        attachments or [],
                    ),
                },
            ]
            sent_messages.append(messages)
            return messages

        try:
            client = _create_client(config)
            if on_reasoning is not None:
                # streamed planning: reasoning tokens reach the operator UI
                # while the model thinks (execute mode otherwise shows only
                # the static "正在理解指令..." placeholder for minutes)
                try:
                    parsed, usage = self._stream_plan(client, build_messages, on_reasoning)
                except Exception as exc:
                    if not is_context_overflow_error(exc):
                        # stream failed mid-flight (rare): fall back to the
                        # non-streaming path so planning is not lost
                        parsed, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
                    else:
                        self.last_error = f"context overflow; retrying with a tighter budget: {exc}"
                        parsed, usage = self._stream_plan(client, build_messages, on_reasoning, window_scale=0.5)
                self.last_error = ""
                self.last_usage = usage
                self._record_usage(sent_messages[-1] if sent_messages else [], usage)
                plan = self._plan_from_payload(command, parsed, {t["name"] for t in available_tools})
                if config:
                    plan.planner_source = str(config.get("provider", "llm"))
                    plan.planner_model = str(config.get("model", ""))
                return plan
            payload, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
            self.last_error = ""
            self.last_usage = usage
            self._record_usage(sent_messages[-1] if sent_messages else [], usage)
            plan = self._plan_from_payload(command, payload, {t["name"] for t in available_tools})
            if config:
                plan.planner_source = str(config.get("provider", "llm"))
                plan.planner_model = str(config.get("model", ""))
            return plan
        except Exception as e:
            self.last_error = str(e)
            raise LLMUnavailableError(f"LLM mission planning failed: {e}") from e

    def _image_attachments(self, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [
            item
            for item in (attachments or [])[:4]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]

    def _context_budget(self, config: dict[str, Any]) -> ContextBudget:
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
        )
        context_window = int(config.get("context_window") or inferred.get("context_window") or 64000)
        return ContextBudget(context_window=context_window, output_reserve=2048, meter=self._token_meter())

    def _token_meter(self) -> TokenMeter:
        meter = getattr(self, "token_meter", None)
        if meter is None:
            meter = TokenMeter()
            self.token_meter = meter
        return meter

    def _record_usage(self, messages: list[dict[str, Any]], usage: dict[str, Any], images: int = 0) -> None:
        """Recalibrate the token meter from the provider's real prompt count."""
        try:
            estimated = estimate_messages(messages, images=images)
            actual = int((usage or {}).get("prompt_tokens") or 0)
            self._token_meter().recalibrate(estimated, actual)
        except Exception:
            pass

    @staticmethod
    def _sections_to_payload(fitted: dict[str, str]) -> dict[str, Any]:
        """Rebuild a prompt payload dict from budgeted section strings.

        Truncated sections may no longer be valid JSON; they are passed through
        as raw text so the model still sees the available information.
        """
        payload: dict[str, Any] = {}
        for key, text in fitted.items():
            if not text or text == "[omitted]":
                payload[key] = text
                continue
            try:
                payload[key] = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                payload[key] = text
        return payload

    def confirm_target_in_image(
        self,
        target_description: str,
        image_base64: str,
        context: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Use the selected multimodal model to verify a target in one image."""
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
            message = f"模型不可用：{model_name} 未启用多模态能力，无法进行图像目标确认。"
            self.last_error = message
            raise LLMUnavailableError(message)

        target = target_description.strip() or "目标"
        payload = {
            "target_description": target,
            "context": context or {},
            "output_schema": {
                "target_found": "boolean, whether the requested target is visible",
                "confidence": "number 0..1",
                "target_label": "short label for the observed target or empty",
                "evidence": ["short visible evidence from the image"],
                "relative_direction": "center/left/right/top/bottom/top-left/top-right/bottom-left/bottom-right/unknown",
                "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null"},
                "recommended_next_action": "continue_search|approach|hold|reposition|insufficient_image",
                "summary_zh": "one concise Chinese sentence for the operator",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是无人机视觉任务的多模态目标确认器。"
                    "只根据图片和给定上下文判断目标是否可见，不要编造传感器数据或飞控状态。"
                    "如果目标只是可能存在但不清楚，target_found=false 或 confidence<0.6。"
                    "返回 JSON only。"
                ),
            },
            {
                "role": "user",
                "content": self._content_with_images(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    [{"data_url": self._data_url_from_base64(image_base64)}],
                ),
            },
        ]
        try:
            client = _create_client(config)
            try:
                parsed, usage = self._chat_json_with_retries(client, messages)
            except Exception:
                text, usage = client.chat_text(messages, max_tokens=700)
                parsed = json.loads(_extract_json(text))
            self.last_error = ""
            self.last_usage = usage
            return self._normalize_vlm_confirmation(parsed, target)
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def analyze_image(
        self,
        question: str,
        image_base64: str,
        context: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Use the selected multimodal model for open-ended image understanding."""
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "current model")
            message = f"模型不可用：{model_name} 未启用多模态能力，无法分析图像。"
            self.last_error = message
            raise LLMUnavailableError(message)

        prompt = question.strip() or "请描述无人机当前摄像头画面中可见的信息。"
        payload = {
            "operator_question": prompt,
            "context": context or {},
            "output_schema": {
                "summary_zh": "one concise Chinese answer to the operator",
                "visible_objects": ["short object or region labels visible in the image"],
                "target_candidates": [
                    {
                        "label": "candidate object label",
                        "confidence": "number 0..1",
                        "relative_direction": "center/left/right/top/bottom/top-left/top-right/bottom-left/bottom-right/unknown",
                        "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null"},
                        "evidence": "short visible evidence",
                    }
                ],
                "navigation_hint": "hold|reposition|approach_possible|insufficient_depth|unsafe",
                "safety_notes": ["short safety-relevant notes; empty if none"],
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a UAV camera image analyst. Answer only from the provided image and context. "
                    "Do not invent telemetry, GPS, depth, or vehicle state. "
                    "For navigation, only provide a hint; do not claim a 3D target position unless context contains one. "
                    "Return JSON only, with concise Chinese operator-facing text in summary_zh."
                ),
            },
            {
                "role": "user",
                "content": self._content_with_images(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    [{"data_url": self._data_url_from_base64(image_base64)}],
                ),
            },
        ]
        try:
            client = _create_client(config)
            try:
                parsed, usage = self._chat_json_with_retries(client, messages)
            except Exception:
                text, usage = client.chat_text(messages, max_tokens=900)
                parsed = json.loads(_extract_json(text))
            self.last_error = ""
            self.last_usage = usage
            return self._normalize_vlm_analysis(parsed, prompt)
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def final_answer(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = self._answer_payload(command, run_status, plan, telemetry, failure_reason, verification)
        if not self._enabled(config):
            return self._fallback_answer(payload)

        messages = self._answer_messages(payload)
        try:
            client = _create_client(config)
            text, usage = client.chat_text(messages)
            self.last_error = ""
            self.last_usage = usage
            return text
        except Exception as e:
            self.last_error = str(e)
            return self._fallback_answer(payload)

    def final_answer_stream(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
        model_id: str | None = None,
        on_token=None,
        on_reasoning=None,
        force_fallback: bool = False,
        should_stop=None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = self._answer_payload(command, run_status, plan, telemetry, failure_reason, verification)
        if force_fallback or not self._enabled(config):
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer

        messages = self._answer_messages(payload)
        chunks: list[str] = []
        try:
            client = _create_client(config)
            stream_events = getattr(client, "stream_events", None)
            if callable(stream_events):
                for event in stream_events(messages):
                    if callable(should_stop) and should_stop():
                        break
                    token = str(event.get("token") or "")
                    if not token:
                        continue
                    if event.get("type") == "reasoning":
                        if on_reasoning:
                            on_reasoning(token)
                        continue
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            else:
                for token in client.stream_text(messages):
                    if callable(should_stop) and should_stop():
                        break
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            self.last_error = ""
            answer = "".join(chunks).strip()
            if answer:
                return answer
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer
        except Exception as e:
            self.last_error = str(e)
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer

    def chat_response_stream(
        self,
        command: str,
        conversation: list[dict[str, Any]] | None,
        agent_state: dict[str, Any],
        memory: dict[str, Any],
        model_id: str | None = None,
        on_token=None,
        on_reasoning=None,
        attachments: list[dict[str, Any]] | None = None,
        should_stop=None,
        readonly_tools: list[dict[str, Any]] | None = None,
        execute_readonly_tool: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        on_tool_call: Callable[[str], None] | None = None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = {
            "operator_message": command,
            "agent_state": agent_state or {},
            "memory_snapshot": self._compact_memory(memory),
        }
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是这个控制台里的通用 Chat 助手。"
                    "优先回答用户当前问题；只有当问题明确涉及无人机、后端、任务、航点或状态时，才引用 agent_state。"
                    "不要把普通知识问答、图片理解、配置排错强行转成 AirSim/PX4 状态汇报。"
                    "Chat 模式不会执行飞控工具；如果用户要求起飞、移动、降落、搜索、跟踪等真实动作，提醒他切换到 Execute 模式。"
                    "如果用户上传图片并要求说明图片内容，直接根据图片作答；不要把用户附件当成无人机传感器画面，除非用户明确说明。"
                    "你可以调用提供的只读查询工具（状态/车辆列表）获取实时数据；"
                    "凡是涉及当前无人机状态、位置、数量的问题，必须先调用工具读取实时数据再回答，禁止仅凭上下文推测。"
                    "必须基于提供的信息作答，不要编造后端连接、车辆状态、位置或传感器结果。"
                    "可以参考 memory_snapshot.guidance 中的偏好和风险提示，但不要把历史任务当成当前事实。"
                    "中文回答，简洁自然。思考过程（reasoning）也用中文。"
                ),
            }
        ]
        for item in self._compact_conversation(conversation or []):
            role = "assistant" if item.get("role") == "assistant" else "user"
            messages.append({
                "role": role,
                "content": self._content_with_images(
                    str(item.get("content") or ""),
                    item.get("attachments") or [],
                ),
            })
        messages.append({
            "role": "user",
            "content": self._content_with_images(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                attachments or [],
            ),
        })

        # Read-only tool pre-round: let the model pull live data (status /
        # vehicle list) through function calling, fold the results back into
        # the context, then answer. Chat still never executes control tools —
        # the whitelist is enforced by the caller.
        if readonly_tools and execute_readonly_tool:
            try:
                probe_client = _create_client(config)
                tool_calls, _text, _usage = probe_client.chat_tools(messages, readonly_tools)
            except Exception:
                tool_calls = []
            executed: list[str] = []
            for call in (tool_calls or [])[:3]:
                name = str(call.get("name") or "").strip()
                if not name:
                    continue
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                if on_tool_call:
                    try:
                        on_tool_call(name)
                    except Exception:
                        pass
                try:
                    result = execute_readonly_tool(name, args)
                except Exception as exc:
                    result = {"ok": False, "data": {"status": "error", "message": str(exc)[:200]}}
                executed.append(f"{name}: {json.dumps(result, ensure_ascii=False, default=str)[:1600]}")
            if executed:
                messages.append({
                    "role": "user",
                    "content": "[只读工具实时查询结果]\n" + "\n".join(executed) + "\n请基于以上实时数据回答用户问题。",
                })

        chunks: list[str] = []
        try:
            client = _create_client(config)
            stream_events = getattr(client, "stream_events", None)
            if callable(stream_events):
                for event in _stream_events_with_first_chunk_retry(client, messages):
                    if callable(should_stop) and should_stop():
                        break
                    token = str(event.get("token") or "")
                    if not token:
                        continue
                    if event.get("type") == "reasoning":
                        if on_reasoning:
                            on_reasoning(token)
                        continue
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            else:
                for token in client.stream_text(messages):
                    if callable(should_stop) and should_stop():
                        break
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            self.last_error = ""
            return "".join(chunks).strip()
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def summarize_attempt(
        self,
        command: str,
        loop_state: dict[str, Any],
        model_id: str | None = None,
    ) -> str:
        """Model-generated final report when an agent loop exhausts its step
        budget (smolagents ``provide_final_answer`` pattern).

        Reviews the tool-call history, failure reason and current telemetry,
        then produces a concise operator-facing Chinese summary. Returns ""
        when the LLM is unavailable so callers keep the local template
        summary instead.
        """
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            return ""
        results = loop_state.get("results") or []
        tool_lines = []
        for item in results[-12:]:
            tool = str(item.get("tool") or "?")
            status = "成功" if item.get("ok") else "失败"
            tool_lines.append(f"- {tool}: {status}")
        world = loop_state.get("observations") or []
        latest_world = {}
        for observation in reversed(world):
            payload = observation.get("world_state") or {}
            if isinstance(payload, dict) and payload:
                latest_world = payload
                break
        drone = latest_world.get("drone") or {}
        position = drone.get("position_ned") or {}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是无人机地面站的执行总结助手。任务未在步数预算内完成，"
                    "请基于已完成/失败的工具调用和当前遥测，用简洁中文总结："
                    "已完成的部分、卡住的原因、当前飞行状态、建议的下一步。"
                    "不要虚构工具结果或遥测数据。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "operator_command": command,
                        "tool_call_history": tool_lines,
                        "failure_reason": loop_state.get("failure_reason", ""),
                        "current_telemetry": {
                            "position_ned": position,
                            "flying": drone.get("flying"),
                            "armed": drone.get("armed"),
                        },
                        "output_format": "2-4 句中文总结",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        try:
            client = _create_client(config)
            text, _usage = client.chat_text(messages, max_tokens=600)
            self.last_error = ""
            summary = (text or "").strip()
            return summary[:800] if summary else ""
        except Exception as exc:
            self.last_error = str(exc)
            return ""

    def _answer_payload(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "operator_command": command,
            "run_status": run_status,
            "failure_reason": failure_reason,
            "plan_summary": plan.summary if plan else "",
            "intent": plan.intent if plan else "",
            "current_telemetry": telemetry or {},
            "tool_results": self._compact_step_results(plan),
            "verification": verification or {},
        }

    def _answer_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是无人机任务助手，向操作员汇报任务执行结果。"
                    "基于给定的 telemetry 和 tool_results 作答，不要编造传感器数据。"
                    "用中文自然地总结任务执行情况与当前状态，风格自由发挥，"
                    "不要套用固定格式或模板，给出对操作员真正有用的总结即可。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            },
        ]

    def _system_prompt(self) -> str:
        return (
            "You are the high-level planner of an AirSim UAV VLA agent. "
            "Convert the operator command into a safe, auditable JSON mission plan. "
            "Only choose tools from available_tools. Use NED coordinates: z must be negative in the air. "
            "Never bypass the safety layer. Prefer conservative altitude, velocity, and geofence behavior. "
            "Do not add objective-changing actions that the operator did not ask for. "
            "Do not add photo, search, tracking, landing, disarm, reset, or disconnect steps unless explicitly requested. "
            "Memory is advisory only; never copy old mission endings into the current plan. "
            "Use memory_snapshot.guidance to prefer historically reliable tools/skills and to add caution notes for repeated failures; safety rules still dominate. "
            "drone_fly_to uses absolute local NED coordinates only. "
            "For relative body-frame commands such as forward/back/left/right/up/down, use drone_move_relative instead of guessing absolute coordinates. "
            "Use current_telemetry position_ned and heading_deg when reasoning about where the drone is. "
            "Use agent_state for backend readiness, connection health, active vehicle state, and active run context. "
            "Use conversation_context only to resolve references from the current command; never execute old requests again. "
            "If the operator says return to the previous start point, prefer memory_snapshot.session.last_task_start_position_ned when available; do not assume home unless memory is missing. "
            "If the task depends on current position or flight state, include drone_get_status before the action. "
            "For any flight mission, include connection and state readback. "
            "For takeoff/search/patrol, include drone_arm before drone_takeoff. "
            "MULTI-VEHICLE RULE: when the operator's command targets several or every vehicle (全部/所有/每架/三架无人机/机群), set vehicle_name='all' on EVERY flight step (arm, takeoff, move, land, hover) so all vehicles act together; a missing or empty vehicle_name only moves the default (first) vehicle. After multi-vehicle actions, add a readback step and verify EVERY vehicle reached the expected state before reporting success. "
            "Use skill_guidance as Markdown operating knowledge, not as callable tools. "
            "For short ordered UAV workflows, follow the flight_sequence guidance when present while still choosing native tools from available_tool_cards. "
            "For visual tasks, follow any relevant Markdown guidance, then use airsim_take_photo plus airsim_vlm_analyze_image for open-ended image descriptions or airsim_vlm_confirm_target for named targets. "
            "For geometric path requests such as square, rectangle, orbit, circle, grid, 正方形, 矩形, 绕圈, or 航线, prefer drone_fly_path when available. Compute conservative local NED waypoints from the latest observed position and avoid rotate-to-heading plus repeated drone_move_relative unless the operator specifically asks to point the camera. "
            "FORMATION / SWARM: when the operator asks for formation flight (编队、队形、swarm、formation) or area coverage (覆盖), use formation_command if available — do NOT land or re-takeoff vehicles that are already airborne; instead set_formation then takeoff(already flying? skip) then move_center. Plan formation tasks as a fixed sequence (auto), not agent_loop. Each formation_command step is synchronous: set_formation → (takeoff if not yet flying) → move_center → status-poll until stable. "
            "Check agent_state.vehicles[*].flying before every takeoff/land step: if a vehicle is already flying, skip the takeoff; if already landed, skip the land. Never re-land an already-grounded vehicle or re-takeoff an already-airborne one. "
            "Respect backend_capabilities and available_tool_cards. Never plan tools whose required capabilities are unavailable. "
            "Markdown skills are references, not executable shortcuts; do not prefer kind=skill unless a real executable skill card is explicitly available. "
            "For vague movement or scan requests, choose conservative distance, altitude, and velocity instead of expanding the mission. "
            "Treat kind=atomic tools as one direct operation. Tools with execution_mode=async only start an operation; the runtime owns task_id polling, timeout, cancellation, and terminal outcome. "
            "If a requested capability is unavailable, produce a safe status/readback plan and explain the limitation in risk_notes. "
            "The plan summary must state concrete numbers: vehicle count, coordinates, or altitude reached — never a bare '已完成/任务完成'."
            "Think and reason in Chinese (中文思考). Return JSON only."
        )

    def _schema_hint(self) -> dict[str, Any]:
        return {
            "intent": "short snake_case intent",
            "summary": "one concise Chinese summary",
            "reasoning": "REQUIRED. 2-4 句中文规划理由：任务理解、关键决策依据（为什么选这些工具/参数）",
            "assumptions": ["operator-visible assumptions"],
            "risk_notes": ["safety or ambiguity notes"],
            "execution_mode": (
                "auto | agent_loop. Use agent_loop when the task requires an "
                "observe-respond cycle that a fixed sequence cannot express: "
                "visual search/confirm targets, tracking, or any step whose "
                "outcome decides the next action. Use auto for fixed sequences "
                "such as takeoff -> waypoints -> land."
            ),
            "steps": [
                {
                    "label": "Chinese step label",
                    "tool": "one available tool name",
                    "layer": "tool|planning|perception|action|memory|safety",
                    "params": {"name": "value"},
                    "needs_observation": (
                        "true when this step's result must be observed before "
                        "the next step can be chosen (photo/VLM/detect steps)"
                    ),
                }
            ],
        }
