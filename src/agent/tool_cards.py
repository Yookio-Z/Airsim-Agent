"""Structured tool cards used by capability-aware planners."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.tools.manifest import manifest_for


@dataclass(frozen=True)
class ToolCard:
    name: str
    purpose: str
    when_to_use: str
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: str = ""
    cost: str = "low"
    preconditions: list[str] = field(default_factory=list)
    not_for: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    risk: str = "low"
    notes: list[str] = field(default_factory=list)
    execution_mode: str = "immediate"
    kind: str = "atomic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "when_to_use": self.when_to_use,
            "inputs": dict(self.inputs),
            "outputs": self.outputs,
            "cost": self.cost,
            "preconditions": list(self.preconditions),
            "not_for": self.not_for,
            "required_capabilities": list(self.required_capabilities),
            "risk": self.risk,
            "notes": list(self.notes),
            "execution_mode": self.execution_mode,
            "kind": self.kind,
        }


TOOL_CARDS: dict[str, ToolCard] = {
    "drone_connect": ToolCard(
        name="drone_connect",
        purpose="Connect to the active vehicle backend.",
        when_to_use="At the beginning of a session or after a link failure.",
        inputs={"ip": "AirSim host", "port": "AirSim RPC port", "url": "MAVLink URL such as udp:127.0.0.1:14550"},
        outputs="Connection status and backend details.",
        required_capabilities=["telemetry"],
        preconditions=[],
        notes=["Use url for PX4/MAVLink backends and ip/port for AirSim."],
    ),
    "drone_disconnect": ToolCard(
        name="drone_disconnect",
        purpose="Disconnect from the vehicle backend.",
        when_to_use="Only when ending a session or recovering from a bad link.",
        outputs="Disconnected status.",
        required_capabilities=["telemetry"],
        risk="medium",
        not_for="Do not disconnect during an active flight mission unless explicitly requested.",
    ),
    "drone_list_vehicles": ToolCard(
        name="drone_list_vehicles",
        purpose="List vehicles known to the backend.",
        when_to_use="When selecting or confirming available vehicles.",
        outputs="Vehicle identifiers.",
        required_capabilities=["telemetry"],
    ),
    "drone_get_status": ToolCard(
        name="drone_get_status",
        purpose="Read vehicle telemetry and flight state.",
        when_to_use="Before and after actions, or when the operator asks for status.",
        outputs="Position, velocity, attitude, armed/flying state, mode, GPS, and battery when available.",
        required_capabilities=["telemetry"],
        cost="low",
    ),
    "drone_arm": ToolCard(
        name="drone_arm",
        purpose="Arm the vehicle motors.",
        when_to_use="Before takeoff when the task requires flight.",
        outputs="Arm success/failure.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "safety checks passed"],
        risk="high",
        not_for="Do not arm for read-only status or planning tasks.",
    ),
    "drone_disarm": ToolCard(
        name="drone_disarm",
        purpose="Disarm the vehicle motors.",
        when_to_use="After landing or when explicitly requested.",
        outputs="Disarm success/failure.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "vehicle is landed or safe to disarm"],
        risk="medium",
    ),
    "drone_takeoff": ToolCard(
        name="drone_takeoff",
        purpose="Take off to a target relative altitude.",
        when_to_use="When a mission requires the vehicle to become airborne.",
        inputs={"altitude": "Positive altitude in meters."},
        outputs="Takeoff command result.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "armed"],
        risk="high",
        not_for="Do not take off for pure telemetry or link-management requests.",
    ),
    "drone_land": ToolCard(
        name="drone_land",
        purpose="Land the active vehicle.",
        when_to_use="When the operator asks to land or a mission is complete and landing is required.",
        outputs="Landing command result.",
        required_capabilities=["flight_control"],
        preconditions=["connected"],
        risk="medium",
    ),
    "drone_hover": ToolCard(
        name="drone_hover",
        purpose="Hold current position or stop motion.",
        when_to_use="For pause, emergency stabilization, or after a failed non-critical action.",
        outputs="Hold/hover command result.",
        required_capabilities=["flight_control"],
        preconditions=["connected"],
        risk="low",
    ),
    "drone_fly_to": ToolCard(
        name="drone_fly_to",
        purpose="Fly to an absolute local NED coordinate.",
        when_to_use="When the target local NED position is known.",
        inputs={"x": "North meters", "y": "East meters", "z": "Down meters; negative is above origin", "velocity": "m/s"},
        outputs="Navigation command result.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "airborne or backend supports ground navigation"],
        not_for="Unknown target locations. Use search/perception first when available.",
        risk="medium",
    ),
    "drone_move_relative": ToolCard(
        name="drone_move_relative",
        purpose="Move relative to current vehicle heading.",
        when_to_use="For operator commands like forward, backward, left, right, up, or down.",
        inputs={"forward_m": "meters forward", "right_m": "meters right", "up_m": "meters upward", "velocity": "m/s"},
        outputs="Relative movement result and target NED position.",
        required_capabilities=["flight_control", "telemetry"],
        preconditions=["connected", "current position and heading available"],
        not_for="Geometric paths such as square/rectangle/orbit/circle/grid. Use drone_fly_path with explicit local NED waypoints instead.",
        risk="medium",
    ),
    "drone_fly_velocity": ToolCard(
        name="drone_fly_velocity",
        purpose="Command NED velocity for a duration or one control update.",
        when_to_use="For short low-level motion commands or skill internals.",
        inputs={"vx": "North m/s", "vy": "East m/s", "vz": "Down m/s", "duration": "seconds; 0 sends one update"},
        outputs="Velocity command result.",
        required_capabilities=["flight_control"],
        risk="high",
        not_for="High-level waypoint missions unless wrapped by a skill.",
    ),
    "drone_fly_path": ToolCard(
        name="drone_fly_path",
        purpose="Fly a local NED waypoint path.",
        when_to_use="For AirSim local path missions, geometric paths such as square/rectangle/orbit/circle/grid, or converted local mission drafts.",
        inputs={"waypoints_json": "JSON array of {x,y,z}", "velocity": "m/s"},
        outputs="Path execution result.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "waypoints validated"],
        risk="medium",
    ),
    "drone_upload_mission": ToolCard(
        name="drone_upload_mission",
        purpose="Upload a backend-neutral waypoint mission to a PX4/MAVLink vehicle.",
        when_to_use="After the operator or map planner has produced global MissionItem waypoints.",
        inputs={"waypoints_json": "JSON array of MissionItem objects or a MissionPlanDraft with items."},
        outputs="Upload acceptance, item count, and normalized mission items.",
        required_capabilities=["flight_control", "gps", "mode_control"],
        preconditions=["connected", "mission validated", "GPS available"],
        risk="high",
        not_for="Immediate manual movement commands. Use navigation tools for direct movement.",
    ),
    "drone_download_mission": ToolCard(
        name="drone_download_mission",
        purpose="Download the current mission stored on the PX4/MAVLink vehicle.",
        when_to_use="When syncing the map plan view with the active vehicle mission.",
        outputs="MissionItem-compatible waypoint list.",
        required_capabilities=["gps", "mode_control"],
        preconditions=["connected"],
        risk="low",
    ),
    "drone_clear_mission": ToolCard(
        name="drone_clear_mission",
        purpose="Clear all mission items stored on the PX4/MAVLink vehicle.",
        when_to_use="Before uploading a replacement mission or when explicitly requested by the operator.",
        outputs="Mission clear acknowledgement.",
        required_capabilities=["flight_control", "gps", "mode_control"],
        preconditions=["connected", "operator intent is explicit"],
        risk="high",
    ),
    "drone_start_mission": ToolCard(
        name="drone_start_mission",
        purpose="Start the uploaded PX4/MAVLink waypoint mission.",
        when_to_use="After mission upload and operator confirmation.",
        outputs="Mission start command acknowledgement and initial progress.",
        required_capabilities=["flight_control", "gps", "mode_control"],
        preconditions=["connected", "mission uploaded", "vehicle satisfies PX4 mission preconditions"],
        risk="high",
    ),
    "drone_get_mission_progress": ToolCard(
        name="drone_get_mission_progress",
        purpose="Read current PX4/MAVLink mission progress.",
        when_to_use="During or after a waypoint mission.",
        outputs="Current sequence, reached sequence, total count, running state, and mode.",
        required_capabilities=["gps", "mode_control"],
        preconditions=["connected"],
        risk="low",
    ),
    "drone_rotate_to": ToolCard(
        name="drone_rotate_to",
        purpose="Rotate to a target heading.",
        when_to_use="When camera orientation or search scan direction matters.",
        inputs={"heading_deg": "0 is North, clockwise positive"},
        outputs="Rotation result.",
        required_capabilities=["flight_control"],
        preconditions=["connected"],
        not_for="Building square/rectangle/orbit/circle/grid paths. Use drone_fly_path for path geometry.",
        risk="low",
    ),
    "drone_set_mode": ToolCard(
        name="drone_set_mode",
        purpose="Set flight mode, especially for PX4/MAVLink.",
        when_to_use="Before PX4 guided/offboard actions, hold, RTL, brake, or landing modes.",
        inputs={"mode": "Flight mode such as OFFBOARD, LOITER, POSCTL, RTL, LAND"},
        outputs="Mode change result.",
        required_capabilities=["mode_control"],
        preconditions=["connected"],
        risk="medium",
        notes=["AirSim may expose the tool but may not support mode changes."],
    ),
    "airsim_take_photo": ToolCard(
        name="airsim_take_photo",
        purpose="Capture an AirSim camera image and optionally verify a target class.",
        when_to_use="When the operator asks for a photo or a visual confirmation is required.",
        inputs={"camera_name": "Camera id", "image_type": "scene/depth/segmentation/infrared", "verify_target_class": "optional class"},
        outputs="Image path/base64 and optional visual verification result.",
        required_capabilities=["image_capture"],
        preconditions=["AirSim backend connected"],
        cost="medium",
        risk="low",
    ),
    "airsim_get_sensors": ToolCard(
        name="airsim_get_sensors",
        purpose="Read AirSim sensor data.",
        when_to_use="When sensor diagnostics or simulator sensor state is needed.",
        outputs="Sensor readings.",
        required_capabilities=["telemetry"],
        preconditions=["AirSim backend connected"],
    ),
    "airsim_get_depth_map": ToolCard(
        name="airsim_get_depth_map",
        purpose="Read AirSim depth data for distance or obstacle reasoning.",
        when_to_use="Before visual approach, obstacle reasoning, or depth-based verification.",
        inputs={"camera_name": "Camera id", "query_points": "optional pixel points x,y;x,y"},
        outputs="Depth summary and optional query distances.",
        required_capabilities=["depth_perception"],
        preconditions=["AirSim backend connected"],
        cost="medium",
    ),
    "airsim_detect_objects": ToolCard(
        name="airsim_detect_objects",
        purpose="Run single-frame object detection.",
        when_to_use="When the vehicle already has a useful camera view and needs target detection.",
        inputs={"target_class": "optional class such as car/person/truck", "confidence": "minimum confidence"},
        outputs="Detected objects and confidence scores.",
        required_capabilities=["object_detection"],
        preconditions=["AirSim backend connected", "image stream/camera available"],
        cost="medium",
    ),
    "airsim_vlm_confirm_target": ToolCard(
        name="airsim_vlm_confirm_target",
        purpose="Use the selected multimodal model to confirm whether the latest captured image contains a requested target.",
        when_to_use="After airsim_take_photo, airsim_detect_objects, or a visual sweep returns an image that needs semantic confirmation.",
        inputs={
            "target_description": "natural language target description such as red car/person/truck",
            "source": "last_image or explicit image_base64",
            "image_base64": "optional PNG/JPEG base64 when not using last_image",
        },
        outputs="Structured VLM confirmation: target_found, confidence, evidence, relative direction, and next-action hint.",
        required_capabilities=["image_capture"],
        preconditions=["A multimodal model is selected", "An image is available from capture/search or image_base64 is provided"],
        cost="medium",
        risk="low",
        notes=["This tool does not move the vehicle; it only analyzes imagery."],
    ),
    "airsim_vlm_analyze_image": ToolCard(
        name="airsim_vlm_analyze_image",
        purpose="Use the selected multimodal model to describe the latest captured image.",
        when_to_use="After airsim_take_photo when the operator asks what the drone can see or asks for open-ended image understanding.",
        inputs={
            "question": "operator question about the image",
            "source": "last_image or explicit image_base64",
            "image_base64": "optional PNG/JPEG base64 when not using last_image",
        },
        outputs="Concise scene description, visible objects, target candidates, and safety-relevant notes.",
        required_capabilities=["image_capture"],
        preconditions=["A multimodal model is selected", "An image is available from capture/search or image_base64 is provided"],
        cost="medium",
        risk="low",
        notes=["This tool does not move the vehicle; it only analyzes imagery."],
    ),
    "provider_bridge_health": ToolCard(
        name="provider_bridge_health",
        purpose="Check whether the configured ROS/provider bridge is reachable.",
        when_to_use="Before using ROS-backed providers, especially after starting PX4 and Micro XRCE-DDS in WSL.",
        inputs={},
        outputs="Bridge health, status, and provider availability metadata.",
        required_capabilities=["ros2_topics"],
        cost="low",
        risk="low",
        notes=["This is a provider health read; it does not publish ROS commands."],
    ),
    "provider_obstacle_summary": ToolCard(
        name="provider_obstacle_summary",
        purpose="Read the current local obstacle or costmap summary from a provider.",
        when_to_use="Before movement when a ROS obstacle, depth, lidar, or costmap node is available.",
        inputs={"max_age_sec": "maximum accepted provider data age", "frame": "local frame name"},
        outputs="Obstacle level, nearest obstacle distance, direction, timestamp, and source metadata when available.",
        required_capabilities=["ros2_topics", "obstacle_avoidance"],
        cost="low",
        risk="low",
        notes=["This is an observation tool. It does not move the vehicle."],
    ),
    "provider_validate_motion": ToolCard(
        name="provider_validate_motion",
        purpose="Ask a provider whether a proposed body-frame motion is currently safe.",
        when_to_use="Immediately before drone_move_relative or drone_fly_velocity when ROS obstacle data is available.",
        inputs={
            "forward_m": "forward body-frame distance",
            "right_m": "right body-frame distance",
            "up_m": "upward distance",
            "velocity": "planned speed",
            "max_age_sec": "maximum accepted provider data age",
        },
        outputs="Safe/blocked decision, reason, nearest obstacle data, and provider confidence.",
        required_capabilities=["ros2_topics", "obstacle_avoidance"],
        cost="low",
        risk="low",
        notes=["Movement remains a separate atomic flight command after validation."],
    ),
    "airsim_search_target": ToolCard(
        name="airsim_search_target",
        purpose="Run an AirSim visual target search mission.",
        when_to_use="When the target location is unknown and visual search is requested.",
        inputs={"target_class": "target class", "search_altitude": "meters", "search_radius": "meters", "scene_description": "optional scene hints"},
        outputs="Target candidate/confirmation, image path/base64, detections, and search status.",
        required_capabilities=["target_search"],
        preconditions=["AirSim backend connected", "airborne or task handles motion safely"],
        cost="high",
        risk="medium",
        not_for="PX4-only backends without image capture/perception.",
        execution_mode="async",
        kind="async_operation",
    ),
    "airsim_approach_target": ToolCard(
        name="airsim_approach_target",
        purpose="Approach a visually identified target.",
        when_to_use="After target search or detection has produced a direction/position.",
        inputs={"direction_hint": "relative direction", "distance_m": "approach distance"},
        outputs="Approach result.",
        required_capabilities=["target_search"],
        preconditions=["target candidate exists", "AirSim backend connected"],
        cost="medium",
        risk="medium",
        execution_mode="async",
        kind="async_operation",
    ),
    "airsim_track_object": ToolCard(
        name="airsim_track_object",
        purpose="Track a visual object for a duration.",
        when_to_use="Only when the operator explicitly requests tracking or following.",
        inputs={"target_class": "class to track", "duration": "seconds", "max_velocity": "m/s"},
        outputs="Tracking task result or task id.",
        required_capabilities=["target_tracking"],
        preconditions=["AirSim backend connected", "target class provided"],
        cost="high",
        risk="medium",
        execution_mode="async",
        kind="async_operation",
    ),
    "airsim_task_status": ToolCard(
        name="airsim_task_status",
        purpose="Check status of a background AirSim task.",
        when_to_use="When a search/tracking task returned a task id and progress is needed.",
        inputs={"task_id": "task id; empty returns recent tasks"},
        outputs="Task state and progress.",
        required_capabilities=["target_search"],
        cost="low",
    ),
    "airsim_task_cancel": ToolCard(
        name="airsim_task_cancel",
        purpose="Cancel a background AirSim task.",
        when_to_use="When stopping a running search/tracking task.",
        inputs={"task_id": "task id"},
        outputs="Cancel result.",
        required_capabilities=["target_search"],
        risk="medium",
    ),
    "airsim_check_obstacle": ToolCard(
        name="airsim_check_obstacle",
        purpose="Check AirSim depth/obstacle risk before movement.",
        when_to_use="Before approach or low-altitude movement in cluttered scenes.",
        outputs="Obstacle level and suggested action.",
        required_capabilities=["obstacle_avoidance"],
        cost="medium",
        risk="low",
    ),
    "memory_store": ToolCard(
        name="memory_store",
        purpose="Record mission summary in runtime memory.",
        when_to_use="At the end of a planned or executed mission.",
        outputs="Memory write acknowledgement.",
        required_capabilities=[],
        cost="low",
    ),
}


def cards_for_capabilities(
    capabilities: dict[str, Any],
    available_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    names = available_tool_names or set(TOOL_CARDS)
    cards: list[dict[str, Any]] = []
    for name in sorted(names):
        card = TOOL_CARDS.get(name)
        if not card:
            continue
        manifest = manifest_for(name)
        if manifest and manifest.kind == "workflow" and manifest.recommended_layer == "skill":
            continue
        if _requirements_met(card.required_capabilities, capabilities):
            payload = card.to_dict()
            payload["manifest"] = manifest.to_dict() if manifest else {}
            cards.append(payload)
    return cards


def _requirements_met(required: list[str], capabilities: dict[str, Any]) -> bool:
    for capability in required:
        if not bool(capabilities.get(capability, False)):
            return False
    return True
