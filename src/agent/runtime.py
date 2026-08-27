"""In-process VLA agent runtime used by the web command center.

按职责拆分为 Mixin（runtime_chat/planner/control/tools/ops/execute/messages），本文件保留 AgentRuntime 组合类与全部符号 re-export，外部 `from src.agent.runtime import ...` 用法不变。"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.autonomy.supervisor import ExecutionSupervisor
from src.gcs import GroundStationServices
from src.modules.mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from src.modules.formation import FLIGHT_ACTIONS as FORMATION_FLIGHT_ACTIONS
from src.replay.session import ReplaySession, list_replay_sessions, read_replay_session

from .agent_loop import AgentLoop
from .llm import LLMMissionPlanner, LLMUnavailableError
from .loop_types import LoopState
from .memory import AgentMemory
from .planner import MissionPlan, MissionPlanner, MissionStep
from .run_log import RunLog, RunLogStore
from .skill_registry import SkillRegistry
from .sub_agent import SubAgentRunner
from .task_runs import TaskRunStore
from .tool_cards import TOOL_CARDS
from .tool_executor import TOOL_OUTPUT_SCHEMAS, ToolCallResult, ToolRuntime
from .llm_protocol import function_tool_schema, tool_schema_from_spec, validate_json_schema
from src.config import config

from .runtime_types import (
    ChatMessage,
    RunState,
    RuntimeEvent,
    ToolApprovalRequest,
)
from .runtime_settings import (
    AIRSIM_SETTINGS_TEMPLATES,
    ATTACHMENTS_DIR,
    REPO_ROOT,
    SESSIONS_DIR,
    SETTINGS_PATH,
    SKILLS_OVERRIDES_PATH,
    _application_settings,
    _build_connect_params,
    _camera_settings,
    _connection_settings,
    _default_application_settings,
    _default_camera_settings,
    _default_connection_settings,
    _load_settings,
    _save_settings,
    _select_connection_for_backend,
)
from .runtime_planner import (
    CORRECTION_ATTEMPTS_MAX,
    OBSERVATION_TOOLS,
    MOTION_TOOLS,
    CONNECTION_FAILURE_TERMS,
    RuntimePlannerMixin,
)
from .runtime_chat import RuntimeChatMixin
from .runtime_control import RuntimeControlMixin
from .runtime_tools import RuntimeToolsMixin
from .runtime_ops import RuntimeOpsMixin
from .runtime_execute import RuntimeExecuteMixin
from .runtime_messages import RuntimeMessagesMixin


class AgentRuntime(
    RuntimeChatMixin,
    RuntimePlannerMixin,
    RuntimeControlMixin,
    RuntimeToolsMixin,
    RuntimeOpsMixin,
    RuntimeExecuteMixin,
    RuntimeMessagesMixin,
):
    """Coordinates planner, tools, memory, and safety supervisor."""

    def __init__(self) -> None:
        self._started_at = time.time()
        self.planner = LLMMissionPlanner()
        self.rule_planner = MissionPlanner()
        self.tools = ToolRuntime(camera_settings_provider=lambda: _camera_settings())
        self.memory = AgentMemory()
        self.task_runs = TaskRunStore()
        self.supervisor = ExecutionSupervisor(default_timeout=30.0)
        self.skills = SkillRegistry(overrides_path=SKILLS_OVERRIDES_PATH)
        self._execution_slot = threading.Lock()
        self._execution_thread_id = 0
        self._cancel_requested = threading.Event()
        self._cancelled_request_ids: set[str] = set()
        self.agent_loop = AgentLoop(
            self.tools,
            self.planner,
            self.memory,
            on_event=self._on_agent_event,
            should_stop=lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set(),
            should_pause=self.supervisor.should_pause,
            skills=self.skills,
            execute_tool=self._execute_agent_tool,
            on_state=self._on_agent_loop_state,
        )
        # the formation control loop stops on emergency stop / task cancel
        self.tools.formation_set_stop_provider(
            lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set()
        )
        # single-vehicle blocking flight commands (fly_to / path / takeoff)
        # also preempt on emergency stop / task cancel
        self.tools.set_flight_stop_provider(
            lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set()
        )
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
        self._thread: threading.Thread | None = None
        self._current_session_id: str = ""
        self._backend_generation = 0
        self._auto_connect_initial_backend_id = self.tools.backend_id
        self._last_visual_frame: dict[str, Any] = {}
        # P5: pending high-risk approvals keyed by run_id
        self._pending_approvals: dict[str, ToolApprovalRequest] = {}
        # Append-only run event log for the currently executing run (or None).
        self._run_log: RunLog | None = None
        # Replay: telemetry recording around runs and manual flights
        self._active_replay: ReplaySession | None = None
        self._manual_replay: ReplaySession | None = None
        self._replay_lock = threading.Lock()
        self.gcs = GroundStationServices(
            self.tools,
            supervisor=self.supervisor,
            current_run_provider=lambda: self._current.to_dict() if self._current else None,
        )
        self._append_event("info", "system", "AirSim VLA Agent runtime ready")
        self._load_or_create_default_session()
        threading.Thread(
            target=self._auto_connect_from_settings,
            args=(self._backend_generation, self._auto_connect_initial_backend_id),
            daemon=True,
        ).start()
