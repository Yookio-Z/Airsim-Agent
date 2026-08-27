"""上下文压缩与降级：long-context 装箱、裁剪、VLM 归一化、无 LLM 兜底输出。

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


class LLMPlannerCompactMixin:
    def _fit_sections(
        self,
        config: dict[str, Any],
        sections: list[dict[str, Any]],
        images: int = 0,
        window_scale: float = 1.0,
    ) -> dict[str, str]:
        """Fit prompt sections to the context budget, optionally scaled down.

        ``window_scale < 1`` is used by the context-overflow recovery path: the
        provider's real window is smaller than our estimate, so the whole
        budget is tightened instead of only the low-priority sections.
        """
        budget = self._context_budget(config)
        if window_scale < 1.0:
            budget = ContextBudget(
                context_window=max(1024, int(budget.context_window * window_scale)),
                output_reserve=2048,
                meter=self._token_meter(),
            )
        return budget.with_reserve(images * IMAGE_TOKEN_QUOTA).fit(sections)

    def _compact_loop_state(self, loop_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": loop_state.get("run_id", ""),
            "status": loop_state.get("status", ""),
            "step_count": len(loop_state.get("decisions") or []),
            "recent_decisions": (loop_state.get("decisions") or [])[-5:],
            "recent_results": (loop_state.get("results") or [])[-5:],
            "failure_reason": loop_state.get("failure_reason", ""),
        }

    def _compact_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        world = observation.get("world_state") or {}
        return {
            "step_index": observation.get("step_index", 0),
            "elapsed_since_start": observation.get("elapsed_since_start", 0),
            "frame_age_s": observation.get("frame_age_s"),
            "backend": world.get("backend", ""),
            "connected": world.get("connected", False),
            "stale_connection": world.get("stale_connection", False),
            "drone": self._trim_data(world.get("drone") or {}),
            "last_action_result": self._trim_data(observation.get("last_action_result") or {}),
        }

    def _fallback_loop_decision(
        self,
        command: str,
        loop_state: dict[str, Any],
        observation: dict[str, Any],
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision:
        lower = command.lower()
        slots = extract_command_slots(command)
        intents = extract_intents(command)
        world = observation.get("world_state") or {}
        drone = world.get("drone") or {}
        connected = bool(world.get("connected", False)) and not bool(world.get("stale_connection", False))
        last = observation.get("last_action_result") or {}
        if last and last.get("ok") is False:
            return LoopDecision(
                action="drone_get_status" if "drone_get_status" in allowed_tools and not self._loop_has_action(loop_state, "drone_get_status", after_failure=True) else "",
                reason="Previous action failed; refresh status before stopping.",
                is_complete="drone_get_status" not in allowed_tools or self._loop_has_action(loop_state, "drone_get_status", after_failure=True),
                reflection=str((last.get("data") or {}).get("message") or "previous action failed"),
            )

        wants_search = intents["search"]
        wants_track = intents["track"]
        wants_photo = intents["photo"]
        wants_visual = wants_search or wants_track or wants_photo
        wants_return = intents["return_home"] or slots.land is True
        wants_navigation = (
            slots.ned_target is not None
            or slots.relative_move is not None
            or any(k in lower for k in NAVIGATION_TERMS)
        )

        completed_navigation_skill = self._loop_has_action(loop_state, "skill:navigation") or self._loop_has_action(loop_state, "skill:return_home")
        completed_search_skill = self._loop_has_action(loop_state, "skill:search")
        if completed_search_skill and wants_track:
            result_text = json.dumps(loop_state.get("results") or [], ensure_ascii=False, default=str).lower()
            if "not_found" in result_text:
                return LoopDecision(
                    action="",
                    reason="Search completed without a target, so tracking cannot start.",
                    is_complete=False,
                    reflection="The target must be found and identified before a tracking operation is allowed.",
                )
        completed_vlm_confirm = self._loop_has_action(loop_state, "airsim_vlm_confirm_target")
        if completed_navigation_skill or (completed_search_skill and completed_vlm_confirm and not wants_track):
            return LoopDecision(
                action="",
                reason="The selected high-level visual workflow has already run and produced a confirmation.",
                is_complete=True,
                reflection="Skill result is available in the previous observation.",
            )
        if completed_search_skill and not completed_vlm_confirm and not self._loop_has_recent_image(loop_state):
            return LoopDecision(
                action="",
                reason="Search did not produce an image for multimodal target confirmation.",
                is_complete=False,
                reflection="No recent image is available for airsim_vlm_confirm_target.",
            )

        if not connected and "drone_connect" in allowed_tools and not self._loop_has_action(loop_state, "drone_connect"):
            return LoopDecision("drone_connect", {}, "Connect before advanced task execution.")
        if "drone_get_status" in allowed_tools and not self._loop_has_action(loop_state, "drone_get_status"):
            return LoopDecision("drone_get_status", {}, "Observe current vehicle state before acting.")

        if wants_visual and not (capabilities.get("target_search") or capabilities.get("image_capture") or capabilities.get("target_tracking")):
            return LoopDecision(
                action="",
                reason="Requested visual capability is unavailable on this backend.",
                is_complete=True,
                reflection="No visual/search/tracking tool is exposed for the active backend.",
            )

        target = slots.target_class or self._loop_target_class(command)
        altitude = slots.altitude or self._loop_altitude(command)
        radius = slots.radius or 25.0
        velocity = slots.velocity or 2.0
        if (
            wants_visual
            and "airsim_vlm_confirm_target" in allowed_tools
            and not completed_vlm_confirm
            and self._loop_has_recent_image(loop_state)
        ):
            return LoopDecision(
                "airsim_vlm_confirm_target",
                {"target_description": target, "source": "last_image"},
                "Use the multimodal model to confirm whether the latest captured frame contains the requested target.",
            )
        if wants_search and "skill:search" in allowed_tools and not completed_search_skill:
            return LoopDecision(
                "skill:search",
                {
                    "target_class": target,
                    "search_altitude": altitude,
                    "search_radius": radius,
                    "scene_description": command,
                },
                "Use the high-level search skill to reduce loop turns.",
            )
        if wants_return and "skill:return_home" in allowed_tools:
            return LoopDecision(
                "skill:return_home",
                {"altitude": altitude, "velocity": velocity, "land": True if slots.land is None else slots.land},
                "Use the return-home skill for a deterministic recovery sequence.",
            )
        if not wants_visual and wants_navigation and "skill:navigation" in allowed_tools:
            params: dict[str, Any] = {
                "altitude": altitude,
                "velocity": velocity,
                "hover_after": True if slots.hover_after is None else slots.hover_after,
            }
            if slots.ned_target:
                params.update(slots.ned_target)
            if slots.relative_move:
                params.update(slots.relative_move)
            return LoopDecision("skill:navigation", params, "Use the navigation skill for the requested movement.")

        needs_flight = wants_visual or any(k in lower for k in FLIGHT_TERMS)
        if needs_flight and not drone.get("armed") and "drone_arm" in allowed_tools and not self._loop_has_action(loop_state, "drone_arm"):
            return LoopDecision("drone_arm", {}, "Arm before airborne task execution.")
        if needs_flight and not drone.get("flying") and "drone_takeoff" in allowed_tools and not self._loop_has_action(loop_state, "drone_takeoff"):
            return LoopDecision("drone_takeoff", {"altitude": altitude}, "Take off to task altitude.")

        if wants_search and "skill:search" in allowed_tools and not self._loop_has_action(loop_state, "skill:search"):
            return LoopDecision(
                "skill:search",
                {"target_class": target, "search_altitude": altitude, "search_radius": radius},
                "Run a bounded visual search skill using atomic perception tools.",
            )
        if wants_track and "skill:track_object" in allowed_tools and not self._loop_has_action(loop_state, "skill:track_object"):
            return LoopDecision("skill:track_object", {"target_class": target}, "Track the requested target class through the skill layer.")
        if wants_photo and "airsim_take_photo" in allowed_tools and not self._loop_has_action(loop_state, "airsim_take_photo"):
            params: dict[str, Any] = {}
            if target:
                params["verify_target_class"] = target
            return LoopDecision("airsim_take_photo", params, "Capture a visual frame for verification.")

        return LoopDecision(
            action="",
            reason="The loop has completed all deterministic fallback actions.",
            is_complete=True,
            reflection="No further safe action is required.",
        )

    def _compact_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep = []
        for tool in tools:
            name = tool.get("name", "")
            if not name:
                continue
            params = tool.get("parameters", {})
            keep.append(
                {
                    "name": name,
                    "category": tool.get("category", ""),
                    "description": tool.get("description", ""),
                    "parameters": {
                        key: {"default": val.get("default"), "annotation": val.get("annotation", "")}
                        for key, val in list(params.items())[:12]
                        if isinstance(val, dict)
                    },
                }
            )
        return keep[:40]

    def _compact_tool_cards(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("name"):
                continue
            keep.append(
                {
                    "name": card.get("name", ""),
                    "purpose": card.get("purpose", ""),
                    "when_to_use": card.get("when_to_use", ""),
                    "inputs": card.get("inputs", {}),
                    "outputs": card.get("outputs", ""),
                    "preconditions": card.get("preconditions", []),
                    "required_capabilities": card.get("required_capabilities", []),
                    "subtools": card.get("subtools", []),
                    "failure_policy": card.get("failure_policy", ""),
                    "verification": card.get("verification", ""),
                    "not_for": card.get("not_for", ""),
                    "cost": card.get("cost", ""),
                    "risk": card.get("risk", ""),
                    "kind": card.get("kind", "atomic"),
                    "execution_mode": card.get("execution_mode", "immediate"),
                }
            )
        return keep[:40]

    def _compact_skill_guidance(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("name"):
                continue
            markdown = str(card.get("markdown") or "")
            if len(markdown) > 5000:
                markdown = markdown[:5000] + "\n..."
            keep.append(
                {
                    "name": card.get("name", ""),
                    "display_name": card.get("display_name", ""),
                    "description": card.get("description", ""),
                    "when_to_use": card.get("when_to_use", ""),
                    "required_capabilities": list(card.get("required_capabilities") or []),
                    "subtools": list(card.get("subtools") or []),
                    "markdown": markdown,
                    "executable": False,
                }
            )
        return keep[:4]

    def _compact_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": memory.get("session", {}),
            "recent_missions": memory.get("missions", [])[:5],
            "lessons": memory.get("lessons", [])[:5],
            "risk_events": memory.get("risk_events", [])[:5],
            "skill_candidates": memory.get("skill_candidates", [])[:5],
            "facts": memory.get("facts", [])[:5],
            "recent_runs": memory.get("runs", [])[:3],
            "guidance": memory.get("guidance", {}),
        }

    def _chat_unavailable_message(self, config: dict[str, Any] | None, reason: str) -> str:
        model_name = "未选择模型"
        if config:
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
        reason = self._sanitize_model_error(reason)
        if reason == "missing_api_key":
            return f"模型不可用：{model_name} 未配置或未启用 API Key。请检查模型设置或切换到可用模型。"
        return f"模型不可用：{model_name} 调用失败。{reason}"

    def _sanitize_model_error(self, reason: str) -> str:
        text = str(reason or "").strip()
        if not text:
            return "未返回具体错误。"
        match = re.match(r"LLM HTTP\s+(\d+):\s*(.*)", text, flags=re.S)
        if match:
            code, detail = match.group(1), match.group(2).strip()
            detail_text = detail
            try:
                parsed = json.loads(detail)
                error = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(error, dict):
                    parts = [str(error.get("message") or "").strip()]
                    if error.get("code"):
                        parts.append(f"code={error.get('code')}")
                    detail_text = "；".join(part for part in parts if part)
            except Exception:
                detail_text = detail
            detail_text = detail_text or "HTTP 请求失败"
            return f"HTTP {code}: {detail_text[:500]}"
        return text[:500]

    def _compact_conversation(self, conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in conversation[-10:]:
            if not isinstance(item, dict):
                continue
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            rows.append({
                "role": role,
                "content": content[:1600],
                "attachments": list(item.get("attachments") or [])[:4] if role == "user" else [],
            })
        return rows

    def _content_with_images(self, text: str, attachments: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        images = [
            item for item in attachments[:4]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]
        if not images:
            return text
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": str(item.get("data_url")), "detail": "auto"},
            })
        return content

    def _compact_step_results(self, plan: MissionPlan | None) -> list[dict[str, Any]]:
        if not plan:
            return []
        rows: list[dict[str, Any]] = []
        for step in plan.steps:
            rows.append(
                {
                    "id": step.id,
                    "label": step.label,
                    "tool": step.tool,
                    "params": step.params,
                    "status": step.status,
                    "result": self._trim_data(step.result or {}),
                }
            )
        return rows

    def _trim_data(self, data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, dict) and self._contains_image_data(data):
            trimmed = self._trim_image_payload(data)
            text = json.dumps(trimmed, ensure_ascii=False, default=str)
            if len(text) <= 1200:
                return trimmed
            return {"summary": text[:1200] + "...", "has_image": True}
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) <= 1200:
            return data
        return {"summary": text[:1200] + "..."}

    def _contains_image_data(self, value: Any) -> bool:
        if isinstance(value, dict):
            if any(key in value for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._contains_image_data(item) for item in value.values())
        if isinstance(value, list):
            return any(self._contains_image_data(item) for item in value)
        return False

    def _trim_image_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            keep: dict[str, Any] = {}
            for key, item in value.items():
                if key == "image_base64":
                    keep["has_image"] = bool(item)
                    keep["image_base64_bytes"] = len(str(item or ""))
                elif key in {
                    "status",
                    "message",
                    "target",
                    "target_class",
                    "target_description",
                    "vehicle",
                    "camera",
                    "image_type",
                    "image_saved_to",
                    "saved_to",
                    "approach_image_saved_to",
                    "selected_view",
                    "current_position",
                    "search_progress",
                    "detections",
                    "all_detections",
                    "target_world_position",
                    "target_depth_meters",
                    "target_distance_meters",
                    "vlm_confirmation",
                }:
                    keep[key] = self._trim_image_payload(item)
            return keep or {"has_image": self._contains_image_data(value)}
        if isinstance(value, list):
            return [self._trim_image_payload(item) for item in value[:5]]
        return value

    @staticmethod
    def _data_url_from_base64(image_base64: str) -> str:
        text = str(image_base64 or "").strip()
        if text.startswith("data:image/"):
            return text
        return f"data:image/png;base64,{text}"

    def _normalize_vlm_confirmation(self, payload: dict[str, Any], target: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        confidence = payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        found = bool(payload.get("target_found")) and confidence >= 0.5
        action = str(payload.get("recommended_next_action") or "").strip().lower()
        if action not in {"continue_search", "approach", "hold", "reposition", "insufficient_image"}:
            action = "approach" if found and confidence >= 0.65 else "continue_search"
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        bbox = payload.get("bbox_hint") or {}
        if not isinstance(bbox, dict):
            bbox = {}
        summary = str(payload.get("summary_zh") or "").strip()
        if not summary:
            summary = f"图像中{'发现' if found else '未确认发现'}目标“{target}”，置信度 {confidence:.2f}。"
        return {
            "status": "target_confirmed" if found else "target_not_confirmed",
            "target_found": found,
            "confidence": round(confidence, 3),
            "target_description": target,
            "target_label": str(payload.get("target_label") or ""),
            "evidence": [str(item)[:160] for item in evidence[:5]],
            "relative_direction": str(payload.get("relative_direction") or "unknown"),
            "bbox_hint": {
                "x": bbox.get("x"),
                "y": bbox.get("y"),
            },
            "recommended_next_action": action,
            "summary_zh": summary,
        }

    def _normalize_vlm_analysis(self, payload: dict[str, Any], question: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        visible = payload.get("visible_objects") or []
        if not isinstance(visible, list):
            visible = [str(visible)]
        candidates = payload.get("target_candidates") or []
        if not isinstance(candidates, list):
            candidates = []
        normalized_candidates: list[dict[str, Any]] = []
        for item in candidates[:6]:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence", 0.0)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.0
            bbox = item.get("bbox_hint") or {}
            if not isinstance(bbox, dict):
                bbox = {}
            normalized_candidates.append({
                "label": str(item.get("label") or "")[:80],
                "confidence": round(confidence, 3),
                "relative_direction": str(item.get("relative_direction") or "unknown"),
                "bbox_hint": {"x": bbox.get("x"), "y": bbox.get("y")},
                "evidence": str(item.get("evidence") or "")[:220],
            })
        safety_notes = payload.get("safety_notes") or []
        if not isinstance(safety_notes, list):
            safety_notes = [str(safety_notes)]
        hint = str(payload.get("navigation_hint") or "hold").strip().lower()
        if hint not in {"hold", "reposition", "approach_possible", "insufficient_depth", "unsafe"}:
            hint = "hold"
        summary = str(payload.get("summary_zh") or "").strip()
        if not summary:
            summary = "已获取摄像头画面，但模型没有返回明确的图像描述。"
        return {
            "status": "image_analyzed",
            "question": question,
            "summary_zh": summary,
            "message": summary,
            "visible_objects": [str(item)[:100] for item in visible[:10]],
            "target_candidates": normalized_candidates,
            "navigation_hint": hint,
            "safety_notes": [str(item)[:180] for item in safety_notes[:5]],
        }

    def _fallback_chat_answer(self, payload: dict[str, Any]) -> str:
        command = str(payload.get("operator_message") or "")
        state = payload.get("agent_state") or {}
        vehicle = state.get("vehicle") or {}
        lower = command.lower()
        wants_status = any(word in lower for word in ["status", "state", "telemetry", "状态", "汇报", "连接", "在哪", "位置"])
        wants_action = any(word in lower for word in [
            "takeoff", "land", "move", "fly", "search", "track", "起飞", "降落", "移动", "飞到", "搜索", "跟踪", "执行"
        ])

        backend = state.get("backend_name") or state.get("backend") or "unknown"
        connected = "已连接" if state.get("connected") else "未连接"
        if state.get("stale_connection"):
            connected = "连接异常或状态过期"
        pos = vehicle.get("position_ned") if isinstance(vehicle, dict) else None
        pos_text = ""
        if isinstance(pos, dict):
            pos_text = f"，NED 位置 x={pos.get('x', '?')}, y={pos.get('y', '?')}, z={pos.get('z', '?')}"
        flight_bits: list[str] = []
        if isinstance(vehicle, dict):
            if "armed" in vehicle:
                flight_bits.append(f"armed={vehicle.get('armed')}")
            if "flying" in vehicle:
                flight_bits.append(f"flying={vehicle.get('flying')}")
            if "has_collided" in vehicle:
                flight_bits.append(f"has_collided={vehicle.get('has_collided')}")
        vehicle_text = f"，车辆状态: {', '.join(flight_bits)}" if flight_bits else ""

        if wants_status:
            return f"当前后端是 {backend}，状态：{connected}{vehicle_text}{pos_text}。这是 Chat 模式汇报，没有执行任何飞控工具。"
        if wants_action:
            return "我现在处于 Chat 模式，不会执行飞控动作。要真正起飞、移动、搜索或降落，请切换到 Execute 模式后再发送指令。"
        return f"我在 Chat 模式，可以帮你解释任务、检查会话上下文和汇报当前状态；当前后端 {backend}，{connected}。"

    def _fallback_answer(self, payload: dict[str, Any]) -> str:
        telemetry = payload.get("current_telemetry") or {}
        command = str(payload.get("operator_command") or "")
        status = str(payload.get("run_status") or "")
        summary = str(payload.get("plan_summary") or "任务")
        failure = str(payload.get("failure_reason") or "")
        verification = payload.get("verification") or {}
        tool_results = payload.get("tool_results") or []
        is_agent_loop = str(payload.get("intent") or "") == "agent_loop"
        tool_names = [
            str(row.get("tool") or "")
            for row in tool_results
            if isinstance(row, dict)
        ] if isinstance(tool_results, list) else []
        has_flight_action = any(
            name.startswith("drone_") and name not in {"drone_connect", "drone_get_status", "drone_list_vehicles"}
            for name in tool_names
        )

        if not is_agent_loop and not has_flight_action:
            for row in reversed(tool_results if isinstance(tool_results, list) else []):
                if not isinstance(row, dict):
                    continue
                if row.get("tool") not in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target"}:
                    continue
                result = row.get("result") if isinstance(row.get("result"), dict) else {}
                message = str(result.get("summary_zh") or result.get("message") or "").strip()
                if message:
                    return message

        wants_status = any(word in command.lower() for word in ["status", "state", "telemetry", "状态", "汇报"])
        if wants_status and isinstance(telemetry, dict):
            pos = telemetry.get("position_ned") or {}
            vel = telemetry.get("velocity_ned") or {}
            altitude = abs(float(pos.get("z") or 0))
            speed = (
                float(vel.get("vx") or 0) ** 2
                + float(vel.get("vy") or 0) ** 2
                + float(vel.get("vz") or 0) ** 2
            ) ** 0.5
            armed = "已解锁" if telemetry.get("armed") else "未解锁"
            flying = "空中飞行" if telemetry.get("flying") else "地面/未飞行"
            collision = "发生碰撞" if telemetry.get("has_collided") else "未检测到碰撞"
            heading = float(telemetry.get("heading_deg") or 0)
            return (
                f"当前无人机{armed}，状态为{flying}。"
                f"高度约 {altitude:.1f} m，NED 位置为 N {float(pos.get('x') or 0):.1f} / "
                f"E {float(pos.get('y') or 0):.1f} / D {float(pos.get('z') or 0):.1f}，"
                f"速度约 {speed:.1f} m/s，航向 {heading:.1f}°。"
                f"{collision}。"
            )

        if (is_agent_loop or has_flight_action) and isinstance(tool_results, list) and tool_results:
            report = self._fallback_agent_loop_report(command, status, summary, failure, verification, telemetry, tool_results)
            if report:
                return report

        if status == "completed":
            if str(payload.get("intent") or "") == "agent_loop":
                summary_text = summary.strip()
                if summary_text and summary_text.lower() not in {"agent loop task", "l3 agent loop task", "agent loop completed", "done", "complete"}:
                    return summary_text
            if verification.get("status") == "failed":
                return f"{summary}执行后校验未通过：{verification.get('summary', '最终状态与任务目标不一致')}。"
            return f"{summary}已完成。所有关键工具调用已经结束，当前状态已回读并写入任务记录。"
        if status in {"failed", "blocked"}:
            return f"{summary}未完成：{failure or '执行被中断或安全层阻止'}。"
        if status == "planned":
            return f"我已经完成规划：{summary}。当前尚未执行仿真动作。"
        return f"我已收到任务：{summary}，正在推进执行。"

    def _fallback_agent_loop_report(
        self,
        command: str,
        status: str,
        summary: str,
        failure: str,
        verification: dict[str, Any],
        telemetry: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        label_map = {
            "drone_get_status": "读取状态",
            "drone_set_mode": "切换模式",
            "drone_arm": "解锁电机",
            "drone_takeoff": "起飞",
            "drone_move_relative": "相对移动",
            "drone_fly_to": "返航/飞至目标点",
            "drone_fly_path": "路径飞行",
            "drone_land": "降落",
            "drone_hover": "悬停",
            "airsim_take_photo": "拍摄图像",
            "airsim_vlm_analyze_image": "图像分析",
            "airsim_vlm_confirm_target": "目标确认",
        }
        completed: list[str] = []
        failed: list[str] = []
        image_text = ""
        for row in tool_results:
            if not isinstance(row, dict):
                continue
            tool = str(row.get("tool") or "")
            label = label_map.get(tool, tool)
            if row.get("status") == "completed":
                completed.append(label)
            elif row.get("status") == "failed":
                failed.append(label)
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if tool in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target", "airsim_take_photo"}:
                image_text = str(
                    result.get("summary_zh")
                    or result.get("message")
                    or result.get("summary")
                    or image_text
                ).strip()
        chain = "、".join(dict.fromkeys(completed)) or "已有工具调用"
        failed_text = f"失败或未确认步骤：{'、'.join(dict.fromkeys(failed))}。" if failed else ""

        pos = telemetry.get("position_ned") if isinstance(telemetry, dict) else {}
        pos = pos if isinstance(pos, dict) else {}
        altitude = abs(self._safe_float(pos.get("z"), self._safe_float(telemetry.get("altitude_m"), 0.0)) if isinstance(telemetry, dict) else 0.0)
        armed = "已解锁" if isinstance(telemetry, dict) and telemetry.get("armed") else "未解锁"
        flying = "飞行中" if isinstance(telemetry, dict) and telemetry.get("flying") else "未飞行/已落地"
        collision = "发生碰撞" if isinstance(telemetry, dict) and telemetry.get("has_collided") else "未检测到碰撞"
        final_state = (
            f"最终状态：{armed}，{flying}，高度约 {altitude:.1f} m，"
            f"NED 位置 N {self._safe_float(pos.get('x')):.1f} / E {self._safe_float(pos.get('y')):.1f} / D {self._safe_float(pos.get('z')):.1f}，{collision}。"
        )

        limit_note = ""
        if "max_steps" in failure:
            limit_note = "Agent 已停止继续决策，并根据已收集结果生成报告。"
        elif status in {"failed", "blocked"} and failure:
            limit_note = f"任务未完全收口：{failure}。"

        verify_note = ""
        if isinstance(verification, dict):
            if verification.get("level") == "failed":
                verify_note = f"校验提示：{verification.get('summary') or '关键状态未达到目标'}。"
            elif verification.get("level") == "warning":
                verify_note = "状态已回读，存在轻微误差但未发现阻断性失败。"

        image_text = image_text.rstrip("。.!！?？")
        image_note = f"图像结果：{image_text}。" if image_text else "图像结果：没有拿到明确的视觉分析摘要。"
        done_prefix = "任务已完成。" if status == "completed" else "任务执行已停止在当前阶段。"
        return " ".join(part for part in [
            done_prefix,
            f"执行链路：{chain}。",
            image_note if any(word in command for word in ["拍照", "图像", "目标", "扫描", "看看"]) else "",
            final_state,
            failed_text,
            verify_note,
            limit_note,
        ] if part).strip()

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
