"""Lightweight memory store for the AirSim VLA agent."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any


class AgentMemory:
    """Short-term event memory plus persisted long-term mission lessons.

    Writes are serialized through a lock: today all writes happen on the
    single execution thread, but the tmp-file swap in ``_save`` would corrupt
    if a second writer ever appeared (chat, telemetry polling, UI panel).
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.data_dir = data_dir or root / ".airsim_agent"
        self.path = self.data_dir / "memory.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "session": {},
                "missions": [],
                "lessons": [],
                "risk_events": [],
                "tool_stats": {},
                "skill_candidates": [],
                "facts": {},
                "runs": [],
            }
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("memory root is not an object")
            data.setdefault("missions", [])
            data.setdefault("lessons", [])
            data.setdefault("risk_events", [])
            data.setdefault("tool_stats", {})
            data.setdefault("session", {})
            data.setdefault("skill_candidates", [])
            data.setdefault("facts", {})
            data.setdefault("runs", [])
            return data
        except Exception:
            backup = self.path.with_suffix(f".corrupt_{int(time.time())}.json")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            return {
                "version": 1,
                "session": {},
                "missions": [],
                "lessons": [],
                "risk_events": [],
                "tool_stats": {},
                "skill_candidates": [],
                "facts": {},
                "runs": [],
            }

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with self._lock:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)

    def remember_tool_call(self, tool: str, ok: bool) -> None:
        with self._lock:
            stats = self._data.setdefault("tool_stats", {})
            item = stats.setdefault(tool, {"calls": 0, "success": 0, "fail": 0})
            item["calls"] += 1
            if ok:
                item["success"] += 1
            else:
                item["fail"] += 1
            self._save()

    def remember_position(
        self,
        position_ned: dict[str, Any],
        heading_deg: float | None = None,
        source: str = "status",
    ) -> None:
        with self._lock:
            session = self._data.setdefault("session", {})
            session["last_known_position_ned"] = position_ned
            if heading_deg is not None:
                session["last_heading_deg"] = heading_deg
            session["last_position_source"] = source
            session["last_position_at"] = time.time()
            self._save()

    def remember_task_start(
        self,
        run_id: str,
        command: str,
        position_ned: dict[str, Any],
        heading_deg: float | None = None,
    ) -> None:
        with self._lock:
            session = self._data.setdefault("session", {})
            session["last_task_start_run_id"] = run_id
            session["last_task_start_command"] = command
            session["last_task_start_position_ned"] = position_ned
            if heading_deg is not None:
                session["last_task_start_heading_deg"] = heading_deg
            session["last_task_start_at"] = time.time()
            self._save()

    def remember_mission(self, mission: dict[str, Any]) -> None:
        with self._lock:
            item = {
                "timestamp": time.time(),
                "run_id": mission.get("run_id"),
                "command": mission.get("command", ""),
                "intent": mission.get("intent", ""),
                "status": mission.get("status", ""),
                "summary": mission.get("summary", ""),
                "duration_sec": mission.get("duration_sec", 0),
                "steps_total": mission.get("steps_total", 0),
                "steps_ok": mission.get("steps_ok", 0),
                "route_strategy": mission.get("route_strategy", ""),
                "tool_sequence": [str(x) for x in mission.get("tool_sequence", []) if x],
                "verification_status": mission.get("verification_status", ""),
            }
            self._data.setdefault("missions", []).append(item)
            self._data["missions"] = self._data["missions"][-50:]

            status = str(item["status"])
            if status in {"failed", "blocked", "error"}:
                self._data.setdefault("risk_events", []).append(
                    {
                        "timestamp": item["timestamp"],
                        "run_id": item["run_id"],
                        "command": item["command"],
                        "reason": mission.get("failure_reason", status),
                        "filtered_from_lessons": True,
                    }
                )
                self._data["risk_events"] = self._data["risk_events"][-50:]
            elif status == "completed" and item["steps_total"]:
                lesson = {
                    "timestamp": item["timestamp"],
                    "intent": item["intent"],
                    "summary": item["summary"],
                    "success_rate": round(item["steps_ok"] / max(1, item["steps_total"]), 2),
                }
                self._merge_lesson(lesson)

            if item["tool_sequence"]:
                self._update_skill_candidate(item)

            self._save()

    def _merge_lesson(self, lesson: dict[str, Any]) -> None:
        lessons = self._data.setdefault("lessons", [])
        key = (lesson.get("intent"), lesson.get("summary"))
        for existing in lessons:
            if (existing.get("intent"), existing.get("summary")) == key:
                existing["timestamp"] = lesson["timestamp"]
                existing["success_rate"] = max(existing.get("success_rate", 0), lesson["success_rate"])
                return
        lessons.append(lesson)
        self._data["lessons"] = lessons[-30:]

    def _update_skill_candidate(self, mission: dict[str, Any]) -> None:
        """Accumulate evidence for a possible reusable skill without auto-promoting it."""
        sequence = list(mission.get("tool_sequence") or [])
        signature = f"{mission.get('intent', '')}|{' > '.join(sequence)}"
        candidates = self._data.setdefault("skill_candidates", [])
        candidate = next((item for item in candidates if item.get("signature") == signature), None)
        if candidate is None:
            candidate = {
                "signature": signature,
                "intent": mission.get("intent", ""),
                "tool_sequence": sequence,
                "runs": 0,
                "successes": 0,
                "failures": 0,
                "avg_duration_sec": 0.0,
                "eligible_for_review": False,
                "example_commands": [],
            }
            candidates.append(candidate)
        candidate["runs"] += 1
        succeeded = mission.get("status") == "completed" and mission.get("verification_status") != "failed"
        candidate["successes" if succeeded else "failures"] += 1
        duration = float(mission.get("duration_sec") or 0.0)
        candidate["avg_duration_sec"] = round(
            ((candidate["avg_duration_sec"] * (candidate["runs"] - 1)) + duration) / candidate["runs"],
            2,
        )
        candidate["success_rate"] = round(candidate["successes"] / max(1, candidate["runs"]), 2)
        candidate["eligible_for_review"] = candidate["successes"] >= 3 and candidate["success_rate"] >= 0.8
        command = str(mission.get("command") or "").strip()
        if command:
            examples = list(candidate.get("example_commands") or [])
            if command not in examples:
                examples.append(command)
            candidate["example_commands"] = examples[-5:]
        candidate["last_summary"] = mission.get("summary", "")
        candidate["updated_at"] = time.time()
        self._data["skill_candidates"] = candidates[-50:]

    def remember_fact(self, key: str, value: str, tags: list[str] | None = None) -> None:
        """Store a durable operator-stated fact for future runs (LLM-active)."""
        key = str(key or "").strip()
        if not key:
            return
        with self._lock:
            facts = self._data.setdefault("facts", {})
            facts[key] = {
                "value": str(value or "")[:1000],
                "tags": [str(item) for item in (tags or [])][:8],
                "updated_at": time.time(),
            }
            if len(facts) > 50:
                for stale in sorted(facts, key=lambda k: facts[k].get("updated_at", 0.0))[: len(facts) - 50]:
                    facts.pop(stale, None)
            self._save()

    def remember_transcript(
        self,
        run_id: str,
        command: str,
        status: str,
        summary: str,
        tools: list[str],
        failure_reason: str = "",
    ) -> None:
        """One bounded cross-run transcript row written at run end (throttled
        to a single file write per run; the RunLog is the step-level truth)."""
        with self._lock:
            runs = self._data.setdefault("runs", [])
            runs.append(
                {
                    "run_id": str(run_id or "")[:80],
                    "command": str(command or "")[:200],
                    "status": str(status or ""),
                    "summary": str(summary or "")[:300],
                    "tools": [str(item)[:40] for item in (tools or [])][:20],
                    "failure_reason": str(failure_reason or "")[:200],
                    "timestamp": time.time(),
                }
            )
            self._data["runs"] = runs[-50:]
            self._save()

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Keyword-overlap recall across facts, missions, lessons, and run
        transcripts, with a recency bonus for records younger than 14 days.

        Deliberately simple: no embeddings, no LLM involvement. CJK queries
        match by bigrams, so recall works without a tokenizer.
        """
        query = str(query or "").strip()
        if not query:
            return []
        tokens = self._recall_tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        now = time.time()

        for key, fact in (self._data.get("facts") or {}).items():
            if not isinstance(fact, dict):
                continue
            text = f"{key} {fact.get('value', '')}"
            raw = self._score_text(tokens, text)
            if raw > 0:
                score = raw + 0.2  # facts are slightly favored over history rows
                scored.append(
                    (score, {"kind": "fact", "key": key, "value": fact.get("value", ""), "tags": fact.get("tags", []), "score": round(score, 3)})
                )

        for kind, field in (("mission", "command"), ("lesson", "summary"), ("run", "command")):
            rows = self._data.get(kind + "s" if kind != "mission" else "missions") or []
            for row in reversed(rows):
                if not isinstance(row, dict):
                    continue
                text = f"{row.get(field) or ''} {row.get('summary') or ''}"
                score = self._score_text(tokens, text)
                if score > 0:
                    age_days = (now - float(row.get("timestamp") or 0.0)) / 86400.0
                    recency = max(0.0, 1.0 - age_days / 14.0)
                    score += recency * 0.3
                    scored.append((score, {**row, "kind": kind, "score": round(score, 3)}))

        scored.sort(key=lambda item: (-item[0], float(item[1].get("timestamp") or 0.0)))
        return [row for _, row in scored[: max(1, min(20, int(limit)))]]

    @staticmethod
    def _recall_tokens(query: str) -> list[str]:
        lower = query.lower()
        parts = [part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", lower) if part]
        tokens: list[str] = []
        for part in parts:
            if len(part) >= 2:
                tokens.append(part)
            if any("\u4e00" <= ch <= "\u9fff" for ch in part) and len(part) >= 2:
                tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _score_text(tokens: list[str], text: str) -> float:
        if not tokens or not text:
            return 0.0
        lowered = text.lower()
        hits = sum(1 for token in tokens if token in lowered)
        return hits / len(tokens)

    def guidance(self) -> dict[str, Any]:
        """Turn raw memories into small actionable hints for routing and planning."""
        preferred_tools: list[dict[str, Any]] = []
        caution_tools: list[dict[str, Any]] = []
        for tool, stats in (self._data.get("tool_stats") or {}).items():
            calls = int(stats.get("calls") or 0)
            if calls < 3:
                continue
            success = int(stats.get("success") or 0)
            fail = int(stats.get("fail") or 0)
            success_rate = round(success / max(1, calls), 2)
            fail_rate = round(fail / max(1, calls), 2)
            row = {"name": tool, "calls": calls, "success_rate": success_rate, "fail_rate": fail_rate}
            if success_rate >= 0.8:
                preferred_tools.append(row)
            if fail_rate >= 0.5:
                caution_tools.append(row)

        preferred_tools.sort(key=lambda item: (-float(item["success_rate"]), -int(item["calls"]), str(item["name"])))
        caution_tools.sort(key=lambda item: (-float(item["fail_rate"]), -int(item["calls"]), str(item["name"])))

        preferred_skills: list[dict[str, Any]] = []
        routing_hints: list[dict[str, Any]] = []
        for candidate in self._data.get("skill_candidates", []) or []:
            if not candidate.get("eligible_for_review"):
                continue
            sequence = [str(item) for item in candidate.get("tool_sequence") or [] if item]
            skill = self._skill_from_sequence(sequence, str(candidate.get("intent") or ""))
            if skill:
                preferred_skills.append({
                    "name": skill,
                    "source": "memory.skill_candidate",
                    "intent": candidate.get("intent", ""),
                    "success_rate": candidate.get("success_rate", 0),
                    "runs": candidate.get("runs", 0),
                    "tool_sequence": sequence,
                })
            match_terms = self._candidate_match_terms(candidate)
            if len(sequence) > 1 and match_terms:
                routing_hints.append({
                    "level": "l3_agent_loop",
                    "strategy": "agent_loop",
                    "match_terms": match_terms,
                    "reason": "历史上类似任务通过多步工具序列稳定完成，优先交给 Agent Loop 观察-决策-执行。",
                    "source": "memory.skill_candidate",
                })

        recent_risks = []
        for item in list(reversed(self._data.get("risk_events", [])[-5:])):
            recent_risks.append({
                "command": item.get("command", ""),
                "reason": item.get("reason", ""),
                "run_id": item.get("run_id", ""),
            })

        return {
            "preferred_tools": preferred_tools[:8],
            "caution_tools": caution_tools[:8],
            "preferred_skills": preferred_skills[:8],
            "routing_hints": routing_hints[:8],
            "recent_risk_events": recent_risks,
        }

    @staticmethod
    def _skill_from_sequence(sequence: list[str], intent: str) -> str:
        text = " ".join([intent, *sequence]).lower()
        if "airsim_search_target" in sequence:
            return "skill:search"
        if "return" in text or "home" in text or "返航" in text or "返回" in text:
            return "skill:return_home"
        if any(tool in sequence for tool in ("drone_fly_to", "drone_move_relative", "drone_takeoff")):
            return "skill:navigation"
        return ""

    @staticmethod
    def _candidate_match_terms(candidate: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        for value in [candidate.get("intent", ""), candidate.get("last_summary", ""), *(candidate.get("example_commands") or [])]:
            text = str(value or "").strip().lower()
            if not text:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in text):
                if len(text) >= 2:
                    terms.append(text[:24])
                continue
            for token in text.replace("_", " ").replace("-", " ").split():
                token = token.strip()
                if len(token) >= 3:
                    terms.append(token[:32])
        deduped: list[str] = []
        for term in terms:
            if term and term not in deduped:
                deduped.append(term)
        return deduped[:8]

    def snapshot(self) -> dict[str, Any]:
        working_state = dict(self._data.get("session", {}))
        facts = self._data.get("facts") or {}
        fact_rows = [
            {"key": key, "value": str(item.get("value") or "")[:300], "tags": item.get("tags", []), "updated_at": item.get("updated_at", 0.0)}
            for key, item in sorted(facts.items(), key=lambda kv: float(kv[1].get("updated_at") or 0.0), reverse=True)[:8]
            if isinstance(item, dict)
        ]
        return {
            "session": working_state,
            "working_state": working_state,
            "missions": list(reversed(self._data.get("missions", [])[-8:])),
            "lessons": list(reversed(self._data.get("lessons", [])[-8:])),
            "risk_events": list(reversed(self._data.get("risk_events", [])[-8:])),
            "tool_stats": self._data.get("tool_stats", {}),
            "skill_candidates": list(reversed(self._data.get("skill_candidates", [])[-8:])),
            "facts": fact_rows,
            "runs": list(reversed(self._data.get("runs", [])[-3:])),
            "guidance": self.guidance(),
        }
