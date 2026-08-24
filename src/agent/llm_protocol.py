"""Pure LLM protocol helpers: schema validation, token metering, context budgeting,
and tool-schema synthesis.

These functions are intentionally side-effect free so they can be unit-tested
without any network, backend, or model configuration. The LLM planner imports
this module (never the other way around).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

# Per-image token quota used when counting multimodal attachments. OpenAI
# roughly charges ~85 tokens per 512x512 tile; a conservative flat quota keeps
# the budget honest without parsing image dimensions.
IMAGE_TOKEN_QUOTA = 850

# Smallest section size we bother truncating to; anything below is dropped.
_MIN_KEEP_CHARS = 300

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


# ---------------------------------------------------------------------------
# JSON Schema subset validation
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate_json_schema(value: Any, schema: dict[str, Any] | None) -> list[str]:
    """Validate ``value`` against a small JSON Schema subset.

    Supported keywords: type, properties, required, items, enum, minimum,
    maximum, x-nullable. Returns a list of human-readable violations; an empty
    list means valid. A missing/empty schema accepts anything.
    """
    if not schema:
        return []
    errors: list[str] = []
    _validate_node(value, schema, "$", errors)
    return errors


def _validate_node(value: Any, schema: dict[str, Any] | None, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict) or not schema:
        return
    if value is None:
        if schema.get("x-nullable"):
            return
        if schema.get("type") != "null":
            errors.append(f"{path}: unexpected null")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in enum {schema['enum']!r}")
        return
    expected = schema.get("type")
    if expected:
        check = _TYPE_CHECKS.get(expected)
        if check is None:
            errors.append(f"{path}: unsupported schema type {expected!r}")
            return
        if not check(value):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
    if expected in {"number", "integer"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} above maximum {schema['maximum']}")
    if expected == "object" and isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}.{key}: missing required field")
        for key, sub_schema in (schema.get("properties") or {}).items():
            if key in value and isinstance(sub_schema, dict):
                _validate_node(value[key], sub_schema, f"{path}.{key}", errors)
    elif expected == "array" and isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(value):
                _validate_node(item, items_schema, f"{path}[{index}]", errors)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: Any) -> int:
    """Rough deterministic token estimate.

    CJK characters cost 1 token each; the remaining text is charged at one
    token per 4 characters. JSON payloads are serialized with ensure_ascii=False
    so Chinese text is counted correctly.
    """
    if text is None:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, default=str)
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    rest_tokens = math.ceil(rest / 4.0) if rest else 0
    return cjk + rest_tokens


def estimate_messages(messages: list[dict[str, Any]], images: int = 0) -> int:
    """Estimate tokens for a chat message list, charging a flat quota per
    attached image (content blocks of type image_url or image)."""
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"image_url", "image"} or "image_url" in block:
                    total += IMAGE_TOKEN_QUOTA
                else:
                    total += estimate_tokens(block.get("text") or "")
        total += 4  # per-message structural overhead
    return total + images * IMAGE_TOKEN_QUOTA


class TokenMeter:
    """Token estimator with EMA recalibration from real usage reports.

    The estimator is deliberately coarse; after each LLM response the observed
    prompt token count recalibrates a global multiplier so future budgets stay
    close to the provider's own accounting. Outlier ratios are ignored.
    """

    def __init__(self, alpha: float = 0.3) -> None:
        self.alpha = alpha
        self._ratio = 1.0
        self.recalibrations = 0

    def estimate(self, text: Any) -> int:
        return max(1, round(estimate_tokens(text) * self._ratio))

    def recalibrate(self, estimated_prompt: int, actual_prompt: int) -> None:
        if estimated_prompt <= 0 or actual_prompt <= 0:
            return
        ratio = actual_prompt / estimated_prompt
        if ratio <= 0.1 or ratio > 10.0:
            return
        self._ratio = self._ratio * (1.0 - self.alpha) + ratio * self.alpha
        self.recalibrations += 1


# ---------------------------------------------------------------------------
# Context budgeting
# ---------------------------------------------------------------------------


class ContextBudget:
    """Total-context safety net layered on top of the field-level compactors.

    Sections carry a priority; the operator command and the current world
    observation are flight-critical and are never dropped (they may overflow
    the budget on very small windows, which is logged by the caller). Lower
    priority sections (tool cards, early history, memory/guidance) are
    truncated or omitted first.

    Priority order follows flight-domain information value:
      0 command, 1 observation/attachments, 2 recent steps,
      3 tool cards, 4 plan/history, 5 memory, 6 skill guidance.
    """

    PRIORITIES = {
        "command": 0,
        "observation": 1,
        "attachments": 1,
        "recent": 2,
        "tool_cards": 3,
        "plan": 4,
        "memory": 5,
        "guidance": 6,
    }
    ESSENTIAL = {0, 1}

    def __init__(self, context_window: int = 64_000, output_reserve: int = 2048, meter: TokenMeter | None = None) -> None:
        self.context_window = max(1024, int(context_window))
        self.output_reserve = max(256, int(output_reserve))
        self.meter = meter or TokenMeter()

    @property
    def budget(self) -> int:
        return max(1024, int(self.context_window * 0.7) - self.output_reserve)

    def with_reserve(self, extra_tokens: int) -> "ContextBudget":
        """Return a budget with extra output reserve (e.g. image token quota)."""
        return ContextBudget(self.context_window, self.output_reserve + max(0, int(extra_tokens)), self.meter)

    def fit(self, sections: list[dict[str, Any]]) -> dict[str, str]:
        """Trim ``sections`` (dicts with key/value/priority) to the budget.

        Returns {key: kept_text}. Sections whose value cannot fit are truncated
        to the remaining space when that is still useful, otherwise replaced by
        an "[omitted]" marker. Essential sections (priority <= 1) are always
        kept whole.
        """
        ordered = sorted(sections, key=lambda s: self._priority(s))
        result: dict[str, str] = {}
        used = 0
        for section in ordered:
            key = str(section.get("key") or "")
            value = str(section.get("value") or "")
            if not value or not key:
                result[key] = value
                continue
            priority = self._priority(section)
            estimated = self.meter.estimate(value)
            remaining = self.budget - used
            if estimated <= remaining:
                result[key] = value
                used += estimated
                continue
            if priority in self.ESSENTIAL:
                result[key] = value
                used += estimated
                continue
            allowed = max(0, remaining - 64)
            if allowed >= _MIN_KEEP_CHARS:
                result[key] = _truncate_to_tokens(value, allowed, self.meter)
                used += allowed
            else:
                result[key] = "[omitted]"
                used += 12
        return result

    @classmethod
    def _priority(cls, section: dict[str, Any]) -> int:
        raw = section.get("priority")
        if isinstance(raw, int):
            return max(0, raw)
        if isinstance(raw, str):
            return cls.PRIORITIES.get(raw, 6)
        return 6


def _truncate_to_tokens(text: str, target_tokens: int, meter: TokenMeter | None = None) -> str:
    """Return the longest prefix of ``text`` whose estimate fits ``target_tokens``."""
    if not text:
        return ""
    if (meter or TokenMeter()).estimate(text) <= target_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if (meter or TokenMeter()).estimate(text[:mid]) <= target_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low] + " ...[truncated]"


# ---------------------------------------------------------------------------
# Tool schema synthesis (function calling contract)
# ---------------------------------------------------------------------------

_TYPE_ALIASES = {
    "str": "string",
    "string": "string",
    "float": "number",
    "double": "number",
    "int": "integer",
    "integer": "integer",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "dict": "object",
    "object": "object",
    "any": "string",
}

# Manual type/range constraints for the high-value flight tools. The runtime
# only knows parameter names, defaults, and annotations; these constraints make
# the function-calling contract precise where it matters for safety.
FLIGHT_TOOL_CONSTRAINTS: dict[str, dict[str, dict[str, Any]]] = {
    "drone_takeoff": {
        "altitude": {"type": "number", "minimum": 0.5, "maximum": 120, "description": "Takeoff altitude in meters"},
        "vehicle_name": {"type": "string", "description": "Set 'all' to command EVERY vehicle (全部/所有无人机); a specific id targets one vehicle; empty means the default vehicle only"},
    },
    "drone_fly_to": {
        "x": {"type": "number", "description": "Target NED x (north, meters)"},
        "y": {"type": "number", "description": "Target NED y (east, meters)"},
        "z": {"type": "number", "description": "Target NED z (down, meters; negative altitude)"},
        "velocity": {"type": "number", "minimum": 0.2, "maximum": 20, "description": "Cruise velocity in m/s"},
        "vehicle_name": {"type": "string", "description": "Set 'all' to command EVERY vehicle (全部/所有无人机); a specific id targets one vehicle; empty means the default vehicle only"},
    },
    "drone_move_relative": {
        "forward_m": {"type": "number", "description": "Forward displacement in body frame, meters"},
        "right_m": {"type": "number", "description": "Right displacement in body frame, meters"},
        "up_m": {"type": "number", "description": "Up displacement in body frame, meters"},
        "velocity": {"type": "number", "minimum": 0.2, "maximum": 20, "description": "Velocity in m/s"},
        "vehicle_name": {"type": "string", "description": "Set 'all' to command EVERY vehicle (全部/所有无人机); a specific id targets one vehicle; empty means the default vehicle only"},
    },
    "drone_fly_velocity": {
        "vx": {"type": "number", "minimum": -20, "maximum": 20, "description": "Body-frame forward velocity m/s"},
        "vy": {"type": "number", "minimum": -20, "maximum": 20, "description": "Body-frame lateral velocity m/s"},
        "vz": {"type": "number", "minimum": -20, "maximum": 20, "description": "Body-frame vertical velocity m/s"},
    },
    "drone_fly_path": {
        "waypoints_json": {"type": "string", "description": "JSON array of local NED waypoints"},
        "velocity": {"type": "number", "minimum": 0.2, "maximum": 20, "description": "Cruise velocity in m/s"},
    },
    "skill:search": {
        "target_class": {"type": "string", "enum": ["car", "person", "truck", "bus", "drone", "target"]},
        "search_altitude": {"type": "number", "minimum": 0.5, "maximum": 120},
        "search_radius": {"type": "number", "minimum": 1.0, "maximum": 500},
        "max_steps": {"type": "integer", "minimum": 1, "maximum": 12},
        "scene_description": {"type": "string"},
    },
    "airsim_take_photo": {
        "image_type": {"type": "string", "enum": ["scene", "depth", "segmentation"]},
        "auto_save": {"type": "boolean"},
        "verify_target_class": {"type": "string"},
    },
    "airsim_vlm_confirm_target": {
        "target_description": {"type": "string", "description": "What to look for in the frame"},
        "source": {"type": "string", "enum": ["last_image", "capture_new"]},
    },
    "airsim_vlm_analyze_image": {
        "question": {"type": "string", "description": "Open question about the camera frame"},
        "source": {"type": "string", "enum": ["last_image", "capture_new"]},
    },
    "airsim_get_depth_map": {
        "camera_name": {"type": "string"},
        "return_vis": {"type": "boolean"},
    },
    "airsim_task_status": {
        "task_id": {"type": "string"},
    },
    "airsim_task_cancel": {
        "task_id": {"type": "string"},
    },
    "formation_command": {
        "action": {
            "type": "string",
            "enum": ["set_drones", "set_formation", "takeoff", "move_center", "rotate", "scale", "coverage_plan", "coverage_start", "hover_all", "land_all", "stop", "status"],
            "description": "Which formation/coverage action to perform",
        },
        "formation_type": {"type": "string", "enum": ["line", "v_shape", "triangle", "diamond", "square", "hexagon", "circle", "arrow"]},
        "spacing": {"type": "number", "minimum": 2.0, "maximum": 50.0, "description": "Formation spacing in meters (min 2m keeps inter-drone clearance)"},
        "altitude": {"type": "number", "minimum": 0.5, "maximum": 50.0, "description": "Takeoff altitude in meters"},
        "x": {"type": "number", "description": "Formation center x (north, meters)"},
        "y": {"type": "number", "description": "Formation center y (east, meters)"},
        "z": {"type": "number", "description": "Formation center z (down, meters; negative altitude)"},
        "angle_deg": {"type": "number", "description": "Rotation angle in degrees"},
        "scale_factor": {"type": "number", "minimum": 0.1, "maximum": 10.0},
        "area_shape": {"type": "string", "enum": ["rectangle", "circle"]},
        "area_width": {"type": "number", "minimum": 1.0, "maximum": 500.0},
        "area_height": {"type": "number", "minimum": 1.0, "maximum": 500.0},
        "area_radius": {"type": "number", "minimum": 1.0, "maximum": 500.0},
        "area_x": {"type": "number", "description": "Coverage area center x (north, meters)"},
        "area_y": {"type": "number", "description": "Coverage area center y (east, meters)"},
        "area_altitude": {"type": "number", "minimum": 0.5, "maximum": 50.0, "description": "Coverage altitude in meters"},
        "resolution": {"type": "number", "minimum": 1.0, "maximum": 50.0},
        "partition": {"type": "string", "enum": ["balanced", "stripe", "quadrant"]},
        "path_algo": {"type": "string", "enum": ["boustrophedon", "spiral", "nearest"]},
        "coverage_speed": {"type": "number", "minimum": 0.5, "maximum": 8.0},
        "vehicle_ids": {"type": "string", "description": "Comma-separated vehicle ids"},
    },
}

_TARGET_ENUM = ["car", "person", "truck", "bus", "drone", "target"]


def tool_schema_from_spec(
    name: str,
    parameters: dict[str, Any],
    card_inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Synthesize an object-parameter JSON Schema for one tool.

    Sources, in priority order:
      1. manual FLIGHT_TOOL_CONSTRAINTS entry (authoritative for flight tools);
      2. ToolSpec parameter metadata (annotation string -> JSON type, explicit
         ``required`` flag when the runtime records it);
      3. ToolCard.inputs description text for the property description.
    """
    overrides = FLIGHT_TOOL_CONSTRAINTS.get(name, {})
    card_inputs = card_inputs or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, spec in (parameters or {}).items():
        if not isinstance(spec, dict):
            continue
        override = overrides.get(key)
        if override is not None:
            prop = dict(override)
            if "description" not in prop:
                description = card_inputs.get(key)
                if description:
                    prop["description"] = description[:160]
            properties[key] = prop
        else:
            annotation = str(spec.get("annotation") or "").lower()
            prop: dict[str, Any] = {"type": _TYPE_ALIASES.get(annotation, "string")}
            description = card_inputs.get(key)
            if description:
                prop["description"] = description[:160]
            properties[key] = prop
        required_flag = spec.get("required")
        if required_flag is True:
            required.append(key)
        elif required_flag is None and spec.get("default") is None and "default" not in spec:
            required.append(key)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def function_tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Wrap a parameter schema in the OpenAI function-calling envelope."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(description or "")[:240],
            "parameters": parameters,
        },
    }


def anthropic_tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI function tool schema to the Anthropic tool shape."""
    return {
        "name": name,
        "description": str(description or "")[:240],
        "input_schema": parameters,
    }


def openai_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        converted.append(anthropic_tool_schema(str(fn.get("name") or ""), str(fn.get("description") or ""), fn.get("parameters") or {}))
    return converted


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        converted.append(function_tool_schema(str(tool["name"]), str(tool.get("description") or ""), tool.get("input_schema") or {}))
    return converted


def target_class_enum() -> list[str]:
    return list(_TARGET_ENUM)
