from __future__ import annotations

import time
from types import SimpleNamespace

from src.agent.llm import infer_model_capabilities
from src.agent.runtime import _application_settings
from src.agent.tool_executor import ToolRuntime
from src.modules.backends.mavlink import MavlinkController


def test_model_capabilities_default_to_provider_auto_detection() -> None:
    vision = infer_model_capabilities("gpt-4.1", "openai")
    mimo = infer_model_capabilities("xiaomi/mimo-v2.5", "openrouter")
    text = infer_model_capabilities("deepseek-chat", "deepseek")
    override = infer_model_capabilities("gpt-4.1", "openai", "text")

    assert vision["multimodal"] is True
    assert mimo["multimodal"] is True
    assert vision["context_window"] == 1_000_000
    assert text["multimodal"] is False
    assert override["multimodal"] is False
    assert override["capability_source"] == "manual"


def test_application_settings_keep_fast_setup_and_bounded_map_guard() -> None:
    settings = _application_settings({
        "application": {
            "telemetry": {"setup_refresh_ms": 5},
            "safety": {"max_display_jump_m": 99_999},
        },
    })

    assert settings["telemetry"]["refresh_ms"] == 250
    assert settings["telemetry"]["setup_refresh_ms"] == 50
    assert settings["safety"]["max_display_jump_m"] == 5000.0


def test_operation_contract_never_blends_real_px4_and_simulation_channels() -> None:
    runtime = ToolRuntime(backend_id="px4_mavlink")
    runtime.backend_profile = runtime.backend_registry.require("px4_mavlink")
    runtime._real_vehicle = True
    runtime.controller = SimpleNamespace(is_connected=True)

    contract = runtime._operation_contract({
        "map_position_valid": False,
        "position_source": "local_position_ned",
    })

    assert contract["vehicle_kind"] == "real_px4"
    assert contract["command_channel"] == "MAVLink"
    assert contract["mission_channel"] == "PX4 native mission protocol"
    assert contract["return_channel"] == "PX4 native RTL mode"
    assert contract["global_mission_ready"] is False


def test_real_px4_map_position_rejects_poor_gps_accuracy() -> None:
    controller = MavlinkController()
    controller._real_vehicle = True
    controller._last_global_position = time.time()
    controller._last_local_position = 0.0
    controller._last_heartbeat = time.time()
    controller._telemetry["HEARTBEAT"] = {"armed": False}
    controller._telemetry["GLOBAL_POSITION_INT"] = {
        "lat": 39.9042,
        "lon": 116.4074,
        "alt": 48.0,
        "relative_alt": 0.0,
        "hdg": 0.0,
    }
    controller._telemetry["GPS_RAW_INT"] = {
        "fix_type": 3,
        "horizontal_accuracy_m": 80.0,
        "satellites_visible": 12,
    }

    poor = controller._status_from_current_telemetry().to_dict()
    controller._telemetry["GPS_RAW_INT"]["horizontal_accuracy_m"] = 2.5
    good = controller._status_from_current_telemetry().to_dict()

    assert poor["map_position_valid"] is False
    assert poor["position_source"] == "none"
    assert good["map_position_valid"] is True
    assert good["position_source"] == "gps"


def test_agent_context_is_system_md_plus_notes_md(monkeypatch, tmp_path):
    """常驻上下文 = SYSTEM.md（操作规范）+ NOTES.md（长期笔记）。

    记忆参照 pi：知识是自动注入的文件，不是模型调用的"记忆工具"
    （memory_store / memory_recall / memory_remember 已删除）。
    """
    from src.agent.runtime import AgentRuntime
    from src.agent.tool_cards import TOOL_CARDS

    system_path = tmp_path / "SYSTEM.md"
    notes_path = tmp_path / "NOTES.md"
    system_path.write_text("系统规范：未起飞不能转向。", encoding="utf-8")
    notes_path.write_text("长期笔记：三号院果树区不要低于 3 m。", encoding="utf-8")
    monkeypatch.setattr(AgentRuntime, "_AGENT_INSTRUCTIONS_PATH", system_path)
    monkeypatch.setattr(AgentRuntime, "_AGENT_NOTES_PATH", notes_path)

    merged = AgentRuntime._agent_instructions()
    assert "系统规范" in merged and "长期笔记" in merged

    # 两份都缺 → 不注入任何东西（不是空字符串加分隔符）
    monkeypatch.setattr(AgentRuntime, "_AGENT_INSTRUCTIONS_PATH", tmp_path / "missing.md")
    monkeypatch.setattr(AgentRuntime, "_AGENT_NOTES_PATH", tmp_path / "missing2.md")
    assert AgentRuntime._agent_instructions() == ""

    # 记忆不再是工具：模型面没有这些卡片
    for gone in ("memory_store", "memory_recall", "memory_remember"):
        assert gone not in TOOL_CARDS, f"{gone} 不该再出现在工具卡片里"


def test_ui_route_table_is_the_single_source_of_truth():
    """HTTP 路由表：声明式、可枚举、自文档（``GET /api/endpoints`` 直接吐它）。

    契约：每条路由都要有方法/路径/摘要，处理器真实存在；匹配语义是"精确优先、
    前缀其次"；已删除的残留端点不得复活。
    """
    from src.ui.server import ROUTES, AgentRequestHandler, match_route

    assert len(ROUTES) >= 50, f"端点数量异常: {len(ROUTES)}"
    seen: set[tuple[str, str]] = set()
    for route in ROUTES:
        assert route.method in {"GET", "POST"}, route
        assert route.pattern.startswith("/"), route
        assert route.summary and len(route.summary) >= 3, route
        assert callable(getattr(AgentRequestHandler, route.handler, None)), f"{route.handler} 不存在"
        key = (route.method, route.pattern)
        assert key not in seen, f"重复路由: {key}"
        seen.add(key)
    assert ("GET", "/api/endpoints") in seen, "自文档端点缺失"

    # 匹配语义：精确优先，其次前缀
    assert match_route("GET", "/api/state").pattern == "/api/state"  # type: ignore[union-attr]
    assert match_route("GET", "/api/sessions/abc").pattern == "/api/sessions/*"  # type: ignore[union-attr]
    assert match_route("POST", "/api/sessions/xyz").pattern == "/api/sessions*"  # type: ignore[union-attr]
    assert match_route("GET", "/api/nope") is None

    # 残留端点已删除，不得复活：/api/backend（与 activate 重复）与
    # /api/camera/frame（面板拍照，前端从未调用）
    assert match_route("POST", "/api/backend") is None
    assert match_route("POST", "/api/camera/frame") is None


def test_operator_device_actions_share_the_governed_entry():
    """设备动作的唯一入口是 ``gcs.command.execute_tool``：先安全校验，再走治理入口。

    这样"操作员手动点一下"和"Agent 自己飞"共享同一套守卫（执行槽、编队冲突、
    真机审批、后端切换检查）；被安全层拦下的动作根本到不了执行器。
    """
    from types import SimpleNamespace

    from src.agent.tool_executor import ToolRuntime
    from src.gcs.services import GroundStationServices, ToolCommandManager

    rt = ToolRuntime(backend_id="airsim")
    assert rt.ensure_ready(), rt.init_error
    governed: list[tuple[str, dict]] = []

    def governed_executor(tool, params, dry_run=False):
        governed.append((tool, dict(params)))
        return SimpleNamespace(ok=True, to_dict=lambda: {"status": "ok"})

    services = GroundStationServices(rt, governed_executor=governed_executor)
    for tool in ("drone_arm", "drone_takeoff", "drone_land", "drone_hover"):
        assert tool in ToolCommandManager.DEVICE_TOOLS, tool

    result = services.command.execute_tool("drone_takeoff", {"altitude": 3.0}, vehicle_id="Drone2")
    assert result.ok is True
    assert governed == [("drone_takeoff", {"altitude": 3.0, "vehicle_name": "Drone2"})]

    # 安全层拦下的动作（正高度 = 撞地）不会到治理入口
    blocked = services.command.execute_tool("drone_fly_to", {"x": 1.0, "y": 0.0, "z": 5.0})
    assert blocked.ok is False
    assert len(governed) == 1, governed
