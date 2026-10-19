"""Tool layer manifest for the UAV agent runtime.

The manifest is intentionally separate from the MCP registration code. It tells
the agent which tools are stable atomic controls and which tools should be
treated as higher-level skills or legacy compatibility entry points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolManifestEntry:
    name: str
    group: str
    kind: str
    surface: str
    required_capabilities: tuple[str, ...] = ()
    recommended_layer: str = "tool"
    replacement_skill: str = ""
    stable: bool = True
    notes: str = ""
    future_backend_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "kind": self.kind,
            "surface": self.surface,
            "required_capabilities": list(self.required_capabilities),
            "recommended_layer": self.recommended_layer,
            "replacement_skill": self.replacement_skill,
            "stable": self.stable,
            "notes": self.notes,
            "future_backend_notes": self.future_backend_notes,
            "metadata": dict(self.metadata),
        }


def _atomic(
    name: str,
    group: str,
    surface: str,
    capabilities: tuple[str, ...],
    notes: str = "",
    future: str = "",
) -> ToolManifestEntry:
    return ToolManifestEntry(
        name=name,
        group=group,
        kind="atomic",
        surface=surface,
        required_capabilities=capabilities,
        notes=notes,
        future_backend_notes=future,
    )


def _skill_candidate(
    name: str,
    group: str,
    surface: str,
    capabilities: tuple[str, ...],
    replacement_skill: str,
    notes: str = "",
    stable: bool = False,
    future: str = "",
) -> ToolManifestEntry:
    return ToolManifestEntry(
        name=name,
        group=group,
        kind="workflow",
        surface=surface,
        required_capabilities=capabilities,
        recommended_layer="skill",
        replacement_skill=replacement_skill,
        stable=stable,
        notes=notes,
        future_backend_notes=future,
    )


TOOL_MANIFEST: dict[str, ToolManifestEntry] = {
    # Link and telemetry.
    "drone_connect": _atomic(
        "drone_connect",
        "core",
        "link",
        ("telemetry",),
        future="ROS backends should implement this through a bridge/link profile, not a topic publish.",
    ),
    "drone_disconnect": _atomic("drone_disconnect", "core", "link", ("telemetry",)),
    "formation_command": _atomic(
        "formation_command",
        "core",
        "formation",
        ("flight_control",),
        notes="Multi-vehicle formation and coverage control on the airsim / px4_mavlink backends. A deterministic 10Hz velocity loop maintains the formation; the agent issues high-level intents and polls action=status until stable. Single-vehicle flight tools are blocked while a formation is active.",
    ),
    "drone_get_status": _atomic(
        "drone_get_status",
        "core",
        "telemetry",
        ("telemetry",),
        future="Real vehicles and ROS bridges should normalize telemetry into the shared GroundStationState model.",
    ),

    # Vehicle command surface.
    "drone_arm": _atomic("drone_arm", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_disarm": _atomic("drone_disarm", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_takeoff": _atomic("drone_takeoff", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_land": _atomic("drone_land", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_hover": _atomic("drone_hover", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_fly_to": _atomic("drone_fly_to", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_move_relative": _atomic(
        "drone_move_relative",
        "core",
        "vehicle_command",
        ("flight_control", "telemetry"),
        notes="vehicle_name: 空=默认机, all=全部, 或具体载具名; Small convenience wrapper over telemetry plus absolute local movement.",
    ),
    "drone_rotate_to": _atomic("drone_rotate_to", "core", "vehicle_command", ("flight_control",), notes="vehicle_name: 空=默认机, all=全部, 或具体载具名"),
    "drone_set_mode": _atomic("drone_set_mode", "core", "mode", ("mode_control",)),

    # Mission management surface. These are atomic service calls, but high-risk.
    "drone_fly_path": _atomic(
        "drone_fly_path",
        "core",
        "mission",
        ("flight_control",),
        notes="vehicle_name: 空=默认机, all=全部, 或具体载具名; Local NED path execution. Complex path planning belongs in skills or planners.",
    ),
    "drone_upload_mission": _atomic("drone_upload_mission", "core", "mission", ("flight_control", "gps", "mode_control")),
    "drone_download_mission": _atomic("drone_download_mission", "core", "mission", ("gps", "mode_control")),
    "drone_clear_mission": _atomic("drone_clear_mission", "core", "mission", ("flight_control", "gps", "mode_control")),
    "drone_start_mission": _atomic("drone_start_mission", "core", "mission", ("flight_control", "gps", "mode_control")),
    "drone_get_mission_progress": _atomic("drone_get_mission_progress", "core", "mission", ("gps", "mode_control")),

    # Perception.
    "airsim_get_sensors": _atomic("airsim_get_sensors", "perception", "sensor_source", ("telemetry",)),
    "locate_image_point": _atomic(
        "locate_image_point",
        "perception",
        "depth_source",
        ("depth_perception",),
        notes="The system's single geometry tool: normalised image point(s) -> metric distance + world position (NED), multi-point reads for target sampling / clearance. Pure geometry, no detection.",
    ),
    "provider_bridge_health": _atomic(
        "provider_bridge_health",
        "provider",
        "provider_bridge",
        ("ros2_topics",),
        notes="Checks the external ROS/provider bridge without touching vehicle control.",
    ),
    "provider_obstacle_summary": _atomic(
        "provider_obstacle_summary",
        "provider",
        "obstacle_provider",
        ("ros2_topics", "obstacle_avoidance"),
        notes="Reads a provider-normalized obstacle or costmap summary. It does not decide a mission policy.",
    ),
    "provider_validate_motion": _atomic(
        "provider_validate_motion",
        "provider",
        "obstacle_provider",
        ("ros2_topics", "obstacle_avoidance"),
        notes="Validates a proposed body-frame motion through the provider. Movement remains a separate command.",
    ),

    # Async task manager.

    # Legacy workflow names kept only as migration records.
    "airsim_search_target": _skill_candidate(
        "airsim_search_target",
        "search",
        "workflow",
        ("flight_control", "telemetry", "target_search"),
        "skill:region-search",
        notes="Former hardcoded workflow name. Runtime registration has been removed; use the skill/provider contract.",
        future="ROS search should compose planner, image/detection, map, and command surfaces instead of exposing one hardcoded MCP tool.",
    ),
    "airsim_approach_target": _skill_candidate(
        "airsim_approach_target",
        "tracking",
        "workflow",
        ("flight_control", "telemetry", "target_search"),
        "skill:visual-approach",
        future="For real vehicles, require depth/world-frame target localization and safety approval before movement.",
    ),
    "airsim_track_object": _skill_candidate(
        "airsim_track_object",
        "tracking",
        "workflow",
        ("flight_control", "target_tracking"),
        "skill:region-search",
        future="ROS tracking should use a TrackingProvider/action interface and keep the agent out of raw topic timing.",
    ),
    "airsim_check_obstacle": _skill_candidate(
        "airsim_check_obstacle",
        "perception",
        "workflow",
        ("depth_perception", "obstacle_avoidance"),
        "skill:visual-approach",
        notes="Depth read is atomic; avoidance policy is a skill or safety manager responsibility.",
    ),
    "airsim_formation_mission": _skill_candidate(
        "airsim_formation_mission",
        "formation",
        "workflow",
        ("flight_control", "multi_vehicle"),
        "skill:formation",
        future="Real multi-vehicle control needs a coordinator/provider with collision and approval gates.",
    ),
    "airsim_precise_formation": _skill_candidate(
        "airsim_precise_formation",
        "formation",
        "workflow",
        ("flight_control", "multi_vehicle"),
        "skill:formation",
    ),
    "airsim_patrol_area": _skill_candidate(
        "airsim_patrol_area",
        "formation",
        "workflow",
        ("flight_control",),
        "skill:formation",
        notes="Coverage path generation belongs in a skill or mission planner.",
    ),
    "inspect_current_frame": ToolManifestEntry(
        name="inspect_current_frame",
        group="perception",
        kind="atomic",
        surface="perception",
        recommended_layer="tool",
        stable=True,
        notes="Multimodal analysis of the latest perception axis frame (requires a vision-capable model).",
        future_backend_notes="Works for sim and real frames via the perception axis cache.",
    ),
    "perception_status": ToolManifestEntry(
        name="perception_status",
        group="perception",
        kind="atomic",
        surface="perception",
        recommended_layer="tool",
        stable=True,
        notes="Read-only camera/frame health (online, fps, capture_age_s, view). No detection information.",
        future_backend_notes="Works unchanged for sim (local engine) and real vehicles (remote Jetson engine).",
    ),
}


def manifest_for(name: str) -> ToolManifestEntry | None:
    return TOOL_MANIFEST.get(name)


def list_tool_manifest() -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in sorted(TOOL_MANIFEST.values(), key=lambda item: item.name)]


def manifest_metadata(name: str) -> dict[str, Any]:
    entry = manifest_for(name)
    return entry.to_dict() if entry else {}
