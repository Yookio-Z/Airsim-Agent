"""规划与看图必须是同一个模型。

用户配了哪个模型，规划与图像分析就都用它：系统不替他另选一个"视觉模型"，
也不在提交带图指令时偷偷切换模型。这些断言锁住那条约束——"两路模型"曾经
同时存在于三个地方（后端 model_id 解析、前端自动切换、可用性上报），任何
一处回潮都会让"看的图"和"做的决策"来自不同模型。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from _runtime_factories import agent_runtime
from src.agent.llm import LLMMissionPlanner, LLMUnavailableError, ModelRegistry
from src.agent.run_state import RunState

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(model_id: str) -> RunState:
    return RunState(run_id="run_1", command="找一辆白车", intent="search_target", summary="找白车", model_id=model_id)


def _runtime(planner: object) -> object:
    return agent_runtime(planner=planner)


def test_active_model_id_follows_current_run():
    """任务在跑时取任务的模型；没任务时交回默认值（""）。"""
    runtime = _runtime(SimpleNamespace(analyze_image=lambda *a, **k: {}))
    assert runtime._active_model_id() == ""
    runtime._current = _run("model_B")
    assert runtime._active_model_id() == "model_B"
    runtime._current = None
    assert runtime._active_model_id() == ""


def test_vlm_analyze_uses_the_run_model():
    """视觉分析的 model_id 必须来自当前任务，而不是留空去回落默认值。"""
    seen: list[str] = []

    def analyze_image(question, image_b64, model_id=None):
        seen.append(str(model_id or ""))
        return {"summary_zh": "ok"}

    runtime = _runtime(SimpleNamespace(analyze_image=analyze_image))
    runtime._current = _run("model_B")
    runtime._vlm_analyze("画面里有什么", "AAAA")
    assert seen == ["model_B"]


def test_text_only_model_points_at_itself_not_a_vision_model(tmp_path):
    """配的模型看不了图时，报错要指向这个模型本身，不能暗示另有视觉模型。"""
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({
        "id": "text_only",
        "name": "纯文本模型",
        "model": "gpt-4o",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "k",
        "api_type": "openai",
        "capability_mode": "text",
    })
    planner = LLMMissionPlanner()
    planner.registry = registry

    with pytest.raises(LLMUnavailableError) as excinfo:
        planner.analyze_image("画面里有什么", "AAAA", model_id="text_only")

    message = str(excinfo.value)
    assert "纯文本模型" in message
    assert "支持图像" in message
    # 断言"没有第二个模型"这件事被说清楚，避免用户去找不存在的设置项
    assert "共用同一个模型" in message


def test_no_hidden_multimodal_auto_switch_setting():
    """自动切换模型的设置必须保持删除——它是一条隐藏的模型选择路径。"""
    from src.agent.settings_store import _default_application_settings

    assert "auto_select_multimodal_model" not in _default_application_settings()["agent"]

    for rel in ("src/ui/static/ui-main.js", "src/ui/static/ui-core.js", "src/ui/static/ui-panel-settings.js"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "auto_select_multimodal_model" not in text, f"{rel} 仍在读自动切换开关"
        assert "appAutoMultimodal" not in text, f"{rel} 仍保留自动切换的控件接线"
