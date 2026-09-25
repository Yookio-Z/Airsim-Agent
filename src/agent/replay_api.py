"""Replay sessions and the run audit log.

Telemetry recording around runs/manual flights, the JSONL run trace, and the
readers the UI uses for both. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from .run_log import RunLogStore
from src.replay.session import (
    ReplaySession,
    list_replay_sessions,
    read_replay_session,
)
from typing import Any
import time


class ReplayMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


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
