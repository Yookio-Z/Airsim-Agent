"""飞行记录（flight log）会话与运行审计日志。

任务执行/手动飞行前后的遥测录制、run 事件流（JSONL）以及界面读取两者用的入口。
录制本身在 ``src/flightlog/``——这里只做"什么时候开始/停止、元数据给什么"。
"""

from __future__ import annotations

from .run_log import RunLogStore
from src.flightlog import FlightLogSession, list_sessions, read_session
from typing import Any
import time


class FlightLogMixin:
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

    def _flightlog_snapshot(self) -> dict[str, Any]:
        """录制内容：面板遥测的唯一读口（gcs.telemetry）+ 后端标识。"""
        snapshot = self.gcs.telemetry.snapshot()
        drone = snapshot.get("drone")
        vehicles = snapshot.get("vehicles") if isinstance(snapshot.get("vehicles"), list) else []
        return {
            "backend": str(snapshot.get("backend") or ""),
            "drone": drone if isinstance(drone, dict) else {},
            "vehicles": [item for item in vehicles if isinstance(item, dict)],
        }

    def _start_flightlog_session(self, name: str, meta: dict[str, Any]) -> FlightLogSession | None:
        with self._flightlog_lock:
            if self._active_flightlog is not None:
                return None
            session = FlightLogSession(
                name,
                snapshot_provider=self._flightlog_snapshot,
                interval=0.2,
                meta=meta,
            )
            session.start()
            self._active_flightlog = session
        self._append_event("info", "flightlog", f"飞行记录开始: {name}", dict(meta))
        return session

    def _stop_flightlog_session(self) -> dict[str, Any] | None:
        with self._flightlog_lock:
            session = self._active_flightlog
            self._active_flightlog = None
        if session is None:
            return None
        summary = session.stop()
        self._append_event(
            "info",
            "flightlog",
            f"飞行记录结束: {summary.get('name', '')}（{summary.get('frame_count', 0)} 帧）",
            dict(summary),
        )
        return dict(summary)

    def start_manual_flightlog(self, name: str = "") -> dict[str, Any]:
        """Manually start recording (no run_id needed, e.g. UI manual flights)."""
        session_name = (str(name).strip() or f"manual_{int(time.time())}")[:120]
        with self._flightlog_lock:
            if self._manual_flightlog is not None:
                return {"ok": False, "error": "manual flightlog already recording"}
            session = FlightLogSession(
                session_name,
                snapshot_provider=self._flightlog_snapshot,
                interval=0.2,
                meta={"mode": "manual"},
            )
            session.start()
            self._manual_flightlog = session
        self._append_event("info", "flightlog", f"手动飞行记录开始: {session_name}", {})
        return {"ok": True, "name": session_name, "recording": True}

    def stop_manual_flightlog(self) -> dict[str, Any]:
        with self._flightlog_lock:
            session = self._manual_flightlog
            self._manual_flightlog = None
        if session is None:
            return {"ok": False, "error": "no manual flightlog recording"}
        summary = session.stop()
        self._append_event(
            "info",
            "flightlog",
            f"手动飞行记录结束: {summary.get('name', '')}（{summary.get('frame_count', 0)} 帧）",
            dict(summary),
        )
        return {"ok": True, **summary}

    def flightlog_sessions(self) -> list[dict[str, Any]]:
        """列出录过的飞行记录（新 → 旧，含摘要索引）。"""
        return list_sessions()

    def flightlog_session(self, name: str) -> dict[str, Any] | None:
        """读取一次飞行记录：meta + summary + 最后 N 帧。"""
        return read_session(str(name or ""))

    def run_trace(self, run_id: str) -> dict[str, Any] | None:
        """回放一次 run 的追加事件日志（审计/诊断用，与飞行记录是两回事）。"""
        reader = RunLogStore().read(str(run_id or ""))
        if reader is None:
            return None
        return reader.replay()

    def run_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent run event logs (ids + metadata, no payloads)."""
        return RunLogStore().list(limit=limit)
