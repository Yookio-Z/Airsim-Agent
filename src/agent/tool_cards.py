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
    "formation_command": ToolCard(
        name="formation_command",
        purpose="Control a multi-vehicle formation or coverage mission on the airsim / px4_mavlink backends (set_drones, set_formation, takeoff, move_center, rotate, scale, coverage_plan, coverage_start, hover_all, land_all, stop, status).",
        when_to_use="When the operator asks for formation flight, swarm movement, or area coverage with multiple vehicles. A deterministic 10Hz control loop maintains the formation; poll action=status until stable.",
        inputs={
            "action": "set_drones / set_formation / takeoff / move_center / rotate / scale / coverage_plan / coverage_start / hover_all / land_all / stop / status",
            "formation_type": "line / v_shape / triangle / diamond / square / hexagon / circle / arrow",
            "spacing": "formation spacing in meters",
            "altitude": "takeoff altitude in meters",
            "x": "formation center x (north, meters)",
            "y": "formation center y (east, meters)",
            "angle_deg": "rotation angle for rotate",
            "scale_factor": "scale factor for scale",
            "area_shape": "rectangle / circle for coverage",
            "area_width": "rectangle width for coverage",
            "area_height": "rectangle height for coverage",
            "area_radius": "circle radius for coverage",
            "resolution": "coverage grid resolution in meters",
            "partition": "balanced / stripe / quadrant",
            "path_algo": "boustrophedon / spiral / nearest",
            "vehicle_ids": "comma-separated vehicle ids for set_drones",
        },
        outputs="mode, stable flag, per-drone positions/targets, coverage progress.",
        required_capabilities=["flight_control"],
        risk="medium",
        not_for="Single-vehicle tasks — use the drone_* tools instead. Not available on MAVLink/ROS2 backends.",
        notes=[
            "While a formation is active, single-vehicle flight tools are blocked (use hover_all/land_all/stop first).",
            "Emergency stop and run end automatically hover all formation drones.",
        ],
    ),
    "drone_get_status": ToolCard(
        name="drone_get_status",
        purpose="Read vehicle telemetry and flight state. Without vehicle_name this returns EVERY vehicle in one call (preferred for multi-vehicle status questions).",
        when_to_use="Before and after actions, or when the operator asks for status. One call without vehicle_name covers the whole fleet.",
        outputs="Position, velocity, attitude, armed/flying state, mode, GPS, and battery when available. Multi-vehicle backends return a per-vehicle list.",
        inputs={"vehicle_name": "留空=一次查询全部车辆（推荐）；或具体载具名"},
        required_capabilities=["telemetry"],
        cost="low",
    ),
    "drone_arm": ToolCard(
        name="drone_arm",
        purpose="Arm the vehicle motors.",
        when_to_use="Before takeoff when the task requires flight.",
        outputs="Arm success/failure.",
        inputs={"vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
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
        inputs={"vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        required_capabilities=["flight_control"],
        preconditions=["connected", "vehicle is landed or safe to disarm"],
        risk="medium",
    ),
    "drone_takeoff": ToolCard(
        name="drone_takeoff",
        purpose="Take off to a target relative altitude.",
        when_to_use=(
            "任务需要飞机离地时使用。目标识别/搜索/追踪类任务定高 2~3m"
            "（机载相机前视 15°，2~3m 才能平视目标），不要飞 5m 以上。"
        ),
        inputs={"altitude": "Positive altitude in meters.", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
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
        inputs={"vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        required_capabilities=["flight_control"],
        preconditions=["connected"],
        risk="medium",
    ),
    "drone_hover": ToolCard(
        name="drone_hover",
        purpose="Hold current position or stop motion.",
        when_to_use="For pause, emergency stabilization, or after a failed non-critical action.",
        outputs="Hold/hover command result.",
        inputs={"vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        required_capabilities=["flight_control"],
        preconditions=["connected"],
        risk="low",
    ),
    "drone_fly_to": ToolCard(
        name="drone_fly_to",
        purpose="Fly to an absolute local NED coordinate.",
        when_to_use="When the target local NED position is known.",
        inputs={"x": "North meters", "y": "East meters", "z": "Down meters; negative is above origin", "velocity": "m/s", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        outputs="Navigation command result.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "airborne or backend supports ground navigation"],
        not_for="Unknown target locations. Use search/perception first when available.",
        risk="medium",
    ),
    "drone_move_relative": ToolCard(
        name="drone_move_relative",
        purpose="Move relative to current vehicle heading.",
        when_to_use=(
            "操作员要求前/后/左/右/上/下移动时使用；也用于目标确认不清晰时抵近观察"
            "（例如向前 3m 拉近距离后再检测/确认），定高 2~3m、不要贴脸（<2m）。"
        ),
        inputs={"forward_m": "meters forward", "right_m": "meters right", "up_m": "meters upward", "velocity": "m/s", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        outputs="Relative movement result and target NED position.",
        required_capabilities=["flight_control", "telemetry"],
        preconditions=["connected", "current position and heading available"],
        not_for="Geometric paths such as square/rectangle/orbit/circle/grid. Use drone_fly_path with explicit local NED waypoints instead.",
        risk="medium",
    ),
    "drone_approach_target": ToolCard(
        name="drone_approach_target",
        purpose="Make one bounded forward step toward the currently centered/locked visual target.",
        when_to_use=(
            "确认目标且目标已在画面中央、需要缩短距离时使用（视觉伺服式抵近）。"
            "单步有界 1~3m，只看机体前方；目标未居中或感知无目标会拒绝执行。"
            "每次抵近后必须重新检测/确认，再决定是否继续靠近或转入持续跟踪。"
        ),
        inputs={"step_m": "本次前向推进距离（米），限制 1~3m，默认 2"},
        outputs="Approach step result with target track_id, pixel offset ex, and new NED position.",
        required_capabilities=["flight_control", "object_detection"],
        preconditions=["connected", "target detected and horizontally centered"],
        not_for="Blind movement when no target is locked, or approaching without re-checking afterwards.",
        risk="medium",
    ),
    "drone_fly_velocity": ToolCard(
        name="drone_fly_velocity",
        purpose="Command NED velocity for a duration or one control update.",
        when_to_use="For short low-level motion commands or skill internals.",
        inputs={"vx": "North m/s", "vy": "East m/s", "vz": "Down m/s", "duration": "seconds; 0 sends one update", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        outputs="Velocity command result.",
        required_capabilities=["flight_control"],
        risk="high",
        not_for="High-level waypoint missions unless wrapped by a skill.",
    ),
    "drone_fly_path": ToolCard(
        name="drone_fly_path",
        purpose="Fly a local NED waypoint path.",
        when_to_use="For AirSim local path missions, geometric paths such as square/rectangle/orbit/circle/grid, or converted local mission drafts.",
        inputs={"waypoints_json": "JSON array of {x,y,z}", "velocity": "m/s", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
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
        inputs={"heading_deg": "0 is North, clockwise positive", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
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
        purpose="Capture a camera image (saves to disk and returns image data).",
        when_to_use=(
            "只用于保存证据帧或操作员明确要求拍照。若要判断画面里有什么/目标是否存在或颜色，"
            "请改用 airsim_detect_objects（结构化检测，首选）或 inspect_current_frame（视觉模型读图）；"
            "纯文本推理循环无法直接阅读图像数据，反复拍照不会得到结论。"
        ),
        inputs={"camera_name": "Camera id", "image_type": "scene/depth/segmentation/infrared", "verify_target_class": "optional class"},
        outputs="Image path/base64 and optional visual verification result.",
        required_capabilities=["image_capture"],
        preconditions=["A camera source is configured and connected"],
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
        purpose="Run single-frame object detection and return structured results (class/confidence/bbox).",
        when_to_use=(
            "首选的目标确认方式：判断画面里有没有某类目标（car/person/truck 等）时直接调用，"
            "返回结构化结果无需读图。检测到目标后：若任务不需要颜色/型号等语义属性，直接进入追踪；"
            "若需要语义属性，再用视觉模型确认一次。"
        ),
        inputs={"target_class": "optional class such as car/person/truck", "confidence": "minimum confidence"},
        outputs="Detected objects with class names and confidence scores.",
        required_capabilities=["object_detection"],
        preconditions=["A camera source / perception stream is available"],
        cost="low",
    ),
    "airsim_vlm_confirm_target": ToolCard(
        name="airsim_vlm_confirm_target",
        purpose="Use the configured multimodal model to confirm whether the current image contains a requested target and report the evidence.",
        when_to_use=(
            "目标已经由检测提示可能存在、需要二次确认时调用一次。每次抵近/换角度后最多调用一次；"
            "若确认结果不确定（目标太远、细节不足），下一步应是抵近观察再确认，而不是原地重复调用。"
            "确认成功后进入追踪阶段，跟踪复检优先用 airsim_detect_objects。"
        ),
        inputs={
            "target_description": "natural language target description such as red car/person/truck",
            "source": "last_image or explicit image_base64",
            "image_base64": "optional PNG/JPEG base64 when not using last_image",
        },
        outputs="Structured VLM confirmation: target_found, confidence, evidence, relative direction, and next-action hint.",
        required_capabilities=["image_capture"],
        preconditions=["A multimodal model is selected", "An image is available from the perception stream or last capture"],
        cost="high",
        risk="low",
        notes=["This tool does not move the vehicle; it only analyzes imagery."],
    ),
    "airsim_vlm_analyze_image": ToolCard(
        name="airsim_vlm_analyze_image",
        purpose="[ALIAS for inspect_current_frame] Use the multimodal model to describe the current frame.",
        when_to_use="[DEPRECATED alias] When you would call this tool, prefer inspect_current_frame which has the same effect. If only this alias is available, the runtime will forward to inspect_current_frame.",
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
    "inspect_current_frame": ToolCard(
        name="inspect_current_frame",
        purpose="Use the configured multimodal model to visually analyze the drone's current camera frame and answer a free-form question about it.",
        when_to_use=(
            "涉及当前画面的语义判断时使用（画面里有什么、目标是什么颜色/类型、场景描述）。"
            "这是昂贵的视觉模型调用：同一位置只调用一次；若回答因目标太远/太小而不确定，"
            "不要重复调用，应当先抵近（drone_move_relative 向前或 drone_fly_to 靠近）再重新检测；"
            "简单的是否存在某类目标优先用 airsim_detect_objects（结构化、低成本）。"
        ),
        inputs={"question": "natural language question about the current frame"},
        outputs="Model answer text plus the YOLO detections on that frame.",
        required_capabilities=[],
        cost="high",
        risk="low",
    ),
    "perception_status": ToolCard(
        name="perception_status",
        purpose="Read the perception axis state: health, detected targets, and recent perception events.",
        when_to_use=(
            "只读：查看感知服务是否在线、当前锁定/检测到哪些目标、最近的目标出现/丢失事件。"
            "注意它**不做检测动作**——要主动识别画面里的目标必须调用 airsim_detect_objects，"
            "perception_status 只读取后台持续检测的快照，不能替代检测。"
        ),
        inputs={"include_snapshot": "include current detection snapshot (default true)", "include_events": "include recent events (default true)", "limit": "event count limit"},
        outputs="Perception health {online, fps, error} plus target snapshot and target_found/target_lost events.",
        required_capabilities=[],
        cost="low",
        risk="low",
    ),
    "perception_start": ToolCard(
        name="perception_start",
        purpose="Start (or ensure) the background target-detection service and optionally set the target class.",
        when_to_use="需要目标检测/跟踪但感知服务未在线时调用（后台持续检测 + 跟踪保持，Agent 只读结果）。也可用于切换检测类别。",
        inputs={"target_class": "optional class such as car/person/truck"},
        outputs="Service health plus started/online flags.",
        required_capabilities=[],
        cost="low",
        risk="low",
    ),
    "perception_stop": ToolCard(
        name="perception_stop",
        purpose="Stop the background target-detection service.",
        when_to_use="操作员要求停止检测/释放算力时调用。",
        inputs={},
        outputs="Confirmation that detection stopped.",
        required_capabilities=[],
        cost="low",
        risk="low",
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
