from __future__ import annotations

import time
from typing import Any

from src.agent.tool_cards import cards_for_capabilities
from src.agent.tool_executor import ToolCallResult, ToolRuntime
from src.agent.skill_registry import SkillRegistry
from src.tools.manifest import manifest_for


def _result(tool: str, ok: bool, data: dict[str, Any]) -> ToolCallResult:
    now = time.time()
    return ToolCallResult(tool, {}, ok, data, now, now)


class FakeTools:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self.names]

    def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
        raise AssertionError(f"unexpected direct tool execution: {name} {params}")


def test_tool_manifest_marks_core_commands_as_atomic() -> None:
    takeoff = manifest_for("drone_takeoff")
    status = manifest_for("drone_get_status")

    assert takeoff is not None
    assert takeoff.kind == "atomic"
    assert takeoff.surface == "vehicle_command"
    assert takeoff.recommended_layer == "tool"

    assert status is not None
    assert status.kind == "atomic"
    assert status.surface == "telemetry"


def test_tool_manifest_marks_workflows_as_skill_candidates() -> None:
    search = manifest_for("airsim_search_target")
    formation = manifest_for("airsim_formation_mission")

    assert search is not None
    assert search.kind == "workflow"
    assert search.recommended_layer == "skill"
    assert search.replacement_skill == "skill:search"

    assert formation is not None
    assert formation.kind == "workflow"
    assert formation.replacement_skill == "skill:formation_mission"


def test_skill_registry_starts_without_legacy_runtime_skills() -> None:
    registry = SkillRegistry()
    cards = {card["name"]: card for card in registry.all_cards()}
    docs = {card["name"]: card for card in registry.doc_cards()}

    assert cards == {}
    assert "skill:flight_sequence" in docs
    assert docs["skill:flight_sequence"]["executable"] is False
    assert "drone_takeoff" in docs["skill:flight_sequence"]["subtools"]
    assert registry.available_cards({"flight_control": True, "telemetry": True}) == []
    assert [card["name"] for card in registry.guidance_cards("起飞拍照返航降落", {"flight_control": True, "telemetry": True})] == ["skill:flight_sequence"]


def test_skill_registry_can_opt_in_to_legacy_builtins_for_migration_tests() -> None:
    registry = SkillRegistry(register_builtins=True)
    cards = {card["name"]: card for card in registry.all_cards()}

    assert "skill:navigation" in cards
    assert "drone_takeoff" in cards["skill:navigation"]["subtools"]
    assert cards["skill:navigation"]["kind"] == "skill"


def test_workflow_tools_are_filtered_from_agent_tool_cards() -> None:
    capabilities = {
        "flight_control": True,
        "telemetry": True,
        "target_search": True,
        "target_tracking": True,
        "obstacle_avoidance": True,
    }
    available = {
        "drone_get_status",
        "airsim_search_target",
        "airsim_track_object",
        "airsim_check_obstacle",
        "airsim_task_status",
    }

    cards = cards_for_capabilities(capabilities, available)
    names = {card["name"] for card in cards}

    assert "airsim_search_target" not in names
    assert "airsim_track_object" not in names
    assert "airsim_check_obstacle" not in names
    assert "airsim_task_status" in names


def test_tool_runtime_does_not_register_legacy_workflow_tools() -> None:
    runtime = ToolRuntime(backend_id="airsim")
    names = {spec["name"] for spec in runtime.list_tools()}

    assert "airsim_search_target" not in names
    assert "airsim_track_object" not in names
    assert "airsim_formation_mission" not in names
    assert "airsim_patrol_area" not in names


def test_ros_bridge_does_not_pollute_px4_mavlink(monkeypatch) -> None:
    monkeypatch.setenv("AIRSIM_AGENT_ROS_BRIDGE_URL", "http://127.0.0.1:8766")

    runtime = ToolRuntime(backend_id="px4_mavlink")
    tool_names = {spec["name"] for spec in runtime.list_tools()}
    card_names = {card["name"] for card in runtime.list_tool_cards()}
    profile = runtime.status_snapshot()["backend_profile"]

    assert profile["id"] == "px4_mavlink"
    assert profile["capabilities"]["ros2_topics"] is False
    assert "provider_bridge_health" not in tool_names
    assert "provider_obstacle_summary" not in tool_names
    assert "provider_validate_motion" not in tool_names
    assert "provider_obstacle_summary" not in card_names
    assert "airsim_search_target" not in tool_names


def test_px4_ros2_backend_exposes_core_and_provider_tools(monkeypatch) -> None:
    monkeypatch.setenv("AIRSIM_AGENT_ROS_BRIDGE_URL", "http://127.0.0.1:8766")

    runtime = ToolRuntime(backend_id="px4_ros2")
    tool_names = {spec["name"] for spec in runtime.list_tools()}
    profile = runtime.status_snapshot()["backend_profile"]

    assert profile["id"] == "px4_ros2"
    assert profile["capabilities"]["flight_control"] is True
    assert profile["capabilities"]["ros2_topics"] is True
    assert "drone_connect" in tool_names
    assert "drone_takeoff" in tool_names
    assert "drone_fly_to" in tool_names
    assert "provider_bridge_health" in tool_names
    assert "provider_validate_motion" in tool_names


def test_search_skill_uses_atomic_visual_tools_not_legacy_search() -> None:
    registry = SkillRegistry(register_builtins=True)
    tools = FakeTools([
        "drone_connect",
        "drone_get_status",
        "drone_arm",
        "drone_takeoff",
        "drone_rotate_to",
        "airsim_take_photo",
        "airsim_detect_objects",
        "airsim_vlm_confirm_target",
    ])
    calls: list[str] = []

    def governed(tool: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
        calls.append(tool)
        if tool == "drone_get_status":
            return _result(tool, True, {"status": "ok", "armed": False, "flying": False})
        if tool == "airsim_take_photo":
            return _result(tool, True, {"status": "ok", "image_base64": "abc"})
        if tool == "airsim_detect_objects":
            return _result(tool, True, {"status": "ok", "detections": [{"class_name": "car", "confidence": 0.91}]})
        return _result(tool, True, {"status": "ok"})

    result = registry.execute(
        "skill:search",
        {"target_class": "car", "max_steps": 2},
        tools,  # type: ignore[arg-type]
        execute_tool=governed,
    )

    assert result.ok is True
    assert result.data["target_found"] is True
    assert result.data["provider"] == "airsim_detect_objects"
    assert "airsim_search_target" not in calls
    assert calls[:4] == ["drone_connect", "drone_get_status", "drone_arm", "drone_takeoff"]
    assert "airsim_take_photo" in calls
    assert "airsim_detect_objects" in calls
