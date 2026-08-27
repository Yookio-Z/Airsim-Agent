"""payload/命令规范化：_plan_from_payload、执行模式解析、步骤归一化与过滤。

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


class LLMPlannerPayloadMixin:
    def _plan_from_payload(self, command: str, payload: dict[str, Any], known_tools: set[str]) -> MissionPlan:
        run_id = f"run_{int(time.time() * 1000)}"
        steps: list[MissionStep] = []
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list):
            raise RuntimeError("LLM plan field 'steps' must be a list")

        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool", "")).strip()
            if tool not in known_tools and tool != "memory_store":
                continue
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=str(raw.get("label") or tool),
                    tool=tool,
                    params=params,
                    layer=str(raw.get("layer") or "tool"),
                    needs_observation=bool(raw.get("needs_observation")),
                )
            )

        steps = self._normalize_steps(command, steps, known_tools)
        if not steps:
            raise RuntimeError("LLM produced no executable known-tool steps")

        return MissionPlan(
            run_id=run_id,
            command=command,
            intent=str(payload.get("intent") or "llm_mission"),
            summary=str(payload.get("summary") or "LLM 生成任务计划"),
            steps=steps,
            assumptions=[str(x) for x in payload.get("assumptions", []) if isinstance(x, (str, int, float))],
            planner_source="llm",
            planner_model="",
            reasoning=str(payload.get("reasoning") or ""),
            risk_notes=[str(x) for x in payload.get("risk_notes", []) if isinstance(x, (str, int, float))],
            execution_mode=self._resolve_execution_mode(command, payload.get("execution_mode")),
            goal=self._goal_from_payload(payload.get("goal"), command),
        )

    @staticmethod
    def _resolve_execution_mode(command: str, payload_mode: Any) -> str:
        """Force plan-execute (auto) for formation/swarm tasks.

        Formation flight is a fixed sequence of formation_command calls
        (set_formation → takeoff → move_center → status-poll). The LLM may
        incorrectly choose agent_loop for it (because of the status-poll step),
        which makes the turn needlessly slow. Use auto for fixed sequences.
        """
        text = str(command or "").lower()
        formation_terms = ("formation", "swarm", "编队", "队形", "coverage", "覆盖", "区域扫描", "网格扫描", "分区扫描")
        if any(term in text for term in formation_terms):
            return "auto"
        raw = str(payload_mode or "").strip().lower()
        return raw if raw in {"auto", "agent_loop"} else "auto"

    VERIFY_METRICS = {
        "target_confirmed",
        "position_reached",
        "flying_at",
        "landed",
        "photo_taken",
        "status_ok",
        "mission_progress_complete",
        "formation_stable",
    }

    def _goal_from_payload(self, raw_goal: Any, command: str) -> dict[str, Any]:
        """Parse the LLM-proposed task contract, keeping only verifiable metrics.

        Unknown metrics are dropped; LLM proposals never widen the verifier's
        vocabulary (fail-safe: a metric the runtime cannot check is not a
        completion gate).
        """
        if not isinstance(raw_goal, dict):
            raw_goal = {}
        criteria: list[dict[str, Any]] = []
        raw_criteria = raw_goal.get("success_criteria") or []
        if isinstance(raw_criteria, list):
            for item in raw_criteria:
                if not isinstance(item, dict):
                    continue
                metric = str(item.get("metric") or "").strip()
                if metric not in self.VERIFY_METRICS:
                    continue
                cleaned: dict[str, Any] = {"metric": metric}
                if metric == "position_reached":
                    try:
                        cleaned.update(
                            {
                                "x": float(item.get("x") or 0.0),
                                "y": float(item.get("y") or 0.0),
                                "z": self._ned_z(item.get("z"), -3.0),
                                "tolerance": float(item.get("tolerance") or 1.5),
                            }
                        )
                    except (TypeError, ValueError):
                        continue
                elif metric == "flying_at":
                    try:
                        cleaned["altitude"] = abs(float(item.get("altitude") or 3.0))
                        cleaned["tolerance"] = float(item.get("tolerance") or 1.0)
                    except (TypeError, ValueError):
                        continue
                elif metric == "target_confirmed":
                    cleaned["target"] = str(item.get("target") or "")
                criteria.append(cleaned)
        return {
            "objective": str(raw_goal.get("objective") or command)[:200],
            "target": str(raw_goal.get("target") or ""),
            "success_criteria": criteria,
        }

    def _normalize_steps(self, command: str, steps: list[MissionStep], known_tools: set[str]) -> list[MissionStep]:
        steps = self._filter_unsolicited_steps(command, steps)
        normalized: list[MissionStep] = []
        for step in steps:
            step.params = dict(step.params or {})
            if step.tool == "drone_takeoff":
                step.params["altitude"] = self._positive_float(step.params.get("altitude"), 3.0)
            elif step.tool == "drone_fly_to":
                if "z" in step.params:
                    step.params["z"] = self._ned_z(step.params.get("z"), -3.0)
                step.params.setdefault("velocity", 2.0)
            elif step.tool == "drone_move_relative":
                step.params["forward_m"] = float(step.params.get("forward_m", 0.0) or 0.0)
                step.params["right_m"] = float(step.params.get("right_m", 0.0) or 0.0)
                step.params["up_m"] = float(step.params.get("up_m", 0.0) or 0.0)
                step.params["velocity"] = self._positive_float(step.params.get("velocity"), 2.0)
            elif step.tool == "drone_fly_path":
                waypoints = step.params.get("waypoints_json")
                if not isinstance(waypoints, str):
                    waypoints = step.params.get("waypoints")
                if not isinstance(waypoints, str):
                    try:
                        waypoints = json.dumps(waypoints or [], ensure_ascii=False, separators=(",", ":"))
                    except (TypeError, ValueError):
                        waypoints = "[]"
                step.params["waypoints_json"] = waypoints
                step.params["velocity"] = self._positive_float(step.params.get("velocity"), 1.5)
            elif step.tool in {"skill:search", "airsim_search_target"}:
                if step.tool == "airsim_search_target" and "skill:search" in known_tools:
                    step.tool = "skill:search"
                    step.layer = "planning"
                step.params["search_altitude"] = self._positive_float(step.params.get("search_altitude"), 3.0)
                step.params["search_radius"] = self._positive_float(step.params.get("search_radius"), 25.0)
                target_class = self._command_target_class(command)
                if target_class:
                    step.params.setdefault("target_class", target_class)
                step.params.setdefault("max_steps", 4)
                step.params.setdefault("scene_description", command)
            elif step.tool == "airsim_vlm_confirm_target":
                target_class = self._command_target_class(command)
                step.params.setdefault("target_description", target_class or command)
                step.params.setdefault("source", "last_image")
            normalized.append(step)

        memory_steps = [s for s in normalized if s.tool == "memory_store"]
        normalized = [s for s in normalized if s.tool != "memory_store"]
        visual_tools = {"airsim_take_photo", "airsim_detect_objects"}
        wants_open_image_analysis = self._command_has_any(
            command,
            ["what", "describe", "scene", "see", "look", "有什么", "看到", "看看", "画面", "照片", "图像内容"],
        )
        needs_vlm_analyze = (
            "airsim_vlm_analyze_image" in known_tools
            and not any(s.tool in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target"} for s in normalized)
            and any(s.tool in visual_tools for s in normalized)
            and wants_open_image_analysis
        )
        needs_vlm_confirm = (
            "airsim_vlm_confirm_target" in known_tools
            and not any(s.tool == "airsim_vlm_confirm_target" for s in normalized)
            and not needs_vlm_analyze
            and any(s.tool in visual_tools for s in normalized)
            and self._command_has_any(command, ["search", "find", "detect", "识别", "搜索", "寻找", "找", "目标", "photo", "image", "拍照", "图像"])
        )
        if needs_vlm_analyze:
            normalized.append(
                MissionStep(
                    "s00",
                    "Analyze camera image",
                    "airsim_vlm_analyze_image",
                    {"question": command, "source": "last_image"},
                    "perception",
                )
            )
        if needs_vlm_confirm:
            target_class = self._command_target_class(command)
            normalized.append(
                MissionStep(
                    "s00",
                    "Confirm visual target",
                    "airsim_vlm_confirm_target",
                    {"target_description": target_class or command, "source": "last_image"},
                    "perception",
                )
            )
        has_control = any(s.tool.startswith("drone_") or s.tool.startswith("airsim_") or s.tool.startswith("skill:") for s in normalized)
        core_tools = [s.tool for s in normalized if s.tool != "memory_store"]
        if has_control and "drone_get_status" in known_tools and (not core_tools or core_tools[-1] != "drone_get_status"):
            normalized.append(MissionStep("s00", "Read final status", "drone_get_status", {}, "perception"))
        if memory_steps:
            normalized.append(memory_steps[0])
        else:
            normalized.append(MissionStep("s00", "Store mission memory", "memory_store", {"source": "mission"}, "memory"))

        for index, step in enumerate(normalized, 1):
            step.id = f"s{index:02d}"
        return normalized

    def _command_has_any(self, command: str, words: list[str]) -> bool:
        lower = command.lower()
        return any(word in lower for word in words)

    def _positive_float(self, value: Any, default: float) -> float:
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            return default
        return number or default

    def _ned_z(self, value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return -abs(number) if number > 0 else number

    def _mission_takeoff_altitude(self, steps: list[MissionStep]) -> float:
        for step in steps:
            if step.tool in {"skill:search", "airsim_search_target"}:
                return self._positive_float(step.params.get("search_altitude"), 3.0)
            if step.tool == "drone_takeoff":
                return self._positive_float(step.params.get("altitude"), 3.0)
        return 3.0

    def _command_target_class(self, command: str) -> str:
        return extract_target_class(command)

    def _filter_unsolicited_steps(self, command: str, steps: list[MissionStep]) -> list[MissionStep]:
        lower = command.lower()
        intents = extract_intents(command)

        def has_any(words: list[str]) -> bool:
            return any(w in lower for w in words)

        wants_photo = intents["photo"]
        wants_search = intents["search"]
        wants_track = intents["track"]
        wants_land = intents["land"] or intents["return_home"]
        wants_takeoff = intents["takeoff"]
        wants_disarm = has_any(["disarm", "锁定电机", "锁电机"])
        wants_disconnect = has_any(["disconnect", "断开"])

        filtered: list[MissionStep] = []
        for step in steps:
            tool = step.tool
            if tool == "drone_takeoff" and wants_land and not (wants_takeoff or wants_search or wants_photo or wants_track):
                continue
            if tool == "airsim_take_photo" and not wants_photo:
                continue
            if tool in {"airsim_detect_objects", "airsim_get_depth_map"} and not (wants_photo or wants_search):
                continue
            if tool == "airsim_search_target":
                continue
            if tool == "skill:search" and not wants_search:
                continue
            if tool in {"airsim_track_object", "airsim_approach_target"}:
                continue
            if tool == "drone_land" and not wants_land:
                continue
            if tool == "drone_disarm" and not (wants_land or wants_disarm):
                continue
            if tool == "drone_disconnect" and not wants_disconnect:
                continue
            filtered.append(step)
        return filtered
