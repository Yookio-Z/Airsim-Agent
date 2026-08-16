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
    "drone_list_vehicles": _atomic("drone_list_vehicles", "core", "telemetry", ("telemetry",)),
    "drone_get_status": _atomic(
        "drone_get_status",
        "core",
        "telemetry",
        ("telemetry",),
        future="Real vehicles and ROS bridges should normalize telemetry into the shared GroundStationState model.",
    ),
    "drone_get_firmware_info": _atomic(
        "drone_get_firmware_info",
        "core",
        "telemetry",
        ("telemetry",),
        notes="Requests MAVLink AUTOPILOT_VERSION, including PX4 firmware version, board ids, UID, capabilities, and git hash when available.",
    ),
    "drone_get_parameters": _atomic(
        "drone_get_parameters",
        "core",
        "telemetry",
        ("telemetry",),
        notes="Requests or queries the cached MAVLink PARAM_VALUE list. Read-only; it does not write, calibrate, or flash firmware.",
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
    "drone_fly_velocity": _atomic(
        "drone_fly_velocity",
        "core",
        "vehicle_command",
        ("flight_control",),
        notes="vehicle_name: 空=默认机, all=全部, 或具体载具名; Low-level command; skills should bound duration and speed.",
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
    "airsim_take_photo": _atomic(
        "airsim_take_photo",
        "perception",
        "image_source",
        ("image_capture",),
        future="ROS image topics should adapt into the same image_source surface before agent use.",
    ),
    "airsim_get_sensors": _atomic("airsim_get_sensors", "perception", "sensor_source", ("telemetry",)),
    "airsim_get_depth_map": _atomic(
        "airsim_get_depth_map",
        "perception",
        "depth_source",
        ("depth_perception",),
        future="ROS depth topics or point clouds should normalize here or into a mapping provider.",
    ),
    "airsim_detect_objects": _atomic(
        "airsim_detect_objects",
        "perception",
        "detection_source",
        ("object_detection",),
        notes="Single-frame inference is atomic. Search/tracking loops are not.",
    ),
    "airsim_vlm_confirm_target": _atomic("airsim_vlm_confirm_target", "perception", "vlm", ("image_capture",)),
    "airsim_vlm_analyze_image": _atomic("airsim_vlm_analyze_image", "perception", "vlm", ("image_capture",)),
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
    "airsim_task_status": _atomic("airsim_task_status", "task", "async_task", ("target_search",)),
    "airsim_task_cancel": _atomic("airsim_task_cancel", "task", "async_task", ("target_search",)),

    # Legacy workflow names kept only as migration records.
    "airsim_search_target": _skill_candidate(
        "airsim_search_target",
        "search",
        "workflow",
        ("flight_control", "telemetry", "target_search"),
        "skill:search",
        notes="Former hardcoded workflow name. Runtime registration has been removed; use the skill/provider contract.",
        future="ROS search should compose planner, image/detection, map, and command surfaces instead of exposing one hardcoded MCP tool.",
    ),
    "airsim_approach_target": _skill_candidate(
        "airsim_approach_target",
        "tracking",
        "workflow",
        ("flight_control", "telemetry", "target_search"),
        "skill:approach_target",
        future="For real vehicles, require depth/world-frame target localization and safety approval before movement.",
    ),
    "airsim_track_object": _skill_candidate(
        "airsim_track_object",
        "tracking",
        "workflow",
        ("flight_control", "target_tracking"),
        "skill:track_object",
        future="ROS tracking should use a TrackingProvider/action interface and keep the agent out of raw topic timing.",
    ),
    "airsim_check_obstacle": _skill_candidate(
        "airsim_check_obstacle",
        "perception",
        "workflow",
        ("depth_perception", "obstacle_avoidance"),
        "skill:avoid_obstacle",
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
        "skill:patrol_area",
        notes="Coverage path generation belongs in a skill or mission planner.",
    ),
    "memory_store": ToolManifestEntry(
        name="memory_store",
        group="memory",
        kind="internal",
        surface="memory",
        recommended_layer="runtime",
        notes="Handled by AgentRuntime, not by external MCP servers.",
    ),
}


def manifest_for(name: str) -> ToolManifestEntry | None:
    return TOOL_MANIFEST.get(name)


def list_tool_manifest() -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in sorted(TOOL_MANIFEST.values(), key=lambda item: item.name)]


def manifest_metadata(name: str) -> dict[str, Any]:
    entry = manifest_for(name)
    return entry.to_dict() if entry else {}
