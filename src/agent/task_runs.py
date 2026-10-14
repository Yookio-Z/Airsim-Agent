"""Persistent task-run records for the web Agent runtime.

This module intentionally stays below the planning/execution layer.  It stores
snapshots of RunState plus run-scoped events and tool results so the UI and
future memory code can replay what happened without scraping transient logs.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class TaskRunStore:
    """Small JSON-backed store for recent Agent task executions."""

    def __init__(self, data_dir: Path | None = None, max_index: int = 80) -> None:
        root = Path(__file__).resolve().parents[2]
        self.data_dir = data_dir or root / "src" / "data" / "task_runs"
        self.index_path = self.data_dir / "index.json"
        self.max_index = max_index
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index = self._load_index()

    def start_run(self, run: Any, session_id: str = "") -> None:
        run_data = self._run_to_dict(run)
        if not run_data.get("run_id"):
            return
        with self._lock:
            existing = self._read_record_unlocked(run_data["run_id"]) or {}
            record = self._base_record(run_data, session_id=session_id)
            record["events"] = existing.get("events", [])
            record["tool_results"] = existing.get("tool_results", [])
            record["counters"] = self._counters(record)
            self._write_record_unlocked(record)
            self._upsert_index_unlocked(record)

    def update_run(self, run: Any) -> None:
        run_data = self._run_to_dict(run)
        run_id = str(run_data.get("run_id") or "")
        if not run_id:
            return
        with self._lock:
            record = self._read_record_unlocked(run_id) or self._base_record(run_data)
            self._merge_run_fields(record, run_data)
            record["updated_at"] = time.time()
            record["counters"] = self._counters(record)
            self._write_record_unlocked(record)
            self._upsert_index_unlocked(record)

    def finalize_run(self, run: Any) -> None:
        run_data = self._run_to_dict(run)
        run_id = str(run_data.get("run_id") or "")
        if not run_id:
            return
        with self._lock:
            record = self._read_record_unlocked(run_id) or self._base_record(run_data)
            self._merge_run_fields(record, run_data)
            record["finalized_at"] = time.time()
            record["updated_at"] = record["finalized_at"]
            record["counters"] = self._counters(record)
            self._write_record_unlocked(record)
            self._upsert_index_unlocked(record)

    def mark_running_as_interrupted(
        self,
        keep_run_ids: set[str] | None = None,
        grace_sec: float = 45.0,
    ) -> int:
        """把残留的"运行中"记录标成 interrupted，返回改动条数。

        进程崩溃/被关掉时，最后一条 run 记录会永远停在 running/executing：界面
        一直转圈，操作员分不清"还在跑"和"早就没了"。这里做**记录层**的崩溃恢复：
        只改状态与说明，绝不重放任何动作——飞行指令是非幂等的，重放一次起飞/降落
        是事故（这一点与 Pi Harness 的"会话可恢复"刻意不同，见
        docs/loop_context_design.md §2）。

        两道保护，缺一不可：
        - ``keep_run_ids``：调用方把本进程在途/活跃的 run_id 传进来；
        - ``grace_sec``：**记录更新时间**新于宽限期的视为"可能仍在飞"——另一个进程
          里真在跑的任务其记录会被持续更新，不能因为"我这边刚启动"就把它标死
          （本地跑测试、或第二个 server 实例都会构造 runtime）。
        幂等：只处理状态在 live 集合里的记录。
        """
        live_statuses = {"running", "queued", "planned", "awaiting_approval"}
        keep = {str(item) for item in (keep_run_ids or set()) if item}
        changed = 0
        now = time.time()
        with self._lock:
            for item in list(self._index.get("runs") or []):
                run_id = str((item or {}).get("run_id") or "")
                status = str((item or {}).get("status") or "").lower()
                if not run_id or run_id in keep or status not in live_statuses:
                    continue
                if now - float((item or {}).get("updated_at") or 0.0) < max(0.0, float(grace_sec)):
                    continue
                record = self._read_record_unlocked(run_id)
                if not record:
                    continue
                if now - float(record.get("updated_at") or 0.0) < max(0.0, float(grace_sec)):
                    continue
                record["status"] = "interrupted"
                record["phase"] = "interrupted"
                record["failure_reason"] = "任务进程已中断（服务重启或崩溃），未执行任何自动恢复。"
                record["finished_at"] = record.get("finished_at") or now
                record["updated_at"] = now
                record["counters"] = self._counters(record)
                self._write_record_unlocked(record)
                self._upsert_index_unlocked(record)
                changed += 1
        return changed

    def maybe_recover_interrupted(
        self,
        keep_run_ids: set[str] | None = None,
        min_interval_sec: float = 20.0,
    ) -> int:
        """节流版的残留清扫：UI 轮询路径调用它，真正的扫描最多每 20s 一次。"""
        now = time.time()
        with self._lock:
            if now - float(getattr(self, "_last_recovery_ts", 0.0)) < max(1.0, float(min_interval_sec)):
                return 0
            self._last_recovery_ts = now
        return self.mark_running_as_interrupted(keep_run_ids=keep_run_ids)

    def record_event(self, event: dict[str, Any], default_run_id: str = "") -> None:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        run_id = str(data.get("run_id") or default_run_id or "")
        if not run_id:
            return
        with self._lock:
            record = self._read_record_unlocked(run_id) or self._base_record({"run_id": run_id})
            events = list(record.get("events") or [])
            events.append(event)
            record["events"] = events[-300:]
            record["updated_at"] = time.time()
            record["counters"] = self._counters(record)
            self._write_record_unlocked(record)
            self._upsert_index_unlocked(record)

    def record_tool_result(self, run: Any, step: Any, result: Any) -> None:
        run_data = self._run_to_dict(run)
        run_id = str(run_data.get("run_id") or "")
        if not run_id:
            return
        step_data = step.to_dict() if hasattr(step, "to_dict") else dict(step or {})
        result_data = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
        entry = {
            "timestamp": time.time(),
            "step_id": step_data.get("id", ""),
            "label": step_data.get("label", ""),
            "tool": step_data.get("tool") or result_data.get("tool", ""),
            "params": step_data.get("params") or result_data.get("params") or {},
            "ok": bool(result_data.get("ok")),
            "status": step_data.get("status", ""),
            "result": result_data,
        }
        with self._lock:
            record = self._read_record_unlocked(run_id) or self._base_record(run_data)
            results = list(record.get("tool_results") or [])
            results.append(entry)
            record["tool_results"] = results[-200:]
            self._merge_run_fields(record, run_data)
            record["updated_at"] = time.time()
            record["counters"] = self._counters(record)
            self._write_record_unlocked(record)
            self._upsert_index_unlocked(record)

    def snapshot(self, limit: int = 12) -> dict[str, Any]:
        # UI 读列表前先做一次低频复扫：崩溃重启后那条记录的 updated_at 还很新
        # （宽限期保护），启动时那一次清扫会跳过它；没有复扫它就会永远停在
        # "运行中"，界面一直转圈。复扫有节流（默认 20s 一次），不会压在轮询路径上。
        self.maybe_recover_interrupted()
        with self._lock:
            recent = list(self._index.get("runs", []))[:limit]
            total = int(self._index.get("total_runs", len(self._index.get("runs", []))))
        return {
            "total_runs": total,
            "recent": recent,
            "storage": "persistent_json",
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read_record_unlocked(run_id)

    def _base_record(self, run_data: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        record = {
            "version": 1,
            "run_id": str(run_data.get("run_id") or ""),
            "session_id": session_id,
            "created_at": time.time(),
            "updated_at": time.time(),
            "events": [],
            "tool_results": [],
        }
        self._merge_run_fields(record, run_data)
        record["counters"] = self._counters(record)
        return record

    def _merge_run_fields(self, record: dict[str, Any], run_data: dict[str, Any]) -> None:
        for key in (
            "run_id",
            "command",
            "intent",
            "summary",
            "status",
            "mode",
            "phase",
            "execute",
            "progress",
            "current_step",
            "started_at",
            "finished_at",
            "failure_reason",
            "assistant_message",
            "model_id",
            "task_level",
            "route_strategy",
            "route_reason",
            "risk_level",
            "start_telemetry",
            "final_telemetry",
            "verification",
            "agent_state",
            "thought_trace",
            "process_trace",
            "loop_state",
        ):
            if key in run_data:
                record[key] = run_data.get(key)
        record["plan"] = run_data.get("plan") or record.get("plan")

    def _counters(self, record: dict[str, Any]) -> dict[str, Any]:
        plan = record.get("plan") if isinstance(record.get("plan"), dict) else {}
        steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
        tool_results = record.get("tool_results") if isinstance(record.get("tool_results"), list) else []
        loop_state = record.get("loop_state") if isinstance(record.get("loop_state"), dict) else {}
        loop_results = loop_state.get("results") if isinstance(loop_state.get("results"), list) else []
        events = record.get("events") if isinstance(record.get("events"), list) else []
        failed_steps = [step for step in steps if step.get("status") in {"failed", "blocked"}]
        return {
            "events": len(events),
            "tool_calls": len(tool_results) or len(loop_results),
            "steps_total": len(steps),
            "steps_ok": sum(1 for step in steps if step.get("status") == "completed"),
            "steps_failed": len(failed_steps),
            "danger_events": sum(1 for event in events if event.get("level") == "danger"),
        }

    def _summary_item(self, record: dict[str, Any]) -> dict[str, Any]:
        counters = self._counters(record)
        verification = record.get("verification") if isinstance(record.get("verification"), dict) else {}
        started_at = float(record.get("started_at") or record.get("created_at") or 0.0)
        finished_at = float(record.get("finished_at") or 0.0)
        duration = round(max(0.0, finished_at - started_at), 2) if finished_at and started_at else 0.0
        return {
            "run_id": record.get("run_id", ""),
            "session_id": record.get("session_id", ""),
            "command": record.get("command", ""),
            "summary": record.get("summary", ""),
            "status": record.get("status", ""),
            "phase": record.get("phase", ""),
            "mode": record.get("mode", ""),
            "intent": record.get("intent", ""),
            "task_level": record.get("task_level", ""),
            "route_strategy": record.get("route_strategy", ""),
            "risk_level": record.get("risk_level", "safe"),
            "started_at": started_at,
            "finished_at": finished_at,
            "updated_at": record.get("updated_at", 0),
            "duration_sec": duration,
            "failure_reason": record.get("failure_reason", ""),
            "verification_level": verification.get("level", ""),
            "verification_status": verification.get("status", ""),
            "counters": counters,
        }

    def _upsert_index_unlocked(self, record: dict[str, Any]) -> None:
        item = self._summary_item(record)
        run_id = item.get("run_id")
        runs = [entry for entry in self._index.get("runs", []) if entry.get("run_id") != run_id]
        runs.insert(0, item)
        runs.sort(key=lambda entry: entry.get("updated_at") or entry.get("started_at") or 0, reverse=True)
        self._index = {
            "version": 1,
            "updated_at": time.time(),
            "total_runs": max(int(self._index.get("total_runs", 0)), len(runs)),
            "runs": runs[: self.max_index],
        }
        self._write_json_unlocked(self.index_path, self._index)

    def _load_index(self) -> dict[str, Any]:
        try:
            if self.index_path.exists():
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("runs"), list):
                    data.setdefault("total_runs", len(data["runs"]))
                    return data
        except Exception:
            pass
        return {"version": 1, "updated_at": 0, "total_runs": 0, "runs": []}

    def _read_record_unlocked(self, run_id: str) -> dict[str, Any] | None:
        path = self._record_path(run_id)
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _write_record_unlocked(self, record: dict[str, Any]) -> None:
        self._write_json_unlocked(self._record_path(str(record.get("run_id") or "")), record)

    def _write_json_unlocked(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    def _record_path(self, run_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in run_id).strip("_")
        return self.data_dir / f"{safe or 'run'}.json"

    @staticmethod
    def _run_to_dict(run: Any) -> dict[str, Any]:
        if hasattr(run, "to_dict"):
            data = run.to_dict()
            return data if isinstance(data, dict) else {}
        return dict(run or {})
