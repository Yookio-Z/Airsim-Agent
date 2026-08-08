"""Task difficulty router for fast and slow agent execution paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskLevel(Enum):
    """Coarse task levels used to pick an execution strategy."""

    L0_DIRECT = "l0_direct"
    L1_TEMPLATE = "l1_template"
    L2_PLAN = "l2_plan"
    L3_AGENT_LOOP = "l3_agent_loop"
    L4_SUPERVISED = "l4_supervised"


@dataclass(frozen=True)
class TaskRoute:
    """Routing decision returned by TaskRouter.route()."""

    level: TaskLevel
    strategy: str
    reason: str
    direct_tool: str = ""
    direct_params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # risk_level: "safe" (read-only / no side effects)
    #            "elevated" (changes vehicle state but reversible / simulation-safe)
    #            "high" (irreversible or real-vehicle hazardous: clear_mission, start_mission, arm, takeoff)
    risk_level: str = "safe"

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "strategy": self.strategy,
            "reason": self.reason,
            "direct_tool": self.direct_tool,
            "direct_params": dict(self.direct_params),
            "notes": list(self.notes),
            "risk_level": self.risk_level,
        }


class TaskRouter:
    """Classifies commands so simple actions avoid unnecessary LLM calls."""

    STATUS_KEYWORDS = ("status", "state", "telemetry", "where", "battery", "position", "状态", "遥测", "位置", "电量", "高度")
    CONNECT_KEYWORDS = ("connect", "reconnect", "link", "连接", "重连")
    LIST_KEYWORDS = ("list vehicles", "vehicles", "vehicle list", "载具列表", "无人机列表", "列出无人机")
    HOVER_KEYWORDS = ("hover", "hold", "pause", "stop moving", "悬停", "保持", "暂停")
    LAND_KEYWORDS = ("land", "landing", "降落", "落地")
    DISARM_KEYWORDS = ("disarm", "lock motors", "上锁", "锁定电机")
    ARM_KEYWORDS = ("arm", "arm motors", "解锁", "上电", "解锁电机")

    TAKEOFF_KEYWORDS = ("takeoff", "take off", "起飞", "升空")
    FLY_KEYWORDS = (
        "fly to",
        "move to",
        "go to",
        "forward",
        "backward",
        "left",
        "right",
        "up",
        "down",
        "飞到",
        "移动",
        "前进",
        "后退",
        "左移",
        "右移",
        "向前",
        "往前",
        "向后",
        "往后",
        "向左",
        "往左",
        "向右",
        "往右",
        "上升",
        "下降",
        "向北",
        "向南",
        "向东",
        "向西",
    )
    PHOTO_KEYWORDS = ("photo", "capture", "image", "screenshot", "拍照", "截图", "图像")
    CAMERA_VIEW_KEYWORDS = (
        "camera",
        "camera view",
        "what do you see",
        "what can you see",
        "visible",
        "look at the image",
        "摄像头",
        "画面",
        "视频流",
        "看到什么",
        "看到了什么",
        "看一下画面",
        "看一下无人机",
    )
    ROTATE_KEYWORDS = ("rotate", "turn", "yaw", "旋转", "转向", "航向")
    PATROL_KEYWORDS = ("patrol", "inspect", "survey", "area", "巡检", "巡航", "区域")
    SEARCH_KEYWORDS = ("search", "find", "detect", "locate", "识别", "搜索", "寻找", "检测", "目标")
    TRACK_KEYWORDS = ("track", "follow", "追踪", "跟踪", "跟随")
    CONDITIONAL_KEYWORDS = ("until", "if", "when", "while", "如果", "直到", "发现后", "条件")

    # ── Mission workflow keywords (P4: L0/L1 mission command semantics) ──
    # Read-only mission queries → L0 direct, risk=safe
    MISSION_PROGRESS_KEYWORDS = (
        "mission progress", "task progress",
        "查看任务进度", "任务进度", "查看进度", "进度查询", "进度",
    )
    MISSION_DOWNLOAD_KEYWORDS = (
        "download mission", "mission download",
        "下载飞控任务", "下载任务", "下载航线", "下载航点",
    )
    # High-risk mission actions → L0 direct (sim) / L4 supervised (real), risk=high
    MISSION_UPLOAD_KEYWORDS = (
        "upload mission", "mission upload",
        "上传当前航线", "上传航线", "上传任务", "上传航点",
    )
    MISSION_START_KEYWORDS = (
        "start mission", "mission start",
        "启动航点任务", "启动任务", "启动航线", "开始任务",
    )
    MISSION_CLEAR_KEYWORDS = (
        "clear mission", "mission clear", "delete mission",
        "清空飞控任务", "清空任务", "清空航线", "清除任务", "删除任务",
    )
    # Combined high-risk mission keywords (for real-vehicle supervised routing)
    MISSION_HIGH_RISK_KEYWORDS = MISSION_UPLOAD_KEYWORDS + MISSION_START_KEYWORDS + MISSION_CLEAR_KEYWORDS

    HIGH_RISK_KEYWORDS = TAKEOFF_KEYWORDS + LAND_KEYWORDS + ARM_KEYWORDS

    def classify(
        self,
        command: str,
        capabilities: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
    ) -> TaskLevel:
        text = self._normalize(command)
        if not text:
            return TaskLevel.L2_PLAN

        capabilities = capabilities or {}
        is_real_vehicle = bool(capabilities.get("real_vehicle"))

        # ── Mission workflow routing (P4/P5) ──
        # All mission commands (read-only and high-risk) route to L0_DIRECT.
        # On real vehicle, route() marks risk_level=high and the runtime gates
        # execution behind operator approval (_await_approval). This is preferred
        # over L4_SUPERVISED because we know the exact tool to call.
        # Read-only mission queries → L0 direct (safe, no side effects)
        if self._has_any(text, self.MISSION_PROGRESS_KEYWORDS):
            return TaskLevel.L0_DIRECT
        if self._has_any(text, self.MISSION_DOWNLOAD_KEYWORDS):
            return TaskLevel.L0_DIRECT

        # High-risk mission actions → L0 direct (both sim and real vehicle).
        # Sim: executes immediately with warning event.
        # Real vehicle: _execute_direct_route() gates behind approval when
        #   capabilities.requires_operator_approval is True.
        if self._has_any(text, self.MISSION_START_KEYWORDS):
            return TaskLevel.L0_DIRECT
        if self._has_any(text, self.MISSION_CLEAR_KEYWORDS):
            return TaskLevel.L0_DIRECT
        if self._has_any(text, self.MISSION_UPLOAD_KEYWORDS):
            return TaskLevel.L0_DIRECT

        # ── Existing routing ──
        # NOTE: high-risk vehicle commands (takeoff/land/arm) on real vehicle
        # previously routed to L4_SUPERVISED. Now they fall through to L0_DIRECT
        # (below) so the approval gate can handle them. L4_SUPERVISED is reserved
        # for genuinely ambiguous tasks that lack a single direct tool mapping.

        if self._has_any(text, self.CAMERA_VIEW_KEYWORDS):
            return TaskLevel.L3_AGENT_LOOP
        if self._has_any(text, self.TRACK_KEYWORDS):
            return TaskLevel.L3_AGENT_LOOP
        if self._has_any(text, self.SEARCH_KEYWORDS):
            return TaskLevel.L3_AGENT_LOOP
        if self._has_any(text, self.CONDITIONAL_KEYWORDS):
            if self._is_simple_conditional_sequence(text):
                return TaskLevel.L1_TEMPLATE
            return TaskLevel.L3_AGENT_LOOP
        if self._has_any(text, self.PATROL_KEYWORDS) and self._has_any(text, self.SEARCH_KEYWORDS + self.PHOTO_KEYWORDS):
            return TaskLevel.L3_AGENT_LOOP
        if self._has_any(text, self.PATROL_KEYWORDS):
            return TaskLevel.L1_TEMPLATE

        action_count = self._action_count(text)
        direct_tool, _ = self._direct_tool(text)
        if direct_tool and action_count <= 1:
            return TaskLevel.L0_DIRECT
        memory_level = self._memory_level_hint(text, memory)
        if memory_level:
            return memory_level
        if action_count >= 2:
            if self._is_template_sequence(text):
                return TaskLevel.L1_TEMPLATE
            return TaskLevel.L2_PLAN
        if self._has_any(text, self.TAKEOFF_KEYWORDS + self.FLY_KEYWORDS + self.PHOTO_KEYWORDS + self.ROTATE_KEYWORDS + self.PATROL_KEYWORDS):
            return TaskLevel.L1_TEMPLATE
        return TaskLevel.L2_PLAN

    def route(
        self,
        command: str,
        level: TaskLevel | None = None,
        capabilities: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
    ) -> TaskRoute:
        text = self._normalize(command)
        capabilities = capabilities or {}
        memory_hint = self._memory_route_hint(text, memory)
        level = level or self.classify(command, capabilities=capabilities, memory=memory)

        if level == TaskLevel.L0_DIRECT:
            tool, params = self._direct_tool(text)
            if tool:
                risk = self._risk_for_tool(text, tool, capabilities)
                notes: list[str] = []
                if risk == "high":
                    notes.append("high-risk action")
                    if capabilities.get("real_vehicle"):
                        notes.append("real-vehicle: operator approval required before execution")
                    else:
                        notes.append("simulation: fast execution, runtime should log warning")
                elif risk == "elevated":
                    notes.append("elevated-risk: changes vehicle state")
                return TaskRoute(
                    level, "direct", "single low-latency command",
                    tool, params, notes=notes, risk_level=risk,
                )
            return TaskRoute(TaskLevel.L1_TEMPLATE, "template", "direct route had no concrete tool")
        if level == TaskLevel.L1_TEMPLATE:
            return TaskRoute(level, "template", "single action can use deterministic template planning")
        if level == TaskLevel.L3_AGENT_LOOP:
            notes = ["downgrade_to_l2_until_agent_loop"]
            reason = "task needs observe-think-act iteration"
            if memory_hint:
                reason = str(memory_hint.get("reason") or reason)
                notes.append(f"memory_hint:{memory_hint.get('source', 'guidance')}")
            return TaskRoute(level, "agent_loop", reason, notes=notes)
        if level == TaskLevel.L4_SUPERVISED:
            risk = "high" if self._has_any(text, self.MISSION_HIGH_RISK_KEYWORDS + self.HIGH_RISK_KEYWORDS) else "elevated"
            return TaskRoute(
                level, "supervised",
                "task requires human supervision (no single direct tool)",
                risk_level=risk,
                notes=["automatic execution blocked; operator approval required"],
            )
        return TaskRoute(level, "plan", "task needs model or richer planner decomposition")

    def _direct_tool(self, text: str) -> tuple[str, dict[str, Any]]:
        # Mission workflow tools (P4)
        if self._has_any(text, self.MISSION_PROGRESS_KEYWORDS):
            return "drone_get_mission_progress", {}
        if self._has_any(text, self.MISSION_DOWNLOAD_KEYWORDS):
            return "drone_download_mission", {}
        if self._has_any(text, self.MISSION_CLEAR_KEYWORDS):
            return "drone_clear_mission", {}
        if self._has_any(text, self.MISSION_START_KEYWORDS):
            return "drone_start_mission", {}
        if self._has_any(text, self.MISSION_UPLOAD_KEYWORDS):
            # upload requires waypoints_json; without it, route to L1 template
            # so the planner can extract waypoints from context. But if the user
            # explicitly says "upload current mission", we still route direct
            # and let the tool handle the missing-param error gracefully.
            return "drone_upload_mission", {}
        # Existing direct tools
        if self._has_any(text, self.LIST_KEYWORDS):
            return "drone_list_vehicles", {}
        if self._has_any(text, self.STATUS_KEYWORDS):
            return "drone_get_status", {}
        if self._has_any(text, self.CONNECT_KEYWORDS):
            return "drone_connect", {}
        if self._has_any(text, self.TAKEOFF_KEYWORDS):
            # Default takeoff altitude; safety layer may clamp to constraints.
            return "drone_takeoff", {"altitude": 3.0}
        if self._has_any(text, self.ARM_KEYWORDS):
            return "drone_arm", {}
        if self._has_any(text, self.LAND_KEYWORDS):
            return "drone_land", {}
        if self._has_any(text, self.HOVER_KEYWORDS):
            return "drone_hover", {}
        if self._has_any(text, self.DISARM_KEYWORDS):
            return "drone_disarm", {}
        return "", {}

    def _risk_for_tool(self, text: str, tool: str, capabilities: dict[str, Any]) -> str:
        """Determine risk level for a direct tool call.

        Returns: "safe" | "elevated" | "high"

        P5 spec high-risk tools (require approval on real vehicle):
          drone_arm, drone_takeoff, drone_start_mission,
          drone_clear_mission, drone_upload_mission, drone_land
        """
        # Read-only mission tools → safe
        if tool in ("drone_get_mission_progress", "drone_download_mission"):
            return "safe"
        # Read-only state tools → safe
        if tool in ("drone_get_status", "drone_list_vehicles", "drone_connect"):
            return "safe"
        # High-risk mission actions (irreversible or vehicle-state-changing)
        if tool in ("drone_clear_mission", "drone_start_mission", "drone_upload_mission"):
            return "high"
        # High-risk vehicle state changes (motors armed / vehicle leaves ground)
        if tool in ("drone_arm", "drone_takeoff"):
            return "high"
        # drone_land: elevated on sim, high on real vehicle (per P5 spec)
        if tool == "drone_land":
            if capabilities.get("real_vehicle"):
                return "high"
            return "elevated"
        # Reversible vehicle control actions
        if tool in ("drone_hover", "drone_disarm"):
            return "elevated"
        # Movement commands → elevated (changes position but generally reversible)
        if tool in ("drone_fly_to", "drone_fly_velocity", "drone_move_relative",
                    "drone_fly_path", "drone_rotate_to"):
            return "elevated"
        return "safe"

    def _action_count(self, text: str) -> int:
        groups = (
            self.STATUS_KEYWORDS,
            self.CONNECT_KEYWORDS,
            self.LIST_KEYWORDS,
            self.HOVER_KEYWORDS,
            self.LAND_KEYWORDS,
            self.TAKEOFF_KEYWORDS,
            self.FLY_KEYWORDS,
            self.PHOTO_KEYWORDS,
            self.ROTATE_KEYWORDS,
            self.PATROL_KEYWORDS,
            self.SEARCH_KEYWORDS,
            self.TRACK_KEYWORDS,
        )
        return sum(1 for group in groups if self._has_any(text, group))

    def _is_template_sequence(self, text: str) -> bool:
        simple_groups = (
            self.TAKEOFF_KEYWORDS,
            self.FLY_KEYWORDS,
            self.HOVER_KEYWORDS,
            self.LAND_KEYWORDS,
            self.ROTATE_KEYWORDS,
            self.PHOTO_KEYWORDS,
        )
        has_simple_action = any(self._has_any(text, group) for group in simple_groups)
        has_advanced_action = self._has_any(
            text,
            self.SEARCH_KEYWORDS + self.TRACK_KEYWORDS + self.CONDITIONAL_KEYWORDS + self.PATROL_KEYWORDS,
        )
        return has_simple_action and not has_advanced_action

    def _is_simple_conditional_sequence(self, text: str) -> bool:
        """Return True for local precondition wording such as "if not flying".

        These commands are still deterministic flight scripts. They should not
        pay the Agent Loop latency tax just because the operator used "if".
        """
        if self._has_any(text, self.SEARCH_KEYWORDS + self.TRACK_KEYWORDS + self.PATROL_KEYWORDS):
            return False
        if self._has_any(text, self.PHOTO_KEYWORDS + self.CAMERA_VIEW_KEYWORDS):
            return False
        simple_groups = (
            self.TAKEOFF_KEYWORDS,
            self.FLY_KEYWORDS,
            self.HOVER_KEYWORDS,
            self.LAND_KEYWORDS,
            self.ROTATE_KEYWORDS,
        )
        return any(self._has_any(text, group) for group in simple_groups)

    def _memory_level_hint(self, text: str, memory: dict[str, Any] | None) -> TaskLevel | None:
        hint = self._memory_route_hint(text, memory)
        if not hint:
            return None
        level = str(hint.get("level") or "").strip().lower()
        return {
            TaskLevel.L1_TEMPLATE.value: TaskLevel.L1_TEMPLATE,
            TaskLevel.L2_PLAN.value: TaskLevel.L2_PLAN,
            TaskLevel.L3_AGENT_LOOP.value: TaskLevel.L3_AGENT_LOOP,
            TaskLevel.L4_SUPERVISED.value: TaskLevel.L4_SUPERVISED,
        }.get(level)

    def _memory_route_hint(self, text: str, memory: dict[str, Any] | None) -> dict[str, Any] | None:
        guidance = (memory or {}).get("guidance") or {}
        hints = guidance.get("routing_hints") or []
        if not isinstance(hints, list):
            return None
        for hint in hints:
            if not isinstance(hint, dict):
                continue
            terms = [self._normalize(str(term)) for term in hint.get("match_terms") or []]
            terms = [term for term in terms if len(term) >= 3]
            if not terms:
                continue
            if any(term in text or (len(text) >= 6 and text in term) for term in terms):
                return hint
        return None

    @staticmethod
    def _normalize(command: str) -> str:
        return " ".join((command or "").strip().lower().split())

    @staticmethod
    def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)
