"""In-process VLA agent runtime used by the web command center."""

from __future__ import annotations

import logging
import threading
import time
import queue
from typing import Any

from .supervisor import ExecutionSupervisor
from src.gcs import GroundStationServices
from src.flightlog import FlightLogSession

from .agent_loop import AgentLoop
from .memory import AgentMemory
from .run_log import RunLog
from .skill_registry import SkillRegistry
from .task_runs import TaskRunStore
from src.config import config
from src.logging_config import get_logger
from .run_state import (
    CANCEL_ABORT_WINDOW_S,
    CONNECTION_FAILURE_TERMS,
    CORRECTION_ATTEMPTS_MAX,
    MOTION_TOOLS,
    OBSERVATION_TOOLS,
    STRUCTURED_PERCEPTION_TOOLS,
    ChatMessage,
    RunState,
    RuntimeEvent,
    ToolApprovalRequest,
    reasoning_delta,
)
from .settings_store import (
    ATTACHMENTS_DIR,
    REPO_ROOT,
    SETTINGS_PATH,
    SKILLS_OVERRIDES_PATH,
    _application_settings,
    _build_connect_params,
    _camera_settings,
    _connection_is_real_vehicle,
    _connection_settings,
    _default_camera_settings,
    _load_settings,
    _perception_rtsp_config,
    _save_settings,
    _select_connection_for_backend,
)
from .agent_bridge import AgentBridgeMixin
from .execution import ExecutionMixin
from .vehicle_api import VehicleMixin
from .cancellation import CancellationMixin
from .session_api import SessionMixin
from .events import EventsMixin
from .approval_gate import ApprovalsMixin
from .gcs_api import GcsMixin
from .flightlog_api import FlightLogMixin
from .llm import LLMMissionPlanner
from .planner import MissionPlanner
from .tool_executor import ToolRuntime
from .guardians import GuardiansMixin
from .session_store import (
    SESSIONS_DIR,
    SESSION_MEMORY_DIR,
    _sessions_by_mtime,
    _slim_result_data,
    _trim_session_message,
    read_session_file,
    trim_loop_state_payload,
)

# ---------------------------------------------------------------------------
# Compatibility surface.
#
# The definitions below live in run_state / settings_store / session_store now;
# they are re-exported here because outside callers import them from
# src.agent.runtime. Rebinding one of these names on THIS module does NOT
# redirect the moved code: each function resolves its own module globals, so a
# test that wants to redirect a path must patch the module that owns it
# (settings_store.ATTACHMENTS_DIR / session_store.SESSIONS_DIR — both expose an
# accessor for exactly that). __all__ states the contract so a name that stops
# being re-exported is a visible decision rather than an accident.
# ---------------------------------------------------------------------------
__all__ = [
    "AgentRuntime",
    "ChatMessage",
    "RunState",
    "RuntimeEvent",
    "ToolApprovalRequest",
    "reasoning_delta",
    "CORRECTION_ATTEMPTS_MAX",
    "CANCEL_ABORT_WINDOW_S",
    "OBSERVATION_TOOLS",
    "STRUCTURED_PERCEPTION_TOOLS",
    "MOTION_TOOLS",
    "CONNECTION_FAILURE_TERMS",
    "REPO_ROOT",
    "SETTINGS_PATH",
    "SKILLS_OVERRIDES_PATH",
    "ATTACHMENTS_DIR",
    "SESSIONS_DIR",
    "SESSION_MEMORY_DIR",
    "_application_settings",
    "_build_connect_params",
    "_camera_settings",
    "_connection_is_real_vehicle",
    "_connection_settings",
    "_default_camera_settings",
    "_load_settings",
    "_perception_rtsp_config",
    "_save_settings",
    "_select_connection_for_backend",
    "_sessions_by_mtime",
    "_slim_result_data",
    "_trim_session_message",
    "read_session_file",
    "trim_loop_state_payload",
]

# 两处 session 读失败的分支调用了 logger.warning("event", key=value)（structlog
# 风格），但本模块从未定义 logger——真走到"会话文件读不出来"那条路径时抛
# NameError：list_sessions 整个失败，而 _persist_current_session 是在持锁的保存
# 路径里抛，破坏面更大。用项目自己的 get_logger（缺 structlog 时它会回落到一个
# 接受关键字字段的适配器），两种环境下都能工作。
logger = get_logger(__name__)


# 记忆按"会话"隔离：一个 session 一份记忆（任务记录/经验/风险/事实），
# 新开会话不再继承上一个会话的记忆。

# 会话文件里最占体积、又没人真的需要落盘的两类字段：
#   loop_state.observations —— 每次工具观测的原文，单条消息就能到 500KB；
#   details.agent_state    —— 每步完整状态快照，约 25KB/条。
# 前端不渲染它们（timeline 用 process_trace / decisions / results），
# 运行时需要的仍在内存里，只是落盘和发给前端时剥掉。
# 发给前端时 loop_state 里只保留界面真正会读的键（界面读 decisions / results）
# 工具返回里界面只读 message（决策行正文）与 status









# 同一个会话文件会被多条线程写（/api/state 的 HTTP 线程、运行线程、兜底线程）。
# 光靠"临时文件 + os.replace"不够：Windows 上并发 replace 会因目标文件正被打开
# 而抛 WinError 5（拒绝访问），上层又是 except 静默吞掉 —— 表现为内容没落盘。
# 所以这里按进程串行化会话写入，并对替换做少量重试。





# Plan-Execute ⇄ ReAct collaboration:
# - OBSERVATION_TOOLS: steps whose outcome必须由 LLM 读图/深度后才能决策
#   (photo/VLM/depth) —— 固定序列无法表达,必须转 ReAct。
# - STRUCTURED_PERCEPTION_TOOLS: 返回结构化 JSON 的感知读取。executor 可
#   直接消费结果,不需要每步一次 LLM 决策;把它们塞进 ReAct 会让普通搜索
#   任务多出 5~10 次模型往返(每次 15~30s),是任务耗时过长的主因。
# - MOTION_TOOLS: steps that change vehicle state.
# 取消旗标能打断阻塞式飞行命令的时间窗（秒）。见 _flight_abort_requested：
# 窗口内算数，窗口外视为残留，避免渗到操作员的下一次手动起飞。




# 真机上必须经操作员签字的动作：改变位置、改变飞行模式（AUTO 会启动已上传的
# 航线）或改写航线的工具。它们的卡片风险是 medium，而审批门只在 high 时开，
# 所以单靠卡片等级挡不住；drone_disarm 在空中等于坠机，一并纳入。
# drone_land 与 formation 的飞行动作在 _tool_risk_level 里另有特判。
# Failures that re-running cannot fix: link/connection problems mean the
# backend itself is unreachable, so a ReAct correction round is pointless.


































class AgentRuntime(GuardiansMixin, FlightLogMixin, GcsMixin, ApprovalsMixin, EventsMixin, SessionMixin, CancellationMixin, VehicleMixin, ExecutionMixin, AgentBridgeMixin):
    """Coordinates planner, tools, memory, and safety supervisor."""

    def __init__(self) -> None:
        """依赖注入 + 启动。

        分成三步是为了让测试能拿到"字段齐全的空壳"，而不是手工注入二三十个属性：
        ``object.__new__(AgentRuntime)`` 之后调 `_init_runtime_state()`，再只替换
        自己关心的字段与少量方法。手工工厂的代价是实测过的——运行时新增一个必需
        字段，就要同时改好几个测试文件的手工属性列表，失败信息还完全指不到原因。

        `_init_runtime_state()` 不依赖任何注入对象、无副作用；`_wire_callbacks()`
        把方法接到工具层与循环上，必须在字段就绪之后。
        """
        self._started_at = time.time()
        self.planner = LLMMissionPlanner()
        self.rule_planner = MissionPlanner()
        self.perception_axis = self._create_perception_axis()
        self.tools = ToolRuntime(
            camera_settings_provider=lambda: _camera_settings(),
            perception_axis=self.perception_axis,
            # 视觉分析（inspect_current_frame）与规划共用同一个模型：用户在设置里
            # 配了哪个模型，规划与看图就都用它。这里必须传当前任务的 model_id，
            # 留空会回落到注册表 default，于是"选的模型"和"看图的模型"变成两个
            # ——用户改了选择器却不生效，且系统背着他另选了一个模型。
            vlm_provider=self._vlm_analyze,
        )
        self.memory = AgentMemory()
        self.task_runs = TaskRunStore()
        # 进程启动时的记录层崩溃恢复：把上一次进程留下的"运行中"记录标成
        # interrupted（界面不再永远转圈）。只改状态，绝不重放动作——飞行指令是
        # 非幂等的，这一条是刻意与"会话可恢复"的编码 agent 划清界限的地方。
        try:
            recovered = self.task_runs.mark_running_as_interrupted()
        except Exception:
            recovered = 0
        if recovered:
            logging.getLogger("runtime").warning("task_runs_marked_interrupted", extra={"count": recovered})
        self.supervisor = ExecutionSupervisor()
        self.skills = SkillRegistry(overrides_path=SKILLS_OVERRIDES_PATH)
        self._init_runtime_state()
        self._auto_connect_initial_backend_id = self.tools.backend_id
        self._wire_callbacks()
        self.gcs = GroundStationServices(
            self.tools,
            supervisor=self.supervisor,
            current_run_provider=lambda: self._current.to_dict() if self._current else None,
            # 设备动作走同一条治理入口：操作员的手动操作与 Agent 的飞行步骤共用
            # 执行槽、编队冲突、真机审批、后端切换这些守卫。
            governed_executor=self._execute_agent_tool,
        )
        self._append_event("info", "system", "AirSim VLA Agent runtime ready")
        self._load_or_create_default_session()
        threading.Thread(
            target=self._auto_connect_from_settings,
            args=(self._backend_generation, self._auto_connect_initial_backend_id),
            daemon=True,
        ).start()

    def _vlm_analyze(self, question: str, image_b64: str) -> dict[str, Any]:
        """`inspect_current_frame` 的视觉分析入口：走当前任务用的那个模型。"""
        return self.planner.analyze_image(question, image_b64, model_id=self._active_model_id())

    def _active_model_id(self) -> str:
        """当前生效的模型 id：跑着的任务用它自己的模型，否则交给注册表默认值。

        规划与视觉分析必须落在同一个模型上。任务内取 `run.model_id`（就是操作员
        在模型选择器里选的那个），任务外返回空串让 `_resolve_config` 回落默认值
        ——空串在这里是"没有任务在跑"，不是"另选一个图像模型"。
        """
        run = self._current
        if run is not None:
            model_id = str(getattr(run, "model_id", "") or "").strip()
            if model_id:
                return model_id
        return ""

    def _init_runtime_state(self) -> None:
        """所有可变运行态字段的初值。不依赖注入对象、无副作用。

        测试用 `object.__new__(AgentRuntime)` 之后调它即可得到一个字段完整的
        空壳（见 tests/_runtime_factories.py）。
        """
        self._execution_slot = threading.Lock()
        self._execution_thread_id = 0
        self._cancel_requested = threading.Event()
        # 取消要绑定"取消的是哪一次工作"：全局旗标会被后续提交（哪怕只是一条
        # chat）清掉，也会残留到下一次手动起飞把命令打断。所以同时记录被取消
        # 的 run id 和置位时刻，判定一律按 id/时间窗来，而不是读裸旗标。
        self._cancel_requested_at = 0.0
        self._cancel_requested_run_id = ""
        self._cancelled_request_ids: set[str] = set()
        # 执行中提交的新指令：作为"补充指令"注入正在跑的循环（steer），而不是
        # 中断任务重来。操作员可以用一句话纠正跑偏的任务，飞机也不会因为中断
        # 收尾而被迫降落。
        self._pending_steer: list[str] = []
        self._lock = threading.RLock()
        self._events: list[RuntimeEvent] = []
        self._messages: list[ChatMessage] = []
        self._subscribers: list[queue.Queue] = []
        self._current: RunState | None = None
        self._active_chat_requests: set[str] = set()
        # Run IDs that have been submitted but whose _plan_and_execute thread
        # has not yet set self._current. Protects against premature orphan
        # marking during the (LLM-bound) gap between message creation and
        # self._current being assigned.
        self._pending_run_ids: set[str] = set()
        # 子 Agent 编号由父级持有，保证同一任务的多个子任务各自拿到唯一的日志名

        self._thread: threading.Thread | None = None
        self._current_session_id: str = ""
        # 追踪辅助：算法负责把锁定目标保持在画面中央（不经 LLM、不暴露工具）
        self._tracking_assist: threading.Thread | None = None
        self._tracking_assist_stop = threading.Event()
        self._tracking_assist_run_id: str = ""
        # 飞行包线看门狗：近距离识别/追踪任务里，如果飞机报出异常高度或水平
        # 位移（真实失控或 EKF 位置估计发散都会如此），立即中止并降落，而不是
        # 让它在无人干预的情况下飞出几公里、飞好几分钟。
        self._envelope_stop = threading.Event()
        self._envelope_thread: threading.Thread | None = None
        self._envelope_run_id: str = ""
        self._backend_generation = 0
        self._auto_connect_initial_backend_id = ""
        self._last_visual_frame: dict[str, Any] = {}
        # P5: pending high-risk approvals keyed by run_id
        self._pending_approvals: dict[str, ToolApprovalRequest] = {}
        # Append-only run event log for the currently executing run (or None).
        self._run_log: RunLog | None = None
        # 飞行记录：任务执行与手动飞行期间自动采集遥测（src/flightlog/）
        self._active_flightlog: FlightLogSession | None = None
        self._manual_flightlog: FlightLogSession | None = None
        self._flightlog_lock = threading.Lock()

    def _wire_callbacks(self) -> None:
        """把 runtime 的方法接到工具层与 Agent 循环上（依赖已注入的对象）。"""
        self.agent_loop = AgentLoop(
            self.tools,
            self.planner,
            self.memory,
            on_event=self._on_agent_event,
            should_stop=lambda: self.supervisor.is_emergency_stopped() or self._active_run_cancelled(),
            should_pause=self.supervisor.should_pause,
            skills=self.skills,
            execute_tool=self._execute_agent_tool,
            on_state=self._on_agent_loop_state,
            steer_provider=self._take_pending_steer,
        )
        # the formation control loop stops on emergency stop / task cancel
        self.tools.formation_set_stop_provider(self._flight_abort_requested)
        # single-vehicle blocking flight commands (fly_to / path / takeoff)
        # also preempt on emergency stop / task cancel
        self.tools.set_flight_stop_provider(self._flight_abort_requested)

    # ------------------------------------------------------------------
    # Perception axis lifecycle (docs/perception_axis_design.md)
    # ------------------------------------------------------------------

    def _create_perception_axis(self) -> Any:
        """Build and start the perception axis from the runtime config.

        A failed start is non-fatal: the flight backend keeps working and the
        axis just reports health/start_error through perception_status.
        """
        try:
            from src.modules.perception.axis import PerceptionAxis
            from src.modules.perception.profile import resolve_profile

            profile = resolve_profile(config)
            if profile is None:
                return None
            # frame_provider: 感知轴走独立的 AirSim 相机通道（与飞控后端类型
            # 解耦）——px4_mavlink 后端的控制器没有拍图能力，飞行链路切后端
            # 不应影响感知画面。独立 AirSimController 带 _rpc 超时+运行时重置，
            # 而本进程内手搓 MultirotorClient 的 simGetImages 会无超时卡死。
            axis = PerceptionAxis(
                profile=profile,
                rtsp_url=str(getattr(config, "perception_rtsp_url", "") or ""),
                rtsp_transport=str(_camera_settings().get("transport") or ""),
                rtsp_config_provider=_perception_rtsp_config,
                frame_provider=self._perception_camera_controller,
            )
            # 只启动画面，不加载 YOLO：产品已无目标检测（语义问视觉模型、距离问
            # 深度图），检测线程保持休眠，不在启动时吃算力。
            ok = axis.start(detect=False)
            if not ok:
                import logging

                logging.getLogger("runtime").warning("perception_axis_start_failed", extra={"error": axis.health().get("start_error", "")})
            return axis
        except Exception as exc:  # the axis must never break the runtime
            import logging

            logging.getLogger("runtime").warning("perception_axis_init_failed", extra={"error": str(exc)})
            return None

    def shutdown(self, timeout_s: float = 20.0) -> dict[str, Any]:
        """进程退出前的收尾：停循环、把飞机放到安全状态、等执行槽释放。

        以前没有任何关闭路径：worker 全是 daemon 线程且从不 join，Ctrl+C 或部署
        重启会在任意时刻把它们杀掉（可能正好夹在一次飞控命令中间），既不会悬停
        也不留记录。终态选"保持悬停"而不是降落，与本系统其它中断路径一致——
        降落是不可逆的，会让操作员失去一架本可继续指挥的飞机。
        """
        self._cancel_active_work()
        try:
            self._stop_envelope_guard()
        except Exception:
            pass
        try:
            self._stop_tracking_assist("runtime shutdown")
        except Exception:
            pass
        with self._lock:
            run = self._current
        if run is not None and getattr(run, "execute", False):
            try:
                self._attempt_hold_position(run, "agent runtime shutdown")
            except Exception:
                pass
        deadline = time.time() + max(0.0, float(timeout_s))
        while self._execution_slot.locked() and time.time() < deadline:
            time.sleep(0.2)
        result = {
            "ok": True,
            "slot_released": not self._execution_slot.locked(),
            "run_id": str(getattr(run, "run_id", "") or ""),
        }
        self._append_event("warning", "system", "Agent 运行时正在关闭", result)
        return result

    def shutdown_perception(self) -> None:
        """Stop the perception axis; safe to call multiple times."""
        if self.perception_axis is not None:
            try:
                self.perception_axis.stop()
            except Exception as exc:
                import logging

                logging.getLogger("runtime").warning("perception_axis_stop_failed", extra={"error": str(exc)})

    def _perception_camera_controller(self) -> Any | None:
        """独立 AirSim 相机通道：感知轴的取帧源，与飞控后端类型解耦。

        px4_mavlink / px4_ros2 后端的控制器没有拍图能力，而感知画面（仿真
        相机/吊舱流）与飞行链路是两条独立通道。这里惰性创建并保活一个
        AirSimController（带 _rpc 超时+运行时重置），连接失败返回 None，
        感知轴自然报告离线，下一次取帧再尝试重建。
        """
        controller = getattr(self, "_perception_camera_ctrl", None)
        if controller is not None and bool(getattr(controller, "is_connected", False)):
            return controller
        if controller is None:
            try:
                from src.config import config as _cfg
                from src.modules.backends.airsim import AirSimController

                controller = AirSimController(ip=str(_cfg.airsim_ip), port=int(_cfg.airsim_port))
            except Exception as exc:
                import logging

                logging.getLogger("runtime").warning("perception_camera_init_failed", extra={"error": str(exc)})
                return None
        try:
            info = controller.connect(ip=controller._ip, port=controller._port)
        except Exception as exc:  # noqa: BLE001 — the camera channel must never crash the runtime
            import logging

            logging.getLogger("runtime").warning("perception_camera_connect_failed", extra={"error": str(exc)})
            self._perception_camera_ctrl = controller
            return None
        if not bool(getattr(info, "connected", False)):
            self._perception_camera_ctrl = controller
            return None
        self._perception_camera_ctrl = controller
        return controller












    _AGENT_INSTRUCTIONS_PATH = REPO_ROOT / ".airsim_agent" / "SYSTEM.md"
    # Agent 的长期笔记（pi 的 AGENTS.md 那个角色）：知识写在文件里、每轮自动注入，
    # 而不是让模型调用"记忆工具"。文件由操作员维护，运行时不写它。
    _AGENT_NOTES_PATH = REPO_ROOT / ".airsim_agent" / "NOTES.md"

















    # ── 飞行记录（flight log）──
























    # 时间线上"模型正在想"的占位文本：模型没有输出思考时必须被收尾/替换，
    # 不能留在时间线上冒充本轮的模型思考。
    _PENDING_REASONING_PLACEHOLDERS = {
        "正在根据最新遥测、工具结果和任务目标选择下一步动作。",
        "正在解析任务意图并生成可执行的工具序列；模型不可用时不会降级发出飞控指令。",
    }




    # 协议噪音：这些键对操作员没有信息量，渲染时剔除
    _RESULT_NOISE_KEYS = {
        "status", "backend", "vehicle_name", "vehicles", "safety", "raw",
        "duration_ms", "skipped", "skip_reason", "error_code", "requested_url",
        "offboard_hold_active", "link_stale",
        # 连接细节与 MAVLink 原始字段：排障用，操作员看要点时是噪音
        "active_link", "connection", "connection_error", "custom_mode", "base_mode",
        "system_status", "autopilot", "vehicle_type", "real_vehicle", "position_source",
        "status_text", "local_listen_url", "configured_peer_endpoint", "observed_peers",
        "probe_targets", "peer_source_verified", "actual_peer_endpoint", "actual_peer_age_s",
        "px4_remote_host", "px4_remote_port", "px4_remote_endpoint", "system_id",
        "component_id", "message",
        # 位置有效性/链路细节：排障字段，操作员看要点时同样是噪音
        "map_position_valid", "navigation_position_valid", "local_position_age_s",
        "global_position_age_s", "gcs_source_system", "gcs_source_component",
        "mavlink_wire_protocol", "gps_fix_type", "position_source",
        # 参数缓存与固件原始信息属于设置面板的内容，出现在工具行只会淹没要点
        "parameter_status", "parameters", "firmware", "capabilities",
        "satellites_visible", "gps_horizontal_accuracy_m", "gps_vertical_accuracy_m",
    }
    _RESULT_TELEMETRY_KEYS = {
        "position_ned", "velocity_ned", "velocity", "attitude_rad", "armed", "flying",
        "mode", "flight_mode", "heading_deg", "battery_voltage", "gps", "drone",
        "altitude", "altitude_m", "landed_state", "heartbeat_age_s",
    }

































































    # ------------------------------------------------------------------
    # P6: GCS MissionManager facade. UI and Agent both call these methods
    # so mission data flows through a single backend-neutral boundary.
    # ------------------------------------------------------------------



























































































