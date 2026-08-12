from __future__ import annotations

import time
from types import SimpleNamespace

from src.agent.llm import infer_model_capabilities
from src.agent.runtime import _application_settings
from src.agent.tool_executor import ToolRuntime
from src.modules.mavlink_controller import MavlinkController


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
