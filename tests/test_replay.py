"""Tests for the replay subsystem: recorder, session recording, and listing."""

from __future__ import annotations

import time

import pytest

from src.replay.recorder import DEFAULT_REPLAY_DIR, ReplayRecorder
from src.replay.player import ReplayPlayer
from src.replay.session import (
    ReplaySession,
    list_replay_sessions,
    read_replay_session,
)


def _fake_provider(position: dict | None = None) -> dict:
    return {
        "backend": "airsim",
        "drone": {
            "position_ned": position or {"x": 1.0, "y": 2.0, "z": -3.0},
            "armed": True,
            "flying": True,
        },
    }


def test_recorder_default_dir_is_project_local(tmp_path, monkeypatch) -> None:
    # The historical default (user home ~/src/replay_data) was a bug:
    # recordings must live inside the repo's gitignored data dir.
    assert DEFAULT_REPLAY_DIR.parts[-3:] == ("src", "data", "replay")
    monkeypatch.chdir(tmp_path)
    recorder = ReplayRecorder(session_name="probe")
    assert recorder.data_dir.resolve() == DEFAULT_REPLAY_DIR.resolve()


def test_recorder_meta_and_player_roundtrip(tmp_path) -> None:
    recorder = ReplayRecorder(session_name="roundtrip", data_dir=str(tmp_path))
    recorder.write_meta({"run_id": "run_1", "command": "起飞"})
    recorder.start()
    recorder.record_telemetry({"position_ned": {"x": 1.0, "y": 2.0, "z": -3.0}})
    recorder.record_telemetry({"position_ned": {"x": 2.0, "y": 2.0, "z": -3.0}})
    recorder.record_airsim_state({"pose": {"x": 1.0}})
    recorder.stop()

    player = ReplayPlayer(str(recorder.session_dir))
    assert player.load() is True
    received: list[dict] = []
    player.subscribe(received.append)
    player.play()
    assert len(received) == 2
    assert received[1]["position_ned"]["x"] == 2.0


def test_replay_session_records_periodically(tmp_path) -> None:
    session = ReplaySession(
        "periodic",
        snapshot_provider=_fake_provider,
        interval=0.02,
        meta={"mode": "manual"},
        data_dir=str(tmp_path),
    )
    session.start()
    try:
        deadline = time.time() + 1.0
        while session.frame_count < 3 and time.time() < deadline:
            time.sleep(0.02)
    finally:
        summary = session.stop()

    assert summary.frame_count >= 3
    assert summary.meta.get("mode") == "manual"
    assert summary.name == "periodic"
    assert not session.is_recording


def test_replay_session_stop_without_start(tmp_path) -> None:
    session = ReplaySession(
        "never-started",
        snapshot_provider=_fake_provider,
        data_dir=str(tmp_path),
    )
    summary = session.stop()  # must not raise
    assert summary.frame_count == 0
    assert summary.finished_at >= summary.started_at


def test_replay_session_survives_provider_errors(tmp_path) -> None:
    calls = {"n": 0}

    def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] <= 2:
            return _fake_provider()
        raise RuntimeError("backend unreachable")

    session = ReplaySession(
        "flaky",
        snapshot_provider=flaky,
        interval=0.01,
        data_dir=str(tmp_path),
    )
    session.start()
    deadline = time.time() + 1.0
    while session.is_recording and time.time() < deadline:
        time.sleep(0.01)
    summary = session.stop()
    assert summary.frame_count == 2
    assert "backend unreachable" in summary.error


def test_list_and_read_sessions(tmp_path) -> None:
    session = ReplaySession(
        "run_abc",
        snapshot_provider=lambda: _fake_provider({"x": 5.0, "y": 6.0, "z": -3.0}),
        interval=0.01,
        meta={"run_id": "run_abc", "command": "巡航"},
        data_dir=str(tmp_path),
    )
    session.start()
    deadline = time.time() + 1.0
    while session.frame_count < 2 and time.time() < deadline:
        time.sleep(0.01)
    session.stop()

    sessions = list_replay_sessions(data_dir=str(tmp_path))
    names = {entry["name"] for entry in sessions}
    assert "run_abc" in names
    entry = next(item for item in sessions if item["name"] == "run_abc")
    assert entry["meta"].get("run_id") == "run_abc"
    assert entry["frame_count"] >= 2

    loaded = read_replay_session("run_abc", data_dir=str(tmp_path))
    assert loaded is not None
    assert loaded["meta"]["command"] == "巡航"
    assert loaded["frame_count"] >= 2
    assert loaded["frames"][0]["data"]["drone"]["position_ned"]["x"] == 5.0


def test_read_replay_session_prevents_path_traversal(tmp_path) -> None:
    assert read_replay_session("..", data_dir=str(tmp_path)) is None
    assert read_replay_session("../other", data_dir=str(tmp_path)) is None
    assert read_replay_session("", data_dir=str(tmp_path)) is None
    assert read_replay_session("missing", data_dir=str(tmp_path)) is None


def test_empty_replay_dir_lists_cleanly(tmp_path) -> None:
    assert list_replay_sessions(data_dir=str(tmp_path)) == []
