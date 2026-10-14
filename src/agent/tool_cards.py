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
        not_for="Single-vehicle tasks — use the drone_* tools instead. Not available on the px4_ros2 backend, and needs at least 2 vehicles.",
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
    "drone_release_yaw": ToolCard(
        name="drone_release_yaw",
        purpose="把机头控制权交还给遥控器，Agent 停止下发任何转向指令。",
        when_to_use="人工准备接管、或操作员要求放开机头时。真机上先调用，否则 Agent 会在下个航点把机头拽回去，操作员却以为朝向仍在自己手上。",
        inputs={"vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        outputs="交还结果。",
        required_capabilities=["flight_control"],
        preconditions=[],
        not_for="不要用它来转动飞机。转向用 drone_rotate_to。本工具只交还控制权，不改变机头朝向。",
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
            "请改用 inspect_current_frame（把画面交给视觉模型读，纯文本循环无法直接阅读图像数据）。"
        ),
        inputs={
            "camera_name": "Camera id",
            "image_type": "scene/depth/segmentation/infrared",
            "auto_save": "是否保存到 captures 目录，默认 true",
        },
        outputs="Image path/base64（只取画面，不做任何识别）。",
        required_capabilities=["image_capture"],
        preconditions=["A camera source is configured and connected"],
        not_for="别用它判断画面里有没有目标/是什么颜色——那是 inspect_current_frame（视觉模型）的工作。",
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
        purpose="Read metric depth at one or more image points — the ruler of this system (there is no object detection).",
        when_to_use=(
            "把视觉模型给的位置变成米：先 inspect_current_frame 拿到目标的归一化点，"
            "再用本工具读那一点的距离，然后才决定这一步走多远。也可以在同一目标附近"
            "多取几个点做中位数（单像素深度容易落在物体边缘）：query_points=\"0.55,0.34;0.545,0.345;0.555,0.335\"。"
            "前向移动之前尤其要先读一次——没有可信距离就不要移动。"
        ),
        inputs={
            "camera_name": "相机ID，默认 \"0\"",
            "query_points": (
                "要测距的点，格式 \"x,y;x,y\"。**用归一化坐标 0~1**（视觉模型 bbox_hint 的 x/y 就是它）。"
                "整数像素只有在你知道**深度图**分辨率时才用：深度图与彩色图分辨率不同，"
                "把彩色图像素直接传进来不会报错，却会读到另一个位置，得到一个看起来正常的错误距离。"
            ),
            "return_vis": "是否附带深度可视化图 base64，默认 false",
            "vehicle_name": "无人机名称，留空=默认机",
        },
        outputs="image_size(深度图分辨率) + 每个点的 distance_m；多点时另给 distance_median_m；天空/无命中会标 plausible=false。",
        required_capabilities=["depth_perception"],
        preconditions=["AirSim backend connected"],
        not_for="不要用它去猜目标位置——点必须来自视觉模型或按画面量取；点错了得到的是那个点的距离。",
        cost="low",
    ),
    "airsim_locate_point": ToolCard(
        name="airsim_locate_point",
        purpose="Turn a NORMALISED image point into a metric distance and a world position (NED) using the depth image and the drone pose.",
        when_to_use=(
            "视觉模型给了目标位置（answer.target_candidates[].bbox_hint 的 x/y，归一化 0~1）之后，"
            "用本工具把它变成可执行的量：距离（米）和世界坐标（NED）。需要朝目标飞（drone_fly_to）、"
            "要记住目标位置、或要把目标与别处的坐标作比较时用它。只做单步相对靠近时可以直接用"
            "airsim_get_depth_map 读距离，不必先算世界坐标。"
        ),
        inputs={
            "x": "归一化横向坐标 0~1（0=最左，1=最右），来自视觉模型或按画面比例量取",
            "y": "归一化纵向坐标 0~1（0=最上，1=最下）",
            "w": "视觉模型给的归一化宽度（bbox_hint.w）。**建议传**：传了会做框内/框外深度交叉验证，并给出米制尺寸",
            "h": "视觉模型给的归一化高度（bbox_hint.h）",
            "vehicle_name": "无人机名称，留空=默认机",
        },
        outputs=(
            "world_pos {x,y,z}(NED 米)、distance_m、depth_m、有效性与失败原因；传了 w/h 时另有 "
            "surface_check（框内外深度对比：same_surface=画的是一整片连续表面、可能不是目标；"
            "object_in_front=像独立物体）与 metric_size（按深度换算的米制尺寸，用于量级把关）。"
        ),
        required_capabilities=["depth_perception"],
        preconditions=["AirSim 已连接", "该点上有有效深度（天空/过远处会返回 invalid）"],
        not_for="目标点不确定时不要用它硬算坐标——点错位置得到的是那个位置的坐标，不是目标的。",
        cost="low",
        risk="low",
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
            "涉及当前画面的语义判断时使用（画面里有什么、目标是什么颜色/类型/朝向、有没有<某个东西>）。"
            "这是昂贵的视觉模型调用：同一位置只调用一次；回答含糊意味着距离或角度不对，"
            "应当先靠近（drone_move_relative 向前）或转向（drone_rotate_to）再看，不要重复问。"
            "回答里的 target_candidates[].bbox_hint {x,y,w,h} 是归一化的粗值：距离交给 "
            "airsim_get_depth_map、世界坐标交给 airsim_locate_point，不要把它当精确测量。"
        ),
        inputs={"question": "natural language question about the current frame"},
        outputs="Model answer (summary, candidates with rough normalised bbox_hint, navigation hint).",
        required_capabilities=[],
        cost="high",
        risk="low",
    ),
    "perception_status": ToolCard(
        name="perception_status",
        purpose="Read camera/frame health: is the perception axis online, fps, how stale the frame is, and which view it is using.",
        when_to_use=(
            "只读：判断相机链路是否正常（`capture_age_s` 持续增大 = 画面不再更新，此时任何基于画面的"
            "判断都不可信）；以及确认感知轴在看哪一路画面（view）。不含目标检测信息。"
        ),
        inputs={"include_events": "include recent camera/link events (default true)", "limit": "event count limit"},
        outputs="Camera health {online, fps, capture_age_s, view, frame size} plus recent events.",
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
