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
        when_to_use="Before and after actions, or when the operator asks for status or for the vehicle list. One call without vehicle_name covers the whole fleet (names + full state, 取代单独的列表工具).",
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
    "drone_fly_path": ToolCard(
        name="drone_fly_path",
        purpose="Fly a local NED waypoint path.",
        when_to_use="For AirSim local path missions, geometric paths such as square/rectangle/orbit/circle/grid, or converted local mission drafts.",
        inputs={"waypoints": "紧凑 \"x,y,z;x,y,z\"（如 \"10,0,-5;20,0,-5\"）或 JSON 对象数组 [{\"x\":10,\"y\":0,\"z\":-5}, ...]（也接受 [[x,y,z], ...]）", "velocity": "m/s", "vehicle_name": "目标载具：空=默认机，all=全部，或具体载具名"},
        outputs="Path execution result.",
        required_capabilities=["flight_control"],
        preconditions=["connected", "waypoints validated"],
        risk="medium",
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
    "airsim_get_sensors": ToolCard(
        name="airsim_get_sensors",
        purpose="Read AirSim sensor data.",
        when_to_use="When sensor diagnostics or simulator sensor state is needed.",
        outputs="Sensor readings.",
        required_capabilities=["telemetry"],
        preconditions=["AirSim backend connected"],
    ),
    "locate_image_point": ToolCard(
        name="locate_image_point",
        purpose="The system's single geometry tool: normalised image point(s) -> metric distance and world position (NED), through the depth image and the drone pose (there is no object detection).",
        when_to_use=(
            "**所有几何量的唯一入口**（原 airsim_get_depth_map 已并入本工具；名字不带后端前缀——"
            "几何走相机链路，AirSim 与 PX4 后端都可用）。视觉模型给了目标位置"
            "（answer.target_candidates[].bbox_hint 的 x/y）之后：读距离、算世界坐标、决定这一步走多远都用它。"
            "前向移动之前必须先读一次——没有可信距离就不要移动。"
            "同一目标附近多给几个点（分号分隔）可做中位数降噪（单点深度容易落在物体边缘）；"
            "判断一个开口够不够宽（净空检查）也用它：中心+上下左右 5 点，看 distance_min_m。"
        ),
        inputs={
            "points": (
                "归一化坐标 0~1，格式 \"x,y\"；多点用分号分隔 \"x,y;x,y;x,y\"（最多 8 个）。"
                "**第一个点**是主点：给 distance_m 和 world_pos；其余点只给距离（extra_measures，"
                "另给 distance_median_m / distance_min_m）。只能用归一化坐标——像素坐标去另一个分辨率上读，"
                "不会报错却会读到别的位置。"
            ),
            "w": "第一个点的目标归一化宽度（bbox_hint.w）。**建议传**：传了会做框内/框外深度交叉验证，并给出米制尺寸",
            "h": "第一个点的目标归一化高度（bbox_hint.h）",
            "vehicle_name": "无人机名称，留空=默认机",
        },
        outputs=(
            "distance_m/depth_m、world_pos {x,y,z}(NED 米)、extra_measures[]（附加点逐点距离）、"
            "多点时的 distance_median_m（目标距离）与 distance_min_m（净空）；传了 w/h 时另有 "
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
        when_to_use="Immediately before drone_move_relative when ROS obstacle data is available.",
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
    "inspect_current_frame": ToolCard(
        name="inspect_current_frame",
        purpose="Use the configured multimodal model to visually analyze the drone's current camera frame and answer a free-form question about it.",
        when_to_use=(
            "涉及当前画面的语义判断时使用（画面里有什么、目标是什么颜色/类型/朝向、有没有<某个东西>）。"
            "这是昂贵的视觉模型调用：同一位置只调用一次；回答含糊意味着距离或角度不对，"
            "应当先靠近（drone_move_relative 向前）或转向（drone_rotate_to）再看，不要重复问。"
            "回答里的 target_candidates[].bbox_hint {x,y,w,h} 是归一化的粗值：把 x/y（连同 w/h）"
            "交给 locate_image_point 换距离和世界坐标，不要把它当精确测量。"
        ),
        inputs={
            "question": "natural language question about the current frame",
            "save_frame": "true 时把这一帧存到 captures 目录（证据帧/拍照）；原 airsim_take_photo 已并入本工具",
        },
        outputs="Model answer (summary, candidates with rough normalised bbox_hint, navigation hint); saved_path when save_frame=true.",
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
