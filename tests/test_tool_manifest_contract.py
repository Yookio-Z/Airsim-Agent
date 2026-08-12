"""Contract tests: registered tools vs the declarative TOOL_MANIFEST.

The manifest (src/tools/manifest.py) is the documentation-of-record for the
tool layer. These tests prevent silent drift in both directions:
  * a newly registered tool that was never documented, and
  * a documented atomic tool whose registration disappeared.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agent.tool_cards import TOOL_CARDS
from src.agent.tool_executor import ToolCollector
from src.tools.core import register_core_tools
from src.tools.manifest import TOOL_MANIFEST, list_tool_manifest
from src.tools.perception import register_perception_tools
from src.tools.providers import register_provider_tools
from src.tools.vision import register_vision_tools


def _capability_stub_controller() -> SimpleNamespace:
    """Controller stub exposing every capability-gated method, so all
    backend-neutral tools register. AirSim-only groups are excluded here
    because register_perception_tools/register_vision_tools require an
    actual AirSimController instance."""
    return SimpleNamespace(
        last_error="",
        get_firmware_info=lambda *a, **k: None,
        get_parameters=lambda *a, **k: {},
        get_last_path_error=lambda *a, **k: None,
        upload_mission=lambda *a, **k: None,
        download_mission=lambda *a, **k: None,
        clear_mission=lambda *a, **k: None,
        start_mission=lambda *a, **k: None,
        get_mission_progress=lambda *a, **k: None,
    )


def _register_all_neutral_tools() -> set[str]:
    collector = ToolCollector()
    register_core_tools(collector, _capability_stub_controller(), lambda data: "{}")
    register_provider_tools(collector, _capability_stub_controller(), lambda data: "{}")
    return set(collector.tools)


def _manifest_entries() -> list[dict]:
    return list_tool_manifest()


def _manifest_by_kind(kind: str) -> set[str]:
    return {entry["name"] for entry in _manifest_entries() if entry["kind"] == kind}


def test_every_registered_backend_neutral_tool_is_documented() -> None:
    registered = _register_all_neutral_tools()
    undocumented = sorted(registered - set(TOOL_MANIFEST))
    assert undocumented == [], (
        f"registered tools missing from TOOL_MANIFEST: {undocumented}. "
        "Add a manifest entry when registering a new tool."
    )


def test_airsim_atomic_tools_are_documented() -> None:
    # AirSim-only tool groups (perception/vision) gate on isinstance
    # AirSimController, so they cannot register against a stub. Their names
    # are the documented contract of the airsim backend surface.
    airsim_atomics = {
        "airsim_detect_objects",
        "airsim_get_depth_map",
        "airsim_get_sensors",
        "airsim_take_photo",
        "airsim_task_cancel",
        "airsim_task_status",
        "airsim_vlm_analyze_image",
        "airsim_vlm_confirm_target",
    }
    missing = sorted(airsim_atomics - set(TOOL_MANIFEST))
    assert missing == [], f"airsim atomic tools missing from TOOL_MANIFEST: {missing}"
    wrong_kind = sorted(airsim_atomics - _manifest_by_kind("atomic"))
    assert wrong_kind == [], f"airsim tools not marked atomic: {wrong_kind}"


def test_every_atomic_manifest_entry_has_a_registration_source() -> None:
    """Each atomic manifest entry must be registered by some backend surface.

    AirSim-specific atomics come from the perception/vision groups; the
    remaining atomics must all be backend-neutral (registered in the probe).
    """
    neutral = _register_all_neutral_tools()
    airsim_atomics = {
        "airsim_detect_objects",
        "airsim_get_depth_map",
        "airsim_get_sensors",
        "airsim_take_photo",
        "airsim_task_cancel",
        "airsim_task_status",
        "airsim_vlm_analyze_image",
        "airsim_vlm_confirm_target",
    }
    atomics = _manifest_by_kind("atomic")
    unreachable = sorted(atomics - neutral - airsim_atomics)
    assert unreachable == [], (
        f"atomic manifest entries with no registration source: {unreachable}. "
        "Either the tool was removed (drop the manifest entry) or a new "
        "registration group appeared without manifest coverage."
    )


def test_workflow_entries_are_skill_candidates_not_registered_tools() -> None:
    neutral = _register_all_neutral_tools()
    workflows = _manifest_by_kind("workflow")
    assert workflows, "expected workflow entries in the manifest"
    wrongly_registered = sorted(workflows & neutral)
    assert wrongly_registered == [], (
        f"legacy workflow tools must not be registered as atomic tools: {wrongly_registered}"
    )
    by_name = {entry["name"]: entry for entry in _manifest_entries()}
    for name in workflows:
        entry = by_name[name]
        assert entry["recommended_layer"] == "skill", f"{name} should route to a skill"
        assert entry["replacement_skill"], f"{name} should declare replacement_skill"


def test_all_tool_cards_are_documented_in_manifest() -> None:
    undocumented = sorted(set(TOOL_CARDS) - set(TOOL_MANIFEST))
    assert undocumented == [], (
        f"TOOL_CARDS entries missing from TOOL_MANIFEST: {undocumented}"
    )


def test_internal_manifest_entries_are_not_registered_tools() -> None:
    neutral = _register_all_neutral_tools()
    internals = _manifest_by_kind("internal")
    assert internals == {"memory_store"}, f"unexpected internal entries: {internals}"
    assert not (internals & neutral), "internal entries must not be registered"
