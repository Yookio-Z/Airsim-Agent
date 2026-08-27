"""运行服务：replay/run 日志、GCS 任务、会话管理、state/订阅。

拆分自 runtime.py（AgentRuntime 方法按职责迁移，行为不变）。
"""

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


class RuntimeOpsMixin:
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

    def gcs_mission_start_multi(
        self,
        assignments: list[Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """多机各自航线并发执行：每机 takeoff(如需) + 各自路径，非阻塞派发。"""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        if not isinstance(assignments, list):
            return {"ok": False, "error": "assignments must be a list"}
        clean = [entry for entry in assignments if isinstance(entry, dict)]
        result = self.gcs.mission.start_multi(clean)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "多机任务已派发" if result.ok else "多机任务派发失败",
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
            self._pending_run_ids.clear()
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
            self._pending_run_ids.clear()
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
