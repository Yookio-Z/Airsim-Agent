"""Unit tests for the pure LLM protocol helpers (llm_protocol.py)."""

from __future__ import annotations

import pytest

from src.agent.llm_protocol import (
    IMAGE_TOKEN_QUOTA,
    ContextBudget,
    TokenMeter,
    anthropic_tool_schema,
    estimate_messages,
    estimate_tokens,
    function_tool_schema,
    tool_schema_from_spec,
    validate_json_schema,
)


# ---------------------------------------------------------------------------
# validate_json_schema
# ---------------------------------------------------------------------------


def test_schema_accepts_valid_object():
    schema = {
        "type": "object",
        "required": ["action", "params"],
        "properties": {
            "action": {"type": "string"},
            "params": {"type": "object"},
            "count": {"type": "integer", "minimum": 0, "maximum": 10},
        },
    }
    assert validate_json_schema({"action": "drone_takeoff", "params": {"altitude": 3.0}, "count": 5}, schema) == []


def test_schema_reports_missing_required():
    schema = {"type": "object", "required": ["action"], "properties": {"action": {"type": "string"}}}
    violations = validate_json_schema({"params": {}}, schema)
    assert any("missing required" in v and ".action" in v for v in violations)


def test_schema_reports_type_mismatch():
    schema = {"type": "object", "properties": {"altitude": {"type": "number"}}}
    violations = validate_json_schema({"altitude": "three"}, schema)
    assert any("expected number" in v for v in violations)


def test_schema_enforces_ranges_and_enum():
    schema = {"type": "number", "minimum": 0.5, "maximum": 120}
    assert validate_json_schema(3.0, schema) == []
    assert any("below minimum" in v for v in validate_json_schema(0.1, schema))
    assert any("above maximum" in v for v in validate_json_schema(300, schema))
    enum_schema = {"type": "string", "enum": ["car", "truck"]}
    assert validate_json_schema("car", enum_schema) == []
    assert any("not in enum" in v for v in validate_json_schema("plane", enum_schema))


def test_schema_handles_arrays_and_nulls():
    schema = {"type": "array", "items": {"type": "string"}}
    assert validate_json_schema(["a", "b"], schema) == []
    assert any("expected string" in v for v in validate_json_schema(["a", 1], schema))
    assert validate_json_schema(None, {"type": "string", "x-nullable": True}) == []
    assert any("unexpected null" in v for v in validate_json_schema(None, {"type": "string"}))


def test_schema_none_or_empty_accepts_everything():
    assert validate_json_schema({"anything": [1, 2]}, None) == []
    assert validate_json_schema({"anything": 1}, {}) == []


# ---------------------------------------------------------------------------
# token estimation
# ---------------------------------------------------------------------------


def test_estimate_tokens_cjk_and_ascii():
    ascii_only = estimate_tokens("hello world this is a test")
    cjk_only = estimate_tokens("中文测试文本")
    assert ascii_only > 0
    assert cjk_only == 6  # one token per CJK char
    mixed = estimate_tokens("飞行到 100 米高度 fly to 100 meters")
    assert mixed > cjk_only


def test_estimate_tokens_serializes_non_strings():
    assert estimate_tokens({"a": 1}) > 0
    assert estimate_tokens(None) == 0


def test_estimate_messages_counts_images():
    messages = [
        {"role": "user", "content": "描述画面"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}]},
    ]
    total = estimate_messages(messages)
    assert total > estimate_tokens("描述画面") + IMAGE_TOKEN_QUOTA


def test_token_meter_recalibrates():
    meter = TokenMeter(alpha=0.5)
    before = meter.estimate("some prompt text")
    meter.recalibrate(before, before * 2)
    assert meter.estimate("some prompt text") > before
    meter.recalibrate(before, -1)  # invalid usage ignored
    assert meter.estimate("some prompt text") > before
    assert meter.recalibrations == 1


def test_token_meter_ignores_outliers():
    meter = TokenMeter(alpha=0.5)
    meter.recalibrate(100, 10_000)
    assert meter.recalibrations == 0


# ---------------------------------------------------------------------------
# context budget
# ---------------------------------------------------------------------------


def test_budget_keeps_essential_and_trims_low_priority():
    budget = ContextBudget(context_window=2000, output_reserve=256)
    sections = [
        {"key": "command", "value": "飞到 100 米高度并拍照", "priority": "command"},
        {"key": "observation", "value": "flying=True armed=True altitude=3.0", "priority": "observation"},
        {"key": "tool_cards", "value": "卡片内容 " * 200, "priority": "tool_cards"},
        {"key": "memory", "value": "历史记忆 " * 200, "priority": "memory"},
    ]
    fitted = budget.fit(sections)
    assert fitted["command"] == sections[0]["value"]
    assert fitted["observation"] == sections[1]["value"]
    # low priority sections must have been trimmed or omitted
    assert fitted["tool_cards"] != sections[2]["value"] or fitted["memory"] != sections[3]["value"]
    total = sum(estimate_tokens(v) for v in fitted.values())
    assert total <= budget.budget + 64  # essential sections may overflow slightly


def test_budget_accepts_overflow_for_essential():
    budget = ContextBudget(context_window=1024, output_reserve=256)
    huge_command = "指令内容 " * 500
    fitted = budget.fit([{"key": "command", "value": huge_command, "priority": "command"}])
    assert fitted["command"] == huge_command


def test_budget_omits_empties():
    budget = ContextBudget(context_window=2000, output_reserve=256)
    fitted = budget.fit([{"key": "a", "value": "", "priority": 0}, {"key": "b", "value": None, "priority": 3}])
    assert fitted["a"] == ""
    assert fitted["b"] == ""


# ---------------------------------------------------------------------------
# tool schema synthesis
# ---------------------------------------------------------------------------


def test_tool_schema_uses_flight_constraints():
    parameters = {"altitude": {"default": 3.0, "annotation": "float"}, "vehicle_name": {"default": None, "annotation": "str"}}
    schema = tool_schema_from_spec("drone_takeoff", parameters, {"altitude": "Takeoff altitude"})
    assert schema["properties"]["altitude"]["type"] == "number"
    assert schema["properties"]["altitude"]["minimum"] == 0.5
    assert schema["properties"]["altitude"]["maximum"] == 120
    assert schema["properties"]["vehicle_name"]["type"] == "string"


def test_tool_schema_uses_required_flag_and_card_descriptions():
    parameters = {
        "task_id": {"default": None, "annotation": "str", "required": True},
        "reason": {"default": None, "annotation": "str"},
    }
    schema = tool_schema_from_spec("airsim_task_cancel", parameters, {"task_id": "The task to cancel"})
    assert schema["required"] == ["task_id"]
    assert schema["properties"]["task_id"]["description"] == "The task to cancel"


def test_function_envelope_and_anthropic_conversion():
    parameters = {"type": "object", "properties": {"a": {"type": "string"}}}
    openai = function_tool_schema("drone_hover", "Hover in place", parameters)
    assert openai["type"] == "function"
    assert openai["function"]["name"] == "drone_hover"
    anthropic = anthropic_tool_schema("drone_hover", "Hover in place", parameters)
    assert anthropic["name"] == "drone_hover"
    assert anthropic["input_schema"] == parameters


def test_schema_limits_description_length():
    schema = tool_schema_from_spec(
        "drone_fly_to",
        {"x": {"default": 0.0, "annotation": "float"}},
        {"x": "d" * 500},
    )
    assert len(schema["properties"]["x"]["description"]) <= 160
