"""简单实现路径的调用契约：看场景 → 量距离 → 推算 → 飞过去。

这一组不测复杂场景（门框/方块那一类），只锁住"最简单的一条链路"在**调用层**
必须成立的东西——它是 2026-10-12 事故之后新分工的落点：

1. 靠近类指令被识别为"近距视觉任务"：计划里会自动补一次视觉确认，
   监控也按 2~3 m 定高的小范围机动包线走；
2. 测距工具的参数教的是**归一化坐标**（视觉模型给的就是它），
   不是彩色图像素——后者会静默读到另一个位置；
3. 拍照工具不再带检测验证参数（那条 YOLO 后门会把一次成功的拍照变成失败）；
4. 模型算"该转多少度"要用的朝向确实随状态一起下发。
"""

from __future__ import annotations

import inspect

from src.agent.llm import LLMMissionPlanner
from src.agent.planner import MissionStep
from src.agent.tool_cards import TOOL_CARDS

# 操作员最简单的两种说法：一个带颜色属性、一个只指代
APPROACH_CMDS = [
    "飞向画面里的浅蓝色汽车，靠近后悬停",
    "靠近那辆白车",
    "飞到画面中间那个人旁边",
]


def test_approach_wording_is_recognised_as_close_range_visual():
    """靠近类指令走近距视觉档（计划补视觉确认 + 2~3m 定高包线）。"""
    planner = LLMMissionPlanner()
    for command in APPROACH_CMDS:
        assert planner._is_close_range_visual_command(command) is True, command


def test_coordinate_flight_is_not_close_range_visual():
    """纯坐标飞行不能被卷进近距档：它的高度/距离上限完全不同。

    "飞到坐标 (10, 0, -5)" 会命中 visual_approach 意图，但没有 visual 意图——
    判据要求两者同时成立正是为了排除它。
    """
    planner = LLMMissionPlanner()
    for command in ("飞到坐标 (10, 0, -5)", "向前飞 2 米", "飞到 (0, 0, -20)"):
        assert planner._is_close_range_visual_command(command) is False, command


def test_plan_completion_injects_a_visual_look_for_approach_commands():
    """计划里没有"看画面"这一步时，靠近类指令必须补上——否则飞完也没看过目标。"""
    planner = LLMMissionPlanner()
    known = {"inspect_current_frame", "airsim_take_photo", "drone_takeoff", "drone_move_relative"}
    steps = [
        MissionStep(id="s1", label="起飞", tool="drone_takeoff", params={"altitude": 3.0}),
        MissionStep(id="s2", label="靠近", tool="drone_move_relative", params={"forward_m": 2.0}),
    ]

    injected = planner._plan_visual_guidance(APPROACH_CMDS[0], list(steps), known)

    assert [step.tool for step in injected].count("inspect_current_frame") == 1
    # 插在机动步骤之前：先看再动
    assert [step.tool for step in injected].index("inspect_current_frame") < [
        step.tool for step in injected
    ].index("drone_move_relative")
    # 视觉确认的提问必须同时要位置与大小——距离/朝向推算都靠它们
    question = next(step.params["question"] for step in injected if step.tool == "inspect_current_frame")
    assert "x,y" in question and "w,h" in question

    # 坐标飞行不注入
    assert planner._plan_visual_guidance("飞到坐标 (10, 0, -5)", list(steps), known) == steps


def test_depth_card_teaches_normalised_points():
    """测距工具的参数说明必须是"归一化坐标"，并点名彩色图像素的坑。"""
    card = TOOL_CARDS["airsim_get_depth_map"].to_dict()
    query_points = str(card["inputs"]["query_points"])
    assert "归一化" in query_points
    assert "分辨率不同" in query_points  # 为什么不能传彩色图像素
    assert "query_points" in card["inputs"]


def test_photo_tool_has_no_detection_verification_parameter():
    """拍照工具不再有 YOLO 验证参数（工具、卡片、协议层三处都不能留）。"""
    from src.agent.llm_protocol import FLIGHT_TOOL_CONSTRAINTS
    from src.tools.perception import register_perception_tools

    # 工具在注册函数内部定义，签名取不到：读源码但跳过注释行（注释里会解释
    # "为什么删掉它"，那是文档不是契约）。
    import src.tools.perception as perception_module

    code_only = "\n".join(
        line for line in inspect.getsource(perception_module).splitlines()
        if not line.strip().startswith("#")
    )
    assert "verify_target_class" not in code_only
    assert "verify_min_confidence" not in code_only

    card = TOOL_CARDS["airsim_take_photo"].to_dict()
    assert "verify_target_class" not in card["inputs"]
    assert "verify_target_class" not in FLIGHT_TOOL_CONSTRAINTS.get("airsim_take_photo", {})
    assert register_perception_tools is not None  # 导入自检：模块仍可加载


def test_heading_deg_reaches_the_model_via_status():
    """转向要用的朝向必须随状态下发（rotate_to 收的是绝对角度）。"""
    from src.modules.flight_controller import DroneStatus

    payload = DroneStatus(extra={"heading_deg": 123.4}).to_dict()
    assert payload["heading_deg"] == 123.4
    # 没有后端注入时仍有姿态可用（弧度的 yaw），至少不会完全没有朝向信息
    fallback = DroneStatus(attitude_rad={"roll": 0.0, "pitch": 0.0, "yaw": 0.5}).to_dict()
    assert fallback["attitude_rad"]["yaw"] == 0.5
