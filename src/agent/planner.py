"""Mission planning primitives for the AirSim VLA agent.

The current planner is intentionally conservative: it turns Chinese or English
operator intent into auditable tool steps. A model-backed planner can replace
`MissionPlanner.plan` later while keeping the `MissionPlan` contract stable.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .command_slots import extract_command_slots, extract_intents, extract_target_class


@dataclass
class MissionStep:
    """One planned tool invocation."""

    id: str
    label: str
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    layer: str = "tool"
    status: str = "pending"
    result: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    # True when this step's outcome must be observed before later steps can
    # be chosen (e.g. a photo/VLM step whose result decides the next move).
    needs_observation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionPlan:
    """A complete mission plan generated from an operator command."""

    run_id: str
    command: str
    intent: str
    summary: str
    steps: list[MissionStep]
    assumptions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    planner_source: str = "rules"
    planner_model: str = ""
    reasoning: str = ""
    risk_notes: list[str] = field(default_factory=list)
    # Execution strategy declared by the planner: "auto" (runtime decides) or
    # "agent_loop" (task needs observe-respond cycles: visual search, tracking,
    # conditional steps — execute step-by-step instead of as a fixed sequence).
    execution_mode: str = "auto"
    # Task contract: machine-verifiable completion criteria. The agent loop
    # checks these before accepting an is_complete decision (LLM 提议 +
    # 确定性验证), so "model said done" is never the only gate.
    goal: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "intent": self.intent,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "assumptions": list(self.assumptions),
            "created_at": self.created_at,
            "planner_source": self.planner_source,
            "planner_model": self.planner_model,
            "reasoning": self.reasoning,
            "risk_notes": list(self.risk_notes),
            "execution_mode": self.execution_mode,
            "goal": dict(self.goal),
        }


class MissionPlanner:
    """Rule planner that establishes the VLA tool contract."""

    def plan(self, command: str, capabilities: dict[str, Any] | None = None) -> MissionPlan:
        normalized = command.strip()
        if not normalized:
            raise ValueError("任务指令不能为空")

        lower = normalized.lower()
        slots = extract_command_slots(normalized)
        intents = extract_intents(normalized)
        run_id = f"run_{int(time.time() * 1000)}"
        steps: list[MissionStep] = []
        assumptions: list[str] = []

        def add(label: str, tool: str, params: dict[str, Any] | None = None, layer: str = "tool") -> None:
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=label,
                    tool=tool,
                    params=params or {},
                    layer=layer,
                )
            )

        altitude = slots.altitude or self._extract_altitude(normalized)
        if altitude is None:
            altitude = 3.0
            assumptions.append("未指定起飞/搜索高度，默认使用 3m。")

        target_class = slots.target_class or extract_target_class(normalized)
        coordinate = _target_tuple(slots.ned_target) or self._extract_coordinate(normalized)
        relative_move = slots.relative_move
        relative_moves = list(slots.relative_moves or [])
        if not relative_moves and relative_move:
            relative_moves = [relative_move]
        velocity = slots.velocity or 2.0
        radius = slots.radius or self._extract_radius(normalized) or 25.0

        wants_return = bool(slots.return_to_start)
        wants_land = slots.land is True or wants_return or intents["land"]
        wants_hover = intents["hover"]
        wants_photo = intents["photo"]
        wants_search = intents["search"]
        wants_patrol = intents["patrol"]
        wants_track = intents["track"]
        wants_connect = intents["connect"]
        wants_takeoff = intents["takeoff"]
        wants_upload_only = any(k in lower for k in ["upload", "plan only", "上传", "下发", "规划", "保存"])
        wants_status = intents["status"]
        wants_conditional = any(k in lower for k in ["if", "when", "如果", "未起飞", "没有起飞"])
        hover_index = _first_keyword_index(lower, ["hover", "悬停", "暂停"])
        motion_index = _first_keyword_index(
            lower,
            [
                "forward",
                "backward",
                "left",
                "right",
                "move to",
                "fly to",
                "向前",
                "往前",
                "前进",
                "向后",
                "往后",
                "后退",
                "向左",
                "往左",
                "左移",
                "向右",
                "往右",
                "右移",
                "飞到",
                "移动",
            ],
        )
        hover_before_motion = wants_hover and hover_index >= 0 and (motion_index < 0 or hover_index < motion_index)

        caps = capabilities or {}
        supports_image = self._supports(caps, "image_capture", True)
        supports_mode_control = self._supports(caps, "mode_control", False)
        supports_mavlink_mission = self._supports(caps, "gps", False) and self._supports(caps, "mode_control", False)

        search_with_camera = False
        effective_photo = wants_photo and supports_image
        effective_search = wants_search and supports_image
        effective_track = False
        if wants_photo and not supports_image:
            assumptions.append("The current backend does not support image capture; photo steps were skipped.")
        if wants_search and not supports_image:
            assumptions.append("The current backend does not support visual search because image capture is unavailable.")
        if wants_track:
            assumptions.append("Target tracking is now a draft skill/provider contract; the legacy tracking workflow tool is not planned.")

        requires_flight = (
            wants_takeoff
            or effective_search
            or (wants_patrol and not supports_mavlink_mission)
            or effective_track
            or coordinate is not None
            or bool(relative_moves)
        )
        has_pre_land_actions = (
            requires_flight
            or wants_patrol
            or effective_photo
            or effective_search
            or effective_track
        )

        if wants_land and not has_pre_land_actions:
            if wants_return and supports_mode_control:
                add("切换返航降落模式", "drone_set_mode", {"mode": "RTL"}, layer="action")
            else:
                add("悬停稳定", "drone_hover", layer="safety")
                add("执行降落", "drone_land", layer="action")
            goal = {"objective": "安全降落", "target": "", "success_criteria": [{"metric": "landed"}]}
            return MissionPlan(run_id, normalized, "land", "安全降落", steps, assumptions, goal=goal)

        if wants_hover and not requires_flight:
            add("进入悬停", "drone_hover", layer="action")
            add("读取状态", "drone_get_status", layer="perception")
            return MissionPlan(run_id, normalized, "hover", "悬停并刷新遥测", steps, assumptions)

        add("连接载具后端", "drone_connect", layer="tool")

        if wants_status or wants_conditional:
            add("读取当前状态", "drone_get_status", layer="perception")

        if wants_connect and not requires_flight:
            add("列出载具", "drone_list_vehicles", layer="perception")
            if not any(step.tool == "drone_get_status" for step in steps):
                add("读取状态", "drone_get_status", layer="perception")
            return MissionPlan(run_id, normalized, "connect", "连接仿真并读取状态", steps, assumptions)

        if requires_flight:
            add("解锁电机", "drone_arm", layer="action")

        if requires_flight:
            add("起飞到任务高度", "drone_takeoff", {"altitude": altitude}, layer="action")

        if wants_takeoff and hover_before_motion and (coordinate is not None or relative_moves or wants_land or wants_patrol or effective_search or effective_track):
            add("起飞后悬停稳定", "drone_hover", layer="action")
        elif wants_takeoff and wants_hover and coordinate is None and not relative_moves and not wants_patrol and not effective_search and not effective_track:
            add("起飞后悬停稳定", "drone_hover", layer="action")

        if coordinate:
            x, y, z = coordinate
            add(
                "飞向指定坐标",
                "drone_fly_to",
                {"x": x, "y": y, "z": z, "velocity": velocity},
                layer="action",
            )
        elif relative_moves:
            for move_index, move in enumerate(relative_moves, 1):
                label = "按机体坐标相对移动" if len(relative_moves) == 1 else f"按机体坐标相对移动 {move_index}"
                add(
                    label,
                    "drone_move_relative",
                    {**move, "velocity": velocity},
                    layer="action",
                )

        if wants_hover and (coordinate is not None or relative_moves) and not wants_land and not hover_before_motion:
            add("移动后悬停等待", "drone_hover", layer="action")

        if wants_land and has_pre_land_actions:
            if wants_return and supports_mode_control:
                add("切换返航降落模式", "drone_set_mode", {"mode": "RTL"}, layer="action")
            else:
                add("降落前悬停稳定", "drone_hover", layer="safety")
                add("执行降落", "drone_land", layer="action")

        if wants_patrol:
            waypoints = self._patrol_waypoints(radius=radius, altitude=altitude)
            if supports_mavlink_mission:
                mission_items = self._mission_items_from_waypoints(waypoints, velocity=velocity, include_takeoff=True)
                add(
                    "上传 PX4 航点任务",
                    "drone_upload_mission",
                    {"waypoints_json": self._jsonish_mission_items(mission_items)},
                    layer="mission",
                )
                if not wants_upload_only:
                    if not any(step.tool == "drone_arm" for step in steps):
                        add("解锁电机", "drone_arm", layer="action")
                    add("启动 PX4 航点任务", "drone_start_mission", layer="mission")
                    add("读取任务进度", "drone_get_mission_progress", layer="perception")
                else:
                    assumptions.append("当前指令包含上传/规划语义，PX4 Mission 已生成但不会自动启动。")
                if supports_image:
                    add("巡检拍照", "airsim_take_photo", {"image_type": "scene"}, layer="perception")
                else:
                    assumptions.append("当前后端不支持图像采集，PX4 巡检任务仅包含飞控航点。")
            else:
                add(
                    "执行区域巡检航线",
                    "drone_fly_path",
                    {"waypoints_json": self._jsonish_waypoints(waypoints), "velocity": velocity},
                    layer="action",
                )
                if supports_image:
                    add("巡检拍照", "airsim_take_photo", {"image_type": "scene"}, layer="perception")
                else:
                    assumptions.append("当前后端不支持图像采集，巡检任务仅执行航线。")

        if effective_search:
            add(
                "Run visual search skill",
                "skill:search",
                {
                    "target_class": target_class,
                    "search_altitude": altitude,
                    "search_radius": radius,
                    "scene_description": normalized,
                    "max_steps": 4,
                },
                layer="planning",
            )

        if effective_track:
            assumptions.append("Tracking execution is unavailable until skill:track_object gets a provider-backed executor.")

        if effective_photo and not wants_patrol:
            photo_title = "采集目标确认图像" if search_with_camera else "采集视觉图像"
            add(photo_title, "airsim_take_photo", {"image_type": "scene"}, layer="perception")
            add(
                "多模态确认目标" if search_with_camera else "多模态确认图像",
                "airsim_vlm_confirm_target",
                {"target_description": target_class or normalized, "source": "last_image"},
                layer="perception",
            )

        add("读取最终状态", "drone_get_status", layer="perception")
        add("写入任务记忆", "memory_store", {"source": "mission"}, layer="memory")

        intent = self._infer_intent(effective_search or search_with_camera, wants_patrol, effective_track, coordinate, effective_photo, wants_takeoff)
        if intent in {"general_mission", "takeoff"} and relative_moves:
            intent = "move_relative"
        summary = self._summary(intent, target_class, radius, altitude)
        goal = self._goal_for(intent, target_class, coordinate, altitude, effective_search)
        if wants_land and not any(c.get("metric") == "landed" for c in goal["success_criteria"]):
            goal["success_criteria"].append({"metric": "landed"})
        return MissionPlan(run_id, normalized, intent, summary, steps, assumptions, goal=goal)

    def _goal_for(
        self,
        intent: str,
        target_class: str,
        coordinate: tuple[float, float, float] | None,
        altitude: float,
        effective_search: bool,
    ) -> dict[str, Any]:
        """Synthesize machine-verifiable completion criteria from the intent.

        The loop checks these before accepting a model-declared completion, so
        a search task cannot end with 'complete' unless the target was found or
        an explicit not-found search ran, and a fly-to task cannot end before
        the reported position is within tolerance.
        """
        criteria: list[dict[str, Any]] = []
        if intent in {"search_target", "search_and_track"} and effective_search:
            criteria.append({"metric": "target_confirmed", "target": target_class})
        elif intent == "fly_to_point" and coordinate:
            criteria.append(
                {
                    "metric": "position_reached",
                    "x": coordinate[0],
                    "y": coordinate[1],
                    "z": coordinate[2],
                    "tolerance": 1.5,
                }
            )
        elif intent == "visual_capture":
            criteria.append({"metric": "photo_taken"})
        elif intent == "takeoff":
            criteria.append({"metric": "flying_at", "altitude": altitude, "tolerance": 1.0})
        elif intent in {"area_patrol", "move_relative", "general_mission"}:
            criteria.append({"metric": "status_ok"})
        objective = self._summary(intent, target_class, 25.0, altitude) if intent else "任务"
        return {"objective": objective, "target": target_class, "success_criteria": criteria}

    def _supports(self, capabilities: dict[str, Any], capability: str, default: bool) -> bool:
        if capability not in capabilities:
            return default
        return bool(capabilities.get(capability))

    def _infer_intent(
        self,
        wants_search: bool,
        wants_patrol: bool,
        wants_track: bool,
        coordinate: tuple[float, float, float] | None,
        wants_photo: bool,
        wants_takeoff: bool,
    ) -> str:
        if wants_search and wants_track:
            return "search_and_track"
        if wants_search:
            return "search_target"
        if wants_patrol:
            return "area_patrol"
        if coordinate:
            return "fly_to_point"
        if wants_photo:
            return "visual_capture"
        if wants_takeoff:
            return "takeoff"
        return "general_mission"

    def _summary(self, intent: str, target_class: str, radius: float, altitude: float) -> str:
        if intent in {"search_target", "search_and_track"}:
            target = target_class or "开放目标"
            return f"在 {radius:g}m 半径内搜索 {target}，任务高度 {altitude:g}m"
        if intent == "area_patrol":
            return f"按 {radius:g}m 半径执行区域巡检，任务高度 {altitude:g}m"
        if intent == "fly_to_point":
            return "执行定点飞行并回传状态"
        if intent == "move_relative":
            return "执行相对移动并回传状态"
        if intent == "visual_capture":
            return "采集 AirSim 摄像头图像"
        if intent == "takeoff":
            return f"起飞到 {altitude:g}m 并读取状态"
        return "解析为通用无人机仿真任务"

    def _extract_altitude(self, text: str) -> float | None:
        patterns = [
            r"(?:高度|高|起飞到|升到)\s*([-+]?\d+(?:\.\d+)?)\s*(?:米|m)?",
            r"([-+]?\d+(?:\.\d+)?)\s*(?:米|m)\s*(?:高度|高空)",
            r"(?:altitude|height|takeoff to|climb to|rise to)\s*([-+]?\d+(?:\.\d+)?)\s*(?:m|meter|meters)?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return abs(float(match.group(1)))
        return None

    def _extract_radius(self, text: str) -> float | None:
        match = re.search(r"(?:半径|范围)\s*([-+]?\d+(?:\.\d+)?)\s*(?:米|m)?", text, re.IGNORECASE)
        if match:
            return abs(float(match.group(1)))
        return None

    def _extract_coordinate(self, text: str) -> tuple[float, float, float] | None:
        explicit = re.search(
            r"x\s*=\s*([-+]?\d+(?:\.\d+)?)\D+"
            r"y\s*=\s*([-+]?\d+(?:\.\d+)?)\D+"
            r"z\s*=\s*([-+]?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if explicit:
            return tuple(float(explicit.group(i)) for i in range(1, 4))  # type: ignore[return-value]

        grouped = re.search(
            r"[（(]\s*([-+]?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*[）)]",
            text,
        )
        if grouped:
            return tuple(float(grouped.group(i)) for i in range(1, 4))  # type: ignore[return-value]
        return None

    def _patrol_waypoints(self, radius: float, altitude: float) -> list[dict[str, float]]:
        r = max(5.0, min(radius, 80.0))
        z = -abs(altitude)
        return [
            {"x": r, "y": r, "z": z},
            {"x": r, "y": -r, "z": z},
            {"x": -r, "y": -r, "z": z},
            {"x": -r, "y": r, "z": z},
            {"x": r, "y": r, "z": z},
        ]

    def _jsonish_waypoints(self, waypoints: list[dict[str, float]]) -> str:
        parts = []
        for wp in waypoints:
            parts.append(
                '{"x":%.3f,"y":%.3f,"z":%.3f}'
                % (wp["x"], wp["y"], wp["z"])
            )
        return "[" + ",".join(parts) + "]"

    def _mission_items_from_waypoints(
        self,
        waypoints: list[dict[str, float]],
        velocity: float,
        include_takeoff: bool,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        first_altitude = abs(float(waypoints[0]["z"])) if waypoints else 3.0
        if include_takeoff:
            items.append(
                {
                    "id": "takeoff",
                    "type": "takeoff",
                    "frame": "local_ned",
                    "x": 0.0,
                    "y": 0.0,
                    "z": -first_altitude,
                    "alt_m": first_altitude,
                    "speed_mps": velocity,
                    "hold_s": 0.0,
                    "acceptance_radius_m": 2.0,
                    "actions": [],
                    "metadata": {"source": "rule_planner"},
                }
            )
        for index, wp in enumerate(waypoints, start=1):
            z = float(wp["z"])
            items.append(
                {
                    "id": f"wp_{index:03d}",
                    "type": "waypoint",
                    "frame": "local_ned",
                    "x": float(wp["x"]),
                    "y": float(wp["y"]),
                    "z": z,
                    "alt_m": abs(z),
                    "speed_mps": velocity,
                    "hold_s": 0.0,
                    "acceptance_radius_m": 2.0,
                    "actions": [],
                    "metadata": {"source": "rule_planner"},
                }
            )
        return items

    def _jsonish_mission_items(self, items: list[dict[str, Any]]) -> str:
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _target_tuple(target: dict[str, float] | None) -> tuple[float, float, float] | None:
    if not target:
        return None
    return (
        float(target.get("x", 0.0) or 0.0),
        float(target.get("y", 0.0) or 0.0),
        float(target.get("z", 0.0) or 0.0),
    )


def _first_keyword_index(text: str, keywords: list[str]) -> int:
    indexes = [text.find(keyword) for keyword in keywords if keyword and text.find(keyword) >= 0]
    return min(indexes) if indexes else -1
