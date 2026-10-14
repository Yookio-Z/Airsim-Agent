"""无检测路径的端到端回归：VLM 取点 + 深度 + 有界相对移动。

背景：产品已移除目标检测（YOLO）。这条用例锁死"没有检测也能靠近"——
工具面里没有任何检测工具，运行时也没有任何以"锁定"为前置的门控，
Agent 依然能靠"视觉模型给点 → 深度给距离 → drone_move_relative 走一步"
完成接近并如实收尾。

场景取自操作员的真实指令形态："看见前方蓝色门框后有一个黄色方块，穿过门框
降落在方块上"——目标不在任何检测类表里，语义由视觉模型回答、几何由深度给出。
"""

from __future__ import annotations

import time
from typing import Any

from src.agent.agent_loop import AgentLoop
from src.agent.loop_types import LoopDecision
from src.agent.memory import AgentMemory
from src.agent.planner import MissionPlan, MissionStep
from src.agent.tool_executor import ToolCallResult

GATE_CMD = "看见前方蓝色门框后有一个黄色方块，穿过门框降落在方块上"


def _ok(tool: str, data: dict[str, Any]) -> ToolCallResult:
    now = time.time()
    return ToolCallResult(tool, {}, True, {"status": "ok", **data}, now, now, terminal=True, task_id="")


class _VisualOnlyTools:
    """只有画面 + 深度 + 位姿：没有任何检测/锁定接口（也没有 perception_axis）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.params_seen: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, ToolCallResult] = {
            "airsim_take_photo": _ok("airsim_take_photo", {"image_base64": "AAAA"}),
            "inspect_current_frame": _ok(
                "inspect_current_frame",
                {
                    "question": "画面里有没有蓝色门框？它在什么位置、大概多大？",
                    "answer": {
                        "target_found": True,
                        "summary_zh": "画面中央偏右有一个蓝色门框，门后能看到黄色方块。",
                        "target_candidates": [
                            {
                                "label": "blue door frame",
                                "confidence": 0.82,
                                "bbox_hint": {"x": 0.52, "y": 0.48, "w": 0.30, "h": 0.50},
                            }
                        ],
                    },
                },
            ),
            "airsim_get_depth_map": _ok("airsim_get_depth_map", {"distance_median_m": 8.0}),
            "airsim_locate_point": _ok(
                "airsim_locate_point",
                {"valid": True, "distance_m": 8.0, "world_pos": {"x": 3.0, "y": 0.4, "z": -3.0}},
            ),
            "drone_move_relative": _ok("drone_move_relative", {"position_ned": {"x": 3.0, "y": 0.4, "z": -3.0}}),
            "drone_hover": _ok("drone_hover", {}),
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self.handlers]

    def status_snapshot(self) -> dict[str, Any]:
        return {"connected": True, "stale_connection": False, "drone": {"flying": True}}

    def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
        self.calls.append(name)
        self.params_seen.append((name, dict(params or {})))
        return self.handlers[name]


class _SequencePlanner:
    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)
        self.calls = 0
        self.last_reasoning = ""

    def decide_next_step(self, **_: Any) -> LoopDecision:
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return LoopDecision(action="", reason="done", is_complete=True)


def _plan() -> MissionPlan:
    return MissionPlan(
        run_id="run_no_detection",
        command=GATE_CMD,
        intent="visual_approach",
        summary="穿过门框，降落在方块上",
        steps=[
            MissionStep(id="s1", label="拍一张current画面", tool="airsim_take_photo"),
            MissionStep(id="s2", label="视觉定位门框", tool="inspect_current_frame"),
            MissionStep(id="s3", label="读门框距离", tool="airsim_get_depth_map"),
            MissionStep(id="s4", label="门框换世界坐标", tool="airsim_locate_point"),
            MissionStep(id="s5", label="有界靠近一步", tool="drone_move_relative", params={"forward_m": 2.0}),
            MissionStep(id="s6", label="到点悬停待命", tool="drone_hover"),
        ],
        goal={"success_criteria": [{"metric": "any_action_succeeded", "detail": "至少成功执行一个动作"}]},
    )


def test_no_detection_path_can_approach_and_finish(tmp_path):
    """没有检测、没有锁定：VLM + 深度 + 有界移动照样走完整条接近链。

    注意最后一步运动（draw close）由模型给出参数：它前面是"点位换算世界坐标"这个
    视觉观察步骤，运动参数必须来自观察（`forward_m=2.0` 而不是计划里任何猜值）。
    """
    tools = _VisualOnlyTools()
    events: list[str] = []
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        _SequencePlanner([LoopDecision("drone_move_relative", {"forward_m": 2.0}, "门框 8m → 先走 2m")]),  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )

    state = loop.run(
        "run_no_detection",
        GATE_CMD,
        {"image_capture": True, "depth_perception": True},
        [{"name": name} for name in tools.handlers],
        initial_plan=_plan(),
        max_steps=8,
        require_llm=True,  # 生产路径
        reactive=True,
    )

    assert tools.calls == [
        "airsim_take_photo",
        "inspect_current_frame",
        "airsim_get_depth_map",
        "airsim_locate_point",
        "drone_move_relative",
        "drone_hover",
    ], tools.calls
    assert state.status == "completed", (state.status, state.failure_reason)
    assert state.verification_status in {"ok", "unverified"}

    joined = " ".join(events)
    for forbidden in ("没有锁定目标", "目标检测服务未启动", "detection_not_running", "detection_locked"):
        assert forbidden not in joined, joined
    # 观察之后的运动由模型决策（不是守卫替它挑），其余计划步骤照常按计划走
    llm_rows = [d for d in state.decisions if d.source == "llm"]
    assert [d.action for d in llm_rows] == ["drone_move_relative"], [(d.source, d.action) for d in state.decisions]
    assert "下一步改由模型按最新数据决定" in joined, events


def test_planned_move_waits_for_the_observation(tmp_path):
    """计划里标了 needs_observation 的步骤之后，移动参数必须由模型基于观察给出。

    实测依据：模型会给视觉确认步骤标 needs_observation=True（"这一步的结果要先看"），
    但运行时以前只解析、从不消费它——于是计划里"猜"的 forward_m 照飞，刚量到的
    距离白读。这条用例锁住"看场景 + 量距离 → 推算 → 飞过去"里最关键的推算环节。
    """
    tools = _VisualOnlyTools()
    events: list[str] = []
    plan = MissionPlan(
        run_id="run_needs_observation",
        command=GATE_CMD,
        intent="visual_approach",
        summary="先看再走",
        steps=[
            MissionStep(
                id="s1",
                label="视觉定位",
                tool="inspect_current_frame",
                params={"question": "画面里有没有蓝色门框？位置与大小？"},
                needs_observation=True,
            ),
            # 计划里"猜"的一步：没有深度数据时随手写的参数，正确行为是**不执行**
            MissionStep(id="s2", label="靠近（参数待定）", tool="drone_move_relative", params={"forward_m": 9.9}),
            MissionStep(id="s3", label="悬停", tool="drone_hover"),
        ],
        goal={"success_criteria": [{"metric": "any_action_succeeded"}]},
    )
    planner = _SequencePlanner(
        [
            # 模型看完才给的动作：深度 8 m → 一步走 1.5 m
            LoopDecision("drone_move_relative", {"forward_m": 1.5}, "深度 8m，先走 1.5m 再看"),
            LoopDecision("drone_hover", {}, "到位悬停"),
        ]
    )
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        planner,  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )

    state = loop.run(
        "run_needs_observation",
        GATE_CMD,
        {"image_capture": True, "depth_perception": True},
        [{"name": name} for name in tools.handlers],
        initial_plan=plan,
        max_steps=8,
        require_llm=True,
        reactive=True,
    )

    moves = [params for name, params in tools.params_seen if name == "drone_move_relative"]
    assert moves, tools.calls
    assert moves[0]["forward_m"] == 1.5, moves
    assert all(params.get("forward_m") != 9.9 for _, params in tools.params_seen), tools.params_seen
    assert planner.calls >= 1, "观察之后必须问过模型（而不是照计划字面参数飞）"
    assert "下一步改由模型按最新数据决定" in " ".join(events), events
    assert state.status == "completed"


def test_observation_only_rounds_are_bounded_and_the_plan_step_runs(tmp_path):
    """模型一直"只观察不动作"时，运行时按计划把那一步执行掉，而不是无限空转。

    实测过的那条失败路径：模型连着 6 轮只读深度/状态/技能指导、0 次移动，最后撞上
    模型空回复，整个任务失败。这里用"模型每次都回答观察工具"复现，断言空转有上限，
    且计划里的移动步骤最终被执行（有界单步，安全层照常校验）。
    """
    tools = _VisualOnlyTools()
    events: list[str] = []
    planner = _SequencePlanner(
        [
            LoopDecision("airsim_get_depth_map", {}, "再看一次深度"),
            LoopDecision("inspect_current_frame", {"question": "再确认一次"}, "再看一眼"),
            LoopDecision("airsim_get_depth_map", {}, "还想再看"),
            LoopDecision("drone_hover", {}, "悬停（不该走到这里）"),
        ]
    )
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        planner,  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )

    state = loop.run(
        "run_bounded_gap",
        GATE_CMD,
        {"image_capture": True, "depth_perception": True},
        [{"name": name} for name in tools.handlers],
        initial_plan=_plan(),
        max_steps=10,
        require_llm=True,
        reactive=True,
    )

    joined = " ".join(events)
    assert "drone_move_relative" in tools.calls, tools.calls  # 计划那一步最终被执行
    assert "连续 3 轮只看不动" in joined, events  # 上限触发的说明对操作员可见
    assert planner.calls == 3, planner.calls  # 空转次数有界（不再无限问下去）
    assert state.status in {"completed", "blocked"}


def test_detection_tool_names_are_gone_from_the_public_surface():
    """检测类工具名必须从工具卡片、清单、白名单里彻底消失。

    留着任何一个都会让模型/守卫重新长出"必须检测锁定"的假设——那正是
    2026-10-12 任务失败的根因。
    """
    from src.agent import agent_loop as agent_loop_module
    from src.agent.tool_cards import TOOL_CARDS
    from src.agent.tool_executor import ToolRuntime
    from src.tools.manifest import TOOL_MANIFEST

    gone = {"perception_start", "perception_stop", "airsim_detect_objects", "drone_approach_target"}
    assert gone.isdisjoint(TOOL_CARDS), sorted(gone & set(TOOL_CARDS))
    assert gone.isdisjoint(TOOL_MANIFEST), sorted(gone & set(TOOL_MANIFEST))
    assert gone.isdisjoint(agent_loop_module._PERCEPTION_TOOLS)
    assert gone.isdisjoint(ToolRuntime.READ_ONLY_TOOLS)
    assert gone.isdisjoint(ToolRuntime.CONTROL_TOOLS)
    # 替代几何路径必须在模型面上：点位 → 距离 + 世界坐标
    assert "airsim_locate_point" in TOOL_CARDS
    assert "airsim_locate_point" in TOOL_MANIFEST
    assert "airsim_locate_point" in ToolRuntime.READ_ONLY_TOOLS
