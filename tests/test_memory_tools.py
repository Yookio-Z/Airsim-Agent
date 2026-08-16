"""Tests for active memory: facts, run transcripts, recall scoring, and the
agent-visible memory tool dispatch in the runtime."""

from __future__ import annotations

import threading

import pytest

from src.agent.memory import AgentMemory
from src.agent.tool_executor import _agent_memory_tool_cards


def test_remember_fact_and_snapshot(tmp_path):
    memory = AgentMemory(data_dir=tmp_path)
    memory.remember_fact("target_area", "搜索区域在东北角 50m 半径", tags=["search"])
    memory.remember_fact("vehicle_id", "PX4-01")
    snapshot = memory.snapshot()
    keys = [item["key"] for item in snapshot["facts"]]
    assert "target_area" in keys and "vehicle_id" in keys
    target = next(item for item in snapshot["facts"] if item["key"] == "target_area")
    assert target["tags"] == ["search"]


def test_remember_fact_requires_key(tmp_path):
    memory = AgentMemory(data_dir=tmp_path)
    memory.remember_fact("", "no key")  # must not raise
    assert memory.snapshot()["facts"] == []


def test_transcript_is_bounded(tmp_path):
    memory = AgentMemory(data_dir=tmp_path)
    for i in range(60):
        memory.remember_transcript(f"run_{i}", f"任务 {i}", "completed", f"完成 {i}", ["drone_takeoff"])
    runs = memory.snapshot()["runs"]
    assert len(runs) <= 3  # snapshot only exposes the newest few
    all_runs = memory._data["runs"]
    assert len(all_runs) == 50  # store is bounded at 50
    assert all_runs[-1]["run_id"] == "run_59"


def test_recall_matches_ascii_and_cjk(tmp_path):
    memory = AgentMemory(data_dir=tmp_path)
    memory.remember_fact("home", "起飞点坐标 (0,0)")
    memory.remember_fact("search_zone", "红色汽车出没区域")
    memory.remember_transcript("run_1", "搜索红色汽车", "completed", "在东北角找到红色汽车", ["skill:search"])
    hits = memory.recall("红色汽车", limit=5)
    kinds = [item["kind"] for item in hits]
    assert "fact" in kinds and "run" in kinds
    top = hits[0]
    assert top["score"] > 0
    assert memory.recall("不存在的内容xyz", limit=5) == []


def test_recall_requires_query(tmp_path):
    memory = AgentMemory(data_dir=tmp_path)
    memory.remember_fact("a", "b")
    assert memory.recall("") == []


def test_memory_cards_are_declared():
    cards = _agent_memory_tool_cards()
    names = [card["name"] for card in cards]
    assert names == ["memory_recall", "memory_remember"]
    assert cards[0]["inputs"]["query"]


def test_runtime_memory_tool_dispatch(tmp_path):
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    rt.memory = AgentMemory(data_dir=tmp_path)
    rt._lock = threading.RLock()
    rt._current = None

    result = rt._execute_memory_remember({"key": "k1", "value": "v1", "tags": "a,b"})
    assert result.ok is True
    assert result.data["key"] == "k1"
    stored = rt.memory.snapshot()["facts"]
    assert stored[0]["tags"] == ["a", "b"]

    recall = rt._execute_memory_recall({"query": "k1"})
    assert recall.ok is True
    assert recall.data["count"] >= 1
    assert recall.data["results"][0]["kind"] == "fact"

    bad = rt._execute_memory_remember({"value": "no key"})
    assert bad.ok is False
    assert bad.error_code == "INVALID_PARAMS"

    dry = rt._execute_memory_recall({"query": "k1"}, dry_run=True)
    assert dry.data["status"] == "planned"
    assert dry.data["count"] == 0


def test_concurrent_writers_do_not_corrupt_memory_file(tmp_path):
    """Concurrent remember_* calls from different threads must serialize both
    the in-memory mutation and the tmp+rename write (no torn JSON)."""
    import json

    memory = AgentMemory(data_dir=tmp_path)

    def writer_facts():
        for i in range(30):
            memory.remember_fact(f"key_{i}", f"value_{i}", tags=["t"])

    def writer_stats():
        for i in range(30):
            memory.remember_tool_call(f"tool_{i % 5}", ok=(i % 2 == 0))

    threads = [threading.Thread(target=writer_facts), threading.Thread(target=writer_stats)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    raw = (tmp_path / "memory.json").read_text(encoding="utf-8")
    data = json.loads(raw)  # must parse: no interleaved/torn write
    assert len(data["facts"]) == 30
    assert data["tool_stats"]["tool_0"]["calls"] == 6  # 30 calls across 5 tools
    assert len(data["tool_stats"]) == 5
