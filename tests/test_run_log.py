"""Unit tests for the run event log (run_log.py)."""

from __future__ import annotations

import json

from src.agent.run_log import RunLog, RunLogReader, RunLogStore, _redact_value


def test_run_log_appends_and_reads(tmp_path):
    log = RunLog("run_123", base_dir=tmp_path)
    log.write("run.start", {"command": "起飞并拍照", "mode": "execute"})
    log.write("loop.decision", {"action": "drone_takeoff", "reason": "先起飞"})
    reader = RunLogReader("run_123", base_dir=tmp_path)
    events = reader.events()
    assert len(events) == 2
    assert events[0]["type"] == "run.start"
    assert events[0]["version"] == 1
    assert events[1]["payload"]["action"] == "drone_takeoff"
    assert events[1]["seq"] == 1


def test_run_log_redacts_image_payloads(tmp_path):
    log = RunLog("run_img", base_dir=tmp_path)
    log.write(
        "tool.result",
        {
            "tool": "airsim_take_photo",
            "ok": True,
            "data": {"image_base64": "x" * 5000, "status": "ok", "saved_to": "captures/a.png"},
        },
    )
    lines = log.path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])["payload"]
    redacted = payload["data"]["image_base64"]
    assert isinstance(redacted, dict) and redacted.get("redacted") is True
    assert redacted["bytes"] == 5000
    assert len(redacted["sha256"]) == 16
    assert payload["data"]["status"] == "ok"  # non-image fields survive


def test_run_log_never_raises_on_bad_input(tmp_path):
    log = RunLog("run_bad", base_dir=tmp_path)
    log.write("tool.result", {"data": object()})  # non-serializable payload
    assert log.count() == 1  # default=str fallback keeps the line


def test_redact_value_truncates_huge_strings():
    value = _redact_value({"text": "a" * 20_000})
    assert len(value["text"]) <= 8000 + len(" ...[truncated]")


def test_redact_value_caps_lists():
    value = _redact_value({"items": list(range(100))})
    assert len(value["items"]) <= 61
    assert value["items"][-1]["redacted"] is True


def test_replay_rebuilds_summary(tmp_path):
    log = RunLog("run_replay", base_dir=tmp_path)
    log.write("run.start", {"command": "搜索目标", "model_id": "m1"})
    log.write("loop.decision", {"action": "skill:search", "reason": "开始搜索"})
    log.write("tool.result", {"tool": "skill:search", "ok": True, "data": {"status": "ok"}})
    log.write("run.end", {"status": "completed", "summary": "搜索完成", "verification_status": "ok"})
    replay = RunLogReader("run_replay", base_dir=tmp_path).replay()
    assert replay["command"] == "搜索目标"
    assert replay["model_id"] == "m1"
    assert replay["status"] == "completed"
    assert replay["summary"] == "搜索完成"
    assert len(replay["decisions"]) == 1
    assert len(replay["results"]) == 1


def test_store_lists_and_prunes(tmp_path):
    for i in range(5):
        RunLog(f"run_{i}", base_dir=tmp_path).write("run.start", {"command": f"c{i}"})
    store = RunLogStore(base_dir=tmp_path)
    listed = store.list(limit=10)
    assert len(listed) == 5
    reader = store.read("run_3")
    assert reader is not None and reader.exists()
    assert store.read("missing") is None
    removed = RunLog.prune(base_dir=tmp_path, keep=2)
    assert removed == 3
    assert len(list(store.runs_dir.glob("*.jsonl"))) == 2
