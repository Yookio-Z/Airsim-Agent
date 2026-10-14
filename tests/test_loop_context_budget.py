"""循环每轮注入的上下文：设计契约（借 Pi Harness 的四条规则）。

规则（详见 docs/loop_context_design.md）：
1. 能力声明只发一次：原生工具调用模式下工具 schema 由 API 的 tools 参数下发，
   正文里不再重复一份 available_tool_cards（实测每轮 ~5.8k tokens 的重复）。
2. 历史压缩成摘要，而不是原样重发或丢弃：`steps_digest` 一行一步、有行数上限。
3. 记忆有界、按需取：逐轮只给"这次决策用得上"的几条；档案（facts/lessons）
   交给 memory_recall 按需取，不再每轮注入。
4. token 账要能对得上：卡片/记忆/摘要都有硬上限，越界由各自的压缩函数兜住。
"""

from __future__ import annotations

import json
from typing import Any

from src.agent.llm import _STEPS_DIGEST_LIMIT, LLMMissionPlanner, _steps_digest
from src.agent.loop_types import LoopDecision


def _planner_with_capturing_client(monkeypatch, native: bool = True):
    """返回 (planner, captured)：captured["payload"] 是模型实际收到的 JSON 载荷。"""
    import src.agent.llm as llm_module

    captured: dict[str, Any] = {}

    class _Client:
        last_reasoning = ""

        def _capture(self, messages) -> None:
            content = messages[-1].get("content")
            if isinstance(content, list):
                text = next(
                    (part.get("text") for part in content if isinstance(part, dict) and part.get("type") == "text"),
                    "",
                )
            else:
                text = str(content or "")
            captured["payload"] = json.loads(text)

        def chat_tools(self, messages, tools):
            self._capture(messages)
            captured["tools"] = tools
            return [{"name": "drone_get_status", "arguments": {}}], "", {}

        def chat_json(self, messages):
            # JSON 模式（不支持原生工具调用的模型）走这条路；载荷形状同样要可查。
            self._capture(messages)
            captured["tools"] = []
            return {"action": "drone_get_status", "reason": "", "params": {}}, {}

    monkeypatch.setattr(llm_module, "_create_client", lambda config: _Client())
    planner = LLMMissionPlanner()
    config = {
        "api_type": "openai",
        "model": "dots-studio",
        "provider": "dots",
        # 假 key：client 已被替换，这里只是让 _enabled() 放行
        "api_key": "test-key",
        "native_tools": native,
        "context_window": 64000,
    }
    monkeypatch.setattr(planner, "_resolve_config", lambda model_id=None: config)
    return planner, captured, config



def _cards() -> list[dict[str, Any]]:
    """至少一张卡片：原生路径要有 tools schema 才会走 tool-calling 分支。"""
    return [
        {
            "name": "drone_get_status",
            "purpose": "读状态",
            "when_to_use": "每步",
            "inputs": {"vehicle_name": "x"},
            "outputs": "巨大的输出说明",
            "failure_policy": "不重试",
            "verification": "n/a",
        }
    ]

def _loop_state(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_id": "r",
        "status": "running",
        "decisions": [{"action": "drone_get_status", "reason": "x"}],
        "results": results,
        "failure_reason": "",
    }


def test_native_mode_omits_tool_cards_but_json_mode_keeps_them(monkeypatch):
    """原生模式：卡片正文不再重复；JSON 模式：仍要发精简后的卡片。"""
    cards = [{"name": "drone_get_status", "purpose": "读状态", "when_to_use": "每步", "inputs": {"vehicle_name": "x"},
              "outputs": "巨大的输出说明", "failure_policy": "不重试", "verification": "n/a"}]

    planner, captured, _ = _planner_with_capturing_client(monkeypatch, native=True)
    planner.decide_next_step(
        command="测试", loop_state=_loop_state([]), observation={}, tool_cards=cards,
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )
    assert captured["tools"], "原生模式必须把工具 schema 交给 API"
    assert "available_tool_cards" not in captured["payload"]

    planner_json, captured_json, _ = _planner_with_capturing_client(monkeypatch, native=False)
    planner_json.decide_next_step(
        command="测试", loop_state=_loop_state([]), observation={}, tool_cards=cards,
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )
    sent = captured_json["payload"]["available_tool_cards"]
    assert sent and sent[0]["name"] == "drone_get_status"
    # 精简字段：只留"怎么选/怎么调"需要的
    for dropped in ("outputs", "failure_policy", "verification", "subtools", "execution_mode"):
        assert dropped not in sent[0], dropped


def test_loop_payload_carries_a_bounded_steps_digest(monkeypatch):
    """轨迹要有摘要（一行一步），且条数有上限——历史靠摘要，不靠原样重发。"""
    results = [
        {
            "tool": "airsim_get_depth_map",
            "ok": True,
            "params": {"query_points": "0.52,0.34"},
            "data": {"status": "ok", "distance_median_m": 72.0 - index, "image_base64": "A" * 5000},
        }
        for index in range(12)
    ]
    planner, captured, _ = _planner_with_capturing_client(monkeypatch, native=True)
    planner.decide_next_step(
        command="测试", loop_state=_loop_state(results), observation={}, tool_cards=_cards(),
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )

    digest = captured["payload"]["loop_state"]["steps_digest"]
    assert len(digest) == 12
    assert digest[0].startswith("1. airsim_get_depth_map ok")
    assert "distance_median_m=72.0" in digest[0]
    # 摘要里不该出现图片负载
    assert all("base64" not in line for line in digest)
    # 最近几步的完整形状另有其位，但同样不许带图：图片负载必须被换成
    # has_image / image_base64_bytes 这类占位。注意别用子串判断——
    # "image_base64" 是 "image_base64_bytes" 的子串，会误报。
    for row in captured["payload"]["loop_state"]["recent_results"]:
        dumped = json.dumps(row, ensure_ascii=False)
        assert '"image_base64":' not in dumped, dumped[:200]
        assert "A" * 100 not in dumped


def test_steps_digest_is_capped():
    rows = [{"tool": "drone_get_status", "ok": True, "data": {"status": "ok"}} for _ in range(_STEPS_DIGEST_LIMIT + 15)]
    digest = _steps_digest(rows)
    assert len(digest) == _STEPS_DIGEST_LIMIT
    assert digest[0].startswith("16.")  # 最老的先丢，序号保持真实步号


def test_loop_memory_payload_is_scoped(monkeypatch):
    """逐轮记忆只给行动相关的几条；档案（facts/lessons）不再每轮注入。"""
    memory = {
        "session": {"last_task_start_position_ned": {"x": 1, "y": 2, "z": -3}},
        "missions": [{"summary": f"任务{i}", "status": "completed"} for i in range(6)],
        "lessons": ["很长的教训" * 50] * 8,
        "facts": ["很长的档案" * 50] * 8,
        "risk_events": [{"reason": "x"}] * 5,
        "guidance": {
            "preferred_tools": [{"name": f"t{i}"} for i in range(8)],
            "caution_tools": [{"name": f"c{i}"} for i in range(8)],
            "preferred_skills": [{"name": f"s{i}"} for i in range(8)],
            "recent_risk_events": [{"reason": f"r{i}"} for i in range(5)],
        },
    }
    planner, captured, _ = _planner_with_capturing_client(monkeypatch, native=True)
    planner.decide_next_step(
        command="测试", loop_state=_loop_state([]), observation={}, tool_cards=_cards(),
        capabilities={}, memory=memory, model_id=None, skill_guidance=[],
    )

    snapshot = captured["payload"]["memory_snapshot"]
    assert len(snapshot["recent_missions"]) == 3
    assert len(snapshot["guidance"]["preferred_tools"]) == 3
    assert len(snapshot["guidance"]["caution_tools"]) == 3
    assert len(snapshot["guidance"]["preferred_skills"]) == 2
    for dropped in ("facts", "lessons", "risk_events", "skill_candidates", "runs"):
        assert dropped not in snapshot, dropped
    # 整体必须小：这是"每轮注入"的东西，不能是档案搬运
    assert len(json.dumps(snapshot, ensure_ascii=False)) < 3000


def test_context_pressure_switches_to_the_tight_digest(monkeypatch):
    """用量超窗口 70% → 自动收紧（摘要 8 行、只留最近 1 条结果）并上报压力。

    这是"自动压缩"的确定性版本：不用 LLM 生成摘要，只按阈值收缩 + 让运行层发
    一条可见事件。此前没有任何按实际用量做的反馈——长会话里只能等 ContextBudget
    兜底（那是丢内容，不是压缩）。
    """
    results = [
        {"tool": "airsim_get_depth_map", "ok": True, "data": {"status": "ok", "distance_median_m": 10.0 + i}}
        for i in range(20)
    ]
    planner, captured, config = _planner_with_capturing_client(monkeypatch, native=True)
    planner.last_usage = {"prompt_tokens": int(config["context_window"] * 0.8)}

    planner.decide_next_step(
        command="测试", loop_state=_loop_state(results), observation={}, tool_cards=_cards(),
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )

    loop_state = captured["payload"]["loop_state"]
    assert len(loop_state["steps_digest"]) == 8, len(loop_state["steps_digest"])
    assert len(loop_state["recent_results"]) == 1
    assert planner.last_context_pressure, planner.last_context_pressure
    assert planner.last_context_pressure["ratio"] >= 0.7

    # 低于阈值：不收紧、不报压力
    planner.last_usage = {"prompt_tokens": int(config["context_window"] * 0.3)}
    planner.decide_next_step(
        command="测试", loop_state=_loop_state(results), observation={}, tool_cards=_cards(),
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )
    assert len(captured["payload"]["loop_state"]["steps_digest"]) == 20
    assert planner.last_context_pressure is None


def test_active_skill_narrows_the_prompt_menu(monkeypatch):
    """按任务类型收窄：激活技能后，只有 常备 ∪ 用过 ∪ 技能 subtools 进菜单。

    收窄的只是提示词/API 菜单；决策白名单仍是全量（模型叫得出名字就允许执行），
    所以藏工具不会变成"不可用工具"的硬拒绝。
    """
    cards = [
        {"name": "skill:visual-approach", "kind": "skill", "purpose": "靠近", "when_to_use": "靠近",
         "inputs": {}, "subtools": ["inspect_current_frame", "airsim_get_depth_map", "drone_move_relative"]},
        {"name": "inspect_current_frame", "purpose": "看图", "when_to_use": "看", "inputs": {}},
        {"name": "airsim_get_depth_map", "purpose": "测距", "when_to_use": "量", "inputs": {}},
        {"name": "drone_move_relative", "purpose": "移动", "when_to_use": "走", "inputs": {}},
        {"name": "drone_get_status", "purpose": "状态", "when_to_use": "读", "inputs": {}},
        {"name": "drone_upload_mission", "purpose": "航线", "when_to_use": "航线", "inputs": {}},
        {"name": "drone_start_mission", "purpose": "航线", "when_to_use": "航线", "inputs": {}},
    ]
    state = _loop_state([
        {"tool": "skill:visual-approach", "ok": True, "params": {}, "data": {"status": "ok"}},
    ])
    planner, captured, _ = _planner_with_capturing_client(monkeypatch, native=False)
    planner.decide_next_step(
        command="测试", loop_state=state, observation={}, tool_cards=cards,
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )
    names = {card["name"] for card in captured["payload"]["available_tool_cards"]}
    assert "inspect_current_frame" in names and "drone_move_relative" in names
    assert "skill:visual-approach" in names  # 用过的工具保留
    assert "drone_upload_mission" not in names and "drone_start_mission" not in names

    # 没有技能激活：任务类型未知 → 不过滤
    planner.decide_next_step(
        command="测试", loop_state=_loop_state([]), observation={}, tool_cards=cards,
        capabilities={}, memory={}, model_id=None, skill_guidance=[],
    )
    names_all = {card["name"] for card in captured["payload"]["available_tool_cards"]}
    assert {"drone_upload_mission", "drone_start_mission"} <= names_all
