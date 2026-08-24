"""In-process VLA agent runtime used by the web command center."""

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


REPO_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = REPO_ROOT / "src" / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = REPO_ROOT / "src" / "data" / "settings.json"
SKILLS_OVERRIDES_PATH = REPO_ROOT / "src" / "data" / "skills.json"
ATTACHMENTS_DIR = REPO_ROOT / "src" / "data" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

# AirSim settings.json 通信模式模板（config/airsim_settings/）：
#   airsim_simpleflight_multirotor -> airsim 后端（本机直接 RPC 控制，3 机）
#   px4_mavlink_udp_sitl          -> px4_mavlink 后端（UDP 连本机/WSL PX4 SITL）
#   px4_ros2_tcp_edge             -> px4_ros2 后端（TCP 连 Jetson/边端 PX4 SITL）
AIRSIM_SETTINGS_TEMPLATES: dict[str, dict[str, str]] = {
    "airsim_simpleflight_multirotor": {
        "label": "AirSim 纯仿真（SimpleFlight · 3 机）",
        "description": "本机 AirSim 直接 API 控制，对应系统 airsim 后端。三架 SimpleFlight 用于多机验证。",
        "backend": "airsim",
        "file": "airsim_simpleflight_multirotor.json",
    },
    "px4_mavlink_udp_sitl": {
        "label": "PX4 MAVLink（UDP · 本机/虚拟机 SITL）",
        "description": "AirSim 作为 PX4 仿真器，UDP 14540/14580 连接本机或 WSL 的 PX4 SITL，对应 px4_mavlink 后端。",
        "backend": "px4_mavlink",
        "file": "px4_mavlink_udp_sitl.json",
    },
    "px4_ros2_tcp_edge": {
        "label": "PX4 ROS2（TCP · Jetson/边端 SITL）",
        "description": "TCP 4560 连接 Jetson 边端 PX4 SITL（ControlIp 按实际 IP 修改），对应 px4_ros2 后端。",
        "backend": "px4_ros2",
        "file": "px4_ros2_tcp_edge.json",
    },
}

# Plan-Execute ⇄ ReAct collaboration:
# - OBSERVATION_TOOLS: steps whose outcome must be seen before the next step
#   can be chosen (photo/VLM/detect/depth).
# - MOTION_TOOLS: steps that change vehicle state.
# A fixed sequence with observation -> motion is structurally dependent on
# mid-execution observations, so it routes to the ReAct loop before executing.
CORRECTION_ATTEMPTS_MAX = 2
OBSERVATION_TOOLS = {
    "airsim_take_photo",
    "airsim_detect_objects",
    "airsim_vlm_analyze_image",
    "airsim_vlm_confirm_target",
    "airsim_get_depth_map",
    "airsim_get_sensors",
}
MOTION_TOOLS = {
    "drone_arm",
    "drone_takeoff",
    "drone_fly_to",
    "drone_move_relative",
    "drone_fly_path",
    "drone_rotate_to",
    "drone_land",
    "drone_hover",
}
# Failures that re-running cannot fix: link/connection problems mean the
# backend itself is unreachable, so a ReAct correction round is pointless.
CONNECTION_FAILURE_TERMS = (
    "connect",
    "connection refused",
    "connect timed out",
    "connection timed out",
    "connect timeout",
    "unreachable",
    "no backend",
    "链接失败",
    "连接失败",
    "无法连接",
)


def _default_connection_settings() -> dict[str, Any]:
    """Return factory defaults matching the UI's QGC Links panel."""
    return {
        "auto_connect": False,
        "active_connection_id": "",
        "connections": [
            {
                "id": "default_airsim",
                "name": "AirSim Local",
                "type": "airsim",
                "params": {"host": "127.0.0.1", "portNumber": "41452"},
            },
            {
                "id": "default_px4_auto",
                "name": "PX4 Auto",
                "type": "auto",
                "params": {
                    "host": "127.0.0.1",
                    "portNumber": "14550",
                    "remotePort": "18570",
                    "realVehicle": False,
                },
            },
            {
                "id": "default_px4_usb",
                "name": "PX4 USB Serial",
                "type": "serial",
                "params": {
                    "port": "",
                    "baud": "115200",
                    "realVehicle": True,
                },
            },
            {
                "id": "default_px4",
                "name": "PX4 SITL UDP",
                "type": "udp",
                "params": {
                    "host": "127.0.0.1",
                    "portNumber": "14550",
                    "remotePort": "18570",
                    "realVehicle": False,
                },
            },
            {
                "id": "default_px4_ros2",
                "name": "PX4 ROS2 Gateway",
                "type": "px4_ros2",
                "params": {
                    "url": config.ros_bridge_url,
                    "workspace": config.ros_workspace_path,
                },
            },
        ],
    }


def _default_camera_settings() -> dict[str, Any]:
    """Return default camera source settings for the UI camera viewer."""
    return {
        "source": "airsim",
        "host": "127.0.0.1",
        "port": 41452,
        "url": "",
        "camera_name": "0",
        "vehicle_name": "",
        "image_type": "scene",
        "timeout_sec": 30.0,
        "auto_save": False,
    }


def _default_application_settings() -> dict[str, Any]:
    return {
        "appearance": {
            "language": "zh-CN",
            "theme": "dark",
            "density": "comfortable",
        },
        "map": {
            "default_layer": "satellite",
            "follow_vehicle": True,
            "show_vehicle_track": False,
            "require_reliable_gps": True,
        },
        "telemetry": {
            "refresh_ms": 250,
            "setup_refresh_ms": 100,
            "history_seconds": 60,
            "chart_sample_hz": 20,
        },
        "safety": {
            "confirm_real_vehicle_actions": True,
            "require_gps_for_global_mission": True,
            "max_display_jump_m": 120.0,
        },
        "agent": {
            "show_context_usage": True,
            "auto_select_multimodal_model": True,
            "persist_full_session_history": True,
        },
    }


def _application_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if settings is None:
        settings = _load_settings()
    defaults = _default_application_settings()
    raw = settings.get("application")
    section = dict(raw) if isinstance(raw, dict) else {}
    merged: dict[str, Any] = {}
    for group, values in defaults.items():
        incoming = section.get(group)
        merged[group] = {
            **values,
            **(dict(incoming) if isinstance(incoming, dict) else {}),
        }
    try:
        merged["telemetry"]["refresh_ms"] = max(100, min(2000, int(merged["telemetry"]["refresh_ms"])))
        merged["telemetry"]["setup_refresh_ms"] = max(
            50,
            min(2000, int(merged["telemetry"]["setup_refresh_ms"])),
        )
        merged["telemetry"]["history_seconds"] = max(10, min(600, int(merged["telemetry"]["history_seconds"])))
        merged["telemetry"]["chart_sample_hz"] = max(5, min(100, int(merged["telemetry"]["chart_sample_hz"])))
        merged["safety"]["max_display_jump_m"] = max(10.0, min(5000.0, float(merged["safety"]["max_display_jump_m"])))
    except (TypeError, ValueError):
        return defaults
    return merged


def _load_settings() -> dict[str, Any]:
    """Load persisted agent settings from disk."""
    try:
        if not SETTINGS_PATH.exists():
            return {}
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist agent settings to disk."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _connection_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the connection subsection, merging with defaults."""
    if settings is None:
        settings = _load_settings()
    defaults = _default_connection_settings()
    conn_section = dict(settings.get("connections") or {})
    connections = list(conn_section.get("connections") or defaults["connections"])
    by_id = {str(item.get("id") or ""): item for item in connections if isinstance(item, dict)}
    for default_conn in defaults["connections"]:
        if default_conn["id"] not in by_id:
            connections.append(default_conn)
    merged = {
        "auto_connect": conn_section.get("auto_connect", defaults["auto_connect"]),
        "active_connection_id": conn_section.get("active_connection_id", defaults["active_connection_id"]),
        "connections": connections,
    }
    # Ensure default connections exist if the list was empty.
    if not merged["connections"]:
        merged["connections"] = defaults["connections"]
    backend_hint = str(settings.get("backend") or "").strip()
    if backend_hint:
        selected_id, _selected = _select_connection_for_backend(merged, backend_hint)
        if selected_id:
            merged["active_connection_id"] = selected_id
    return merged


def _camera_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return persisted camera source settings, merging with UI defaults."""
    if settings is None:
        settings = _load_settings()
    defaults = _default_camera_settings()
    raw_section = settings.get("camera")
    section = dict(raw_section) if isinstance(raw_section, dict) else {}
    airsim_link = next(
        (
            c for c in _connection_settings(settings).get("connections", [])
            if str(c.get("type") or "").lower() == "airsim"
        ),
        {},
    )
    airsim_params = dict(airsim_link.get("params") or {}) if isinstance(airsim_link, dict) else {}
    image_type = str(section.get("image_type") or defaults["image_type"]).lower()
    if image_type not in {"scene", "depth", "segmentation", "infrared"}:
        image_type = defaults["image_type"]
    try:
        timeout_sec = float(section.get("timeout_sec", defaults["timeout_sec"]))
    except (TypeError, ValueError):
        timeout_sec = float(defaults["timeout_sec"])
    timeout_sec = max(3.0, min(120.0, timeout_sec))
    host = str(section.get("host") or airsim_params.get("host") or defaults["host"]).strip() or defaults["host"]
    try:
        port = int(section.get("port") or airsim_params.get("portNumber") or airsim_params.get("port") or defaults["port"])
    except (TypeError, ValueError):
        port = int(defaults["port"])
    return {
        "source": str(section.get("source") or defaults["source"]).strip().lower() or defaults["source"],
        "host": host,
        "port": port,
        "url": str(section.get("url") or "").strip(),
        "camera_name": str(section.get("camera_name") or defaults["camera_name"]).strip() or defaults["camera_name"],
        "vehicle_name": str(section.get("vehicle_name") or defaults["vehicle_name"]).strip(),
        "image_type": image_type,
        "timeout_sec": timeout_sec,
        "auto_save": bool(section.get("auto_save", defaults["auto_save"])),
    }


def _build_connect_params(connection: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate a UI connection entry into backend_id + connect_params."""
    conn_type = str(connection.get("type") or "auto").lower()
    params = dict(connection.get("params") or {})
    real_vehicle = bool(params.get("realVehicle", False))

    if conn_type == "airsim":
        ip = str(params.get("host") or params.get("ip") or "127.0.0.1").strip()
        port = int(params.get("portNumber") or params.get("port") or "41452")
        return "airsim", {"ip": ip, "port": port}

    if conn_type in {"px4_ros2", "ros2", "ros", "px4_ros"}:
        url = str(
            params.get("url")
            or params.get("base_url")
            or params.get("host")
            or config.ros_bridge_url
            or "http://127.0.0.1:8766"
        ).strip()
        workspace = str(params.get("workspace") or config.ros_workspace_path or "").strip()
        payload = {"url": url}
        if workspace:
            payload["workspace"] = workspace
        return "px4_ros2", payload

    # Everything else maps to the PX4 MAVLink backend.
    if conn_type == "serial":
        port = str(params.get("port") or "").strip()
        baud = normalize_serial_baud(params.get("baud") or "115200")
        url = f"serial:{port}:{baud}" if port else "auto:serial"
        return "px4_mavlink", {"url": url, "real_vehicle": True}
    elif conn_type == "tcp":
        address = str(params.get("address") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "5760").strip()
        url = f"tcp:{address}:{port_number}"
        return "px4_mavlink", {"url": url, "real_vehicle": real_vehicle}
    elif conn_type == "auto":
        host = str(params.get("host") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "14550").strip()
        if ":" in host and any(host.startswith(p) for p in ("udp:", "udpout:", "udpin:", "tcp:", "serial:", "auto:")):
            fallback_url = host
        else:
            fallback_url = f"udp:{host}:{port_number}"
        connect_params = {
            "url": "auto:",
            "fallback_url": fallback_url,
            "real_vehicle": real_vehicle,
        }
        remote_port = str(params.get("remotePort") or "").strip()
        if remote_port:
            connect_params["remote_host"] = host
            connect_params["remote_port"] = int(remote_port)
        return "px4_mavlink", connect_params
    else:
        # udp — use bare ``udp:`` prefix so that the MAVLink
        # controller can try both listen (udpin) and send (udpout)
        # modes.  PX4 SITL's GCS link broadcasts heartbeats to a
        # well-known port (14550 by default), so binding (udpin) is
        # usually the fastest path.
        host = str(params.get("host") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "14550").strip()
        # Allow users to type the full mavlink URL prefix directly.
        if ":" in host and any(host.startswith(p) for p in ("udp:", "udpout:", "udpin:", "tcp:", "serial:")):
            url = host
        else:
            url = f"udp:{host}:{port_number}"
        connect_params: dict[str, Any] = {
            "url": url,
            "real_vehicle": real_vehicle,
        }
        remote_port = str(params.get("remotePort") or "").strip()
        if remote_port:
            connect_params["remote_host"] = host
            connect_params["remote_port"] = int(remote_port)
        return "px4_mavlink", connect_params


def _select_connection_for_backend(
    conn_section: dict[str, Any],
    backend_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Return an active connection id compatible with the requested backend."""

    connections = [c for c in conn_section.get("connections", []) if isinstance(c, dict)]
    active_id = str(conn_section.get("active_connection_id") or "")
    active = next((c for c in connections if str(c.get("id") or "") == active_id), None)
    if active is not None:
        try:
            active_backend, _ = _build_connect_params(active)
        except Exception:
            active_backend = ""
        if active_backend == backend_id:
            return active_id, active

    preferred_ids = {
        "px4_mavlink": ["default_px4_auto", "default_px4_usb", "default_px4"],
        "airsim": ["default_airsim"],
        "px4_ros2": ["default_px4_ros2"],
    }.get(backend_id, [])
    for preferred_id in preferred_ids:
        candidate = next((c for c in connections if str(c.get("id") or "") == preferred_id), None)
        if candidate is not None:
            try:
                candidate_backend, _ = _build_connect_params(candidate)
            except Exception:
                continue
            if candidate_backend == backend_id:
                return str(candidate.get("id") or ""), candidate

    for candidate in connections:
        try:
            candidate_backend, _ = _build_connect_params(candidate)
        except Exception:
            continue
        if candidate_backend == backend_id:
            return str(candidate.get("id") or ""), candidate
    return "", None


@dataclass
class RuntimeEvent:
    timestamp: float
    level: str
    source: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class ChatMessage:
    id: str
    role: str
    content: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    status: str = "complete"
    details: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "attachments": list(self.attachments),
            "run_id": self.run_id,
            "status": self.status,
            "details": self.details,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RunState:
    run_id: str
    command: str
    intent: str
    summary: str
    status: str = "created"
    mode: str = "execute"
    phase: str = "created"
    execute: bool = False
    progress: float = 0.0
    current_step: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    failure_reason: str = ""
    assistant_message: str = ""
    model_id: str = ""
    plan: MissionPlan | None = None
    task_level: str = ""
    route_strategy: str = ""
    route_reason: str = ""
    risk_level: str = "safe"  # safe / elevated / high
    answer_with_llm: bool = True
    loop_state: dict[str, Any] = field(default_factory=dict)
    start_position_recorded: bool = False
    start_telemetry: dict[str, Any] = field(default_factory=dict)
    final_telemetry: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    agent_state: dict[str, Any] = field(default_factory=dict)
    thought_trace: list[dict[str, Any]] = field(default_factory=list)
    process_trace: list[dict[str, Any]] = field(default_factory=list)
    # ReAct correction rounds already spent after a failed Plan-Execute run.
    correction_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "intent": self.intent,
            "summary": self.summary,
            "status": self.status,
            "mode": self.mode,
            "phase": self.phase,
            "execute": self.execute,
            "progress": round(self.progress, 1),
            "current_step": self.current_step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reason": self.failure_reason,
            "assistant_message": self.assistant_message,
            "start_telemetry": self.start_telemetry,
            "final_telemetry": self.final_telemetry,
            "verification": self.verification,
            "agent_state": self.agent_state,
            "thought_trace": list(self.thought_trace),
            "process_trace": list(self.process_trace),
            "correction_attempts": self.correction_attempts,
            "plan": self.plan.to_dict() if self.plan else None,
            "task_level": self.task_level,
            "route_strategy": self.route_strategy,
            "route_reason": self.route_reason,
            "risk_level": self.risk_level,
            "loop_state": self.loop_state,
        }


@dataclass
class ToolApprovalRequest:
    """P5: lightweight approval gate for high-risk direct tool calls.

    Created when a direct route has ``risk_level == 'high'`` AND the active
    backend declares ``requires_operator_approval == True`` (real vehicle).
    The worker thread blocks on ``event`` until the operator approves/rejects
    via :meth:`AgentRuntime.approve_run` / :meth:`AgentRuntime.reject_run`.
    """

    run_id: str
    command: str
    tool: str
    params: dict[str, Any]
    risk_level: str
    reason: str
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 300.0
    # Decision: None=pending, True=approved, False=rejected
    approved: bool | None = None
    event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "tool": self.tool,
            "params": dict(self.params),
            "risk_level": self.risk_level,
            "reason": self.reason,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
            "approved": self.approved,
            "status": "pending" if self.approved is None else ("approved" if self.approved else "rejected"),
        }


class AgentRuntime:
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

    def submit_command(
        self,
        command: str,
        execute: bool = False,
        model_id: str = "",
        mode: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        command = command.strip()
        if attachments and not self.planner.supports_multimodal(model_id or None):
            return {"ok": False, "error": "当前模型未启用多模态能力，请选择或配置支持图像的模型。"}
        stored_attachments = self._store_attachments(attachments or [])
        if not command and stored_attachments:
            command = "请分析我提供的图片。"
        if not command:
            return {"ok": False, "error": "command is empty"}
        model_attachments = self._hydrate_attachments(stored_attachments)

        requested_mode = str(mode or "").strip().lower()
        if requested_mode == "chat":
            active_mode = "chat"
        elif requested_mode == "execute" or execute:
            active_mode = "execute"
        else:
            active_mode = "plan"
        execute = active_mode == "execute"
        request_id = f"{active_mode}_{time.time_ns()}"

        self._append_message("user", command, attachments=stored_attachments)
        if active_mode == "execute" and self._is_status_readback_command(command):
            tool_runtime = self.tools.status_snapshot()
            agent_state = self._agent_state_context(tool_runtime)
            return self._complete_status_readback_command(command, request_id, agent_state)

        if execute and self._is_conflicting(command):
            event = self._append_event("warning", "llm", "指令存在冲突或过于模糊", {"command": command})
            self._append_message("assistant", event.message, status="error")
            return {"ok": False, "error": event.message}

        busy_error = ""
        execution_slot_acquired = False
        if execute and self._execution_slot.locked():
            # 打断语义：新指令提交时自动中断旧任务（用户要求"打断对话即后台
            # 停止调用"）。_cancel_active_work 置取消旗标并标记旧 run；阻塞中
            # 的飞行命令由 stop_provider 安全中断；随后等待旧线程退出并释放
            # 执行槽（acquire 返回即旧线程已走完 finally），此时清除旗标启动
            # 新任务不会放跑旧任务的收尾工作。
            self._append_event(
                "warning",
                "system",
                "检测到任务执行中，自动中断旧任务后执行新指令",
                {"command": command[:80]},
            )
            self._cancel_active_work()
            execution_slot_acquired = self._execution_slot.acquire(timeout=25.0)
            if not execution_slot_acquired:
                busy_error = "旧任务未能及时停止，请稍后重试。"
            else:
                self._cancel_requested.clear()
        elif execute:
            execution_slot_acquired = self._execution_slot.acquire(blocking=False)
            if not execution_slot_acquired:
                busy_error = "已有任务正在理解、规划或执行，请等待当前任务结束。"
        elif active_mode == "plan" and self._execution_slot.locked():
            busy_error = "执行任务进行中，暂不生成会覆盖当前运行态的计划预览。"
        if busy_error:
            self._append_message(
                "assistant",
                busy_error,
                status="error",
                details={"mode": "execute", "phase": "blocked"},
            )
            return {"ok": False, "error": busy_error}

        self._cancel_requested.clear()
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

        tool_runtime = self.tools.status_snapshot()
        telemetry = tool_runtime.get("drone")
        agent_state = self._agent_state_context(tool_runtime)
        if active_mode == "chat":
            with self._lock:
                self._active_chat_requests.add(request_id)
            self._append_message(
                "assistant",
                "正在生成回复...",
                run_id=request_id,
                status="running",
                details={
                    "mode": "chat",
                    "phase": "responding",
                    "agent_state": agent_state,
                    "thought_trace": [
                        {
                            "timestamp": time.time(),
                            "title": "读取上下文",
                            "body": "Chat 模式只基于会话和当前状态回答，不执行工具。",
                            "status": "running",
                        }
                    ],
                },
            )
            thread = threading.Thread(
                target=self._handle_chat_command,
                args=(command, model_id, request_id, agent_state, model_attachments),
                daemon=True,
            )
            try:
                thread.start()
            except Exception:
                with self._lock:
                    self._active_chat_requests.discard(request_id)
                raise
            return {"ok": True, "mode": "chat", "run_id": request_id, "status": "responding"}

        self._append_message(
            "assistant",
            "",
            run_id=request_id,
            status="running",
            details={
                "mode": active_mode,
                "phase": "understanding" if execute else "planning",
                "agent_state": agent_state,
                "thought_trace": [
                    {
                        "timestamp": time.time(),
                        "title": "理解指令" if execute else "规划预览",
                        "body": "正在读取后端连接、车辆状态和会话上下文。" if execute else "正在生成只读计划预览，不执行工具。",
                        "status": "running",
                    }
                ],
            },
        )

        self._thread = threading.Thread(
            target=self._plan_and_execute,
            args=(command, execute, telemetry, model_id),
            kwargs={
                "run_id": request_id,
                "agent_state": agent_state,
                "attachments": model_attachments,
                "release_execution_slot": execution_slot_acquired,
            },
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            if execution_slot_acquired and self._execution_slot.locked():
                self._execution_slot.release()
            raise

        return {"ok": True, "mode": active_mode, "run_id": request_id, "status": "queued" if execute else "planned"}

    def _is_status_readback_command(self, command: str) -> bool:
        text = str(command or "").strip().lower()
        if not text:
            return False
        status_terms = (
            "status", "state", "telemetry", "position", "location", "where", "connected", "connection",
            "状态", "位置", "在哪", "哪里", "高度", "坐标", "遥测", "连接", "航向", "速度", "是否在线",
            "几架", "几台", "多少架", "多少台", "数量", "哪几架", "哪几台", "多少",
        )
        # "三台/两架/共四台" 等数字+量词组合 -> 数量类只读问句
        has_status_term = any(term in text for term in status_terms) or bool(
            re.search(r"[0-9一二两三四五六七八九十百]+[台架]", text)
        )
        if not has_status_term:
            return False
        action_terms = (
            "takeoff", "fly", "move", "land", "rtl", "return", "photo", "capture", "search", "scan",
            "起飞", "飞行", "向前", "向后", "向左", "向右", "移动", "降落", "返航", "拍照", "截图",
            "搜索", "扫描", "巡航", "航点", "航线", "路径", "绕圈", "正方形", "悬停", "解锁",
        )
        return not any(term in text for term in action_terms)

    def _complete_status_readback_command(
        self,
        command: str,
        request_id: str,
        agent_state: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = time.time()
        # multi-vehicle aware: report every vehicle, not only the default one
        names: list[str] = []
        try:
            list_result = self.tools.execute("drone_list_vehicles", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
            raw_names = (list_result.data or {}).get("vehicles") or []
            names = [str(n) for n in raw_names if str(n)]
        except Exception:
            names = []
        if len(names) > 1 and len(names) <= 4:
            per_vehicle: list[str] = []
            ok_all = True
            failures = 0
            for name in names:
                sub = self.tools.execute("drone_get_status", {"vehicle_name": name}, dry_run=False, blocked_by_supervisor=False)
                if not sub.ok:
                    ok_all = False
                    failures += 1
                    reason = str((sub.data or {}).get("message") or (sub.data or {}).get("error") or "未知原因")[:120]
                    per_vehicle.append(f"{name}: 状态读取失败（{reason}）")
                    continue
                per_vehicle.append(self._format_vehicle_line(name, sub.data))
            if failures == len(names):
                # every per-vehicle read failed: the cached vehicle list is
                # stale and the link is actually down — say so instead of a
                # table of failures
                answer = (
                    f"检测到 {len(names)} 架无人机的缓存列表，但全部状态读取失败——"
                    "后端连接实际已断开。请检查仿真器/飞控是否在运行，然后在连接面板重新连接。"
                )
                ok = False
                body = answer
                process_trace = [
                    {
                        "timestamp": time.time(),
                        "title": "读取无人机状态",
                        "body": body,
                        "status": "failed",
                        "tool": "drone_list_vehicles",
                        "params": {},
                        "kind": "tool",
                    }
                ]
            else:
                answer = f"当前后端共 {len(names)} 架无人机：\n" + "\n".join(per_vehicle)
                dashboard = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False)
                ok = dashboard.ok and ok_all
                body = answer
                process_trace = [
                    {
                        "timestamp": time.time(),
                        "title": "读取无人机状态",
                        "body": body,
                        "status": "completed" if ok else "failed",
                        "tool": "drone_list_vehicles",
                        "params": {},
                        "kind": "tool",
                    }
                ]
        else:
            result = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False)
            process_trace = [
                {
                    "timestamp": time.time(),
                    "title": "读取无人机状态",
                    "body": self._format_loop_result_body(result.data) or ("ok" if result.ok else "状态读取失败"),
                    "status": "completed" if result.ok else "failed",
                    "tool": "drone_get_status",
                    "params": {},
                    "kind": "tool",
                }
            ]
            answer = self._format_status_readback_answer(result.data if result.ok else {}, result.ok)
            ok = result.ok
            if not result.ok:
                message = str(result.data.get("message") or result.data.get("error") or "无人机状态读取失败")
                answer = f"状态读取失败：{message}"
        process_trace.append(
            {
                "timestamp": time.time(),
                "title": "状态总结",
                "body": answer,
                "status": "completed" if ok else "failed",
                "tool": "",
                "params": {},
                "kind": "reasoning",
            }
        )
        self._append_message(
            "assistant",
            answer,
            run_id=request_id,
            status="complete" if ok else "error",
            details={
                "mode": "execute",
                "phase": "completed" if ok else "failed",
                "run_status": "completed" if ok else "failed",
                "started_at": started_at,
                "finished_at": time.time(),
                "agent_state": agent_state,
                "process_trace": process_trace,
                "fast_readback": True,
                "command": command,
            },
        )
        return {
            "ok": bool(ok),
            "mode": "execute",
            "run_id": request_id,
            "status": "completed" if ok else "failed",
            "fast_readback": True,
        }

    def _format_vehicle_line(self, name: str, telemetry: dict[str, Any]) -> str:
        """One compact per-vehicle summary line for multi-vehicle readbacks."""
        position = telemetry.get("position_ned") if isinstance(telemetry.get("position_ned"), dict) else {}
        x = self._finite_float(position.get("x"))
        y = self._finite_float(position.get("y"))
        z = self._finite_float(position.get("z"))
        pos_text = f"N {x:.2f} / E {y:.2f} / D {z:.2f}" if x is not None else "--"
        alt = abs(z) if z is not None else None
        flying = telemetry.get("flying")
        state_text = "飞行中" if flying else "未飞行/已落地"
        armed = "已解锁" if telemetry.get("armed") else "未解锁"
        if flying:
            alt_text = f"，高度约 {alt:.2f} m" if alt is not None else ""
        else:
            # AirSim keeps the last airborne z after landing; reporting it as
            # altitude would confuse operators ("landed at 2.9m")
            alt_text = "，高度 0 m（已着陆）"
        return f"{name}：{armed}，{state_text}{alt_text}，位置 {pos_text}"

    def _format_status_readback_answer(self, telemetry: dict[str, Any], ok: bool = True) -> str:
        if not ok:
            return "无人机状态读取失败。"
        active_link = telemetry.get("active_link") if isinstance(telemetry.get("active_link"), dict) else {}
        backend = str(telemetry.get("backend") or active_link.get("backend") or self.tools.backend_id)
        position = telemetry.get("position_ned") if isinstance(telemetry.get("position_ned"), dict) else {}
        velocity = telemetry.get("velocity_ned") if isinstance(telemetry.get("velocity_ned"), dict) else {}
        gps = telemetry.get("gps") if isinstance(telemetry.get("gps"), dict) else {}
        x = self._finite_float(position.get("x")) or 0.0
        y = self._finite_float(position.get("y")) or 0.0
        z = self._finite_float(position.get("z")) or 0.0
        vx = self._finite_float(velocity.get("vx")) or 0.0
        vy = self._finite_float(velocity.get("vy")) or 0.0
        vz = self._finite_float(velocity.get("vz")) or 0.0
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        heading = self._finite_float(telemetry.get("heading_deg"))
        heading_text = f"，航向 {heading:.1f}°" if heading is not None else ""
        armed = "已解锁" if telemetry.get("armed") else "未解锁"
        flying = "飞行中" if telemetry.get("flying") else "未飞行/已落地"
        mode = str(telemetry.get("mode") or "--")
        collision = telemetry.get("has_collided")
        collision_text = "，未检测到碰撞" if collision is False or collision is None else "，检测到碰撞"
        gps_text = ""
        lat = self._finite_float(gps.get("lat"))
        lon = self._finite_float(gps.get("lon"))
        alt = self._finite_float(gps.get("alt"))
        if lat is not None and lon is not None:
            gps_text = f" GPS 约为北纬 {lat:.6f}°、东经 {lon:.6f}°"
            if alt is not None:
                gps_text += f"，海拔 {alt:.1f} m"
            gps_text += "。"
        flying_now = bool(telemetry.get("flying"))
        if flying_now:
            altitude_text = f"高度约 {abs(z):.2f} m"
        else:
            # AirSim keeps the last airborne z after landing; do not report it
            # as altitude ("landed at 2.9m" confuses operators)
            altitude_text = "高度 0 m（已着陆）"
        return (
            f"已读取当前无人机状态：后端为 {backend}，{armed}，{flying}，模式 {mode}。"
            f"当前位置 NED 为 N {x:.2f} / E {y:.2f} / D {z:.2f} m，{altitude_text}，"
            f"速度约 {speed:.2f} m/s{heading_text}{collision_text}。"
            f"{gps_text}"
        ).strip()

    def _handle_chat_command(
        self,
        command: str,
        model_id: str,
        request_id: str,
        agent_state: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        # Chat mode never executes flight-control tools, but a state question
        # must not be answered from a stale/busy snapshot either. Refresh the
        # read-only state once so the model answers from real data.
        agent_state = self._refresh_chat_state(agent_state)
        buffer: list[str] = []
        reasoning_buffer: list[str] = []
        tool_trace: list[dict[str, Any]] = []
        readonly_tools = self._chat_readonly_tools()

        def execute_readonly_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
            result = self.tools.execute(str(name), dict(args or {}), dry_run=False, allow_reconnect=True)
            data = result.data if isinstance(result.data, dict) else {"result": str(result.data)[:400]}
            return {"ok": bool(result.ok), "data": data}

        def on_tool_call(name: str) -> None:
            tool_trace.append(
                {
                    "timestamp": time.time(),
                    "title": f"只读查询 {name}",
                    "body": "chat 模式只读工具调用，获取实时数据",
                    "status": "completed",
                    "kind": "tool",
                }
            )
            self._update_assistant_message(
                request_id,
                "".join(buffer),
                "running",
                details("responding"),
                persist=False,
            )

        def cancelled() -> bool:
            with self._lock:
                return request_id in self._cancelled_request_ids

        def details(phase: str, process_status: str = "running") -> dict[str, Any]:
            process_trace: list[dict[str, Any]] = list(tool_trace)
            reasoning = self._compact_process_text("".join(reasoning_buffer).strip())
            if reasoning:
                process_trace.append(
                    {
                        "timestamp": time.time(),
                        "title": "模型推理",
                        "body": reasoning,
                        "status": process_status,
                    }
                )
            elif phase == "responding" and not tool_trace:
                process_trace.append(
                    {
                        "timestamp": time.time(),
                        "title": "生成回复",
                        "body": "正在根据会话上下文组织回答。",
                        "status": process_status,
                    }
                )
            return {
                "mode": "chat",
                "phase": phase,
                "process_trace": process_trace,
            }

        def on_reasoning(token: str) -> None:
            reasoning_buffer.append(token)
            self._update_assistant_message(
                request_id,
                "".join(buffer),
                "running",
                details("responding"),
                persist=False,
            )

        def on_token(token: str) -> None:
            buffer.append(token)
            self._append_assistant_delta(
                request_id,
                token,
                "".join(buffer),
                details("responding"),
            )

        try:
            answer = self.planner.chat_response_stream(
                command=command,
                conversation=self._recent_chat_context(),
                agent_state=agent_state,
                memory=self.memory.snapshot(),
                model_id=model_id or None,
                on_token=on_token,
                on_reasoning=on_reasoning,
                attachments=attachments or [],
                should_stop=cancelled,
                readonly_tools=readonly_tools,
                execute_readonly_tool=execute_readonly_tool,
                on_tool_call=on_tool_call,
            )
            if not answer and buffer:
                answer = "".join(buffer)
            if cancelled():
                self._update_assistant_message(
                    request_id,
                    "已中断当前回复。",
                    "complete",
                    details("cancelled", "completed"),
                )
                return
            self._update_assistant_message(
                request_id,
                answer,
                "complete",
                details("completed", "completed"),
            )
        except LLMUnavailableError as exc:
            if cancelled():
                self._update_assistant_message(
                    request_id,
                    "已中断当前回复。",
                    "complete",
                    details("cancelled", "completed"),
                )
                return
            message = str(exc)
            self._append_event("danger", "chat", message, {"model_id": model_id})
            self._update_assistant_message(
                request_id,
                message,
                "error",
                {
                    "mode": "chat",
                    "phase": "failed",
                    "agent_state": agent_state,
                    "error": {"type": "model_unavailable", "message": message},
                },
            )
        except Exception as exc:
            self._append_event("danger", "chat", f"Chat response failed: {exc}", {})
            self._update_assistant_message(
                request_id,
                f"Chat 处理失败: {exc}",
                "error",
                {"mode": "chat", "phase": "failed", "agent_state": agent_state},
            )
        finally:
            with self._lock:
                self._active_chat_requests.discard(request_id)
                self._cancelled_request_ids.discard(request_id)

    def _recent_chat_context(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            messages = list(self._messages)
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                del messages[index]
                break
        if limit is not None:
            recent_messages = messages[-max(1, int(limit)):]
        else:
            planner = getattr(self, "planner", None)
            registry = getattr(planner, "registry", None)
            model = registry.get_default() if registry else {}
            public = registry._public_model(model) if registry and model else {}
            context_window = int(public.get("context_window") or 64_000)
            # Reserve roughly 40% for system/tool prompts and the response.
            budget = max(4_000, int(context_window * 0.6))
            selected: list[ChatMessage] = []
            used = 0
            for message in reversed(messages):
                estimate = max(1, math.ceil(len(str(message.content or "")) / 4))
                if selected and used + estimate > budget:
                    break
                selected.append(message)
                used += estimate
            recent_messages = list(reversed(selected))
        latest_image_message_id = next(
            (message.id for message in reversed(recent_messages) if message.role == "user" and message.attachments),
            "",
        )
        context: list[dict[str, Any]] = []
        for message in recent_messages:
            content = str(message.content or "").strip()
            if not content:
                continue
            context.append({
                "role": "assistant" if message.role == "assistant" else "user",
                "content": content[:1600],
                "attachments": self._hydrate_attachments(message.attachments)
                if message.id == latest_image_message_id else [],
            })
        return context

    def _store_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        if len(attachments) > 4:
            raise ValueError("每条消息最多附加 4 张图片")
        stored: list[dict[str, Any]] = []
        total_size = 0
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise ValueError("attachment must be an object")
            mime_type = str(item.get("mime_type") or item.get("type") or "").lower()
            data_url = str(item.get("data_url") or "")
            prefix = f"data:{mime_type};base64,"
            if mime_type not in allowed or not data_url.startswith(prefix):
                raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片")
            try:
                raw = base64.b64decode(data_url[len(prefix):], validate=True)
            except Exception as exc:
                raise ValueError(f"图片数据无法解析: {exc}") from exc
            if not raw:
                raise ValueError("图片不能为空")
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("单张图片不能超过 5 MB")
            total_size += len(raw)
            if total_size > 12 * 1024 * 1024:
                raise ValueError("单条消息图片总大小不能超过 12 MB")
            digest = hashlib.sha256(raw).hexdigest()
            storage_key = f"{digest}{allowed[mime_type]}"
            path = ATTACHMENTS_DIR / storage_key
            if not path.exists():
                path.write_bytes(raw)
            stored.append({
                "id": digest[:16],
                "name": str(item.get("name") or f"image-{index + 1}{allowed[mime_type]}")[:120],
                "mime_type": mime_type,
                "size": len(raw),
                "storage_key": storage_key,
                "url": f"/api/attachments/{storage_key}",
            })
        return stored

    def _hydrate_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hydrated: list[dict[str, Any]] = []
        for item in attachments[:4]:
            if not isinstance(item, dict):
                continue
            key = Path(str(item.get("storage_key") or "")).name
            path = ATTACHMENTS_DIR / key
            mime_type = str(item.get("mime_type") or "")
            if not key or not path.is_file() or mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                continue
            raw = path.read_bytes()
            hydrated.append({
                **item,
                "data_url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
            })
        return hydrated

    def attachment_file(self, storage_key: str) -> tuple[Path, str] | None:
        key = Path(storage_key).name
        if not key or key != storage_key:
            return None
        path = ATTACHMENTS_DIR / key
        mime_by_suffix = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
        mime_type = mime_by_suffix.get(path.suffix.lower())
        if not mime_type or not path.is_file():
            return None
        return path, mime_type

    def _agent_state_context(self, tool_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = tool_runtime or self.tools.status_snapshot()
        profile = runtime.get("backend_profile") or {}
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else None
        with self._lock:
            current = self._current
            active_run = {
                "run_id": current.run_id,
                "status": current.status,
                "phase": current.phase,
                "progress": round(current.progress, 1),
                "current_step": current.current_step,
                "summary": current.summary,
            } if current and current.status in {"queued", "running", "paused", "responding", "awaiting_approval"} else None
        return {
            "ready": bool(runtime.get("ready")),
            "connected": bool(runtime.get("connected")),
            "stale_connection": bool(runtime.get("stale_connection")),
            "busy": bool(runtime.get("busy")) or self._execution_slot.locked(),
            "backend": str(runtime.get("backend") or ""),
            "backend_name": str(profile.get("name") or profile.get("id") or runtime.get("backend") or ""),
            "capabilities": dict(profile.get("capabilities") or {}),
            "vehicle": self._compact_vehicle_state(drone),
            "vehicles": self._compact_vehicles_state(runtime.get("vehicles")),
            "active_run": active_run,
        }

    def _compact_vehicles_state(self, raw_vehicles: Any) -> list[dict[str, Any]]:
        """Compact per-vehicle states for the LLM context (multi-vehicle)."""
        if not isinstance(raw_vehicles, list):
            return []
        compact: list[dict[str, Any]] = []
        for item in raw_vehicles:
            if not isinstance(item, dict):
                continue
            state = self._compact_vehicle_state(item) or {}
            state.setdefault("vehicle_name", item.get("vehicle_name", ""))
            compact.append(state)
        return compact

    def _compact_vehicle_state(self, drone: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(drone, dict):
            return None
        keys = [
            "vehicle_name",
            "armed",
            "flying",
            "landed_state",
            "flight_mode",
            "mode",
            "altitude",
            "altitude_m",
            "position_ned",
            "velocity",
            "velocity_ned",
            "heading_deg",
            "battery",
            "battery_percent",
            "has_collided",
            "collision",
            "connection_error",
        ]
        compact = {key: drone.get(key) for key in keys if key in drone}
        if "error" in drone:
            compact["error"] = drone.get("error")
        return compact

    def _plan_and_execute(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str = "",
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        release_execution_slot: bool = False,
    ) -> None:
        if execute:
            self._execution_thread_id = threading.get_ident()
        replay_session = None
        if execute and run_id:
            replay_session = self._start_replay_session(
                run_id,
                {"run_id": run_id, "command": command, "mode": "execute"},
            )
        if run_id:
            with self._lock:
                self._run_log = RunLog(run_id)
            tool_runtime = self.tools.status_snapshot()
            self._run_log.write(
                "run.start",
                {
                    "command": command,
                    "mode": "execute" if execute else "plan",
                    "model_id": model_id or "",
                    "backend": str(tool_runtime.get("backend") or ""),
                    "attachments": len(attachments or []),
                },
            )
        else:
            with self._lock:
                self._run_log = None
        try:
            tool_runtime = self.tools.status_snapshot()
            agent_state = agent_state or self._agent_state_context(tool_runtime)
            backend_profile = tool_runtime.get("backend_profile") or {}
            capabilities = backend_profile.get("capabilities") or {}
            memory_snapshot = self.memory.snapshot()
            if run_id:
                self._update_assistant_message(
                    run_id,
                    "正在理解指令并准备执行计划...",
                    "running",
                    {
                        "mode": "execute" if execute else "plan",
                        "phase": "planning",
                        "agent_state": agent_state,
                        "process_trace": [
                            {
                                "timestamp": time.time(),
                                "title": "理解指令",
                                "body": "正在解析任务意图并生成可执行的工具序列；模型不可用时不会降级发出飞控指令。",
                                "status": "running",
                            }
                        ],
                    },
                )
            skill_guidance = self.skills.guidance_cards(command, capabilities, memory_snapshot)
            if skill_guidance:
                agent_state = self._agent_state_with_skill_guidance(agent_state, skill_guidance)
            # Primary path: Plan-Execute. The LLM plans once and the runtime
            # executes/verifies the sequence — simple commands finish after a
            # few deterministic steps without an agent loop, and failures or
            # observation-dependent tasks enter the correction loop.
            route = {
                "level": "plan_execute",
                "strategy": "plan_execute",
                "reason": "Plan-Execute primary path: LLM plans once, runtime executes and verifies; correction loop only on failure",
                "risk_level": "elevated" if capabilities.get("flight_control") else "safe",
            }
            self._append_event("info", "planner", "Plan-Execute 主路径启动", route)
            self._execute_plan_execute_route(
                command,
                execute,
                telemetry,
                model_id,
                route,
                capabilities,
                tool_runtime,
                memory_snapshot,
                run_id,
                agent_state,
                attachments=attachments or [],
            )
            return
        except Exception as e:
            failed_run = None
            hover_result = None
            if run_id:
                with self._lock:
                    if self._current and self._current.run_id == run_id:
                        self._current.status = "failed"
                        self._current.phase = "failed"
                        self._current.failure_reason = str(e)
                        self._current.finished_at = time.time()
                        failed_run = self._current
            if execute:
                hover_result = self._attempt_failure_hover(failed_run, str(e))
            if run_id:
                message = f"任务处理失败: {str(e)}"
                if hover_result:
                    message += " 已尝试执行安全悬停。"
                details = (
                    self._message_details(failed_run)
                    if failed_run
                    else {
                        "mode": "execute" if execute else "plan",
                        "phase": "failed",
                        "agent_state": agent_state or {},
                    }
                )
                if hover_result:
                    details["failure_safety_hover"] = hover_result
                self._update_assistant_message(
                    run_id,
                    message,
                    "error",
                    details,
                )
                if failed_run:
                    self._publish_run_update(failed_run)
                    try:
                        self._finalize_task_run(failed_run)
                    except Exception:
                        pass
            else:
                self._append_message("assistant", f"任务处理失败: {str(e)}", status="error")
            self._append_event("danger", "planner", "任务处理失败", {"error": str(e)})
        finally:
            if replay_session is not None:
                self._stop_replay_session()
            self._close_run_log(run_id, execute)
            if execute and self._execution_thread_id == threading.get_ident():
                self._execution_thread_id = 0
            if release_execution_slot and self._execution_slot.locked():
                self._execution_slot.release()
            if run_id:
                with self._lock:
                    self._cancelled_request_ids.discard(run_id)

    def _close_formation(self, reason: str) -> bool:
        """Hover all formation drones and stop the control thread.

        Called on run end, backend switches, and emergency stop so the swarm
        never keeps flying without an owner. Returns True when a mission was
        actually active.
        """
        try:
            return self.tools.formation_shutdown(reason)
        except Exception:
            return False

    def _close_run_log(self, run_id: str, execute: bool) -> None:
        """Write the terminal run.end event, drop the active log reference,
        and store one bounded transcript row in long-term memory."""
        with self._lock:
            run_log = self._run_log
            current = self._current
            if run_log is None:
                return
            self._run_log = None
        payload: dict[str, Any] = {"status": "planned" if not execute else "stopped", "command": ""}
        if current is not None and current.run_id == run_id:
            payload = {
                "status": current.status,
                "command": current.command,
                "summary": current.summary or "",
                "failure_reason": current.failure_reason or "",
                "verification_status": str((current.verification or {}).get("level") or ""),
                "finished_at": current.finished_at or time.time(),
                "phase": current.phase or "",
            }
            try:
                tools = [
                    str(row.get("tool") or "")
                    for row in ((current.loop_state or {}).get("results") or [])
                    if isinstance(row, dict)
                ]
                self.memory.remember_transcript(
                    run_id,
                    current.command,
                    current.status,
                    current.summary or "",
                    tools,
                    current.failure_reason or "",
                )
            except Exception:
                pass
            if "native tool calling unavailable" in (self.planner.last_error or ""):
                run_log.write("protocol.degraded", {"reason": self.planner.last_error[:300]})
        # a run ending with an active formation/coverage mission must not leave
        # the swarm flying without an owner
        if self._close_formation("run_end"):
            run_log.write("formation.shutdown", {"reason": "run_end", "phase": payload.get("phase", "")})
        run_log.write("run.end", payload)

    def _try_llm_plan(
        self,
        *,
        command: str,
        telemetry: dict[str, Any] | None,
        model_id: str,
        run_id: str,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        agent_state: dict[str, Any],
        attachments: list[dict[str, Any]],
        reasoning_sink: Callable[[str], None] | None = None,
    ) -> MissionPlan | None:
        planner_tool_cards = self._planner_tool_cards(
            command,
            tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            capabilities,
            memory_snapshot,
        )
        plan = self.planner.plan(
            command=command,
            tools=self.tools.list_tools(),
            safety=self._safety_snapshot(),
            telemetry=telemetry,
            memory=memory_snapshot,
            model_id=model_id or None,
            backend=str(tool_runtime.get("backend") or (tool_runtime.get("backend_profile") or {}).get("id") or ""),
            capabilities=capabilities,
            tool_cards=planner_tool_cards,
            agent_state=agent_state,
            conversation_context=self._recent_chat_context(),
            attachments=attachments,
            on_reasoning=reasoning_sink,
        )
        if run_id:
            plan.run_id = run_id
        run_log = self._run_log
        if run_log is not None:
            run_log.write(
                "plan",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "intent": plan.intent,
                    "summary": plan.summary,
                    "goal": plan.goal,
                    "steps": [
                        {"id": step.id, "label": step.label, "tool": step.tool, "params": step.params, "layer": step.layer}
                        for step in plan.steps
                    ],
                },
            )
        if str(plan.planner_source).startswith("rules"):
            self._append_event(
                "warning",
                "planner",
                "LLM 规划不可用，回退到规则路径",
                {
                    "planner_source": plan.planner_source,
                    "planner_model": plan.planner_model,
                    "risk_notes": list(plan.risk_notes),
                },
            )
            return None
        return plan

    @staticmethod
    def _approval_reason(tool: str, params: dict[str, Any]) -> str:
        """Approval reason including the target vehicle(s) so the operator
        sees exactly what will be controlled (multi-vehicle aware)."""
        vehicle = str((params or {}).get("vehicle_name") or "")
        base = f"governed high-risk tool call: {tool}"
        if tool == "formation_command":
            action = str((params or {}).get("action") or "")
            ids = str((params or {}).get("vehicle_ids") or "")
            detail = f"action={action}"
            if ids:
                detail += f", vehicles={ids}"
            return f"{base} ({detail})"
        if not vehicle:
            return base
        return f"{base} (vehicle={vehicle})"

    def _await_tool_approval(
        self,
        run: RunState,
        tool: str,
        params: dict[str, Any],
        risk_level: str,
        reason: str = "",
    ) -> bool:
        """Block until the operator approves/rejects one governed tool call.

        Returns True if approved, False if rejected or timed out. Updates
        ``run.status`` / ``run.failure_reason`` accordingly.
        """
        req = ToolApprovalRequest(
            run_id=run.run_id,
            command=run.command,
            tool=tool,
            params=dict(params),
            risk_level=risk_level,
            reason=reason or f"high-risk tool: {tool}",
        )
        with self._lock:
            self._pending_approvals[run.run_id] = req
            run.status = "awaiting_approval"
            run.phase = "awaiting_approval"
        self._append_event(
            "warning",
            "safety",
            f"等待操作员确认: {tool}",
            {
                "run_id": run.run_id,
                "approval": req.to_dict(),
                "message": "真机环境高风险操作，需操作员审批后方可执行",
            },
        )
        self._publish_run_update(run)
        self._publish("approval_required", {"approval": req.to_dict()})

        # Block until decision or timeout. Poll every 1s so emergency_stop can interrupt.
        deadline = req.created_at + req.timeout_seconds
        while True:
            if self.supervisor.is_emergency_stopped():
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = "emergency stop during approval"
                    run.finished_at = time.time()
                self._append_event("danger", "safety", "审批期间触发急停，任务取消", {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            remaining = deadline - time.time()
            if remaining <= 0:
                req.approved = False
                with self._lock:
                    run.status = "cancelled"
                    run.phase = "cancelled"
                    run.failure_reason = "approval timeout"
                    run.finished_at = time.time()
                self._append_event("warning", "safety", "审批超时，任务取消", {"run_id": run.run_id})
                self._cleanup_approval(run.run_id)
                return False
            if req.event.wait(timeout=1.0):
                break

        approved = bool(req.approved)
        with self._lock:
            if approved:
                run.status = "running"
                run.phase = "executing"
                self._append_event("info", "safety", f"操作员已确认，开始执行: {tool}", {"run_id": run.run_id})
            else:
                run.status = "cancelled"
                run.phase = "cancelled"
                run.failure_reason = "operator rejected"
                run.finished_at = time.time()
                self._append_event("warning", "safety", "操作员拒绝，任务取消", {"run_id": run.run_id})
        self._cleanup_approval(run.run_id)
        self._publish_run_update(run)
        return approved

    def _cleanup_approval(self, run_id: str) -> None:
        with self._lock:
            self._pending_approvals.pop(run_id, None)

    def approve_run(self, run_id: str) -> dict[str, Any]:
        """Operator approves a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = True
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "approved"}

    def reject_run(self, run_id: str) -> dict[str, Any]:
        """Operator rejects a pending high-risk run."""
        with self._lock:
            req = self._pending_approvals.get(run_id)
            if not req:
                return {"ok": False, "error": "no pending approval for this run_id"}
            if req.approved is not None:
                return {"ok": False, "error": f"approval already decided: {req.approved}"}
            req.approved = False
            req.event.set()
        return {"ok": True, "run_id": run_id, "status": "rejected"}

    # ── Replay 录制 ──

    def _replay_snapshot(self) -> dict[str, Any]:
        """Telemetry frame content for recording: lightweight drone states + backend id."""
        snapshot = self.tools.status_snapshot()
        drone = snapshot.get("drone")
        vehicles = snapshot.get("vehicles") if isinstance(snapshot.get("vehicles"), list) else []
        return {
            "backend": str(snapshot.get("backend") or ""),
            "drone": drone if isinstance(drone, dict) else {},
            "vehicles": [item for item in vehicles if isinstance(item, dict)],
        }

    def _start_replay_session(self, name: str, meta: dict[str, Any]) -> ReplaySession | None:
        with self._replay_lock:
            if self._active_replay is not None:
                return None
            session = ReplaySession(
                name,
                snapshot_provider=self._replay_snapshot,
                interval=0.2,
                meta=meta,
            )
            session.start()
            self._active_replay = session
        self._append_event("info", "replay", f"遥测录制开始: {name}", dict(meta))
        return session

    def _stop_replay_session(self) -> dict[str, Any] | None:
        with self._replay_lock:
            session = self._active_replay
            self._active_replay = None
        if session is None:
            return None
        summary = session.stop()
        self._append_event(
            "info",
            "replay",
            f"遥测录制结束: {summary.name}（{summary.frame_count} 帧）",
            summary.to_dict(),
        )
        return summary.to_dict()

    def start_manual_replay(self, name: str = "") -> dict[str, Any]:
        """Manually start recording (no run_id needed, e.g. UI manual flights)."""
        session_name = (str(name).strip() or f"manual_{int(time.time())}")[:120]
        with self._replay_lock:
            if self._manual_replay is not None:
                return {"ok": False, "error": "manual replay already recording"}
            session = ReplaySession(
                session_name,
                snapshot_provider=self._replay_snapshot,
                interval=0.2,
                meta={"mode": "manual"},
            )
            session.start()
            self._manual_replay = session
        self._append_event("info", "replay", f"手动录制开始: {session_name}", {})
        return {"ok": True, "name": session_name, "recording": True}

    def stop_manual_replay(self) -> dict[str, Any]:
        with self._replay_lock:
            session = self._manual_replay
            self._manual_replay = None
        if session is None:
            return {"ok": False, "error": "no manual replay recording"}
        summary = session.stop()
        self._append_event(
            "info",
            "replay",
            f"手动录制结束: {summary.name}（{summary.frame_count} 帧）",
            summary.to_dict(),
        )
        return {"ok": True, **summary.to_dict()}

    def replay_sessions(self) -> list[dict[str, Any]]:
        """List recorded sessions (for the UI replay panel)."""
        return list_replay_sessions()

    def get_replay_session(self, name: str) -> dict[str, Any] | None:
        """Read one recorded session: metadata + capped telemetry frames."""
        return read_replay_session(str(name or ""))

    def run_trace(self, run_id: str) -> dict[str, Any] | None:
        """Replay one run's append-only event log for diagnostics/UI."""
        reader = RunLogStore().read(str(run_id or ""))
        if reader is None:
            return None
        return reader.replay()

    def run_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent run event logs (ids + metadata, no payloads)."""
        return RunLogStore().list(limit=limit)

    @staticmethod
    def _plan_has_observation_dependency(plan: MissionPlan | None) -> bool:
        """A fixed sequence fails when an observation step precedes a motion
        step: the later move depends on what the observation shows (photo ->
        decide -> move), so it must run in the ReAct loop instead."""
        steps = list(plan.steps) if plan else []
        for index, step in enumerate(steps):
            if step.tool in OBSERVATION_TOOLS:
                if any(s.tool in MOTION_TOOLS for s in steps[index + 1 :]):
                    return True
        return False

    @staticmethod
    def _plan_requires_agent_loop(plan: MissionPlan | None) -> bool:
        """Choose Plan-Execute vs ReAct. The planner may declare agent_loop
        explicitly (visual search, tracking, conditional tasks); otherwise a
        fixed sequence with observation -> motion steps is detected
        structurally — no natural-language classification involved."""
        if plan is None:
            return False
        return plan.execution_mode == "agent_loop" or AgentRuntime._plan_has_observation_dependency(plan)

    def _correction_command(self, run: RunState) -> str:
        """Structured failure context for the ReAct correction loop: the LLM
        needs the failed step, tool output, verification summary, and current
        position to choose a meaningful corrective action."""
        parts = [f"继续完成原始任务并修正失败步骤。原始任务：{run.command}"]
        if run.failure_reason:
            parts.append(f"失败原因：{run.failure_reason}")
        verification = run.verification or {}
        if verification.get("summary"):
            parts.append(f"校验摘要：{verification.get('summary')}")
        failed_step = next(
            (s for s in (run.plan.steps if run.plan else []) if s.status == "failed"),
            None,
        )
        if failed_step is not None:
            detail = failed_step.result if isinstance(failed_step.result, dict) else {}
            message = str(detail.get("message") or detail.get("error") or "")
            parts.append(f"失败步骤：{failed_step.id} {failed_step.tool}{'：' + message if message else ''}")
        final = run.final_telemetry or {}
        position = final.get("position_ned") if isinstance(final, dict) else None
        if isinstance(position, dict) and any(position.get(k) is not None for k in ("x", "y", "z")):
            parts.append(
                f"当前 NED 位置：N {position.get('x')} / E {position.get('y')} / D {position.get('z')}"
            )
        return "；".join(parts)

    @staticmethod
    def _agent_loop_primary_command(run: RunState) -> str:
        """Command for a plan routed to ReAct before execution: the fixed
        sequence cannot express the task, so the loop decides per step."""
        return (
            f"按已生成的计划逐步执行。原始任务：{run.command}\n"
            "计划依赖中间观察结果（拍照/识别/确认后决策），请逐步执行："
            "每次先观察最新状态和工具返回，再选择下一步工具，直到任务完成。"
        )

    @staticmethod
    def _agent_state_with_skill_guidance(
        agent_state: dict[str, Any],
        skill_guidance: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not skill_guidance:
            return agent_state
        enriched = dict(agent_state or {})
        enriched["skill_guidance"] = [
            {
                "name": card.get("name", ""),
                "display_name": card.get("display_name", ""),
                "description": card.get("description", ""),
                "when_to_use": card.get("when_to_use", ""),
                "required_capabilities": list(card.get("required_capabilities") or []),
                "subtools": list(card.get("subtools") or []),
                "markdown": card.get("markdown", ""),
                "executable": False,
            }
            for card in skill_guidance[:3]
        ]
        return enriched


    def _execute_plan_execute_route(
        self,
        command: str,
        execute: bool,
        telemetry: dict[str, Any] | None,
        model_id: str,
        route: dict[str, Any],
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        memory_snapshot: dict[str, Any],
        run_id: str = "",
        agent_state: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skill_guidance: list[dict[str, Any]] | None = None,
    ) -> None:
        run_id = run_id or f"run_{int(time.time() * 1000)}"
        skill_guidance = skill_guidance or self.skills.guidance_cards(command, capabilities, memory_snapshot)
        agent_state = self._agent_state_with_skill_guidance(
            agent_state or self._agent_state_context(tool_runtime),
            skill_guidance,
        )
        reasoning_sink = self._plan_reasoning_sink(run_id, command)
        plan = self._try_llm_plan(
            command=command,
            telemetry=telemetry,
            model_id=model_id,
            run_id=run_id,
            capabilities=capabilities,
            tool_runtime=tool_runtime,
            memory_snapshot=memory_snapshot,
            agent_state={**agent_state, "planner_mode": "plan_execute"},
            attachments=attachments or [],
            reasoning_sink=reasoning_sink,
        )
        final_flush = getattr(reasoning_sink, "final_flush", None)
        if callable(final_flush):
            final_flush()
        if plan is None:
            if execute:
                # LLM 失效时的安全原则：不自动退化为规则规划继续飞行。
                # 规则规划覆盖不了模型级任务理解，自动执行会把 LLM 失效的
                # 影响面扩大到真实飞控；改为失败 + 安全悬停（由
                # _plan_and_execute 的异常路径执行 _attempt_failure_hover）。
                self._append_event(
                    "danger",
                    "planner",
                    "LLM 规划不可用，已停止执行以保护无人机",
                    {"command": command, "phase": "planning"},
                )
                raise LLMUnavailableError("LLM 规划不可用，已停止执行以保护无人机。请检查模型配置后重试。")
            plan = self.rule_planner.plan(command, capabilities=capabilities)
            plan.run_id = run_id
            plan.planner_source = "rules_plan_execute_fallback"
            plan.assumptions.append("仅规划预览：LLM 不可用，使用本地规则规划器生成只读预览。")
        else:
            plan.assumptions.append("采用 Plan-Execute：LLM 一次性规划，runtime 串行执行并校验；失败时进入 Agent Loop 纠错。")

        run = RunState(
            run_id=run_id,
            command=command,
            intent=plan.intent,
            summary=plan.summary,
            status="queued" if execute else "planned",
            mode="execute" if execute else "plan",
            phase="planning",
            execute=execute,
            model_id=model_id,
            plan=plan,
            task_level=route["level"],
            route_strategy=route["strategy"],
            route_reason=route["reason"],
            risk_level=route["risk_level"],
            answer_with_llm=False,
            start_telemetry=dict(telemetry or {}),
            agent_state=agent_state,
        )
        with self._lock:
            self._current = run
        self._start_task_run(run)
        self._append_event(
            "info",
            "planner",
            "Plan-Execute route selected",
            {"run_id": run.run_id, "execute": execute, "planner_source": plan.planner_source, **route},
        )
        if execute:
            self._begin_execution_trace(run, "任务适合一次性规划执行：先生成完整工具序列，再由 runtime 逐步执行、回读和校验。")
            for card in skill_guidance[:3]:
                self._append_process(
                    run,
                    "技能参考",
                    f"{card.get('display_name') or card.get('name')}: {card.get('description') or 'Markdown guidance loaded'}",
                    status="completed",
                    kind="reasoning",
                )
            if self._plan_requires_agent_loop(run.plan):
                # The plan depends on mid-execution observations (photo ->
                # decide -> move) or the planner declared agent_loop: a fixed
                # sequence would fail, so ReAct runs as the primary path.
                run.route_strategy = "agent_loop"
                self._append_event(
                    "info",
                    "planner",
                    "计划依赖中间观察，转入 Agent Loop 逐步执行",
                    {"run_id": run.run_id, "execution_mode": run.plan.execution_mode if run.plan else "auto"},
                )
                self._run_correction_loop(
                    run,
                    capabilities=capabilities,
                    tool_runtime=tool_runtime,
                    model_id=model_id,
                    attachments=attachments or [],
                    label="Agent Loop",
                    command_override=self._agent_loop_primary_command(run),
                )
            else:
                self._run_plan(run, finalize=False, remember=False)
                while self._should_enter_correction_loop(run):
                    run.correction_attempts += 1
                    self._append_event(
                        "warning",
                        "planner",
                        f"计划执行失败，进入 Agent Loop 纠错（{run.correction_attempts}/{CORRECTION_ATTEMPTS_MAX}）",
                        {"run_id": run.run_id, "failure_reason": run.failure_reason},
                    )
                    self._run_correction_loop(
                        run,
                        capabilities=capabilities,
                        tool_runtime=tool_runtime,
                        model_id=model_id,
                        attachments=attachments or [],
                    )
                total = len(run.plan.steps if run.plan else [])
                ok_count = sum(1 for step in (run.plan.steps if run.plan else []) if step.status == "completed")
                self._remember_plan_run(run, total=max(1, total), ok_count=ok_count)
            self._finalize_assistant_response(run)
        else:
            self._simulate_plan(run)
            self._finalize_assistant_response(run)

    def _should_enter_correction_loop(self, run: RunState) -> bool:
        if not run.execute or self._is_run_cancelled(run.run_id):
            return False
        if run.route_strategy != "plan_execute":
            return False
        if run.correction_attempts >= CORRECTION_ATTEMPTS_MAX:
            return False
        reason = (run.failure_reason or "").lower()
        if any(term in reason for term in ["operator", "approval", "emergency stop", "急停", "操作员"]):
            return False
        # Link-level failures cannot be fixed by re-deciding the plan.
        if any(term in reason for term in CONNECTION_FAILURE_TERMS):
            return False
        return run.status in {"failed", "blocked"} or run.verification.get("level") == "failed"

    def _run_correction_loop(
        self,
        run: RunState,
        *,
        capabilities: dict[str, Any],
        tool_runtime: dict[str, Any],
        model_id: str,
        attachments: list[dict[str, Any]],
        label: str = "纠错 Loop",
        command_override: str | None = None,
    ) -> None:
        self._append_process(
            run,
            label,
            "一次性计划未完全达成，进入 Agent Loop 回读当前状态并选择修正动作。"
            if label == "纠错 Loop"
            else "任务需要观察-响应循环，进入 Agent Loop 逐步执行。",
            status="running",
            kind="reasoning",
        )
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        correction_command = command_override or self._correction_command(run)
        loop = self.agent_loop.run(
            run_id=run.run_id,
            command=correction_command,
            capabilities=capabilities,
            tool_cards=tool_runtime.get("tool_cards") or self.tools.list_tool_cards(),
            initial_plan=run.plan,
            model_id=model_id or None,
            max_steps=6,
            execute=True,
            attachments=attachments,
            require_llm=True,
            conversation_context=self._recent_chat_context(),
        )
        correction_plan = self._plan_from_loop_state(loop)
        if run.plan:
            offset = len(run.plan.steps)
            for index, step in enumerate(correction_plan.steps, 1):
                step.id = f"s{offset + index:02d}"
                run.plan.steps.append(step)
        else:
            run.plan = correction_plan
        run.loop_state = loop.to_dict()
        run.summary = loop.summary or run.summary
        run.status = loop.status if loop.status in {"completed", "failed", "blocked"} else "completed"
        # a recovered earlier failure must never leak into a completed run:
        # the frontend renders the error badge from failure_reason
        run.failure_reason = "" if run.status == "completed" else loop.failure_reason
        run.finished_at = loop.finished_at or time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.verification = self._verify_run_outcome(run)
        # Loop-level task-contract verification (machine-checked completion
        # criteria) feeds the same failed-verification gate as the plan path.
        if loop.verification_status == "failed" and run.verification.get("level") != "failed":
            run.verification = {
                "level": "failed",
                "summary": f"完成判据未满足：{loop.summary or loop.failure_reason or '任务目标未达成'}",
            }
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.failure_reason = run.verification.get("summary", "纠错后任务校验仍未通过")
        run.phase = run.status if run.status in {"completed", "failed", "blocked"} else "completed"
        self._append_process(
            run,
            label,
            loop.summary or run.failure_reason or f"{label} 已结束。",
            status="completed" if run.status == "completed" else "failed",
            kind="reasoning",
        )
        self._publish_run_update(run)

    def _plan_from_loop_state(self, loop: LoopState, planned: bool = False) -> MissionPlan:
        """Rebuild a plan from the loop's audit trail.

        Decisions and results are paired by tool name with consumption order,
        so corrective decisions and batch results are never lost from the
        rebuilt plan; leftover results (e.g. batch extras) become their own
        steps at the end.
        """
        steps: list[MissionStep] = []
        consumed: set[int] = set()

        def status_for(result: Any) -> str:
            if result is None:
                return "pending"
            return "planned" if planned and result.ok else ("completed" if result.ok else "failed")

        for decision in loop.decisions:
            if decision.is_complete or not decision.action:
                continue
            result = None
            for ridx, row in enumerate(loop.results):
                if ridx in consumed:
                    continue
                if row.tool == decision.action:
                    result = row
                    consumed.add(ridx)
                    break
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=decision.reason or decision.action,
                    tool=decision.action,
                    params=dict(decision.params or {}),
                    layer="agent_loop",
                    status=status_for(result),
                    result=result.data if result else None,
                )
            )
        for ridx, row in enumerate(loop.results):
            if ridx in consumed:
                continue
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=row.tool,
                    tool=row.tool,
                    params=dict(row.params or {}),
                    layer="agent_loop",
                    status="completed" if row.ok else "failed",
                    result=row.data,
                )
            )
        return MissionPlan(
            run_id=loop.run_id,
            command=loop.command,
            intent="agent_loop",
            summary=loop.summary or "Agent Loop task",
            steps=steps,
            planner_source="agent_loop",
            reasoning="Loop decisions are stored in loop_state.decisions.",
            risk_notes=[loop.failure_reason] if loop.failure_reason else [],
        )

    def _planner_tool_cards(
        self,
        command: str,
        atomic_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
        memory_snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a small LLM-facing action surface.

        The model should reason over skills first. Atomic tools remain visible
        only when they are safe/read-only, needed for visual grounding, or the
        active backend has no suitable skill for the requested capability.
        """
        skill_names: set[str] = set()
        atomic_by_name = {
            str(card.get("name")): card
            for card in atomic_cards
            if isinstance(card, dict) and card.get("name")
        }
        allowed_atomic = self._allowed_planner_atomic_tools(command, skill_names, capabilities)
        cards: list[dict[str, Any]] = []
        for name in sorted(allowed_atomic):
            card = atomic_by_name.get(name)
            if card:
                cards.append(card)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            name = str(card.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(card)
        # Agent-level cards (memory/subtask) always keep a slot.
        agent_names = {"memory_recall", "memory_remember", "agent_subtask"}
        agent_cards = [card for card in deduped if card.get("name") in agent_names]
        regular = [card for card in deduped if card.get("name") not in agent_names]
        return (regular[: max(0, 18 - len(agent_cards))] + agent_cards)[:18]

    def _allowed_planner_atomic_tools(
        self,
        command: str,
        skill_names: set[str],
        capabilities: dict[str, Any],
    ) -> set[str]:
        text = (command or "").lower()
        allowed = {"drone_connect", "drone_get_status"}
        visual_terms = (
            "camera", "image", "photo", "see", "look", "detect", "search", "find", "target",
            "摄像头", "画面", "图像", "图片", "照片", "拍照", "看到", "看一下", "看看", "识别", "检测", "搜索", "寻找", "目标",
        )
        mission_terms = ("mission", "waypoint", "航点", "航线", "任务", "上传", "下载", "进度", "清空", "启动")
        landing_terms = ("land", "rtl", "return", "降落", "返航", "返回")
        hover_terms = ("hover", "hold", "pause", "悬停", "保持", "暂停")
        path_terms = ("path", "route", "orbit", "circle", "scan", "patrol", "绕圈", "转圈", "盘旋", "扫描", "巡航", "巡检", "半径")

        if any(term in text for term in visual_terms):
            allowed.update({
                "airsim_take_photo",
                "airsim_vlm_analyze_image",
                "airsim_vlm_confirm_target",
                "airsim_get_depth_map",
                "airsim_task_status",
                "airsim_task_cancel",
            })
            if "skill:visual_observe" not in skill_names:
                allowed.update({"airsim_take_photo", "airsim_vlm_analyze_image", "airsim_vlm_confirm_target"})

        if any(term in text for term in mission_terms):
            allowed.update({
                "drone_download_mission",
                "drone_get_mission_progress",
                "drone_upload_mission",
                "drone_start_mission",
                "drone_clear_mission",
            })

        if any(term in text for term in landing_terms):
            allowed.add("drone_land")
        if any(term in text for term in hover_terms):
            allowed.add("drone_hover")
        if any(term in text for term in path_terms):
            allowed.add("drone_fly_path")
        formation_terms = (
            "formation", "swarm", "编队", "队形", "coverage", "覆盖", "区域扫描", "网格扫描", "分区扫描",
        )
        if any(term in text for term in formation_terms):
            allowed.add("formation_command")

        if "skill:navigation" not in skill_names:
            allowed.update({"drone_arm", "drone_takeoff", "drone_fly_to", "drone_move_relative", "drone_hover"})
        if "skill:return_home" not in skill_names and capabilities.get("flight_control"):
            allowed.update({"drone_fly_to", "drone_land"})
        return allowed

    def _on_agent_loop_state(self, loop: LoopState) -> None:
        with self._lock:
            run = self._current
            if not run or run.run_id != loop.run_id:
                return
            if run.status == "cancelled":
                self._publish_run_update(run)
                return
            previous_decision_count = len((run.loop_state or {}).get("decisions") or [])
            previous_result_count = len((run.loop_state or {}).get("results") or [])
            previous_observation_count = len((run.loop_state or {}).get("observations") or [])
            run.loop_state = loop.to_dict()
            decision_count = len(loop.decisions)
            result_count = len(loop.results)
            observation_count = len(loop.observations)
            run.current_step = f"loop-{decision_count}" if decision_count else "observe"
            run.progress = min(95.0, decision_count / max(1, loop.max_steps) * 100.0)
            if run.execute and run.status not in {"paused", "awaiting_approval", "cancelled", "blocked"}:
                run.status = "running"
                run.phase = "executing"
            if observation_count > previous_observation_count and decision_count == previous_decision_count:
                self._append_process(
                    run,
                    "模型决策",
                    "正在根据最新遥测、工具结果和任务目标选择下一步动作。",
                    status="running",
                    kind="reasoning",
                )
            if decision_count > previous_decision_count:
                decision = loop.decisions[-1]
                decision_text = self._loop_decision_public_text(decision)
                run.thought_trace.append({
                    "timestamp": time.time(),
                    "title": f"循环决策 {decision_count}",
                    "body": decision_text or decision.action or "检查任务是否完成",
                    "status": "completed" if decision.is_complete else "running",
                })
                run.thought_trace = run.thought_trace[-30:]
                if decision.is_complete:
                    self._append_process(
                        run,
                        "模型决策",
                        "任务目标已满足，正在整理最终报告。",
                        status="completed",
                        kind="reasoning",
                    )
                if decision_text:
                    self._append_process(
                        run,
                        "模型总结" if decision.is_complete else "模型决策",
                        decision_text,
                        status="completed",
                        kind="reasoning",
                    )
                if decision.action:
                    self._append_process(
                        run,
                        decision.action,
                        self._format_tool_call_body(decision.params),
                        status="running",
                        tool=decision.action,
                        params=decision.params,
                        kind="tool",
                    )
            if result_count > previous_result_count and loop.results:
                result = loop.results[-1]
                self._append_process(
                    run,
                    result.tool,
                    self._format_loop_result_body(result.data),
                    status="completed" if result.ok else "failed",
                    tool=result.tool,
                    params=result.params,
                    kind="tool",
                )
        self._publish_run_update(run)
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))

    @staticmethod
    def _loop_decision_public_text(decision: Any) -> str:
        parts: list[str] = []
        reason = str(getattr(decision, "reason", "") or "").strip()
        reflection = str(getattr(decision, "reflection", "") or "").strip()
        if reason:
            parts.append(reason)
        if reflection and reflection != reason:
            parts.append(reflection)
        return "\n".join(parts).strip()

    @staticmethod
    def _format_tool_call_body(params: dict[str, Any] | None) -> str:
        if not params:
            return "准备调用工具。"
        try:
            payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            payload = str(params)
        return f"参数 {payload}"

    @staticmethod
    def _format_loop_result_body(data: dict[str, Any] | None) -> str:
        if not isinstance(data, dict):
            return ""
        message = str(data.get("message") or data.get("summary_zh") or data.get("status") or "").strip()
        tool_results = data.get("tool_results")
        if isinstance(tool_results, list) and tool_results:
            parts: list[str] = []
            for item in tool_results[:12]:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool") or item.get("name") or "tool")
                ok = "ok" if item.get("ok") is True else ("failed" if item.get("ok") is False else "")
                nested = item.get("data") if isinstance(item.get("data"), dict) else {}
                detail = str(nested.get("message") or nested.get("summary_zh") or nested.get("status") or "").strip()
                label = f"{tool} {ok}".strip()
                parts.append(f"{label}: {detail}" if detail else label)
            summary = " → ".join(parts)
            if len(tool_results) > 12:
                summary += f" → +{len(tool_results) - 12} more"
            return f"{message}\n{summary}".strip() if message else summary
        return message

    def _on_agent_event(self, level: str, source: str, message: str, data: dict[str, Any]) -> None:
        self._append_event(level, source, message, data)
        with self._lock:
            run_log = self._run_log
        if run_log is not None:
            kind = str(data.get("kind") or "")
            if kind == "loop.decision":
                run_log.write("loop.decision", data)
            elif kind == "tool.result":
                run_log.write("tool.result", data)
            elif kind == "observation":
                run_log.write("observation", data)
            elif kind == "replan":
                run_log.write("replan", data)
            elif kind == "verification":
                run_log.write("verification", data)
            elif kind == "async.poll":
                run_log.write(
                    "async.poll",
                    {
                        "task_id": str(data.get("task_id") or ""),
                        "status": str(data.get("status") or ""),
                    },
                )
        if source != "async_task":
            return
        with self._lock:
            run = self._current
            if not run:
                return
            run.agent_state = dict(run.agent_state or {})
            run.agent_state["active_operation"] = {
                "message": message,
                "task_id": str(data.get("task_id") or (data.get("data") or {}).get("task_id") or ""),
                "status": str((data.get("data") or {}).get("status") or data.get("status") or "running"),
                "updated_at": time.time(),
            }
        self._publish_run_update(run)

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _ned_value(self, position: dict[str, Any] | None, key: str, default: float | None = None) -> float | None:
        if not isinstance(position, dict):
            return default
        value = self._finite_float(position.get(key))
        return value if value is not None else default

    def _active_run_is_interruptible(self) -> bool:
        with self._lock:
            return bool(
                self._current
                and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}
            )

    def _attempt_failure_hover(self, run: RunState | None, reason: str) -> dict[str, Any] | None:
        try:
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if not capabilities.get("flight_control"):
                return None
            drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
            position = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
            z = self._finite_float(position.get("z")) or 0.0
            min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
            active_airframe = bool(drone.get("flying") or drone.get("armed") or abs(z) >= min_altitude)
            if not active_airframe:
                return None
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            payload = result.to_dict()
            self._append_event(
                "warning" if result.ok else "danger",
                "tool",
                "任务异常后安全悬停",
                {"reason": reason, "hover": payload},
            )
            if run is not None:
                self._append_process(
                    run,
                    "异常安全悬停",
                    "Agent 决策中断，已发送悬停保位指令。"
                    if result.ok
                    else f"Agent 决策中断，悬停保位失败：{result.data.get('message', '')}",
                    status="completed" if result.ok else "failed",
                    tool="drone_hover",
                    params={},
                    kind="tool",
                )
            return payload
        except Exception as exc:
            self._append_event("warning", "tool", "任务异常后安全悬停失败", {"reason": reason, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _manual_return_home(self) -> dict[str, Any]:
        if self.tools.formation_active():
            return {
                "ok": False,
                "error": "a formation/coverage mission is active; use formation_command(action=land_all) or hover_all before return home",
            }
        runtime = self.tools.status_snapshot()
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        if not connected:
            return {"ok": False, "error": "flight controller link is offline or stale"}
        if not isinstance(drone, dict) or not drone or drone.get("error"):
            return {"ok": False, "error": str(drone.get("error") or "vehicle status unavailable")}

        current = drone.get("position_ned") if isinstance(drone.get("position_ned"), dict) else {}
        current_x = self._ned_value(current, "x", 0.0) or 0.0
        current_y = self._ned_value(current, "y", 0.0) or 0.0
        current_z = self._ned_value(current, "z", 0.0) or 0.0
        altitude = abs(current_z)
        armed = bool(drone.get("armed"))
        flying = bool(drone.get("flying"))
        min_altitude = float(getattr(self.tools.safety.constraints, "min_altitude", 0.5) or 0.5)
        max_altitude = float(getattr(self.tools.safety.constraints, "max_altitude", 50.0) or 50.0)

        if not flying and not armed and altitude < min_altitude:
            self._append_event("info", "tool", "无人机已在地面，无需返航", {"drone": drone})
            return {"ok": True, "message": "vehicle already on ground", "drone": drone}
        if not flying and altitude < min_altitude:
            return {"ok": False, "error": "vehicle is not airborne; return_home command was not sent", "drone": drone}

        memory = self.memory.snapshot()
        session = memory.get("session") if isinstance(memory, dict) else {}
        start_position = session.get("last_task_start_position_ned") if isinstance(session, dict) else None
        target_source = "last_task_start_position_ned"
        target_x = self._ned_value(start_position, "x")
        target_y = self._ned_value(start_position, "y")
        if target_x is None or target_y is None:
            home_x, home_y = self.tools.safety.constraints.home_position
            target_x = float(home_x)
            target_y = float(home_y)
            target_source = "safety_home_position"

        if current_z < -min_altitude:
            target_z = -min(max(altitude, min_altitude), max_altitude)
        else:
            target_z = -min(max(3.0, min_altitude), max_altitude)
        target = {"x": round(float(target_x), 3), "y": round(float(target_y), 3), "z": round(float(target_z), 3)}
        horizontal_error = math.hypot(current_x - target["x"], current_y - target["y"])
        if horizontal_error < 0.6:
            hover_result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            self._append_event(
                "info" if hover_result.ok else "warning",
                "tool",
                "无人机已在返航点附近，执行悬停",
                {"target_position_ned": target, "target_source": target_source, "hover": hover_result.to_dict()},
            )
            return {
                "ok": hover_result.ok,
                "message": "already near return point; holding position",
                "target_position_ned": target,
                "target_source": target_source,
                "hover": hover_result.to_dict(),
            }

        speed = min(3.0, float(getattr(self.tools.safety.constraints, "max_velocity", 3.0) or 3.0))
        move_result = self.tools.execute(
            "drone_fly_to",
            {**target, "velocity": max(1.0, speed)},
            dry_run=False,
            blocked_by_supervisor=False,
        )
        self._remember_position_from_payload(move_result.data, source="return_home")
        hover_result = None
        if move_result.ok:
            hover_result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
        ok = move_result.ok and (hover_result is None or hover_result.ok)
        self._append_event(
            "info" if ok else "warning",
            "tool",
            "手动返航",
            {
                "target_position_ned": target,
                "target_source": target_source,
                "move": move_result.to_dict(),
                "hover": hover_result.to_dict() if hover_result else None,
            },
        )
        return {
            "ok": ok,
            "message": "return home command completed" if ok else "return home command failed",
            "target_position_ned": target,
            "target_source": target_source,
            "result": move_result.to_dict(),
            "hover": hover_result.to_dict() if hover_result else None,
        }

    def control(self, action: str, expected_backend: str = "") -> dict[str, Any]:
        action = action.strip().lower()
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        if action in {"cancel", "stop", "interrupt"}:
            return self._cancel_active_work()
        if action == "pause":
            self.supervisor.pause()
            if self._current and self._current.status == "running":
                self._current.status = "paused"
                self._current.phase = "paused"
            self._append_event("warning", "safety", "任务已暂停")
            return {"ok": True}
        if action == "resume":
            self.supervisor.resume()
            if self._current and self._current.status == "paused":
                self._current.status = "running"
                self._current.phase = "executing"
            self._append_event("info", "safety", "任务已恢复")
            return {"ok": True}
        if action == "emergency_stop":
            self.supervisor.emergency_stop()
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            # hover every formation drone too — the single-vehicle hover only
            # covers the default vehicle
            formation_stopped = self._close_formation("emergency_stop")
            self._append_event(
                "danger",
                "safety",
                "急停已触发，尝试悬停",
                {**result.to_dict(), "formation_stopped": formation_stopped},
            )
            if self._current:
                self._current.status = "blocked"
                self._current.phase = "blocked"
                self._current.failure_reason = "emergency stop"
                self._current.finished_at = time.time()
            return {"ok": result.ok, "result": result.to_dict(), "formation_stopped": formation_stopped}
        if action == "reset_emergency":
            self.supervisor.reset_emergency()
            self._append_event("info", "safety", "急停状态已复位")
            return {"ok": True}
        if action == "hover":
            result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)
            self._append_event("info", "tool", "手动悬停", result.to_dict())
            return {"ok": result.ok, "result": result.to_dict()}
        if action == "land":
            result = self.tools.execute("drone_land", {}, dry_run=False, blocked_by_supervisor=False)
            self._append_event("warning", "tool", "手动降落", result.to_dict())
            return {"ok": result.ok, "result": result.to_dict()}
        if action in {"return_home", "rtl"}:
            cancel_result = self._cancel_active_work() if self._active_run_is_interruptible() else None
            runtime = self.tools.status_snapshot()
            backend = str(runtime.get("backend") or "")
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if backend in {"px4_mavlink", "px4_ros2"} and capabilities.get("mode_control"):
                rtl_result = self.tools.execute(
                    "drone_set_mode",
                    {"mode": "RTL"},
                    dry_run=False,
                    blocked_by_supervisor=False,
                )
                result = {
                    "ok": rtl_result.ok,
                    "message": "PX4 native RTL requested" if rtl_result.ok else "PX4 rejected native RTL",
                    "control_channel": "MAVLink native mode" if backend == "px4_mavlink" else "ROS2 gateway native mode",
                    "result": rtl_result.to_dict(),
                }
            else:
                result = self._manual_return_home()
                result["control_channel"] = "local NED guided path"
            if cancel_result:
                result["cancelled_active_task"] = cancel_result
            return result
        if action in {"connect", "reconnect"}:
            result = self.tools.reconnect()
            self._append_event("info", "tool", "Reconnect AirSim", result.to_dict())
            return {"ok": result.ok, "result": result.to_dict()}
        if action == "clear_events":
            with self._lock:
                self._events.clear()
            return {"ok": True}
        return {"ok": False, "error": f"unknown control action: {action}"}

    def _backend_mismatch(self, expected_backend: str = "") -> dict[str, Any] | None:
        expected = str(expected_backend or "").strip()
        active = str(self.tools.backend_id or "").strip()
        if expected and expected != active:
            return {
                "ok": False,
                "error": f"active backend changed from {expected} to {active}; command was not sent",
                "expected_backend": expected,
                "active_backend": active,
            }
        return None

    def _cancel_active_work(self) -> dict[str, Any]:
        self._cancel_requested.set()
        run_to_publish: RunState | None = None
        previous_phase = ""
        cancelled_ids: list[str] = []
        with self._lock:
            if self._current and self._current.status in {"queued", "running", "paused", "responding", "awaiting_approval"}:
                previous_phase = self._current.phase
                self._current.status = "cancelled"
                self._current.phase = "cancelled"
                self._current.failure_reason = "operator cancelled task"
                self._current.finished_at = time.time()
                self._current.progress = 100.0
                self._current.assistant_message = "任务已中断。"
                self._cancelled_request_ids.add(self._current.run_id)
                cancelled_ids.append(self._current.run_id)
                run_to_publish = self._current
            for message in self._messages:
                if message.role == "assistant" and message.status == "running" and message.run_id:
                    self._cancelled_request_ids.add(message.run_id)
                    cancelled_ids.append(message.run_id)
                    if not str(message.content or "").strip():
                        message.content = "已中断当前回复。"
                    message.status = "complete"
                    details = dict(message.details or {})
                    details["phase"] = "cancelled"
                    details["cancelled"] = True
                    message.details = details
                    message.updated_at = time.time()

        hover_result = None
        if run_to_publish and run_to_publish.mode == "execute" and previous_phase != "responding":
            runtime = self.tools.status_snapshot()
            capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
            if capabilities.get("flight_control"):
                hover_result = self.tools.execute("drone_hover", {}, dry_run=False, blocked_by_supervisor=False)

        if run_to_publish:
            self._update_assistant_message(
                run_to_publish.run_id,
                "任务已中断。",
                "complete",
                self._message_details(run_to_publish),
            )
            self._publish_run_update(run_to_publish)
        self._append_event(
            "warning",
            "agent",
            "已发送中断请求",
            {"run_ids": sorted(set(cancelled_ids)), "hover_result": hover_result.to_dict() if hover_result else None},
        )
        return {"ok": True, "cancelled": sorted(set(cancelled_ids)), "hover": hover_result.to_dict() if hover_result else None}

    def _is_run_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return self._cancel_requested.is_set() or run_id in self._cancelled_request_ids

    def connection_settings(self) -> dict[str, Any]:
        """Return current connection settings (QGC Links style)."""
        settings = _connection_settings()
        try:
            settings["detected_mavlink_links"] = [
                candidate.to_dict()
                for candidate in discover_serial_mavlink_candidates()
            ]
        except Exception:
            settings["detected_mavlink_links"] = []
        try:
            settings["vehicle_info"] = self.tools.vehicle_info(refresh=False)
        except Exception as exc:
            settings["vehicle_info"] = {"status": "error", "message": str(exc)}
        return settings

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return current active vehicle connection and firmware metadata."""
        try:
            info = self.tools.vehicle_info(refresh=refresh)
            return {"ok": info.get("status") != "error", "vehicle_info": info}
        except Exception as exc:
            return {"ok": False, "vehicle_info": {"status": "error", "message": str(exc)}}

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return current active vehicle parameter cache/query results."""
        try:
            data = self.tools.vehicle_parameters(
                refresh=refresh,
                query=query,
                limit=limit,
                offset=offset,
                timeout=timeout,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "parameter_info": data}
        except Exception as exc:
            return {"ok": False, "parameter_info": {"status": "error", "message": str(exc), "parameters": []}}

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one active vehicle parameter through MAVLink."""
        try:
            data = self.tools.set_vehicle_parameter(
                name=name,
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=timeout,
            )
            return {"ok": data.get("status") == "ok", "parameter_write": data}
        except Exception as exc:
            return {"ok": False, "parameter_write": {"status": "error", "message": str(exc)}}

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return current active vehicle setup diagnostics and telemetry histories."""
        try:
            data = self.tools.vehicle_setup_snapshot(
                include_history=include_history,
                history_limit=history_limit,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_setup": data}
        except Exception as exc:
            return {"ok": False, "vehicle_setup": {"status": "error", "message": str(exc), "history": {}}}

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return current active vehicle lightweight telemetry and histories."""
        try:
            data = self.tools.vehicle_telemetry_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            return {"ok": data.get("status") not in {"error", "busy"}, "vehicle_telemetry": data}
        except Exception as exc:
            return {"ok": False, "vehicle_telemetry": {"status": "error", "message": str(exc), "history": {}}}

    def camera_settings(self) -> dict[str, Any]:
        """Return current UI camera source settings."""
        return _camera_settings()

    def application_settings(self) -> dict[str, Any]:
        """Return operator-facing application preferences."""
        return _application_settings()

    def save_application_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist validated application preferences."""
        try:
            settings = _load_settings()
            current = _application_settings(settings)
            incoming = payload if isinstance(payload, dict) else {}
            for group in current:
                update = incoming.get(group)
                if isinstance(update, dict):
                    current[group].update(update)
            settings["application"] = _application_settings({"application": current})
            _save_settings(settings)
            return {"ok": True, "application": settings["application"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_connection_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the connection list / active id / auto-connect flag."""
        try:
            settings = _load_settings()
            if payload is None:
                payload = {}
            settings["connections"] = {
                "auto_connect": bool(payload.get("auto_connect", True)),
                "active_connection_id": str(payload.get("active_connection_id", "")),
                "connections": list(payload.get("connections") or []),
            }
            _save_settings(settings)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_camera_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the camera source settings used by the UI camera viewer."""
        try:
            settings = _load_settings()
            current = _camera_settings(settings)
            if payload is None:
                payload = {}
            payload_dict = dict(payload)
            merged = _camera_settings({"camera": {**current, **payload_dict}})
            persisted = dict(merged)
            if "host" not in payload_dict:
                persisted.pop("host", None)
            if "port" not in payload_dict:
                persisted.pop("port", None)
            settings["camera"] = persisted
            _save_settings(settings)
            return {"ok": True, "camera": merged}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── AirSim settings.json 模板（通信模式一键切换） ──

    def airsim_settings_info(self) -> dict[str, Any]:
        """List AirSim settings.json templates (with full content) + target path.

        AirSim's communication mode is decided by the settings.json:
          * SimpleFlight           -> airsim backend (direct RPC control)
          * PX4Multirotor + UDP    -> px4_mavlink backend (local/WSL PX4 SITL)
          * PX4Multirotor + TCP    -> px4_ros2 backend (Jetson/edge PX4 SITL)
        """
        templates_dir = REPO_ROOT / "config" / "airsim_settings"
        templates: list[dict[str, Any]] = []
        for template_id, meta in AIRSIM_SETTINGS_TEMPLATES.items():
            path = templates_dir / meta["file"]
            templates.append(
                {
                    "id": template_id,
                    "label": meta["label"],
                    "description": meta["description"],
                    "backend": meta["backend"],
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                    "content": path.read_text(encoding="utf-8") if path.is_file() else "",
                }
            )
        target = self._airsim_settings_path()
        return {
            "templates": templates,
            "target_path": str(target),
            "target_exists": target.is_file(),
        }

    @classmethod
    def _airsim_settings_path(cls) -> Path:
        """Resolve the AirSim settings.json location automatically.

        Nothing is hard-coded: %USERPROFILE% resolves from the current
        Windows user (Path.home()), so moving to another machine just works.
        The AIRSIM_SETTINGS_PATH environment variable remains available for
        deployment-level overrides (e.g. a custom AirSim install location);
        it is not exposed in the UI.
        """
        env_path = os.environ.get("AIRSIM_SETTINGS_PATH", "").strip()
        if env_path:
            return Path(env_path)
        candidates = [
            Path.home() / "Documents" / "AirSim" / "settings.json",
            Path.home() / "AirSim" / "settings.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def apply_airsim_settings_template(self, template_id: str) -> dict[str, Any]:
        """Backup the current settings.json and write the requested template.

        Never destructive: the existing file (if any) is copied to
        settings.json.bak-<timestamp> before writing.
        """
        meta = AIRSIM_SETTINGS_TEMPLATES.get(str(template_id or ""))
        if meta is None:
            return {"ok": False, "error": f"unknown template: {template_id}"}
        template_path = REPO_ROOT / "config" / "airsim_settings" / meta["file"]
        if not template_path.is_file():
            return {"ok": False, "error": f"template file missing: {template_path.name}"}
        target = self._airsim_settings_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup_path = None
            if target.is_file():
                backup_path = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
                backup_path.write_bytes(target.read_bytes())
            target.write_bytes(template_path.read_bytes())
            return {
                "ok": True,
                "template": template_id,
                "target_path": str(target),
                "backup_path": str(backup_path) if backup_path else None,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_backend(
        self,
        backend_id: str,
        connect_params: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Switch backend and reconnect, persisting the choice to disk.

        Args:
            backend_id: Target backend identifier.
            connect_params: Optional connection overrides. For PX4 MAVLink
                pass ``{"url": "udp:127.0.0.1:14540"}``. For AirSim pass
                ``{"ip": "127.0.0.1", "port": 41452}``.
            connection_id: Optional link id from the QGC Links panel. When
                provided, ``connect_params`` are taken from the stored link
                definition unless explicitly overridden.
        """
        with self._lock:
            self._backend_generation += 1
        # Resolve params: explicit overrides > stored link definition > defaults.
        resolved_params = dict(connect_params) if connect_params else {}
        if connection_id:
            conn_section = _connection_settings()
            connection = next(
                (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
                None,
            )
            if connection:
                _, built_params = _build_connect_params(connection)
                # Connection definition takes precedence; explicit overrides are fallback defaults.
                resolved_params = {**(dict(connect_params) if connect_params else {}), **built_params}

        switch = self.tools.set_backend(backend_id)
        if not switch.ok:
            return {"ok": False, "error": switch.data.get("message", "backend switch failed")}

        # Persist a minimal backend record for backwards compatibility.
        try:
            settings = _load_settings()
            settings["backend"] = self.tools.backend_id
            if resolved_params:
                settings["connect_params"] = resolved_params
            _save_settings(settings)
        except Exception:
            pass

        ip = str(resolved_params.get("ip", "127.0.0.1"))
        port = int(resolved_params.get("port", 41452))
        url = str(resolved_params.get("url", ""))
        fallback_url = str(resolved_params.get("fallback_url", ""))
        remote_host = str(resolved_params.get("remote_host", ""))
        remote_port = int(resolved_params.get("remote_port", 0) or 0)
        real_vehicle = bool(resolved_params.get("real_vehicle", False))
        result = self.tools.reconnect(
            ip=ip,
            port=port,
            url=url,
            fallback_url=fallback_url,
            remote_host=remote_host,
            remote_port=remote_port,
            real_vehicle=real_vehicle,
        )
        self._append_event(
            "info" if result.ok else "warning",
            "tool",
            f"Backend {self.tools.backend_id} reconnect",
            result.to_dict(),
        )
        return {
            "ok": result.ok,
            "backend": self.tools.backend_id,
            "switch": switch.to_dict(),
            "result": result.to_dict(),
        }

    def activate_connection(self, connection_id: str) -> dict[str, Any]:
        """Connect/disconnect a QGC Links entry.

        If the requested link is already the active one and currently connected,
        disconnect. Otherwise switch backend and reconnect with the link params.
        """
        if not connection_id:
            return {"ok": False, "error": "connection_id required"}

        conn_section = _connection_settings()
        connection = next(
            (c for c in conn_section.get("connections", []) if c.get("id") == connection_id),
            None,
        )
        if not connection:
            return {"ok": False, "error": f"unknown connection {connection_id}"}

        backend_id, connect_params = _build_connect_params(connection)
        tool_runtime = self.tools.status_snapshot()
        active_id = str(conn_section.get("active_connection_id") or "")
        already_active = tool_runtime.get("backend") == backend_id
        currently_connected = bool(tool_runtime.get("connected")) and not tool_runtime.get("stale_connection")

        if already_active and currently_connected and active_id == connection_id:
            result = self.tools.execute("drone_disconnect", {})
            ok = result.ok
            if ok:
                settings = _load_settings()
                settings["connections"] = settings.get("connections") or {}
                settings["connections"]["active_connection_id"] = ""
                _save_settings(settings)
            self._append_event(
                "info" if ok else "warning",
                "tool",
                "Disconnect link",
                result.to_dict(),
            )
            return {"ok": ok, "action": "disconnect", "result": result.to_dict()}

        result = self.set_backend(backend_id, connect_params=connect_params, connection_id=connection_id)
        if result.get("ok"):
            settings = _load_settings()
            settings["connections"] = settings.get("connections") or {}
            settings["connections"]["active_connection_id"] = connection_id
            _save_settings(settings)
        return result

    def _auto_connect_from_settings(self, expected_generation: int = 0, expected_backend_id: str = "") -> None:
        """Load persisted active link and reconnect on startup."""
        try:
            time.sleep(0.05)
            with self._lock:
                if (
                    self._backend_generation != expected_generation
                    or self.tools.backend_id != (expected_backend_id or self.tools.backend_id)
                ):
                    self._append_event(
                        "info",
                        "system",
                        "Auto-connect skipped because backend changed during startup",
                        {
                            "expected_generation": expected_generation,
                            "current_generation": self._backend_generation,
                            "expected_backend": expected_backend_id,
                            "current_backend": self.tools.backend_id,
                        },
                    )
                    return
            conn_section = _connection_settings()
            if not conn_section.get("auto_connect"):
                return
            expected_backend = self.tools.backend_id
            active_id, connection = _select_connection_for_backend(conn_section, expected_backend)
            if not connection:
                return
            backend_id, connect_params = _build_connect_params(connection)
            if active_id != conn_section.get("active_connection_id"):
                settings = _load_settings()
                settings["connections"] = settings.get("connections") or {}
                settings["connections"]["active_connection_id"] = active_id
                _save_settings(settings)
            self._append_event(
                "info",
                "system",
                f"Auto-connecting to {connection.get('name', active_id)}",
                {"backend": backend_id, "connect_params": connect_params},
            )
            self.set_backend(backend_id, connect_params=connect_params, connection_id=active_id)
        except Exception as exc:
            self._append_event("warning", "system", f"Auto-connect failed: {exc}", {})

    def _execute_agent_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
        approval_already_granted: bool = False,
    ) -> ToolCallResult:
        """Single governed entry point used by plans, loops, skills, and the tool API."""
        params = dict(params or {})
        caller_owns_run = self._execution_thread_id == threading.get_ident()
        with self._lock:
            run = run or (self._current if caller_owns_run else None)

        manual_safety_tools = {"drone_hover", "drone_land", "airsim_task_cancel"}
        read_only_tools = set(self.tools.READ_ONLY_TOOLS) | {"airsim_task_status"}
        if self._execution_slot.locked() and not caller_owns_run and tool not in read_only_tools | manual_safety_tools:
            return self._blocked_tool_result(tool, params, "an Agent execution is active; use pause/hover/land or wait for completion")

        # Formation conflict guard: while the deterministic formation/coverage
        # control loop is commanding vehicles, single-vehicle flight tools are
        # blocked so two control paths can never fight over the same drone.
        # Hover/land/status/connect stay available as safe recovery actions.
        formation_active = getattr(self.tools, "formation_active", None)
        if (
            callable(formation_active)
            and formation_active()
            and tool in self.tools.CONTROL_TOOLS
            and tool not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect", "airsim_task_cancel"}
        ):
            return self._blocked_tool_result(
                tool,
                params,
                "a formation/coverage mission is active; use formation_command(action=hover_all) or land_all before single-vehicle control",
            )

        if tool.startswith("skill:"):
            return self._execute_skill_tool(tool, params, dry_run=dry_run, run=run)
        if tool == "agent_subtask":
            return self._execute_sub_agent_tool(params, dry_run=dry_run, run=run)
        if tool == "memory_recall":
            return self._execute_memory_recall(params, dry_run=dry_run)
        if tool == "memory_remember":
            return self._execute_memory_remember(params, dry_run=dry_run)
        if tool == "airsim_vlm_confirm_target":
            return self._execute_vlm_confirm_tool(params, dry_run=dry_run, run=run)
        if tool == "airsim_vlm_analyze_image":
            return self._execute_vlm_analyze_tool(params, dry_run=dry_run, run=run)
        if dry_run:
            return self.tools.execute(tool, params, dry_run=True)

        runtime = self.tools.status_snapshot()
        profile = runtime.get("backend_profile") or {}
        capabilities = profile.get("capabilities") or {}
        risk_level = self._tool_risk_level(tool, capabilities, run, params)
        requires_approval = bool(capabilities.get("requires_operator_approval"))
        if risk_level == "high" and requires_approval and not approval_already_granted:
            if run is None:
                return self._blocked_tool_result(
                    tool,
                    params,
                    "high-risk real-vehicle tools must be submitted through Execute mode and approved",
                )
            approved = self._await_tool_approval(
                run,
                tool,
                params,
                risk_level,
                reason=self._approval_reason(tool, params),
            )
            if not approved:
                return self._blocked_tool_result(tool, params, run.failure_reason or "operator approval rejected")

        low_altitude_block = self._low_altitude_motion_guard(tool, params, runtime, capabilities)
        if low_altitude_block:
            return low_altitude_block

        obstacle_block = self._obstacle_guard(tool, params, capabilities)
        if obstacle_block:
            return obstacle_block

        result = self.tools.execute(
            tool,
            params,
            dry_run=False,
            blocked_by_supervisor=self.supervisor.is_emergency_stopped(),
        )
        self._remember_visual_frame_from_payload(result.data, source=tool, params=params)
        return result

    def _low_altitude_motion_guard(
        self,
        tool: str,
        params: dict[str, Any],
        runtime: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        if tool != "drone_move_relative" or not capabilities.get("flight_control"):
            return None
        try:
            forward = abs(float(params.get("forward_m", 0.0) or 0.0))
            right = abs(float(params.get("right_m", 0.0) or 0.0))
            up = float(params.get("up_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if (forward * forward + right * right) ** 0.5 < 0.15:
            return None
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        altitude = self._vehicle_altitude_m(drone)
        if altitude is None:
            return None
        projected_altitude = altitude + max(0.0, up)
        if bool(drone.get("flying")) and projected_altitude >= 1.5:
            return None
        return self._blocked_tool_result(
            tool,
            params,
            (
                "horizontal relative movement is blocked below safe altitude; "
                f"current altitude is {altitude:.2f} m. Take off to at least 3 m before moving horizontally."
            ),
        )

    def _execute_memory_recall(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        query = str(params.get("query") or "").strip()
        limit = 5
        try:
            limit = max(1, min(10, int(params.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        results = [] if dry_run else self.memory.recall(query, limit=limit)
        data = {"status": "planned" if dry_run else "ok", "query": query, "count": len(results), "results": results}
        return ToolCallResult("memory_recall", dict(params), True, data, started, time.time())

    def _execute_memory_remember(self, params: dict[str, Any], dry_run: bool = False) -> ToolCallResult:
        started = time.time()
        key = str(params.get("key") or "").strip()
        value = str(params.get("value") or "").strip()
        if not key:
            return ToolCallResult(
                "memory_remember",
                dict(params),
                False,
                {"status": "error", "message": "memory_remember requires a non-empty 'key'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        tags = params.get("tags")
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",") if item.strip()]
        if not dry_run:
            self.memory.remember_fact(key, value, tags)
        data = {"status": "planned" if dry_run else "ok", "key": key, "message": f"fact '{key}' stored"}
        return ToolCallResult("memory_remember", dict(params), True, data, started, time.time())

    def _execute_sub_agent_tool(self, params: dict[str, Any], dry_run: bool = False, run: RunState | None = None) -> ToolCallResult:
        """Delegate an open-ended subtask to a bounded sub-agent.

        Runs synchronously in the caller's thread, so the execution slot and
        approval context are shared with the parent loop. The sub-agent gets
        its own run log and step budget; the parent only receives the report.
        """
        started = time.time()
        goal = str(params.get("goal") or "").strip()
        if not goal:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                False,
                {"status": "error", "message": "agent_subtask requires a non-empty 'goal'"},
                started,
                time.time(),
                error_code="INVALID_PARAMS",
            )
        max_steps = 6
        try:
            max_steps = max(2, min(12, int(params.get("max_steps") or 6)))
        except (TypeError, ValueError):
            max_steps = 6
        model_id = str(params.get("model_id") or "").strip() or (run.model_id if run else "") or ""
        if dry_run:
            return ToolCallResult(
                "agent_subtask",
                dict(params),
                True,
                {"status": "planned", "goal": goal, "message": "dry run only"},
                started,
                time.time(),
            )
        tool_runtime = self.tools.status_snapshot()
        capabilities = ((tool_runtime.get("backend_profile") or {}).get("capabilities")) or {}
        tool_cards = self.tools.list_tool_cards()
        parent_run_id = run.run_id if run else f"run_{int(time.time() * 1000)}"
        runner = SubAgentRunner(
            tools=self.tools,
            planner=self.planner,
            memory=self.memory,
            execute_tool=lambda name, sub_params, sub_dry: self._execute_agent_tool(name, sub_params, dry_run=sub_dry, run=run),
            should_stop=lambda: self.supervisor.is_emergency_stopped() or self._cancel_requested.is_set(),
            should_pause=self.supervisor.should_pause,
            on_ui_event=self._on_agent_event,
            on_ui_state=self._on_agent_loop_state,
        )
        report = runner.run(
            parent_run_id,
            goal,
            constraints=str(params.get("constraints") or ""),
            tool_cards=tool_cards,
            capabilities=capabilities,
            model_id=model_id or None,
            max_steps=max_steps,
        )
        ok = report.get("status") == "completed"
        error_code = "" if ok else ("BLOCKED" if report.get("status") == "blocked" else "TOOL_ERROR")
        return ToolCallResult(
            "agent_subtask",
            dict(params),
            ok,
            {"status": "ok" if ok else "failed", **report},
            started,
            time.time(),
            error_code=error_code,
        )

    def _execute_skill_tool(
        self,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()

        def governed(subtool: str, subparams: dict[str, Any], sub_dry_run: bool) -> ToolCallResult:
            return self._execute_agent_tool(subtool, subparams, dry_run=sub_dry_run, run=run)

        result = self.skills.execute(
            tool,
            params,
            self.tools,
            dry_run=dry_run,
            execute_tool=governed,
        )
        data = result.to_dict()
        self._remember_visual_frame_from_payload(data, source=tool, params=params)
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=result.ok,
            data=data,
            started_at=started,
            finished_at=time.time(),
        )

    def _tool_risk_level(
        self,
        tool: str,
        capabilities: dict[str, Any],
        run: RunState | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        card = TOOL_CARDS.get(tool)
        card_risk = str(card.risk if card else "low")
        if run and run.route_strategy == "direct" and run.plan and any(step.tool == tool for step in run.plan.steps):
            route_risk = {
                "safe": "low",
                "elevated": "medium",
                "high": "high",
            }.get(str(run.risk_level), str(run.risk_level))
            risk = self._max_risk(route_risk, card_risk)
        else:
            risk = card_risk
        if tool == "drone_land" and capabilities.get("real_vehicle"):
            return "high"
        if tool == "formation_command" and capabilities.get("real_vehicle"):
            action = str((params or {}).get("action") or "status")
            if action in {"set_drones", "set_formation"} or action in FORMATION_FLIGHT_ACTIONS:
                return "high"
        return risk if risk in {"low", "medium", "high"} else "medium"

    def _max_risk(self, first: str, second: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        first = first if first in order else "medium"
        second = second if second in order else "medium"
        return first if order[first] >= order[second] else second

    def _obstacle_guard(
        self,
        tool: str,
        params: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> ToolCallResult | None:
        guarded_tools = {"drone_move_relative"}
        if tool not in guarded_tools or not capabilities.get("obstacle_avoidance"):
            return None

        collector = getattr(self.tools, "collector", None)
        available_tools = getattr(collector, "tools", {}) if collector else {}
        if "provider_validate_motion" not in available_tools:
            return None

        validation_params = {
            "forward_m": float(params.get("forward_m", 0.0) or 0.0),
            "right_m": float(params.get("right_m", 0.0) or 0.0),
            "up_m": float(params.get("up_m", 0.0) or 0.0),
            "velocity": float(params.get("velocity", 1.0) or 1.0),
            "max_age_sec": 1.0,
        }
        check = self.tools.execute(
            "provider_validate_motion",
            validation_params,
            dry_run=False,
            allow_reconnect=False,
        )
        if check.ok:
            return None

        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={
                "status": "blocked",
                "message": (
                    "motion blocked by obstacle provider: "
                    f"{check.data.get('message') or check.data.get('status') or 'unsafe motion'}"
                ),
                "provider_check": check.to_dict(),
            },
            started_at=now,
            finished_at=now,
        )

    def _blocked_tool_result(self, tool: str, params: dict[str, Any], message: str) -> ToolCallResult:
        now = time.time()
        return ToolCallResult(
            tool=tool,
            params=dict(params),
            ok=False,
            data={"status": "blocked", "message": message},
            started_at=now,
            finished_at=now,
            error_code="BLOCKED",
        )

    def _execute_vlm_confirm_tool(
        self,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()
        target = str(
            params.get("target_description")
            or params.get("target_class")
            or params.get("verify_target_class")
            or (run.command if run else "")
            or "目标"
        ).strip()
        if dry_run:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                True,
                {"status": "planned", "message": f"dry run only: confirm target '{target}' with VLM"},
                started,
                time.time(),
            )

        image_base64 = str(params.get("image_base64") or "").strip()
        source = str(params.get("source") or "last_image").strip().lower()
        context = dict(params.get("context") or {}) if isinstance(params.get("context"), dict) else {}
        with self._lock:
            last_frame = dict(self._last_visual_frame)
        if not image_base64 and source in {"", "last_image", "latest", "latest_image"}:
            image_base64 = str(last_frame.get("image_base64") or "")
            context.setdefault("image_source", last_frame.get("source_tool", "last_image"))
            context.setdefault("image_saved_to", last_frame.get("image_saved_to", ""))
            context.setdefault("visual_metadata", last_frame.get("metadata", {}))
        if not image_base64:
            image_path = str(params.get("image_path") or params.get("saved_to") or last_frame.get("image_saved_to") or "")
            image_base64 = self._read_image_base64(image_path)
            if image_path:
                context.setdefault("image_saved_to", image_path)
        if not image_base64:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {
                    "status": "error",
                    "message": "no image available for VLM confirmation; capture/search an image first or pass image_base64",
                },
                started,
                time.time(),
            )

        try:
            confirmation = self.planner.confirm_target_in_image(
                target_description=target,
                image_base64=image_base64,
                context={
                    **context,
                    "agent_state": self._agent_state_context(),
                    "operator_command": run.command if run else "",
                },
                model_id=(run.model_id if run and run.model_id else str(params.get("model_id") or "") or None),
            )
            data = {
                **confirmation,
                "message": confirmation.get("summary_zh", ""),
                "source": context.get("image_source") or source or "last_image",
                "image_saved_to": context.get("image_saved_to", ""),
            }
            if not dry_run:
                violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS.get("airsim_vlm_confirm_target"))
                if violations:
                    return ToolCallResult(
                        "airsim_vlm_confirm_target",
                        params,
                        False,
                        {**data, "status": "error", "validation_errors": violations, "message": "VLM confirmation output failed shape validation"},
                        started,
                        time.time(),
                        error_code="INVALID_TOOL_OUTPUT",
                    )
            return ToolCallResult("airsim_vlm_confirm_target", params, True, data, started, time.time())
        except LLMUnavailableError as exc:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {"status": "error", "message": str(exc), "target_description": target},
                started,
                time.time(),
            )
        except Exception as exc:
            return ToolCallResult(
                "airsim_vlm_confirm_target",
                params,
                False,
                {"status": "error", "message": f"VLM target confirmation failed: {exc}", "target_description": target},
                started,
                time.time(),
            )

    def _execute_vlm_analyze_tool(
        self,
        params: dict[str, Any],
        dry_run: bool = False,
        run: RunState | None = None,
    ) -> ToolCallResult:
        started = time.time()
        question = str(params.get("question") or params.get("prompt") or (run.command if run else "") or "").strip()
        if dry_run:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                True,
                {"status": "planned", "message": "dry run only: analyze latest image with VLM"},
                started,
                time.time(),
            )

        image_base64, context = self._image_for_vlm(params)
        if not image_base64:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {
                    "status": "error",
                    "message": "no image available for VLM analysis; capture/search an image first or pass image_base64",
                },
                started,
                time.time(),
            )

        try:
            analysis = self.planner.analyze_image(
                question=question,
                image_base64=image_base64,
                context={
                    **context,
                    "agent_state": self._agent_state_context(),
                    "operator_command": run.command if run else "",
                },
                model_id=(run.model_id if run and run.model_id else str(params.get("model_id") or "") or None),
            )
            data = {
                **analysis,
                "source": context.get("image_source") or str(params.get("source") or "last_image"),
                "image_saved_to": context.get("image_saved_to", ""),
            }
            if not dry_run:
                violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS.get("airsim_vlm_analyze_image"))
                if violations:
                    return ToolCallResult(
                        "airsim_vlm_analyze_image",
                        params,
                        False,
                        {**data, "status": "error", "validation_errors": violations, "message": "VLM analysis output failed shape validation"},
                        started,
                        time.time(),
                        error_code="INVALID_TOOL_OUTPUT",
                    )
            return ToolCallResult("airsim_vlm_analyze_image", params, True, data, started, time.time())
        except LLMUnavailableError as exc:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {"status": "error", "message": str(exc), "question": question},
                started,
                time.time(),
            )
        except Exception as exc:
            return ToolCallResult(
                "airsim_vlm_analyze_image",
                params,
                False,
                {"status": "error", "message": f"VLM image analysis failed: {exc}", "question": question},
                started,
                time.time(),
            )

    def _image_for_vlm(self, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        image_base64 = str(params.get("image_base64") or "").strip()
        source = str(params.get("source") or "last_image").strip().lower()
        context = dict(params.get("context") or {}) if isinstance(params.get("context"), dict) else {}
        with self._lock:
            last_frame = dict(self._last_visual_frame)
        if not image_base64 and source in {"", "last_image", "latest", "latest_image"}:
            image_base64 = str(last_frame.get("image_base64") or "")
            context.setdefault("image_source", last_frame.get("source_tool", "last_image"))
            context.setdefault("image_saved_to", last_frame.get("image_saved_to", ""))
            context.setdefault("visual_metadata", last_frame.get("metadata", {}))
        if not image_base64:
            image_path = str(params.get("image_path") or params.get("saved_to") or last_frame.get("image_saved_to") or "")
            image_base64 = self._read_image_base64(image_path)
            if image_path:
                context.setdefault("image_saved_to", image_path)
        return image_base64, context

    def _remember_visual_frame_from_payload(
        self,
        payload: dict[str, Any] | None,
        source: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return
        image_base64 = self._find_visual_value(payload, {"image_base64"})
        image_saved_to = self._find_visual_value(payload, {"image_saved_to", "saved_to", "approach_image_saved_to"})
        if not image_base64 and image_saved_to:
            image_base64 = self._read_image_base64(str(image_saved_to))
        if not image_base64:
            return
        metadata = self._visual_metadata(payload)
        with self._lock:
            self._last_visual_frame = {
                "source_tool": source,
                "params": dict(params or {}),
                "image_base64": str(image_base64),
                "image_saved_to": str(image_saved_to or ""),
                "metadata": metadata,
                "updated_at": time.time(),
            }

    def _find_visual_value(self, value: Any, keys: set[str], _depth: int = 0) -> Any:
        if _depth > 24:
            return None
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item:
                    return item
            for nested in value.values():
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in reversed(value):
                found = self._find_visual_value(nested, keys, _depth + 1)
                if found:
                    return found
        return None

    def _read_image_base64(self, image_path: str) -> str:
        if not image_path:
            return ""
        try:
            path = Path(image_path).expanduser()
            if not path.exists() or not path.is_file():
                return ""
            if path.stat().st_size > 12 * 1024 * 1024:
                return ""
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            return ""

    def _visual_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {
            "status",
            "message",
            "target",
            "target_class",
            "target_description",
            "vehicle",
            "camera",
            "image_type",
            "selected_view",
            "current_position",
            "search_progress",
            "detections",
            "target_world_position",
            "target_depth_meters",
            "target_distance_meters",
            "task_id",
        }
        metadata = {key: payload.get(key) for key in keep_keys if key in payload}
        task = payload.get("task")
        if isinstance(task, dict):
            result = task.get("result")
            if isinstance(result, dict):
                metadata["task_result"] = {
                    key: result.get(key)
                    for key in keep_keys
                    if key in result and key not in {"image_base64"}
                }
        return metadata

    def execute_tool(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        result = self._execute_agent_tool(tool, params or {}, dry_run=dry_run)
        self.memory.remember_tool_call(tool, result.ok)
        self._remember_position_from_payload(result.data, source=tool)
        self._append_event("info" if result.ok else "warning", "tool", f"工具调用: {tool}", result.to_dict())
        return {"ok": result.ok, "result": result.to_dict()}

    def capture_camera_frame(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capture a UI stream frame without polluting task history or tool statistics."""
        result = self._execute_agent_tool("airsim_take_photo", params or {}, dry_run=False)
        return {"ok": result.ok, "result": result.to_dict()}

    def camera_preview_frame(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight camera preview frame for the frontend."""
        return self.tools.capture_camera_preview(params or {})

    # ------------------------------------------------------------------
    # P6: GCS MissionManager facade. UI and Agent both call these methods
    # so mission data flows through a single backend-neutral boundary.
    # ------------------------------------------------------------------

    def gcs_mission_get(self) -> dict[str, Any]:
        """Return the current local draft and mission progress."""
        mission = self.gcs.mission
        draft = mission.get_draft()
        progress = mission.progress()
        return {
            "ok": True,
            "draft": draft.to_dict() if draft else None,
            "progress": progress.to_dict(),
        }

    def gcs_mission_set(self, draft_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Replace the local mission draft with ``draft_data``."""
        from src.gcs.mission import MissionPlanDraft

        if not isinstance(draft_data, dict):
            return {"ok": False, "error": "draft payload must be an object"}
        try:
            draft = MissionPlanDraft.from_dict(draft_data)
        except Exception as e:
            return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.set_draft(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "本地任务草稿已更新" if result.ok else "本地任务草稿更新失败",
            {"items": len(draft.items), "result": result.to_dict()},
        )
        return {"ok": result.ok, "result": result.to_dict(), "draft": draft.to_dict()}

    def gcs_mission_download(self, expected_backend: str = "") -> dict[str, Any]:
        """Download the active vehicle mission into a local draft."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        draft = self.gcs.mission.download()
        progress = self.gcs.mission.progress()
        ok = draft is not None
        self._append_event(
            "info" if ok else "warning",
            "gcs.mission",
            "飞控任务已下载" if ok else "飞控任务下载失败或为空",
            {"items": len(draft.items) if draft else 0},
        )
        return {
            "ok": ok,
            "draft": draft.to_dict() if draft else None,
            "progress": progress.to_dict(),
        }

    def gcs_mission_upload(
        self,
        draft_data: dict[str, Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """Upload the current or provided draft to the active vehicle."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        from src.gcs.mission import MissionPlanDraft

        draft = None
        if isinstance(draft_data, dict) and draft_data:
            try:
                draft = MissionPlanDraft.from_dict(draft_data)
            except Exception as e:
                return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.upload(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "任务已上传至飞控" if result.ok else "任务上传失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_start(
        self,
        draft_data: dict[str, Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """Start the uploaded mission, optionally replacing/uploading the draft first."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        from src.gcs.mission import MissionPlanDraft

        draft = None
        if isinstance(draft_data, dict) and draft_data:
            try:
                draft = MissionPlanDraft.from_dict(draft_data)
            except Exception as e:
                return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.start(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "任务已启动" if result.ok else "任务启动失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_clear(self, expected_backend: str = "") -> dict[str, Any]:
        """Clear the active vehicle mission and local draft."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        result = self.gcs.mission.clear()
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "飞控任务已清空" if result.ok else "飞控任务清空失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_progress(self) -> dict[str, Any]:
        """Return mission execution progress."""
        progress = self.gcs.mission.progress()
        return {"ok": True, "progress": progress.to_dict()}

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(SESSIONS_DIR.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data.get("id", path.stem),
                    "name": data.get("name", "未命名对话"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
        return sessions

    def session_history(self, session_id: str) -> dict[str, Any]:
        """Return the complete persisted session without changing the active session."""
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"failed to load session: {exc}"}
        return {"ok": True, "session": data}

    def create_session(self, name: str = "") -> dict[str, Any]:
        now = time.time()
        session_id = f"session_{int(now * 1000)}"
        session = {
            "id": session_id,
            "name": name.strip() or "新对话",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        self._save_session(session)
        self._current_session_id = session_id
        with self._lock:
            self._messages.clear()
            self._current = None
        self._publish("snapshot", self.state())
        return {"ok": True, "session": session}

    def load_session(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"failed to load session: {e}"}

        messages = []
        for raw in data.get("messages", []):
            if not isinstance(raw, dict):
                continue
            messages.append(ChatMessage(
                id=str(raw.get("id", f"msg_{int(time.time() * 1000)}_")),
                role=str(raw.get("role", "assistant")),
                content=str(raw.get("content", "")),
                attachments=list(raw.get("attachments") or []),
                run_id=str(raw.get("run_id", "")),
                status=str(raw.get("status", "complete")),
                details=raw.get("details") or {},
                created_at=float(raw.get("created_at", time.time())),
                updated_at=float(raw.get("updated_at", time.time())),
            ))

        with self._lock:
            self._current_session_id = session_id
            self._messages = messages
            self._current = None
            changed = self._mark_orphan_running_messages_locked()
        if changed:
            self._persist_current_session()
        self._publish("snapshot", self.state())
        return {"ok": True, "session": data}

    def rename_session(self, session_id: str, name: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["name"] = name.strip() or data.get("name", "未命名对话")
            data["updated_at"] = time.time()
            self._save_session(data)
            self._publish("snapshot", self.state())
            return {"ok": True, "session": data}
        except Exception as e:
            return {"ok": False, "error": f"rename failed: {e}"}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            path.unlink()
            if self._current_session_id == session_id:
                self._load_or_create_default_session()
            else:
                self._publish("snapshot", self.state())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"delete failed: {e}"}

    def export_session(self, session_id: str, export_format: str = "markdown") -> dict[str, Any]:
        """Export the complete persisted conversation without the model context filter."""
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"failed to read session: {exc}"}

        clean_format = str(export_format or "markdown").strip().lower()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(data.get("name") or session_id)).strip("_") or session_id
        if clean_format == "json":
            content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            return {
                "ok": True,
                "filename": f"{safe_name}.json",
                "content_type": "application/json; charset=utf-8",
                "content": content,
            }

        lines = [
            f"# {data.get('name') or 'Conversation'}",
            "",
            f"- Session: `{data.get('id') or session_id}`",
            f"- Messages: {len(data.get('messages') or [])}",
            "",
        ]
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "assistant").capitalize()
            lines.extend((f"## {role}", "", str(message.get("content") or ""), ""))
            attachments = message.get("attachments") or []
            if attachments:
                lines.extend(("Attachments:", ""))
                for item in attachments:
                    if isinstance(item, dict):
                        label = item.get("name") or item.get("filename") or item.get("url") or "attachment"
                    else:
                        label = str(item)
                    lines.append(f"- {label}")
                lines.append("")
        return {
            "ok": True,
            "filename": f"{safe_name}.md",
            "content_type": "text/markdown; charset=utf-8",
            "content": "\n".join(lines).rstrip() + "\n",
        }

    def _load_or_create_default_session(self) -> None:
        sessions = self.list_sessions()
        if sessions:
            self.load_session(sessions[0]["id"])
            return
        self.create_session("新对话")

    def _persist_current_session(self) -> None:
        if not self._current_session_id:
            return
        path = self._session_path(self._current_session_id)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                data = {"id": self._current_session_id, "name": "新对话", "created_at": time.time()}
            with self._lock:
                data["messages"] = [m.to_dict() for m in self._messages]
            data["updated_at"] = time.time()
            self._save_session(data)
        except Exception:
            pass

    def _save_session(self, data: dict[str, Any]) -> None:
        path = self._session_path(data["id"])
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _session_path(self, session_id: str) -> Path:
        return SESSIONS_DIR / f"{session_id}.json"

    def state(self) -> dict[str, Any]:
        with self._lock:
            orphan_changed = self._mark_orphan_running_messages_locked()
            events = [e.to_dict() for e in self._events[-80:]]
            messages = [self._message_public_dict(m) for m in self._messages[-80:]]
            current = self._run_public_dict(self._current) if self._current else None
            pending_approvals = [req.to_dict() for req in self._pending_approvals.values()]
        if orphan_changed:
            self._persist_current_session()

        tool_runtime = self.tools.status_snapshot()
        agent_state = self._agent_state_context(tool_runtime)
        gcs_state = self.gcs.state().to_dict()
        agent_skill_cards = self.agent_loop.skills.guidance_cards(
            "",
            gcs_state.get("capabilities") or {},
            memory=self.memory.snapshot(),
        )

        return {
            "runtime": {
                "status": current["status"] if current else "idle",
                "time": time.time(),
            },
            "supervisor": self.supervisor.get_status(),
            "tool_runtime": tool_runtime,
            "agent_state": agent_state,
            "gcs": gcs_state,
            "agent_skills": agent_skill_cards,
            "llm": self.planner.status(),
            "current_run": current,
            "messages": messages,
            "events": events,
            "memory": self._memory_state(),
            "task_runs": self.task_runs.snapshot(),
            "tools": self.tools.list_tools(),
            "sessions": self.list_sessions(),
            "current_session": self._get_current_session_summary(),
            # P5: pending high-risk approvals (real vehicle only)
            "pending_approvals": pending_approvals,
        }

    def telemetry_state(self) -> dict[str, Any]:
        """Return the lightweight frame used by the flight HUD and map."""
        with self._lock:
            current = self._run_public_dict(self._current) if self._current else None
        return {
            "ok": True,
            "runtime": {
                "status": current["status"] if current else "idle",
                "time": time.time(),
            },
            "supervisor": self.supervisor.get_status(),
            "tool_runtime": self.tools.status_snapshot(),
            "current_run": current,
            "llm": self.planner.status(),
        }

    def _memory_state(self) -> dict[str, Any]:
        memory = self.memory.snapshot()
        with self._lock:
            message_count = len(self._messages)
        memory["conversation"] = {
            "session_id": self._current_session_id,
            "messages_saved": message_count,
            **self._conversation_context_usage(),
        }
        memory["scope"] = {
            "conversation": "per_session",
            "working_state": "global_runtime",
            "missions_lessons_risks": "global_persistent",
            "task_runs": "persistent_replay",
            "events": "process_only",
        }
        memory["task_runs"] = self.task_runs.snapshot(limit=6)
        return memory

    def _conversation_context_usage(self) -> dict[str, Any]:
        model = self.planner.registry.get_default() or {}
        public = self.planner.registry._public_model(model) if model else {}
        context_window = int(public.get("context_window") or 64_000)
        context = self._recent_chat_context(limit=None)
        estimated_tokens = sum(
            max(1, math.ceil(len(str(item.get("content") or "")) / 4))
            for item in context
        )
        return {
            "messages_sent_to_model": len(context),
            "session_message_limit": None,
            "history_policy": "full_session_saved_recent_context_selected_by_token_budget",
            "estimated_context_tokens": estimated_tokens,
            "context_window": context_window,
            "context_percent": round(min(100.0, estimated_tokens / max(1, context_window) * 100.0), 2),
            "model_id": str(public.get("id") or ""),
        }

    def _get_current_session_summary(self) -> dict[str, Any] | None:
        if not self._current_session_id:
            return None
        path = self._session_path(self._current_session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "id": data.get("id", self._current_session_id),
                "name": data.get("name", "未命名对话"),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(data.get("messages", [])),
            }
        except Exception:
            return None

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=300)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _simulate_plan(self, run: RunState) -> None:
        total = max(1, len(run.plan.steps if run.plan else []))
        if not run.plan:
            return
        run.phase = "planning"
        for index, step in enumerate(run.plan.steps, 1):
            result = self.tools.execute(step.tool, step.params, dry_run=True)
            step.status = "planned" if result.ok else "blocked"
            step.result = result.data
            step.safety = result.safety
            run.progress = index / total * 100
        run.status = "planned"
        run.phase = "planned"
        run.finished_at = time.time()

    def _begin_execution_trace(self, run: RunState, content: str = "") -> None:
        if not run.plan:
            return
        run.phase = "executing" if run.execute else "planning"
        if content:
            self._append_thought(run, "思考", content)
            self._append_process(run, "理解任务", content, status="completed")
        else:
            overview = self._thought_overview(run)
            self._append_thought(run, "理解任务", overview)
            self._append_process(run, "理解任务", overview, status="completed")
        tools = " → ".join(step.tool for step in run.plan.steps if step.tool and step.tool != "memory_store")
        if tools:
            self._append_thought(run, "工具选择", tools)
            self._append_process(run, "选择工具", tools, status="completed")
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._publish_run_update(run)
        self._frontend_render_grace()

    def _update_execution_trace_for_step(
        self,
        run: RunState,
        step: MissionStep,
        index: int,
        total: int,
    ) -> None:
        if step.tool == "memory_store":
            return
        self._append_thought(
            run,
            f"调用 {step.tool}",
            f"{index}/{total} · {step.label}",
            status="running",
        )
        self._append_process(
            run,
            self._tool_action_label(step.tool),
            f"{index}/{total} · {step.label}",
            status="running",
            tool=step.tool,
            params=step.params,
            kind="tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )
        self._frontend_render_grace(0.08)

    def _update_execution_trace_after_step(
        self,
        run: RunState,
        step: MissionStep,
        ok: bool,
    ) -> None:
        if step.tool == "memory_store":
            return
        message = ""
        if isinstance(step.result, dict):
            message = str(step.result.get("message") or step.result.get("status") or "")
        self._append_thought(
            run,
            f"{step.tool} {'完成' if ok else '失败'}",
            message,
            status="completed" if ok else "failed",
        )
        self._append_process(
            run,
            self._tool_action_label(step.tool),
            message or ("完成" if ok else "失败"),
            status="completed" if ok else "failed",
            tool=step.tool,
            params=step.params,
            kind="tool",
        )
        self._update_assistant_message(
            run.run_id,
            self._progress_message(run),
            "running",
            self._message_details(run),
        )

    def _append_thought(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
    ) -> None:
        run.thought_trace.append(
            {
                "timestamp": time.time(),
                "title": title,
                "body": body,
                "status": status,
            }
        )

    def _append_process(
        self,
        run: RunState,
        title: str,
        body: str = "",
        status: str = "completed",
        tool: str = "",
        params: dict[str, Any] | None = None,
        kind: str = "",
    ) -> None:
        body = self._compact_process_text(body)
        item_kind = kind or ("tool" if tool else "reasoning")
        if status in {"running", "completed", "failed", "blocked"}:
            for item in reversed(run.process_trace):
                same_item = item.get("tool") == tool if tool else item.get("title") == title
                if same_item and item.get("status") == "running":
                    item.update(
                        {
                            "timestamp": time.time(),
                            "title": title,
                            "body": body,
                            "status": status,
                            "params": dict(params or {}),
                            "kind": item_kind,
                        }
                    )
                    return
        run.process_trace.append(
            {
                "timestamp": time.time(),
                "title": title,
                "body": body,
                "status": status,
                "tool": tool,
                "params": dict(params or {}),
                "kind": item_kind,
            }
        )
        run.process_trace = run.process_trace[-80:]

    @staticmethod
    def _compact_process_text(text: str, limit: int = 6000) -> str:
        value = str(text or "")
        if len(value) <= limit:
            return value
        return "...\n" + value[-limit:]

    def _tool_action_label(self, tool: str) -> str:
        labels = {
            "drone_connect": "Connect flight link",
            "drone_disconnect": "Disconnect flight link",
            "drone_list_vehicles": "List vehicles",
            "drone_get_status": "Read vehicle status",
            "drone_arm": "Arm motors",
            "drone_disarm": "Disarm motors",
            "drone_takeoff": "Take off",
            "drone_land": "Land",
            "drone_hover": "Hold position",
            "drone_fly_to": "Fly to local coordinate",
            "drone_move_relative": "Move in body frame",
            "drone_fly_velocity": "Fly by velocity",
            "drone_fly_path": "Fly waypoint path",
            "drone_upload_mission": "Upload mission",
            "drone_download_mission": "Download mission",
            "drone_clear_mission": "Clear mission",
            "drone_start_mission": "Start mission",
            "drone_get_mission_progress": "Read mission progress",
            "drone_rotate_to": "Rotate heading",
            "drone_set_mode": "Set flight mode",
            "airsim_take_photo": "Capture image",
            "airsim_get_sensors": "Read sensors",
            "airsim_get_depth_map": "Read depth map",
            "airsim_detect_objects": "Detect objects",
            "airsim_vlm_analyze_image": "Analyze camera image",
            "airsim_vlm_confirm_target": "Confirm visual target",
            "provider_bridge_health": "Check provider bridge",
            "provider_obstacle_summary": "Read obstacle provider",
            "provider_validate_motion": "Validate motion provider",
            "airsim_task_status": "Read legacy task status",
            "airsim_task_cancel": "Cancel legacy task",
            "memory_store": "Store mission memory",
        }
        return labels.get(tool, tool.replace("_", " "))

    def _thought_overview(self, run: RunState) -> str:
        if not run.plan:
            return run.route_reason or "正在整理任务上下文。"
        reasoning = (run.plan.reasoning or "").strip()
        if reasoning:
            return reasoning
        if run.route_strategy == "direct" and run.plan.steps:
            tool = run.plan.steps[0].tool
            return f"这是一个明确的单步飞控意图，我直接选择 {tool}，随后用遥测回读确认结果。"
        if run.route_strategy == "template":
            return "这是结构清晰的飞行任务，我先形成可审计的工具序列，再逐步执行并校验状态。"
        if run.route_strategy == "plan_execute":
            return "这是短序列飞行任务，我采用一次性规划执行：LLM 先给出完整工具序列，runtime 逐步执行并校验，失败时再进入 Agent Loop 纠错。"
        if run.route_reason:
            return run.route_reason
        return run.plan.summary or "正在整理任务上下文。"

    def _frontend_render_grace(self, seconds: float = 0.15) -> None:
        with self._lock:
            has_subscribers = bool(self._subscribers)
        if has_subscribers:
            time.sleep(max(0.0, seconds))

    def _run_plan(self, run: RunState, finalize: bool = True, remember: bool = True) -> None:
        if not run.plan:
            return
        run.status = "running"
        run.phase = "executing"
        total = max(1, len(run.plan.steps))
        ok_count = 0
        preapproved = self._preapprove_first_high_risk_tool(run)
        if not (preapproved and preapproved.get("approved") is False):
            self._capture_start_telemetry(run)

        for index, step in enumerate(run.plan.steps, 1):
            if preapproved and preapproved.get("approved") is False:
                break
            while self.supervisor.should_pause() and not self.supervisor.is_emergency_stopped():
                run.status = "paused"
                run.phase = "paused"
                run.current_step = step.id
                time.sleep(0.2)

            if self.supervisor.is_emergency_stopped():
                run.status = "blocked"
                run.phase = "blocked"
                run.failure_reason = "emergency stop"
                break

            run.status = "running"
            run.phase = "executing"
            run.current_step = step.id
            step.status = "running"
            self._publish_run_update(run)
            self._append_event(
                "info",
                step.layer,
                f"执行步骤 {step.id}: {step.label}",
                {"tool": step.tool, "params": step.params},
            )
            self._update_execution_trace_for_step(run, step, index, total)

            result = self._maybe_skip_idempotent_step(step)
            if result is None:
                already_approved = bool(
                    preapproved
                    and preapproved.get("approved") is True
                    and preapproved.get("tool") == step.tool
                    and preapproved.get("params") == dict(step.params)
                )
                result = self._execute_agent_tool(
                    step.tool,
                    step.params,
                    dry_run=False,
                    run=run,
                    approval_already_granted=already_approved,
                )
            step.result = result.data
            step.safety = result.safety
            step.status = "completed" if result.ok else "failed"
            self._record_task_tool_result(run, step, result)
            self.memory.remember_tool_call(step.tool, result.ok)
            if not run.start_position_recorded:
                self._remember_task_start(run, result.data)
            self._remember_position_from_payload(result.data, source=step.tool)
            run.progress = index / total * 100
            self._publish_run_update(run)
            self._update_execution_trace_after_step(run, step, result.ok)

            if result.ok:
                ok_count += 1
                self._append_event("info", "tool", f"{step.tool} 完成", result.to_dict())
            else:
                run.status = "failed"
                run.phase = "failed"
                run.failure_reason = result.data.get("message", f"{step.tool} failed")
                self._publish_run_update(run)
                self._append_event("danger", "tool", f"{step.tool} 失败", result.to_dict())
                if step.tool not in {"drone_land", "drone_hover"}:
                    self.tools.execute("drone_hover", {}, dry_run=False)
                break

            run.progress = index / total * 100

        if run.status == "running":
            run.status = "completed"
            run.phase = "verifying"
            run.progress = 100.0

        run.finished_at = time.time()
        run.final_telemetry = dict(self.tools.status_snapshot().get("drone") or {})
        run.agent_state = self._agent_state_context()
        self._append_thought(run, "校验结果", "正在回读最终状态并核对任务目标。", status="running")
        self._append_process(run, "回读与校验", "正在回读最终状态并核对任务目标。", status="running", kind="verify")
        self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))
        self._publish_run_update(run)
        run.verification = self._verify_run_outcome(run)
        if run.status == "completed" and run.verification.get("level") == "failed":
            run.status = "failed"
            run.phase = "failed"
            run.failure_reason = run.verification.get("summary", "任务后状态校验失败")
            self._append_thought(run, "校验未通过", run.failure_reason, status="failed")
            self._append_process(run, "回读与校验", run.failure_reason, status="failed", kind="verify")
            self._append_event("warning", "verifier", "任务后状态校验失败", run.verification)
        elif run.verification:
            self._append_thought(run, "校验完成", str(run.verification.get("summary") or ""), status="completed")
            self._append_process(run, "回读与校验", str(run.verification.get("summary") or ""), status="completed", kind="verify")
            self._append_event("info", "verifier", "任务后状态校验完成", run.verification)
        if run.status == "completed":
            run.phase = "completed"
        if run.status == "completed":
            self._append_event("info", "memory", "任务闭环完成，写入经验")
        if remember:
            self._remember_plan_run(run, total=total, ok_count=ok_count)
        if finalize:
            self._finalize_assistant_response(run)

    def _remember_plan_run(self, run: RunState, total: int, ok_count: int) -> None:
        self.memory.remember_mission(
            {
                "run_id": run.run_id,
                "command": run.command,
                "intent": run.intent,
                "status": run.status,
                "summary": run.summary,
                "duration_sec": round((run.finished_at or time.time()) - run.started_at, 2),
                "steps_total": total,
                "steps_ok": ok_count,
                "failure_reason": run.failure_reason,
                "route_strategy": run.route_strategy,
                "tool_sequence": [step.tool for step in (run.plan.steps if run.plan else [])],
                "verification_status": run.verification.get("status", ""),
            }
        )

    def _maybe_skip_idempotent_step(self, step: MissionStep) -> ToolCallResult | None:
        """Skip already-satisfied setup steps in deterministic plans."""
        runtime = self.tools.status_snapshot()
        drone = runtime.get("drone") if isinstance(runtime.get("drone"), dict) else {}
        connected = bool(runtime.get("connected")) and not bool(runtime.get("stale_connection"))
        message = ""
        if step.tool == "drone_connect" and connected:
            message = "already connected"
        elif step.tool == "drone_arm" and bool(drone.get("armed")):
            message = "already armed"
        elif step.tool == "drone_takeoff" and self._is_takeoff_already_satisfied(drone, step.params):
            message = "already airborne near requested altitude"
        else:
            return None

        now = time.time()
        return ToolCallResult(
            tool=step.tool,
            params=dict(step.params),
            ok=True,
            data={
                "status": "ok",
                "message": f"{message}; skipped duplicate {step.tool}",
                "skipped": True,
                "drone": drone,
            },
            started_at=now,
            finished_at=now,
        )

    def _is_takeoff_already_satisfied(self, drone: dict[str, Any], params: dict[str, Any]) -> bool:
        if not isinstance(drone, dict):
            return False
        altitude = self._vehicle_altitude_m(drone)
        try:
            target = abs(float(params.get("altitude", 3.0) or 3.0))
        except (TypeError, ValueError):
            target = 3.0
        if altitude is None:
            return bool(drone.get("flying"))
        target = max(0.5, target)
        minimum = max(0.5, min(target * 0.85, target - 0.3 if target > 1.0 else target * 0.85))
        return bool(drone.get("flying")) and altitude >= minimum

    def _vehicle_altitude_m(self, drone: dict[str, Any]) -> float | None:
        for key in ("altitude_m", "altitude"):
            value = drone.get(key)
            if value is None:
                continue
            try:
                return abs(float(value))
            except (TypeError, ValueError):
                pass
        pos = drone.get("position_ned")
        if isinstance(pos, dict) and pos.get("z") is not None:
            try:
                return abs(float(pos.get("z")))
            except (TypeError, ValueError):
                return None
        return None

    def _preapprove_first_high_risk_tool(self, run: RunState) -> dict[str, Any] | None:
        if not run.plan or run.risk_level != "high":
            return None
        runtime = self.tools.status_snapshot()
        capabilities = (runtime.get("backend_profile") or {}).get("capabilities") or {}
        if not capabilities.get("requires_operator_approval"):
            return None
        for step in run.plan.steps:
            risk_level = self._tool_risk_level(step.tool, capabilities, run)
            if risk_level != "high":
                continue
            approved = self._await_tool_approval(
                run,
                step.tool,
                dict(step.params),
                risk_level,
                reason=self._approval_reason(step.tool, dict(step.params)),
            )
            return {
                "approved": approved,
                "tool": step.tool,
                "params": dict(step.params),
                "risk_level": risk_level,
            }
        return None

    def _append_message(
        self,
        role: str,
        content: str,
        run_id: str = "",
        status: str = "complete",
        details: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        now = time.time()
        if role == "assistant" and run_id:
            updated_message: ChatMessage | None = None
            updated_payload: dict[str, Any] | None = None
            with self._lock:
                for existing in reversed(self._messages):
                    if existing.role == "assistant" and existing.run_id == run_id:
                        existing.content = content
                        existing.status = status
                        existing.details = details or existing.details
                        existing.updated_at = now
                        updated_message = existing
                        updated_payload = self._message_public_dict(existing)
                        self._dedupe_assistant_run_messages_locked(run_id, existing.id)
                        break
            if updated_message and updated_payload:
                self._publish("message_update", updated_payload)
                self._persist_current_session()
                return updated_message
        message = ChatMessage(
            id=f"msg_{int(now * 1000)}_{len(self._messages) + 1}",
            role=role,
            content=content,
            attachments=list(attachments or []),
            run_id=run_id,
            status=status,
            details=details or {},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._messages.append(message)
        self._publish("message_create", self._message_public_dict(message))
        self._persist_current_session()
        return message

    def _update_assistant_message(
        self,
        run_id: str,
        content: str,
        status: str,
        details: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        updated = None
        with self._lock:
            # 优先按 run_id 精确匹配
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    message.content = content
                    message.status = status
                    message.details = details or message.details
                    message.updated_at = time.time()
                    updated = self._message_public_dict(message)
                    self._dedupe_assistant_run_messages_locked(run_id, message.id)
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not updated:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        message.content = content
                        message.status = status
                        message.run_id = run_id
                        message.details = details or message.details
                        message.updated_at = time.time()
                        updated = self._message_public_dict(message)
                        self._dedupe_assistant_run_messages_locked(run_id, message.id)
                        break
        if updated:
            self._publish("message_update", updated)
            # persist=False 时跳过磁盘写入，避免 reasoning token 逐个触发全量 IO
            if persist:
                self._persist_current_session()
            return
        self._append_message("assistant", content, run_id=run_id, status=status, details=details)

    def _dedupe_assistant_run_messages_locked(self, run_id: str, keep_id: str) -> bool:
        if not run_id:
            return False
        before = len(self._messages)
        self._messages = [
            message
            for message in self._messages
            if not (
                message.role == "assistant"
                and message.run_id == run_id
                and message.id != keep_id
            )
        ]
        return len(self._messages) != before

    def _mark_orphan_running_messages_locked(self) -> bool:
        active_run_id = self._current.run_id if self._current else ""
        live_statuses = {"running", "queued", "planned", "responding", "awaiting_approval"}
        now = time.time()
        startup_grace_sec = 30.0
        changed = False
        seen_assistant_runs: set[str] = set()
        for message in list(reversed(self._messages)):
            if message.role != "assistant" or not message.run_id:
                continue
            if message.run_id in seen_assistant_runs:
                self._messages.remove(message)
                changed = True
                continue
            seen_assistant_runs.add(message.run_id)
            if message.status in live_statuses and message.run_id != active_run_id:
                message_age = now - max(float(message.updated_at or 0.0), float(message.created_at or 0.0))
                mode = str((message.details or {}).get("mode") or "").lower()
                if mode == "chat" and message.run_id in self._active_chat_requests:
                    continue
                created_in_this_process = float(message.created_at or 0.0) >= self._started_at - 1.0
                if mode == "chat" and created_in_this_process and message_age < 300.0:
                    continue
                if not active_run_id and created_in_this_process and message_age < startup_grace_sec:
                    continue
                if not str(message.content or "").strip():
                    message.content = "任务进程已中断或服务已重启，请重新执行该指令。"
                message.status = "error"
                details = dict(message.details or {})
                details["phase"] = "interrupted"
                details["interrupted"] = True
                message.details = details
                message.updated_at = time.time()
                changed = True
        return changed

    def _finalize_assistant_response(self, run: RunState) -> None:
        final_status = run.status
        if final_status == "cancelled" or self._is_run_cancelled(run.run_id):
            run.status = "cancelled"
            run.phase = "cancelled"
            run.finished_at = run.finished_at or time.time()
            run.assistant_message = run.assistant_message or "任务已中断。"
            self._update_assistant_message(run.run_id, run.assistant_message, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        if final_status in {"completed", "planned", "failed", "blocked"} and run.answer_with_llm:
            run.status = "responding"
            run.phase = "responding"
            self._publish_run_update(run)
            self._update_assistant_message(run.run_id, self._progress_message(run), "running", self._message_details(run))

        telemetry = self.tools.status_snapshot().get("drone")
        run.final_telemetry = dict(telemetry or {})
        if not run.verification:
            run.verification = self._verify_run_outcome(run)
        if not run.answer_with_llm:
            answer = self.planner.final_answer_stream(
                command=run.command,
                run_status=final_status,
                plan=run.plan,
                telemetry=telemetry,
                failure_reason=run.failure_reason,
                verification=run.verification,
                model_id=run.model_id or None,
                force_fallback=True,
                should_stop=lambda: self._is_run_cancelled(run.run_id),
            )
            if self._is_run_cancelled(run.run_id):
                final_status = "cancelled"
                answer = answer or "任务已中断。"
            run.status = final_status
            run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
            run.assistant_message = answer
            self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
            self._publish_run_update(run)
            self._finalize_task_run(run)
            with self._lock:
                self._cancelled_request_ids.discard(run.run_id)
            return
        buffer: list[str] = []
        reasoning_buffer: list[str] = []

        def on_reasoning(token: str) -> None:
            reasoning_buffer.append(token)
            reasoning = "".join(reasoning_buffer).strip()
            if not reasoning:
                return
            self._append_process(run, "模型推理", reasoning, status="running", kind="reasoning")
            self._update_assistant_message(
                run.run_id,
                "".join(buffer) or self._progress_message(run),
                "running",
                None,
                persist=False,
            )

        def on_token(token: str) -> None:
            buffer.append(token)
            self._append_assistant_delta(run.run_id, token, "".join(buffer), None)

        answer = self.planner.final_answer_stream(
            command=run.command,
            run_status=final_status,
            plan=run.plan,
            telemetry=telemetry,
            failure_reason=run.failure_reason,
            verification=run.verification,
            model_id=run.model_id or None,
            on_token=on_token,
            on_reasoning=on_reasoning,
            force_fallback=not run.answer_with_llm,
            should_stop=lambda: self._is_run_cancelled(run.run_id),
        )
        if not answer and buffer:
            answer = "".join(buffer)
        if reasoning_buffer:
            self._append_process(run, "模型推理", "".join(reasoning_buffer).strip(), status="completed", kind="reasoning")
        if self._is_run_cancelled(run.run_id):
            final_status = "cancelled"
            answer = answer or "任务已中断。"
        run.status = final_status
        run.phase = final_status if final_status in {"completed", "planned", "failed", "blocked", "cancelled"} else "completed"
        run.assistant_message = answer
        self._update_assistant_message(run.run_id, answer, "complete", self._message_details(run))
        self._publish_run_update(run)
        self._finalize_task_run(run)
        with self._lock:
            self._cancelled_request_ids.discard(run.run_id)

    def _append_assistant_delta(
        self,
        run_id: str,
        token: str,
        content: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] | None = None
        with self._lock:
            # 优先按 run_id 精确匹配
            target = None
            for message in reversed(self._messages):
                if message.role == "assistant" and message.run_id == run_id:
                    target = message
                    break
            # 回退：更新最近一条 running 状态的助手消息
            if not target:
                for message in reversed(self._messages):
                    if message.role == "assistant" and message.status == "running":
                        target = message
                        break
            if target:
                target.content = content
                target.status = "running"
                target.run_id = run_id
                target.details = details or target.details
                target.updated_at = time.time()
                payload = {
                    "id": target.id,
                    "run_id": run_id,
                    "token": token,
                    "content": content,
                    "message": self._message_public_dict(target),
                }
        if payload:
            self._publish("message_delta", payload)

    def _progress_message(self, run: RunState) -> str:
        phase = run.phase or run.status
        if phase == "planning":
            return "正在规划任务并选择可用工具..."
        if phase == "responding":
            return "工具调用已完成，正在整理最终回复..."
        if phase == "verifying":
            return "工具调用已完成，正在回读状态并校验结果..."

        loop_state = run.loop_state if isinstance(run.loop_state, dict) else {}
        decisions = loop_state.get("decisions") if isinstance(loop_state, dict) else []
        results = loop_state.get("results") if isinstance(loop_state, dict) else []
        if isinstance(decisions, list) and isinstance(results, list) and len(decisions) > len(results):
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."
        if isinstance(results, list) and results:
            latest_result = results[-1] if isinstance(results[-1], dict) else {}
            result_tool = str(latest_result.get("tool") or "")
            if result_tool:
                return f"已完成：{self._tool_action_label(result_tool)}，正在处理结果..."
        if isinstance(decisions, list) and decisions:
            latest_decision = decisions[-1] if isinstance(decisions[-1], dict) else {}
            action = str(latest_decision.get("action") or "")
            if action:
                return f"正在执行：{self._tool_action_label(action)}..."

        if run.plan and run.current_step:
            for step in run.plan.steps:
                if step.id == run.current_step:
                    if step.tool == "memory_store":
                        return "正在整理最终结果..."
                    label = step.label or self._tool_action_label(step.tool)
                    return f"正在执行：{label}..."
        if run.plan and run.plan.steps:
            current = next((step for step in run.plan.steps if step.status == "running"), None)
            if not current:
                current = next((step for step in run.plan.steps if step.status in {"pending", "planned"}), None)
            if current:
                if current.tool == "memory_store":
                    return "正在整理最终结果..."
                label = current.label or self._tool_action_label(current.tool)
                return f"正在执行：{label}..."

        if phase == "executing":
            return "正在执行任务，请稍候..."
        return "正在处理任务，请稍候..."

    def _message_details(self, run: RunState) -> dict[str, Any]:
        return {
            "mode": run.mode,
            "phase": run.phase,
            "run_status": run.status,
            "progress": round(run.progress, 1),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "plan": self._sanitize_for_frontend(run.plan.to_dict()) if run.plan else None,
            "failure_reason": run.failure_reason,
            "task_level": run.task_level,
            "route_strategy": run.route_strategy,
            "route_reason": run.route_reason,
            "loop_state": self._sanitize_for_frontend(run.loop_state),
            "verification": self._sanitize_for_frontend(run.verification),
            "agent_state": self._sanitize_for_frontend(run.agent_state),
            "thought_trace": self._sanitize_for_frontend(list(run.thought_trace)),
            "process_trace": self._sanitize_for_frontend(list(run.process_trace)),
        }

    def _run_public_dict(self, run: RunState) -> dict[str, Any]:
        return self._sanitize_for_frontend(run.to_dict())

    def _message_public_dict(self, message: ChatMessage) -> dict[str, Any]:
        return self._sanitize_for_frontend(message.to_dict())

    def _sanitize_for_frontend(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text == "image_base64":
                    image_text = str(item or "")
                    sanitized["image_base64_omitted"] = True
                    sanitized["image_base64_bytes"] = len(image_text)
                    continue
                if key_text in {"data_url", "image_data_url"}:
                    image_text = str(item or "")
                    sanitized[f"{key_text}_omitted"] = True
                    sanitized[f"{key_text}_bytes"] = len(image_text)
                    continue
                sanitized[key_text] = self._sanitize_for_frontend(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_for_frontend(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_for_frontend(item) for item in value]
        if isinstance(value, str) and len(value) > 12000:
            return f"{value[:12000]}... [omitted {len(value) - 12000} chars]"
        return value

    def _append_event(
        self,
        level: str,
        source: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(time.time(), level, source, message, data or {})
        with self._lock:
            self._events.append(event)
            self._events = self._events[-200:]
            terminal = {"completed", "planned", "failed", "blocked", "cancelled"}
            default_run_id = (
                self._current.run_id
                if self._current and self._current.status not in terminal
                else ""
            )
        self._record_task_event(event, default_run_id=default_run_id)
        self._publish("runtime_event", event.to_dict())
        return event

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "type": event_type,
            "payload": payload,
            "time": time.time(),
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(envelope)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(envelope)
                except Exception:
                    pass

    def _publish_run_update(self, run: RunState) -> None:
        self._update_task_run(run)
        self._publish("run_update", self._run_public_dict(run))

    def _start_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.start_run(run, session_id=self._current_session_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _update_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.update_run(run)
        except Exception:
            pass

    def _finalize_task_run(self, run: RunState) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.finalize_run(run)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_event(self, event: RuntimeEvent, default_run_id: str = "") -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_event(event.to_dict(), default_run_id=default_run_id)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _record_task_tool_result(self, run: RunState, step: MissionStep, result: ToolCallResult) -> None:
        store = getattr(self, "task_runs", None)
        if not store:
            return
        try:
            store.record_tool_result(run, step, result)
            self._publish("task_runs_update", store.snapshot())
        except Exception:
            pass

    def _capture_start_telemetry(self, run: RunState) -> None:
        if isinstance(run.start_telemetry, dict) and isinstance(run.start_telemetry.get("position_ned"), dict):
            self._remember_task_start(run, run.start_telemetry)
            return
        result = self.tools.execute("drone_get_status", {}, dry_run=False)
        if result.ok and isinstance(result.data, dict):
            run.start_telemetry = dict(result.data)
            self._remember_task_start(run, run.start_telemetry)
            self._append_event("info", "verifier", "任务起点状态已回读", result.to_dict())
        else:
            self._append_event("warning", "verifier", "任务起点状态回读失败", result.to_dict())

    def _verify_run_outcome(self, run: RunState) -> dict[str, Any]:
        if not run.execute or run.status == "planned":
            return {
                "status": "not_executed",
                "level": "info",
                "summary": "当前仅完成规划，未执行仿真动作，因此不进行任务后位置校验。",
            }

        start = run.start_telemetry or {}
        end = run.final_telemetry or {}
        start_pos = start.get("position_ned") if isinstance(start, dict) else None
        end_pos = end.get("position_ned") if isinstance(end, dict) else None
        checks: list[dict[str, Any]] = []

        result: dict[str, Any] = {
            "status": "unknown",
            "level": "info",
            "summary": "已回读任务后状态。",
            "start_position_ned": start_pos or {},
            "final_position_ned": end_pos or {},
            "final_flying": end.get("flying") if isinstance(end, dict) else None,
            "final_landed_state": end.get("landed_state") if isinstance(end, dict) else None,
            "checks": checks,
        }

        if isinstance(start_pos, dict) and isinstance(end_pos, dict):
            dx = self._float(end_pos.get("x")) - self._float(start_pos.get("x"))
            dy = self._float(end_pos.get("y")) - self._float(start_pos.get("y"))
            dz = self._float(end_pos.get("z")) - self._float(start_pos.get("z"))
            result["delta_ned"] = {"x": round(dx, 3), "y": round(dy, 3), "z": round(dz, 3)}
            result["delta_xy_m"] = round((dx * dx + dy * dy) ** 0.5, 3)
            result["delta_3d_m"] = round((dx * dx + dy * dy + dz * dz) ** 0.5, 3)

        lower = run.command.lower()
        steps = list(run.plan.steps if run.plan else [])
        wants_land = any(k in lower for k in ["land", "降落", "落地"])
        final_landing_expected = wants_land or any(step.tool == "drone_land" for step in steps)

        def later_has_position_goal(index: int) -> bool:
            later_tools = {step.tool for step in steps[index + 1 :]}
            return bool(later_tools & {"drone_fly_to", "drone_move_relative", "drone_upload_mission", "drone_start_mission"})

        def later_lands(index: int) -> bool:
            return any(step.tool == "drone_land" for step in steps[index + 1 :])

        if final_landing_expected:
            landed = bool(end.get("flying") is False or end.get("landed_state") == "landed")
            checks.append({
                "name": "landed_state",
                "ok": landed,
                "severity": "hard",
                "expected": "flying=false 或 landed_state=landed",
                "actual": {"flying": end.get("flying"), "landed_state": end.get("landed_state")},
            })

        takeoff_steps = [step for step in steps if step.tool == "drone_takeoff"]
        if takeoff_steps and isinstance(end, dict) and not final_landing_expected:
            expected_altitude = max(self._float(step.params.get("altitude"), 3.0) for step in takeoff_steps)
            ned_altitude = abs(self._float(end_pos.get("z"))) if isinstance(end_pos, dict) else 0.0
            gps = end.get("gps") if isinstance(end.get("gps"), dict) else {}
            gps_altitude = abs(self._float(gps.get("alt"))) if isinstance(gps, dict) else 0.0
            actual_altitude = max(ned_altitude, gps_altitude)
            min_altitude = max(0.5, expected_altitude * 0.85, expected_altitude - 0.5)
            flying = bool(end.get("flying") is True or actual_altitude >= 0.5)
            checks.append({
                "name": "takeoff_altitude",
                "ok": flying and actual_altitude >= min_altitude,
                "severity": "hard",
                "expected": {"altitude_m": round(expected_altitude, 3), "min_observed_m": round(min_altitude, 3)},
                "actual": {
                    "altitude_m": round(actual_altitude, 3),
                    "flying": end.get("flying"),
                    "armed": end.get("armed"),
                    "mode": end.get("mode"),
                },
            })

        for index, step in enumerate(steps):
            if step.tool == "drone_move_relative" and isinstance(start_pos, dict) and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                expected_xy = (self._float(step.params.get("forward_m")) ** 2 + self._float(step.params.get("right_m")) ** 2) ** 0.5
                actual_xy = float(result.get("delta_xy_m", 0.0))
                tolerance = max(1.0, expected_xy * 0.45)
                error = abs(actual_xy - expected_xy)
                hard_tolerance = max(3.0, expected_xy * 1.5)
                ok = error <= tolerance
                checks.append({
                    "name": "relative_xy_distance",
                    "ok": ok,
                    "severity": "hard" if error > hard_tolerance else "soft",
                    "expected": round(expected_xy, 3),
                    "actual": round(actual_xy, 3),
                    "tolerance": round(tolerance, 3),
                    "error_m": round(error, 3),
                })
            elif step.tool == "drone_fly_to" and isinstance(end_pos, dict):
                if later_has_position_goal(index):
                    continue
                target = step.params
                dx = self._float(end_pos.get("x")) - self._float(target.get("x"))
                dy = self._float(end_pos.get("y")) - self._float(target.get("y"))
                dz = self._float(end_pos.get("z")) - self._float(target.get("z"))
                err_xy = (dx * dx + dy * dy) ** 0.5
                ignore_z = later_lands(index) or final_landing_expected
                err = err_xy if ignore_z else (dx * dx + dy * dy + dz * dz) ** 0.5
                tolerance = 2.0
                hard_tolerance = 6.0
                checks.append({
                    "name": "absolute_position_target",
                    "ok": err <= tolerance,
                    "severity": "hard" if err > hard_tolerance else "soft",
                    "expected": {"x": target.get("x"), "y": target.get("y"), "z": target.get("z")},
                    "actual": end_pos,
                    "error_m": round(err, 3),
                    "xy_error_m": round(err_xy, 3),
                    "z_ignored_after_land": ignore_z,
                    "tolerance": tolerance,
                })

        if isinstance(end, dict) and ("has_collided" in end or "collision" in end):
            collision_value = end.get("has_collided")
            if collision_value is None and isinstance(end.get("collision"), dict):
                collision_value = end["collision"].get("has_collided")
            checks.append({
                "name": "collision_free",
                "ok": collision_value is not True,
                "severity": "hard",
                "expected": False,
                "actual": collision_value,
            })

        wants_search = any(k in lower for k in ["search", "find", "locate", "搜索", "寻找", "查找", "目标"])
        search_steps = [step for step in steps if step.tool in {"skill:search", "airsim_search_target", "airsim_vlm_confirm_target"}]
        if wants_search or search_steps:
            search_statuses = {
                str(value).strip().lower()
                for step in search_steps
                for value in self._collect_field_values(step.result, "status")
            }
            found_markers = {"candidate_found", "target_found", "found", "locked", "target_confirmed"}
            failed_markers = {"not_found", "target_not_confirmed", "failed", "cancelled", "canceled", "error", "blocked"}
            search_ok = (
                bool(search_steps)
                and bool(search_statuses & (found_markers | {"completed"}))
                and not bool(search_statuses & failed_markers)
            )
            if search_statuses & found_markers:
                search_ok = True
            checks.append({
                "name": "target_search_outcome",
                "ok": search_ok,
                "severity": "hard",
                "expected": "search reaches a terminal non-failure outcome",
                "actual": sorted(search_statuses),
            })

        wants_track = any(k in lower for k in ["track", "follow", "追踪", "跟踪", "跟随"])
        tracking_steps = [step for step in steps if step.tool == "airsim_track_object"]
        if wants_track or tracking_steps:
            tracking_statuses = {
                str(value).strip().lower()
                for step in tracking_steps
                for value in self._collect_field_values(step.result, "status")
            }
            tracking_ok = bool(tracking_steps) and "completed" in tracking_statuses and not bool(
                tracking_statuses & {"failed", "cancelled", "canceled", "error", "blocked"}
            )
            checks.append({
                "name": "tracking_outcome",
                "ok": tracking_ok,
                "severity": "hard",
                "expected": "tracking task completed",
                "actual": sorted(tracking_statuses),
            })

        if checks:
            failed = [check for check in checks if not check.get("ok")]
            hard_failed = [check for check in failed if check.get("severity") == "hard"]
            result["status"] = "failed" if hard_failed else ("passed_with_warnings" if failed else "passed")
            result["level"] = "failed" if hard_failed else ("warning" if failed else "ok")
            if hard_failed:
                result["summary"] = "任务执行后关键状态未达到目标。"
            elif failed:
                result["summary"] = "任务执行后状态已回读，未发现阻断性失败。"
            else:
                result["summary"] = "任务执行后状态与目标一致。"
        else:
            result["status"] = "observed"
        return result

    def _float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _collect_field_values(self, value: Any, field_name: str) -> list[Any]:
        values: list[Any] = []
        if isinstance(value, dict):
            if field_name in value:
                values.append(value.get(field_name))
            for nested in value.values():
                values.extend(self._collect_field_values(nested, field_name))
        elif isinstance(value, list):
            for nested in value:
                values.extend(self._collect_field_values(nested, field_name))
        return values

    def _remember_task_start(self, run: RunState, telemetry: dict[str, Any] | None) -> None:
        if run.start_position_recorded or not isinstance(telemetry, dict):
            return
        position = telemetry.get("position_ned")
        if not isinstance(position, dict):
            return
        heading = telemetry.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_task_start(run.run_id, run.command, position, heading_float)
        self.memory.remember_position(position, heading_float, source="task_start")
        run.start_position_recorded = True

    def _remember_position_from_payload(self, payload: dict[str, Any] | None, source: str) -> None:
        if not isinstance(payload, dict):
            return
        position = payload.get("position_ned") or payload.get("target_position_ned")
        if not isinstance(position, dict):
            return
        heading = payload.get("heading_deg")
        try:
            heading_float = float(heading) if heading is not None else None
        except (TypeError, ValueError):
            heading_float = None
        self.memory.remember_position(position, heading_float, source=source)

    def _chat_readonly_tools(self) -> list[dict[str, Any]]:
        """Read-only query tools exposed to chat mode (function-calling
        schemas). The whitelist is the safety boundary: chat can pull live
        status data but can never arm/move/land a vehicle."""
        allowed = {"drone_get_status", "drone_list_vehicles"}
        schemas: list[dict[str, Any]] = []
        try:
            for spec in self.tools.list_tools():
                name = str(spec.get("name") or "")
                if name not in allowed:
                    continue
                schemas.append(
                    function_tool_schema(
                        name,
                        str(spec.get("description") or name),
                        tool_schema_from_spec(name, spec.get("parameters") or {}, {}),
                    )
                )
        except Exception:
            return []
        return schemas

    def _refresh_chat_state(self, agent_state: dict[str, Any]) -> dict[str, Any]:
        """Refresh the read-only vehicle state once before answering a chat
        question when the snapshot is busy or stale.

        Chat mode does not execute control tools, but it must not answer from
        fabricated/outdated numbers either — a single read-only status + list
        call gives the model real telemetry to reason about.
        """
        try:
            runtime = self.tools.status_snapshot()
        except Exception:
            return agent_state
        if not runtime.get("connected") or runtime.get("stale_connection"):
            return agent_state
        busy = bool(runtime.get("busy"))
        has_vehicle = bool((agent_state or {}).get("vehicle") or (runtime.get("vehicles")))
        if not busy and has_vehicle:
            return agent_state
        result = self.tools.execute("drone_get_status", {}, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False)
        if not result.ok:
            return agent_state
        try:
            fresh_runtime = self.tools.status_snapshot()
        except Exception:
            fresh_runtime = runtime
        fresh = self._agent_state_context(fresh_runtime)
        if fresh:
            agent_state = fresh
        return agent_state

    def _plan_reasoning_sink(self, run_id: str, command: str) -> Callable[[str], None]:
        """Throttled reasoning-token sink for streamed planning.

        Tokens are batched and flushed to the event panel every ~0.5s so the
        operator sees the model's thinking during execute planning (a
        per-token SSE write would flood the browser)."""
        buffer: list[str] = []
        last_flush: list[float] = [0.0]

        def flush() -> None:
            if not buffer:
                return
            text = "".join(buffer).strip()
            buffer.clear()
            if text:
                self._append_event("info", "model_reasoning", text[-1500:], {"run_id": run_id, "command": command[:60]})

        def sink(token: str) -> None:
            buffer.append(token)
            now = time.time()
            if now - last_flush[0] >= 0.5:
                last_flush[0] = now
                flush()

        # attach the final flush so the wrapper can drain the tail
        sink.final_flush = flush  # type: ignore[attr-defined]
        return sink

    def _safety_snapshot(self) -> dict[str, Any]:
        constraints = self.tools.safety.constraints
        return {
            "max_altitude_m": constraints.max_altitude,
            "min_altitude_m": constraints.min_altitude,
            "max_velocity_ms": constraints.max_velocity,
            "geofence_radius_m": constraints.max_distance_from_home,
            "home_position_ned": list(constraints.home_position),
            "no_fly_zones": constraints.no_fly_zones,
            "hard_rules": [
                "NED z must be negative in the air",
                "danger-level safety validation blocks execution",
                "emergency stop may override every action",
                "long-running search/tracking tools must return task_id and be polled",
            ],
        }

    def _is_conflicting(self, command: str) -> bool:
        lower = command.lower()
        if not command.strip():
            return True
        landish = any(k in lower for k in ["land", "降落", "落地"])
        takeoffish = any(k in lower for k in ["takeoff", "起飞", "升空"])
        if landish and takeoffish and len(lower) < 20:
            return True
        return False
