"""飞行记录会话收尾的契约：`FlightLogSession.stop()` 返回 **dict**。

历史事故（2026-10-20）：把 replay 换成就地重建的 flightlog 后，收尾代码仍按
旧的对象写法读 `summary.name`，在 `finally` 里抛 AttributeError，导致后面的
`_close_run_log` 与执行槽释放都被跳过——界面一直报"旧任务未能及时停止，请稍后重试"。
"""

from __future__ import annotations

import threading

from src.agent.flightlog_api import FlightLogMixin


class _FakeSession:
    def __init__(self, summary: dict) -> None:
        self._summary = summary
        self.stopped = False

    def stop(self) -> dict:
        self.stopped = True
        return dict(self._summary)


class _Runtime(FlightLogMixin):
    """只测 mixin：记录事件、持有锁与会话引用。"""

    def __init__(self) -> None:
        self._flightlog_lock = threading.Lock()
        self._active_flightlog = None
        self._manual_flightlog = None
        self.events: list[dict] = []

    def _append_event(self, level: str, source: str, message: str, data: dict) -> None:
        self.events.append({"level": level, "source": source, "message": message, "data": data})


def test_stop_flightlog_session_accepts_writer_summary_dict():
    runtime = _Runtime()
    session = _FakeSession({"name": "run_42", "frame_count": 7, "duration_s": 1.4})
    runtime._active_flightlog = session

    summary = runtime._stop_flightlog_session()

    assert session.stopped is True
    assert runtime._active_flightlog is None
    assert summary["name"] == "run_42"
    assert summary["frame_count"] == 7
    assert "run_42" in runtime.events[-1]["message"]
    assert "7 帧" in runtime.events[-1]["message"]


def test_stop_manual_flightlog_accepts_writer_summary_dict():
    runtime = _Runtime()
    runtime._manual_flightlog = _FakeSession({"name": "manual_1", "frame_count": 3})

    result = runtime.stop_manual_flightlog()

    assert result["ok"] is True
    assert result["name"] == "manual_1"
    assert runtime._manual_flightlog is None


def test_stop_without_a_session_is_a_noop():
    runtime = _Runtime()
    assert runtime._stop_flightlog_session() is None
    assert runtime.stop_manual_flightlog()["ok"] is False
