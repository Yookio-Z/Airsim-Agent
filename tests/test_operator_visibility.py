"""操作员可见性的三条契约（2026-10-13 实跑反馈）：

1. "模型思考"里不许出现运行时编的句子；模型没输出文本时那行直接消失，
   等待期间显示的是"⏳ 等待模型回复"，不是"模型思考 + 一句固定话术"。
2. "运行时守卫"这种内部术语不上界面：这一类统一叫"系统自动处理"，
   正文用人话说明它做了什么。
3. 感知/几何类工具返回要渲染成中文要点，而不是 `valid=True, depth_meters=…,
   distance_to_drone=…, distance_m=…` 这样同一个数字出现四遍的字段流。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

from src.agent.agent_loop import AgentLoop
from src.agent.loop_types import LoopDecision, LoopObservation, LoopState
from src.agent.planner import MissionPlan, MissionStep
from src.agent.runtime import AgentRuntime

ROOT = Path(__file__).resolve().parents[1]


def _stub() -> SimpleNamespace:
    return SimpleNamespace(
        _lock=threading.RLock(),
        _PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS,
        _decision_line=AgentRuntime._decision_line,
    )


def test_pending_row_becomes_a_decision_line_when_the_model_gives_no_text():
    """模型只回了工具调用、没有正文：这一行变成"模型决策"并写出它选了什么。

    操作员反馈过（2026-10-13）："只要请求了模型就一定会收到信息，为什么不能显示"。
    工具调用本身就是模型的输出——写出来，不隐藏、也不冒充思考。
    """
    run = SimpleNamespace(process_trace=[{"kind": "pending", "status": "running", "title": "等待模型回复", "body": ""}])
    AgentRuntime._close_pending_reasoning(_stub(), run, "drone_move_relative", {"forward_m": 3.0, "velocity": 1.0})
    item = run.process_trace[0]
    assert item["kind"] == "decision" and item["title"] == "模型决策"
    assert item["body"].startswith("调用 drone_move_relative(")
    assert "forward_m" in item["body"]


def test_pending_row_is_dropped_only_when_there_is_nothing_to_show():
    """既没有正文、也没有动作（异常路径）：才允许摘掉这一行。"""
    run = SimpleNamespace(process_trace=[{"kind": "pending", "status": "running", "title": "等待模型回复", "body": ""}])
    AgentRuntime._close_pending_reasoning(_stub(), run, "", {})
    assert run.process_trace == []


def test_pending_row_keeps_real_text_but_is_promoted():
    """有真实文本时不是丢掉整行，而是升格成模型思考行（由 events 层负责升格）。"""
    run = SimpleNamespace(
        process_trace=[{"kind": "reasoning", "status": "running", "title": "模型思考", "body": "目标在画面中央，先走一步"}]
    )
    AgentRuntime._close_pending_reasoning(_stub(), run, "drone_move_relative")
    assert run.process_trace[0]["body"] == "目标在画面中央，先走一步"
    assert run.process_trace[0]["status"] == "completed"


def test_humanizer_renders_locate_point_as_readable_points():
    """点位定位：距离 + 世界坐标 + 偏角 +（必要时）探测点警告，不再是一坨字段。"""
    from src.agent.agent_bridge import AgentBridgeMixin

    data = {
        "valid": True,
        "distance_m": 30.03,
        "depth_m": 30.03,
        "world_pos": {"x": 29.02, "y": 6.37, "z": -7.49},
        "angle_h_deg": 8.4,
        "angle_v_deg": -9.0,
        "probe_warning": "视线明显朝下：这个点很可能落在目标前面的地面/路面上。",
        "message": "该点深度有效",
    }
    text = AgentBridgeMixin._humanize_result(data)
    assert "点位定位" in text and "30.0 m" in text
    assert "N 29.02" in text and "E 6.37" in text
    assert "垂直 -9.0°" in text
    assert "⚠" in text and "地面" in text
    # 同一个数字只出现一次（以前 distance_m/depth_m/depth_meters/distance_to_drone 四份）
    assert text.count("30.0") == 1, text


def test_humanizer_renders_vision_answer_as_candidates():
    """视觉模型返回：提问 + 摘要 + 候选（位置/大小/方向），不看原始 JSON。"""
    from src.agent.agent_bridge import AgentBridgeMixin

    data = {
        "status": "ok",
        "question": "画面里有没有浅蓝色汽车？",
        "answer": {
            "summary_zh": "画面右侧有一辆浅蓝色汽车，距离较远。",
            "target_found": True,
            "target_candidates": [
                {"label": "car", "confidence": 0.82, "relative_direction": "right",
                 "bbox_hint": {"x": 0.6, "y": 0.39, "w": 0.08, "h": 0.12}},
            ],
        },
    }
    text = AgentBridgeMixin._humanize_result(data)
    assert "提问：画面里有没有浅蓝色汽车？" in text
    assert "视觉模型：画面右侧有一辆浅蓝色汽车" in text
    assert "候选：car" in text and "中心 0.60,0.39" in text and "大小 8%×12%" in text


def test_humanizer_renders_depth_readings_as_metres():
    from src.agent.agent_bridge import AgentBridgeMixin

    data = {
        "status": "ok",
        "distance_median_m": 30.0,
        "query_results": [{"distance_m": 29.9}, {"distance_m": 30.1}, {"distance_m": 30.0}],
    }
    text = AgentBridgeMixin._humanize_result(data)
    assert "中值 30.0 m" in text and "3 个点" in text


def test_ui_labels_do_not_expose_internal_jargon():
    """界面上不许再出现"运行时守卫"，等待行有自己的标签与图标。

    只查代码行：注释里会解释这段历史（"以前叫运行时守卫"），那是文档不是界面文案。
    """
    text = (ROOT / "src" / "ui" / "static" / "ui-chat.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    assert '"运行时守卫"' not in code
    assert 'return "系统自动处理"' in code
    assert '"pending"' in code.split(".includes(explicit)")[0]
    assert 'pending ? "⏳" : (decision ? "🧭" : "🧠")' in code
    assert 'pending ? "等待模型回复" : (decision ? "模型决策" : "模型思考")' in code
    assert '"decision"' in code.split(".includes(explicit)")[0]


def test_overshoot_veto_blocks_flying_onto_the_located_point():
    """近距接近里，飞到"单点深度推出的坐标"上必须被否决（实测事故）。"""
    loop = object.__new__(AgentLoop)
    state = LoopState(run_id="r", command="飞向画面里那辆浅蓝色汽车，靠近后悬停", status="running")
    state.results.append(
        SimpleNamespace(
            tool="airsim_locate_point",
            ok=True,
            data={"valid": True, "distance_m": 30.0, "world_pos": {"x": 29.02, "y": 6.37, "z": -7.49}},
        )
    )
    observation = LoopObservation(
        step_index=1,
        world_state={"drone": {"position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}},
    )
    decision = LoopDecision(action="drone_fly_to", params={"x": 29.02, "y": 6.37, "z": -3.0})

    reason = loop._approach_overshoot_reason(decision, state, observation, state.command)

    assert reason, "飞到目标坐标上必须被否决"
    assert "站离" in reason and "drone_move_relative" in reason


def test_overshoot_veto_allows_stopping_short_and_bypassing():
    loop = object.__new__(AgentLoop)
    state = LoopState(run_id="r", command="飞向画面里那辆浅蓝色汽车，靠近后悬停", status="running")
    state.results.append(
        SimpleNamespace(
            tool="airsim_locate_point",
            ok=True,
            data={"valid": True, "distance_m": 30.0, "world_pos": {"x": 30.0, "y": 0.0, "z": -7.0}},
        )
    )
    observation = LoopObservation(step_index=1, world_state={"drone": {"position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}})

    # 停在目标前方（20 m 处，目标在 30 m 外）：放行
    short = LoopDecision(action="drone_fly_to", params={"x": 20.0, "y": 0.0, "z": -3.0})
    assert loop._approach_overshoot_reason(short, state, observation, state.command) == ""

    # 明显从旁边绕过去（横向 12 m）：放行
    bypass = LoopDecision(action="drone_fly_to", params={"x": 29.0, "y": 12.0, "z": -3.0})
    assert loop._approach_overshoot_reason(bypass, state, observation, state.command) == ""

    # 非"靠近"类指令：不介入（飞航线不该被这条拦）
    plain = LoopState(run_id="r", command="飞到坐标 (30, 0, -3)", status="running")
    plain.results = state.results
    assert loop._approach_overshoot_reason(short, plain, observation, plain.command) == ""

    # 不是 fly_to：不介入
    assert loop._approach_overshoot_reason(
        LoopDecision(action="drone_move_relative", params={"forward_m": 3.0}), state, observation, state.command
    ) == ""


def _synthetic_depth(width: int = 200, height: int = 150, ground_m: float = 30.0,
                     object_box: tuple[float, float, float, float] | None = None,
                     object_m: float = 12.0):
    """造一张合成深度图：整幅是地面距离；给了框则框内替换为物体距离。"""
    import numpy as np

    depth = np.full((height, width), float(ground_m), dtype=np.float32)
    if object_box:
        x, y, w, h = object_box
        x0, x1 = int((x - w / 2) * width), int((x + w / 2) * width)
        y0, y1 = int((y - h / 2) * height), int((y + h / 2) * height)
        depth[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = object_m
    return depth


def test_surface_check_reports_flat_surface_when_the_box_is_on_the_ground():
    """框内外深度一致 → 明确告诉调用方"这画的是一整片表面，不是目标"。

    实测事故（2026-10-13）：视觉点落在 30 m 外的路面上，深度没错、点错了；
    以前没有任何机制能发现这一点。
    """
    from src.modules.perception_airsim_adapter import _surface_consistency

    depth = _synthetic_depth()
    check = _surface_consistency(depth, (150, 200), 0.6, 0.39, 0.08, 0.12, 90.0)

    assert check["surface_check"] == "same_surface", check
    assert "连续表面" in check["surface_hint"]
    assert abs(check["inside_median_m"] - check["outside_median_m"]) < 1.0


def test_surface_check_detects_an_object_in_front():
    """框内明显比框外近 → 像独立前景目标（点大概率真的在目标上）。"""
    from src.modules.perception_airsim_adapter import _surface_consistency

    depth = _synthetic_depth(object_box=(0.6, 0.39, 0.10, 0.14), object_m=12.0)
    check = _surface_consistency(depth, (150, 200), 0.6, 0.39, 0.08, 0.12, 90.0)

    assert check["surface_check"] == "object_in_front", check
    assert check["inside_median_m"] < check["outside_median_m"]


def test_surface_check_is_silent_without_a_box():
    """没给框就不做交叉验证（没有"内外对比"可言）。"""
    from src.modules.perception_airsim_adapter import _surface_consistency

    depth = _synthetic_depth()
    assert _surface_consistency(depth, (150, 200), 0.6, 0.39, 0.0, 0.0, 90.0) == {}


def test_locate_tool_passes_the_vision_box_through():
    """工具层必须把 w/h 透传下去，否则交叉验证永远不会发生。"""
    import json

    from src.agent.tool_executor import ToolCollector
    from src.tools.perception_axis import register_perception_axis_tools

    calls: list[tuple] = []

    def locate(x, y, vehicle_name="", hint_w=0.0, hint_h=0.0):
        calls.append((x, y, hint_w, hint_h))
        return {"valid": True, "distance_m": 30.0, "world_pos": {"x": 1.0, "y": 2.0, "z": -3.0},
                "surface_check": "same_surface", "surface_hint": "框内外深度几乎一样"}

    collector = ToolCollector()
    register_perception_axis_tools(
        collector, None, lambda data: json.dumps(data, ensure_ascii=False), locate=locate
    )

    payload = json.loads(collector.tools["airsim_locate_point"](0.6, 0.39, 0.08, 0.12))
    assert calls == [(0.6, 0.39, 0.08, 0.12)]
    assert payload["surface_check"] == "same_surface"


def test_overshoot_veto_ignores_a_known_flat_surface_locate():
    """surface_check=same_surface 的坐标不是目标：不能拿它去拦模型的移动。"""
    loop = object.__new__(AgentLoop)
    state = LoopState(run_id="r", command="飞向画面里那辆浅蓝色汽车，靠近后悬停", status="running")
    state.results.append(
        SimpleNamespace(
            tool="airsim_locate_point",
            ok=True,
            data={
                "valid": True,
                "distance_m": 30.0,
                "world_pos": {"x": 29.02, "y": 6.37, "z": -7.49},
                "surface_check": "same_surface",  # 已知是路面：不是目标
            },
        )
    )
    observation = LoopObservation(step_index=1, world_state={"drone": {"position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}})
    fly = LoopDecision(action="drone_fly_to", params={"x": 29.02, "y": 6.37, "z": -3.0})

    assert loop._approach_overshoot_reason(fly, state, observation, state.command) == ""


def _approach_state(command: str, *, height: float | str | None, distance: float | None) -> LoopState:
    """height: 数值=视觉确认并带尺寸；"no_size"=确认到目标但视觉模型没给尺寸；None=没有确认。"""
    state = LoopState(run_id="r", command=command, status="running")
    if height is not None:
        candidate = {"label": "car"} if height == "no_size" else {
            "label": "car", "bbox_hint": {"x": 0.5, "y": 0.5, "w": 0.4, "h": float(height)}
        }
        state.results.append(
            SimpleNamespace(
                tool="inspect_current_frame", ok=True,
                data={"answer": {"target_found": True, "target_candidates": [candidate]}},
            )
        )
    if distance is not None:
        state.results.append(
            SimpleNamespace(tool="airsim_locate_point", ok=True,
                            data={"valid": True, "distance_m": distance, "world_pos": {"x": 1.0, "y": 2.0, "z": -3.0}})
        )
    return state


def test_no_declared_criterion_means_no_runtime_judgement():
    """计划没声明完成判据时，运行时不替任务发明标准（既不算到位，也不否决）。

    操作员明确否掉了"任务类型默认"这种做法：把一种任务的标准强加给所有任务。
    判定标准只能来自任务声明；没声明时由模型自己判断完成。
    """
    loop = object.__new__(AgentLoop)
    observation = LoopObservation(step_index=1, world_state={})
    command = "飞向画面里那辆浅蓝色汽车，靠近后悬停"
    for height, distance in ((0.60, None), (0.05, 25.0)):
        state = _approach_state(command, height=height, distance=distance)
        reason, source = loop._stopping_criterion_status(state, _plan_with_criteria([]), observation)
        assert (reason, source) == ("", "none"), (reason, source)


def _plan_with_criteria(criteria: list[dict]) -> MissionPlan:
    return MissionPlan(
        run_id="r",
        command="飞向画面里那辆浅蓝色汽车，靠近后悬停",
        intent="visual_approach",
        summary="",
        steps=[],
        goal={"objective": "x", "target": "", "success_criteria": criteria},
    )


def test_declared_criterion_is_the_only_judge():
    """判定只认任务声明的判据：更松的阈值更早判到位，更严的更晚；没声明就不判。"""
    loop = object.__new__(AgentLoop)
    observation = LoopObservation(step_index=1, world_state={})
    state = _approach_state("飞向画面里那辆浅蓝色汽车，靠近后悬停", height=0.18, distance=None)

    # 没声明判据 → 不判
    assert loop._stopping_criterion_status(state, _plan_with_criteria([]), observation) == ("", "none")
    # 任务声明 min_fill=0.15 → 到位，并说明是"计划声明的判据"
    reason, source = loop._stopping_criterion_status(state, _plan_with_criteria([{"metric": "target_fill", "min_fill": 0.15}]), observation)
    assert source == "declared" and "min_fill=0.15" in reason, (reason, source)
    # 任务声明 min_fill=0.60 → 还没到
    reason2, source2 = loop._stopping_criterion_status(state, _plan_with_criteria([{"metric": "target_fill", "min_fill": 0.60}]), observation)
    assert reason2 == "" and source2 == "declared"


def test_declared_position_criterion_suppresses_the_fill_rule():
    """"落在 H 点上"这类任务：位置判据说了算，不做"占画面比例"的到位判定。

    H 降落点的到位标准是"水平误差 ≤ 容差"，靠得够近既不必要也不充分；
    运行时不该拿靠近类任务的常数去判它。
    """
    loop = object.__new__(AgentLoop)
    observation = LoopObservation(step_index=1, world_state={})
    state = _approach_state("飞向画面里那辆浅蓝色汽车，靠近后悬停", height=0.60, distance=None)

    plan = _plan_with_criteria([{"metric": "position_reached", "x": 10.0, "y": 2.0, "z": -3.0, "tolerance": 0.5},
                                {"metric": "landed"}])
    reason, source = loop._stopping_criterion_status(state, plan, observation)
    assert reason == "" and source == "declared"

    # 声明了 target_distance 时，距离判据按声明执行
    state_far = _approach_state("飞向画面里那辆浅蓝色汽车，靠近后悬停", height="no_size", distance=2.4)
    reason2, source2 = loop._stopping_criterion_status(
        state_far, _plan_with_criteria([{"metric": "target_distance", "max_m": 1.0}]), observation
    )
    assert reason2 == "" and source2 == "declared"  # 2.4 m 未达 1.0 m
    reason3, source3 = loop._stopping_criterion_status(
        state_far, _plan_with_criteria([{"metric": "target_distance", "max_m": 3.0}]), observation
    )
    assert source3 == "declared" and "max_m=3.0" in reason3


def test_planner_accepts_the_new_stopping_metrics():
    """计划可以声明 target_fill / target_distance 当停止判据（白名单 + 参数清洗）。"""
    from src.agent.llm import LLMMissionPlanner

    planner = LLMMissionPlanner.__new__(LLMMissionPlanner)
    goal = planner._goal_from_payload(
        {
            "objective": "靠近后悬停",
            "success_criteria": [
                {"metric": "target_fill", "min_fill": 0.3},
                {"metric": "target_distance", "max_m": 2.0},
                {"metric": "invented_metric"},  # 未知判据必须被丢掉
            ],
        },
        "飞向画面里那辆浅蓝色汽车，靠近后悬停",
    )

    metrics = [item["metric"] for item in goal["success_criteria"]]
    assert metrics == ["target_fill", "target_distance"], metrics
    assert goal["success_criteria"][0]["min_fill"] == 0.3
    assert goal["success_criteria"][1]["max_m"] == 2.0


def _loop_plan(command: str, goal: dict) -> MissionPlan:
    return MissionPlan(
        run_id="run_x",
        command=command,
        intent="visual_approach",
        summary="",
        steps=[
            MissionStep(id="s1", label="视觉定位", tool="inspect_current_frame", needs_observation=True),
            MissionStep(id="s2", label="靠近", tool="drone_move_relative", params={"forward_m": 2.0}),
            MissionStep(id="s3", label="悬停", tool="drone_hover"),
        ],
        goal=goal,
    )


def _run_reach_case(tmp_path, goal: dict) -> tuple[list[str], list[str]]:
    from test_no_detection_approach import _SequencePlanner, _VisualOnlyTools

    from src.agent.memory import AgentMemory

    command = "飞向画面里那辆浅蓝色汽车，靠近后悬停"
    tools = _VisualOnlyTools()  # 视觉确认返回 h=0.5（远超 25% 阈值）
    events: list[str] = []
    planner = _SequencePlanner([
        LoopDecision("drone_move_relative", {"forward_m": 3.0}, "再靠近一点"),
        LoopDecision("drone_move_relative", {"forward_m": 3.0}, "还想再近"),
        LoopDecision("drone_move_relative", {"forward_m": 3.0}, "继续靠近"),
    ])
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        planner,  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )
    state = loop.run(
        "run_reach_case",
        command,
        {"image_capture": True, "depth_perception": True},
        [{"name": name} for name in tools.handlers],
        initial_plan=_loop_plan(command, goal),
        max_steps=12,
        require_llm=True,
        reactive=True,
    )
    assert state.status in {"completed", "blocked"}, (state.status, state.failure_reason)
    return [str(m) for m in events], tools.calls


def test_declared_criterion_stops_further_approaching(tmp_path):
    """任务声明了 target_fill 且已满足：继续大步逼近被否决，连续两次由运行时收尾。

    实测问题（2026-10-13）：判据缺失时模型自己认定"还不够近"，一路微调，28 步撞上限。
    """
    goal = {"objective": "靠近后悬停", "target": "", "success_criteria": [{"metric": "target_fill", "min_fill": 0.25}]}
    events, calls = _run_reach_case(tmp_path, goal)

    joined = " ".join(events)
    assert "已到达任务判据" in joined, events
    assert "否决一次继续逼近的动作" in joined, events
    assert calls.count("drone_move_relative") == 0, calls  # 到位后一步都没再飞


def test_without_a_declared_criterion_the_runtime_does_not_intervene(tmp_path):
    """对照：没声明判据 → 运行时不否决、不播报，模型的动作照常执行。

    "判定标准属于任务"要彻底：没声明就不替它发明标准，也不替它拦动作。
    """
    events, calls = _run_reach_case(tmp_path, {})

    joined = " ".join(events)
    assert "已到达任务判据" not in joined, events
    assert "否决一次继续逼近的动作" not in joined, events
    assert "drone_move_relative" in calls, calls


def test_criterion_status_is_announced_when_it_changes(tmp_path):
    """判据状态变化时播报一次：操作员能看到"怎么算做到、现在到哪了"。"""
    import time as _time
    from typing import Any

    from src.agent.memory import AgentMemory
    from src.agent.tool_executor import ToolCallResult

    command = "飞向画面里那辆浅蓝色汽车，靠近后悬停"
    heights = [0.10, 0.30]  # 第一次未满足，第二次满足
    calls: list[str] = []

    def _inspect(params: dict) -> ToolCallResult:
        height = heights[min(calls.count("inspect_current_frame") - 1, len(heights) - 1)]
        now = _time.time()
        return ToolCallResult(
            "inspect_current_frame",
            dict(params or {}),
            True,
            {
                "status": "ok",
                "answer": {
                    "target_found": True,
                    "target_candidates": [
                        {"label": "car", "bbox_hint": {"x": 0.5, "y": 0.5, "w": 0.3, "h": height}}
                    ],
                },
            },
            now,
            now,
        )

    def _ok(name: str) -> ToolCallResult:
        now = _time.time()
        return ToolCallResult(name, {}, True, {"status": "ok"}, now, now)

    class _Tools:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {
                "inspect_current_frame": _inspect,
                "drone_hover": _ok("drone_hover"),
            }

        def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": name} for name in self.handlers]

        def status_snapshot(self) -> dict[str, Any]:
            return {"connected": True, "stale_connection": False, "drone": {"flying": True}}

        def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
            calls.append(name)
            handler = self.handlers[name]
            return handler(params) if callable(handler) else handler

    class _Planner:
        def __init__(self) -> None:
            self.last_reasoning = ""

        def decide_next_step(self, **_: Any) -> LoopDecision:
            return LoopDecision("drone_hover", {}, "悬停汇报")

    tools = _Tools()
    events: list[str] = []
    plan = MissionPlan(
        run_id="run_criteria",
        command=command,
        intent="visual_approach",
        summary="",
        steps=[
            MissionStep(id="s1", label="视觉定位", tool="inspect_current_frame", needs_observation=True),
            MissionStep(id="s2", label="再看一眼", tool="inspect_current_frame", needs_observation=True),
            MissionStep(id="s3", label="悬停", tool="drone_hover"),
        ],
        goal={"objective": "靠近", "target": "", "success_criteria": [{"metric": "target_fill", "min_fill": 0.25}]},
    )
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        _Planner(),  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )
    loop.run(
        "run_criteria",
        command,
        {"image_capture": True, "depth_perception": True},
        [{"name": name} for name in tools.handlers],
        initial_plan=plan,
        max_steps=8,
        require_llm=True,
        reactive=True,
    )

    joined = " ".join(events)
    assert "完成判据 target_fill：未满足" in joined, events
    assert "完成判据 target_fill：已满足" in joined, events


def test_hold_for_requires_the_criterion_to_keep_holding():
    """hold_s：判据要持续成立够久才作数（防抖，防一次噪声读数提前收工）。

    文献里的 hold_for。默认 0 = 单次成立即可，与旧行为一致。
    """
    loop = object.__new__(AgentLoop)
    loop._criteria_hold = {}
    criterion = {"metric": "target_fill", "min_fill": 0.25, "hold_s": 2.0}

    assert loop._criterion_holds(criterion, True, 100.0) is False   # 刚满足：还没持续够
    assert loop._criterion_holds(criterion, True, 101.5) is False
    assert loop._criterion_holds(criterion, True, 102.0) is True    # 持续满 2s
    # 中间断一次 → 重新计时
    loop._criterion_holds(criterion, False, 103.0)
    assert loop._criterion_holds(criterion, True, 103.5) is False
    # 没声明 hold_s 时单次成立即可
    assert loop._criterion_holds({"metric": "target_fill", "min_fill": 0.25}, True, 200.0) is True


def test_stall_is_detected_and_ends_honestly(tmp_path):
    """无进展监控：声明的必需判据连续多轮没改善 → 提醒换策略 → 否决重复动作 → 如实收尾。

    通用机制：只看"任务自己声明的判据有没有改善"，不含任何任务类型常数。
    实测场景（模型的"无限逼近"）就是这条要收口的东西。
    """
    import time as _time
    from typing import Any

    from src.agent.memory import AgentMemory
    from src.agent.tool_executor import ToolCallResult

    command = "飞向画面里那辆浅蓝色汽车，靠近后悬停"
    calls: list[str] = []

    def _inspect(params: dict) -> ToolCallResult:
        now = _time.time()
        return ToolCallResult(
            "inspect_current_frame",
            dict(params or {}),
            True,
            {
                "status": "ok",
                "answer": {
                    "target_found": True,
                    # 目标始终只占画面 10%（阈值 25%）：一直没有改善
                    "target_candidates": [{"label": "car", "bbox_hint": {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.10}}],
                },
            },
            now,
            now,
        )

    def _ok(name: str, params: dict | None = None) -> ToolCallResult:
        # 参数必须原样回填：循环靠它判断"是不是重复了同一个动作"
        now = _time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok"}, now, now)

    class _Tools:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {
                "inspect_current_frame": _inspect,
                "drone_move_relative": lambda params: _ok("drone_move_relative", params),
                "drone_hover": lambda params: _ok("drone_hover", params),
            }

        def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": name} for name in self.handlers]

        def status_snapshot(self) -> dict[str, Any]:
            return {"connected": True, "stale_connection": False, "drone": {"flying": True}}

        def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
            calls.append(name)
            handler = self.handlers[name]
            return handler(params) if callable(handler) else handler

    class _Planner:
        """每轮都选同一个动作、同一组参数（典型的"原地打转"）。"""

        def __init__(self) -> None:
            self.last_reasoning = ""

        def decide_next_step(self, **_: Any) -> LoopDecision:
            return LoopDecision("drone_move_relative", {"forward_m": 1.0}, "再靠近一点")

    events: list[str] = []
    plan = MissionPlan(
        run_id="run_stall",
        command=command,
        intent="visual_approach",
        summary="",
        steps=[
            MissionStep(id="s1", label="视觉定位", tool="inspect_current_frame", needs_observation=True),
            MissionStep(id="s2", label="再看一眼", tool="inspect_current_frame", needs_observation=True),
            MissionStep(id="s3", label="靠近", tool="drone_move_relative", params={"forward_m": 1.0}),
        ],
        goal={"objective": "靠近后悬停", "target": "", "success_criteria": [{"metric": "target_fill", "min_fill": 0.25, "required": True}]},
    )
    loop = AgentLoop(
        _Tools(),  # type: ignore[arg-type]
        _Planner(),  # type: ignore[arg-type]
        AgentMemory(data_dir=tmp_path),
        on_event=lambda level, source, message, data: events.append(str(message)),
    )
    state = loop.run(
        "run_stall",
        command,
        {"image_capture": True, "depth_perception": True},
        [{"name": "inspect_current_frame"}, {"name": "drone_move_relative"}, {"name": "drone_hover"}],
        initial_plan=plan,
        max_steps=16,
        require_llm=True,
        reactive=True,
        # keep_reacting=true 正是实测里"无限逼近"的场景：计划跑完不收敛，模型反复微调
        keep_reacting=True,
    )

    joined = " ".join(events)
    assert "没有进展" in joined, events
    assert "否决一次重复动作" in joined, events
    assert "连续没有进展" in joined, events
    # 收尾必须诚实：判据没满足 → 不能报"已验证成功"
    assert state.verification_status in {"failed", "unverified"}, state.verification_status
    assert state.verification_status != "ok"
