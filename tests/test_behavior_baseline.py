"""Behavior baseline: intent extraction and target-class routing across the
whole keyword-consolidation refactor.

Every command below is a real operator phrasing from the test suite, docs, or
the UI examples. Any change to extract_intents / extract_target_class /
command_slots that flips one of these flags is a behavior regression unless the
test is deliberately updated with a review.
"""

from __future__ import annotations

import pytest

from src.agent.command_slots import extract_command_slots, extract_intents, extract_target_class
from src.agent.planner import MissionPlanner

# command -> (expected intent keys that MUST be True, expected target class)
GOLDEN_CORPUS: list[tuple[str, set[str], str]] = [
    # --- flight basics ------------------------------------------------------
    ("起飞到 5 米高度", {"takeoff", "motion", "visual_approach"}, ""),  # "起飞到" contains "飞到"
    ("起飞", {"takeoff", "motion"}, ""),
    ("降落", {"land", "motion"}, ""),
    ("返航", {"return_home", "motion"}, ""),
    ("返回起飞点", {"return_home", "motion", "takeoff"}, ""),  # "起飞点" contains "起飞"
    ("悬停", {"hover"}, ""),
    ("暂停任务", {"hover"}, ""),
    # "连接车辆" also triggers the visual/target tables via the "车辆" alias
    ("连接车辆", {"connect", "visual", "target_confirmation"}, "car"),
    ("查看状态", {"status"}, ""),
    ("汇报当前遥测", {"status"}, ""),
    # --- motion -------------------------------------------------------------
    ("前进 5 米", {"motion"}, ""),
    ("向前飞 10 米", {"motion"}, ""),
    ("向左移动 3 米", {"motion"}, ""),
    ("飞到 x=10 y=20 z=-5", {"motion", "visual_approach"}, ""),
    ("飞到坐标 (10, 20, -5)", {"motion", "visual_approach"}, ""),
    # --- patrol -------------------------------------------------------------
    ("执行区域巡检", {"patrol"}, ""),
    ("按半径 30 米巡航", {"patrol"}, ""),
    # --- visual / photo -----------------------------------------------------
    ("拍一张照片", {"visual", "photo"}, ""),
    ("拍照", {"visual", "photo"}, ""),
    ("采集图像", {"visual", "photo"}, ""),
    ("看看画面里有什么", {"visual", "open_image_analysis"}, ""),
    ("描述一下当前画面", {"visual", "open_image_analysis"}, ""),
    ("检测画面中的车辆", {"visual", "target_confirmation", "search"}, "car"),
    # --- search / target ----------------------------------------------------
    ("搜索一辆红色汽车", {"visual", "search", "target_confirmation"}, "car"),
    ("寻找目标", {"visual", "search", "target_confirmation"}, ""),
    ("找到一辆卡车", {"visual", "search", "target_confirmation"}, "truck"),
    ("搜索行人", {"visual", "search", "target_confirmation"}, "person"),
    ("寻找公交车", {"visual", "search", "target_confirmation"}, "bus"),
    ("搜索无人机", {"visual", "search", "target_confirmation"}, "drone"),
    ("search for a red car", {"visual", "search", "target_confirmation"}, "car"),
    ("find the target", {"visual", "search", "target_confirmation"}, ""),
    ("detect a truck on the road", {"visual", "search", "target_confirmation"}, "truck"),
    # --- visual approach ----------------------------------------------------
    ("飞到目标旁边", {"search", "motion", "visual", "visual_approach", "target_confirmation"}, ""),
    ("靠近那辆车", {"visual", "visual_approach", "target_confirmation"}, "car"),
    ("approach the target", {"visual", "visual_approach", "target_confirmation"}, ""),
    # --- track --------------------------------------------------------------
    ("跟踪目标", {"visual", "track", "search", "target_confirmation"}, ""),
    ("follow the car", {"visual", "track", "target_confirmation"}, "car"),
    # --- combined multi-intent ----------------------------------------------
    ("起飞后搜索目标并拍照", {"visual", "takeoff", "search", "photo", "target_confirmation", "motion"}, ""),
    ("搜索并跟踪卡车", {"visual", "search", "track", "target_confirmation"}, "truck"),
    # --- negative / non-UAV -------------------------------------------------
    ("今天天气怎么样", set(), ""),
    ("你好", set(), ""),
    # pinned quirk: "无人机" is a target-class alias but not a visual term
    ("什么是无人机", set(), "drone"),
]

# commands whose planned step sequence is asserted as a contract
PLAN_CONTRACT_CORPUS: list[tuple[str, set[str]]] = [
    ("起飞到 5 米高度", {"drone_connect", "drone_arm", "drone_takeoff", "drone_get_status", "memory_store"}),
    # plain land short-circuits before the generic status/memory tail
    ("降落", {"drone_hover", "drone_land"}),
    ("前进 5 米", {"drone_connect", "drone_arm", "drone_takeoff", "drone_move_relative", "drone_get_status", "memory_store"}),
    ("执行区域巡检", {"drone_connect", "drone_arm", "drone_takeoff", "drone_fly_path", "memory_store"}),
]


@pytest.mark.parametrize("command,expected_intents,target_class", GOLDEN_CORPUS)
def test_intent_golden_corpus(command, expected_intents, target_class):
    intents = extract_intents(command)
    actual = {key for key, value in intents.items() if value}
    # exact equality: a False->True regression on any unintended intent is caught
    assert actual == expected_intents, f"{command!r}: expected {expected_intents}, got {actual}"
    assert extract_target_class(command) == target_class


@pytest.mark.parametrize("command,expected_tools", PLAN_CONTRACT_CORPUS)
def test_rule_plan_tool_contract(command, expected_tools):
    plan = MissionPlanner().plan(command)
    tools = {step.tool for step in plan.steps}
    assert expected_tools.issubset(tools), f"{command!r}: plan {sorted(tools)} misses {sorted(expected_tools - tools)}"


def test_command_slots_still_extracts_parameters():
    slots = extract_command_slots("搜索半径 30 米高度 5 米内的红色汽车，速度 2m/s")
    assert slots.radius == 30.0
    assert slots.altitude == 5.0
    assert slots.velocity == 2.0
    assert slots.target_class == "car"


def test_target_class_priority_truck_over_car():
    assert extract_target_class("卡车") == "truck"
    assert extract_target_class("货车") == "truck"
    assert extract_target_class("公交车") == "bus"
    assert extract_target_class("汽车") == "car"
    assert extract_target_class("车辆") == "car"


def test_open_image_analysis_is_exclusive_of_target_confirmation():
    intents = extract_intents("看看画面里有什么")
    assert intents["open_image_analysis"] is True
    assert intents["target_confirmation"] is False
    named = extract_intents("看看画面里有没有红色汽车")
    assert named["open_image_analysis"] is False
    assert named["target_confirmation"] is True
