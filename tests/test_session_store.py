"""Session store: 容错读取、元数据缓存、落盘瘦身与原子写。

背景：会话文件里 loop_state.observations 单条消息就能到 500KB（49 条消息的
会话超过 14MB），而 UI 每秒都轮询 /api/state、切会话时还要整份下发，所以
这些用例盯住"读得快、传得小、别再写坏"。
"""

import json
import time
from pathlib import Path

from src.agent import runtime as runtime_module
from src.agent.runtime import (
    AgentRuntime,
    _trim_session_message,
    read_session_file,
    trim_loop_state_payload,
)


def _bare_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_module, "SESSIONS_DIR", tmp_path)
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._session_meta_cache = {}
    return runtime


def _write_session(tmp_path: Path, session_id: str, messages: list[dict]) -> Path:
    path = tmp_path / f"{session_id}.json"
    path.write_text(json.dumps({
        "id": session_id,
        "name": "测试会话",
        "messages": messages,
        "created_at": 1.0,
        "updated_at": 2.0,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _big_message(msg_id: str = "m1") -> dict:
    return {
        "id": msg_id,
        "role": "assistant",
        "content": "ok",
        "details": {
            "mode": "execute",
            "agent_state": {"vehicle": {"x": 1.0}},
            "plan": {"steps": [{"tool": "drone_takeoff", "result": {"message": "起飞完成", "huge": "x" * 5000}}]},
            "process_trace": [{"tool": "drone_takeoff", "title": "起飞", "status": "completed"}],
            "thought_trace": [{"title": "思考"}],
            "loop_state": {
                "decisions": [{"action": "drone_takeoff", "reason": "指令要求"}],
                "results": [{"step_index": 1, "ok": True, "data": {"message": "起飞完成", "huge": "y" * 5000}}],
                "original_plan": {"steps": ["x" * 4000]},
                "observations": [{"payload": "z" * 20000}],
                "command": "起飞到3米",
                "summary": "已完成",
            },
        },
    }


# ── 容错读取：被写坏成"两份 JSON"的会话文件也要能打开 ────────────────────

def test_read_session_file_recovers_concatenated_json(tmp_path):
    """历史上有 3 个会话就是这样坏掉并从列表里消失的。"""
    broken = tmp_path / "session_broken.json"
    first = {"id": "session_broken", "name": "第一份", "messages": [{"id": "a"}]}
    second = {"id": "session_broken", "name": "第二份", "messages": []}
    broken.write_text(json.dumps(first, ensure_ascii=False) + json.dumps(second, ensure_ascii=False), encoding="utf-8")

    with __import__("pytest").raises(json.JSONDecodeError):
        json.loads(broken.read_text(encoding="utf-8"))

    recovered = read_session_file(broken)

    assert recovered is not None
    assert recovered["name"] == "第一份"


def test_read_session_file_returns_none_for_garbage(tmp_path):
    junk = tmp_path / "session_junk.json"
    junk.write_text("这不是 JSON", encoding="utf-8")

    assert read_session_file(junk) is None


def test_list_sessions_includes_recovered_session(tmp_path, monkeypatch):
    runtime = _bare_runtime(tmp_path, monkeypatch)
    good = {"id": "s1", "name": "正常", "messages": [{"id": "a"}], "updated_at": 2.0}
    (tmp_path / "session_s1.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    broken = {"id": "s2", "name": "被写坏的", "messages": [{"id": "a"}, {"id": "b"}], "updated_at": 1.0}
    (tmp_path / "session_s2.json").write_text(
        json.dumps(broken, ensure_ascii=False) + json.dumps({"id": "s2", "messages": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    sessions = runtime.list_sessions()

    assert {s["id"] for s in sessions} == {"s1", "s2"}
    assert next(s for s in sessions if s["id"] == "s2")["message_count"] == 2


# ── 元数据缓存：每轮轮询不该重解析几 MB 的文件 ──────────────────────────

def test_list_sessions_caches_until_file_changes(tmp_path, monkeypatch):
    runtime = _bare_runtime(tmp_path, monkeypatch)
    _write_session(tmp_path, "session_s1", [{"id": "a"}])

    calls = {"n": 0}
    original = runtime_module.read_session_file

    def counting_read(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(runtime_module, "read_session_file", counting_read)

    first = runtime.list_sessions()
    second = runtime.list_sessions()

    assert calls["n"] == 1, "第二次轮询不应该重新解析会话文件"
    assert first == second

    # 文件真的变了才重新解析
    path = tmp_path / "session_s1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["messages"].append({"id": "b"})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    third = runtime.list_sessions()

    assert calls["n"] == 2
    assert third[0]["message_count"] == 2


# ── 瘦身：进程内内存对象不被改动，只瘦副本 ─────────────────────────────

def test_trim_drops_blobs_without_mutating_source():
    message = _big_message()
    before_observations = len(message["details"]["loop_state"]["observations"])

    trimmed = _trim_session_message(message, for_payload=True)

    # 原对象保持完整（运行时的 loop_state 还要用）
    assert len(message["details"]["loop_state"]["observations"]) == before_observations
    assert "agent_state" in message["details"]
    # 副本被瘦身
    loop_state = trimmed["details"]["loop_state"]
    assert "observations" not in loop_state and loop_state["observations_omitted"] is True
    assert "agent_state" not in trimmed["details"]
    assert loop_state["results"][0]["data"] == {"message": "起飞完成"}
    assert trimmed["details"]["plan"]["steps"][0]["result"] == {"message": "起飞完成"}
    # 界面要用的东西一个都不能少
    assert loop_state["decisions"][0]["action"] == "drone_takeoff"
    assert loop_state["command"] == "起飞到3米"
    assert trimmed["details"]["process_trace"][0]["title"] == "起飞"
    assert trimmed["details"]["thought_trace"] == [{"title": "思考"}]
    assert trimmed["details"]["mode"] == "execute"


def test_slimmed_payload_is_far_smaller():
    message = _big_message()
    raw = len(json.dumps(message, ensure_ascii=False))
    slim = len(json.dumps(_trim_session_message(message, for_payload=True), ensure_ascii=False))

    assert slim < raw / 5


def test_trim_loop_state_payload_for_run():
    run = {"run_id": "r1", "plan": {"steps": []}, "loop_state": _big_message()["details"]["loop_state"]}

    slim = trim_loop_state_payload(run)

    assert "observations" not in slim["loop_state"]
    assert slim["loop_state"]["results"][0]["data"] == {"message": "起飞完成"}
    assert slim["run_id"] == "r1"


def test_trim_leaves_messages_without_details_untouched():
    message = {"id": "m1", "role": "user", "content": "你好"}

    assert _trim_session_message(message) is message


# ── 落盘：原子写 + 剥掉大字段 ─────────────────────────────────────────

def test_save_session_strips_blobs_and_writes_atomically(tmp_path, monkeypatch):
    runtime = _bare_runtime(tmp_path, monkeypatch)
    message = _big_message()
    data = {"id": "session_save", "name": "落盘", "messages": [message], "created_at": 1.0}

    runtime._save_session(data)

    path = tmp_path / "session_save.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    loop_state = written["messages"][0]["details"]["loop_state"]
    assert "observations" not in loop_state, "落盘前必须剥掉观测原文"
    assert "agent_state" not in written["messages"][0]["details"]
    assert path.stat().st_size < len(json.dumps(data, ensure_ascii=False)) / 2
    # 临时文件不能残留
    assert list(tmp_path.glob("*.tmp*")) == []


def test_saved_session_reloads_through_cache(tmp_path, monkeypatch):
    runtime = _bare_runtime(tmp_path, monkeypatch)
    runtime._save_session({"id": "session_rt", "name": "往返", "messages": [_big_message()], "created_at": 1.0})

    sessions = runtime.list_sessions()

    assert [s["id"] for s in sessions] == ["session_rt"]
    assert sessions[0]["message_count"] == 1


def test_list_sessions_reports_input_count(tmp_path, monkeypatch):
    """会话列表同时给出"输入条数"：导航条一条输入一根线，两者要对得上。"""
    runtime = _bare_runtime(tmp_path, monkeypatch)
    _write_session(tmp_path, "session_s1", [
        {"id": "u1", "role": "user"},
        {"id": "a1", "role": "assistant"},
        {"id": "u2", "role": "user"},
        {"id": "a2", "role": "assistant"},
        {"id": "u3", "role": "user"},
    ])

    meta = runtime.list_sessions()[0]

    assert meta["message_count"] == 5
    assert meta["input_count"] == 3
