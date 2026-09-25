"""Session / attachment file storage.

The on-disk session layout, the aggressive trimming applied before a session is
written or handed to the UI, and the readers. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .settings_store import REPO_ROOT


SESSIONS_DIR = REPO_ROOT / "src" / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def sessions_dir() -> Path:
    """The sessions directory.

    Runtime methods go through this accessor instead of importing the constant,
    so a test that redirects sessions to a tmp_path patches exactly one place
    (session_store.SESSIONS_DIR) and both the reader and the writer follow.
    """
    return SESSIONS_DIR

SESSION_MEMORY_DIR = REPO_ROOT / ".airsim_agent" / "session_memory"

_SESSION_DROP_DETAIL_KEYS = ("agent_state", "observations")

_UI_KEEP_LOOP_STATE_KEYS = (
    "decisions", "results", "command", "summary", "status", "run_id",
    "started_at", "finished_at", "verification_status", "max_steps",
)

_UI_KEEP_RESULT_KEYS = ("message", "status")

def _slim_result_data(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: value[key] for key in _UI_KEEP_RESULT_KEYS if key in value}

def _trim_session_message(message: Any, for_payload: bool = False) -> Any:
    """剥掉消息 details 里的大字段，返回副本（不改内存里的原对象）。

    ``for_payload=True`` 时进一步瘦身：界面只读 results[].data.message 与
    plan.steps[].result.message/status，工具原始返回对渲染没用。
    """
    if not isinstance(message, dict):
        return message
    details = message.get("details")
    if not isinstance(details, dict):
        return message
    slim = {key: value for key, value in details.items() if key not in _SESSION_DROP_DETAIL_KEYS}
    loop_state = slim.get("loop_state")
    if isinstance(loop_state, dict):
        loop_state = {key: value for key, value in loop_state.items() if key != "observations"}
        loop_state["observations_omitted"] = True
        if for_payload:
            loop_state = {
                key: value for key, value in loop_state.items()
                if key in _UI_KEEP_LOOP_STATE_KEYS or key == "observations_omitted"
            }
            results = loop_state.get("results")
            if isinstance(results, list):
                loop_state["results"] = [
                    {**item, "data": _slim_result_data(item.get("data"))} if isinstance(item, dict) else item
                    for item in results
                ]
        slim["loop_state"] = loop_state
    if for_payload:
        plan = slim.get("plan")
        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
            slim["plan"] = {
                **plan,
                "steps": [
                    {**step, "result": _slim_result_data(step.get("result"))}
                    if isinstance(step, dict) and "result" in step else step
                    for step in plan["steps"]
                ],
            }
    trimmed = dict(message)
    trimmed["details"] = slim
    return trimmed

def trim_loop_state_payload(payload: Any) -> Any:
    """run 顶层 loop_state 的瘦身：丢掉 observations 与界面不读的大块。

    run.to_dict() 里 loop_state.observations 是工具观测原文（可到数百 KB），
    界面只用 decisions / results 画时间线。
    """
    if not isinstance(payload, dict):
        return payload
    loop_state = payload.get("loop_state")
    if not isinstance(loop_state, dict):
        return payload
    slim = {
        key: value for key, value in loop_state.items()
        if key in _UI_KEEP_LOOP_STATE_KEYS or key == "observations_omitted"
    }
    if "observations" in loop_state:
        slim["observations_omitted"] = True
    results = slim.get("results")
    if isinstance(results, list):
        slim["results"] = [
            {**item, "data": _slim_result_data(item.get("data"))} if isinstance(item, dict) else item
            for item in results
        ]
    return {**payload, "loop_state": slim}

_SESSION_WRITE_LOCK = threading.RLock()

_REPLACE_RETRIES = 3

def _sessions_by_mtime() -> list[Path]:
    """按修改时间倒序列出会话文件；stat 失败（并发删除）时退回按名字排。"""
    def sort_key(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(SESSIONS_DIR.glob("session_*.json"), key=sort_key, reverse=True)

def read_session_file(path: Path) -> dict[str, Any] | None:
    """容错读取会话文件。

    历史文件可能被并发/中断的写入拼成"两份 JSON"，标准 json.loads 会以
    "Extra data" 直接失败，会话就会从列表里凭空消失。这里退回解析第一份，
    至少让该会话还能打开（原始文件不修改）。
    """
    text = ""
    for attempt in range(3):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except OSError:
            # 目标可能正被另一个线程 os.replace，短暂退让后重试
            if attempt == 2:
                return None
            time.sleep(0.02 * (attempt + 1))
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
        # 文件被写成"多份 JSON 拼接"时，取最后一份能解析的文档（最新的状态）
        decoder = json.JSONDecoder()
        rest = text.lstrip()
        while rest.strip():
            try:
                document, end = decoder.raw_decode(rest)
            except ValueError:
                break
            if isinstance(document, dict):
                data = document
            rest = rest[end:].lstrip()
    return data if isinstance(data, dict) else None
