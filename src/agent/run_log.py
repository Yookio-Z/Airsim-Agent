"""Append-only run event log: the audit trail for agent decision traces.

Each run (or sub-run) appends JSONL events to
``<data_dir>/runs/<run_id>.jsonl``. The log is the single source of truth for
"what the agent saw and did" — the UI, diagnostics, and future replay all read
from it. Sensitive payloads (image base64 frames) are redacted to a hash plus
size before they touch disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

RUN_LOG_VERSION = 1

_IMAGE_KEYS = ("image_base64", "image", "frame", "data_url", "base64", "image_data", "frame_data")
_REDACT_STRING_MIN = 256
_LINE_CAP = 200_000  # chars; defensive ceiling for a single event line
_KEEP_RUNS = 200


def _redact_value(value: Any, depth: int = 0) -> Any:
    """Recursively strip large embedded image payloads, keeping a fingerprint."""
    if depth > 6:
        return {"redacted": True, "reason": "depth_limit"}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if (
                isinstance(item, str)
                and len(item) > _REDACT_STRING_MIN
                and any(marker in lowered for marker in _IMAGE_KEYS)
            ):
                result[key] = {
                    "redacted": True,
                    "kind": "image_payload",
                    "sha256": hashlib.sha256(item.encode("utf-8", errors="ignore")).hexdigest()[:16],
                    "bytes": len(item),
                }
            else:
                result[key] = _redact_value(item, depth + 1)
        return result
    if isinstance(value, list):
        items = [_redact_value(item, depth + 1) for item in value]
        if len(items) > 60:
            items = items[:60] + [{"redacted": True, "reason": "list_cap"}]
        return items
    if isinstance(value, str) and len(value) > 8000:
        return value[:8000] + " ...[truncated]"
    return value


class RunLog:
    """Append-only event log for one run. Thread-safe."""

    def __init__(self, run_id: str, base_dir: Path | None = None) -> None:
        self.run_id = str(run_id)
        root = base_dir or Path(__file__).resolve().parents[2] / ".airsim_agent"
        self.path = root / "runs" / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0

    def write(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Append one event. Never raises: logging must not break the agent."""
        try:
            payload = _redact_value(payload or {})
            line = json.dumps(
                {
                    "version": RUN_LOG_VERSION,
                    "seq": self._seq,
                    "ts": time.time(),
                    "type": event_type,
                    "payload": payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if len(line) > _LINE_CAP:
                # A truncated JSON line would be unreadable; write a compact
                # marker event instead so the reader never loses the sequence.
                line = json.dumps(
                    {
                        "version": RUN_LOG_VERSION,
                        "seq": self._seq,
                        "ts": time.time(),
                        "type": "truncated",
                        "payload": {
                            "event_type": event_type,
                            "bytes": len(line),
                            "reason": "line exceeded cap",
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            with self._lock:
                self._seq += 1
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
        except Exception:
            pass

    def count(self) -> int:
        try:
            with self._lock:
                if not self.path.exists():
                    return 0
                return sum(1 for _ in self.path.open("r", encoding="utf-8"))
        except Exception:
            return 0

    @staticmethod
    def prune(base_dir: Path | None = None, keep: int = _KEEP_RUNS) -> int:
        """Delete oldest run logs beyond ``keep``. Returns the number removed."""
        root = base_dir or Path(__file__).resolve().parents[2] / ".airsim_agent"
        runs_dir = root / "runs"
        if not runs_dir.exists():
            return 0
        try:
            files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return 0
        removed = 0
        for stale in files[keep:]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
        return removed


class RunLogReader:
    """Read and replay one run log."""

    def __init__(self, run_id: str, base_dir: Path | None = None) -> None:
        root = base_dir or Path(__file__).resolve().parents[2] / ".airsim_agent"
        self.path = root / "runs" / f"{run_id}.jsonl"

    def exists(self) -> bool:
        return self.path.exists()

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return rows

    def replay(self) -> dict[str, Any]:
        """Rebuild a compact run summary from the event log."""
        events = self.events()
        decisions: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        summary: dict[str, Any] = {
            "run_id": self.path.stem,
            "command": "",
            "status": "unknown",
            "summary": "",
            "failure_reason": "",
            "model_id": "",
            "events": len(events),
            "decisions": decisions,
            "results": results,
            "started_at": events[0].get("ts", 0.0) if events else 0.0,
            "finished_at": events[-1].get("ts", 0.0) if events else 0.0,
        }
        for event in events:
            payload = event.get("payload") or {}
            event_type = event.get("type", "")
            if event_type == "run.start":
                summary["command"] = str(payload.get("command") or "")
                summary["model_id"] = str(payload.get("model_id") or "")
                summary["mode"] = str(payload.get("mode") or "")
            elif event_type == "run.end":
                summary["status"] = str(payload.get("status") or summary["status"])
                summary["summary"] = str(payload.get("summary") or "")
                summary["failure_reason"] = str(payload.get("failure_reason") or "")
                summary["verification_status"] = str(payload.get("verification_status") or "")
            elif event_type == "loop.decision":
                decisions.append(payload)
            elif event_type == "tool.result":
                results.append(payload)
        return summary


class RunLogStore:
    """List and read run logs."""

    def __init__(self, base_dir: Path | None = None) -> None:
        root = base_dir or Path(__file__).resolve().parents[2] / ".airsim_agent"
        self.runs_dir = root / "runs"

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        RunLog.prune(self.runs_dir.parent)
        if not self.runs_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            files = sorted(self.runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        for path in files[:limit]:
            rows.append({
                "run_id": path.stem,
                "modified_at": path.stat().st_mtime,
                "bytes": path.stat().st_size,
            })
        return rows

    def read(self, run_id: str) -> RunLogReader | None:
        reader = RunLogReader(run_id, self.runs_dir.parent)
        if not reader.exists():
            return None
        return reader
