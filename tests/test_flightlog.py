"""飞行记录（flightlog）数据层契约：帧格式、写盘、会话、索引、保留策略、读取。

目的只有一个：**每一次飞行的记录都完整、可追溯、不会互相追加**。前端分析后做，
这里只锁数据层的行为。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.flightlog import (
    FLIGHTLOG_SCHEMA_VERSION,
    FlightLogSession,
    FlightLogWriter,
    build_frame,
    list_sessions,
    read_session,
    summarize_frames,
)
from src.flightlog import sessions as flightlog_sessions


def _snapshot(x: float = 0.0, z: float = -3.0, vx: float = 2.0, flying: bool = True) -> dict:
    return {
        "backend": "airsim",
        "drone": {
            "position_ned": {"x": x, "y": 0.0, "z": z},
            "velocity_ned": {"vx": vx, "vy": 0.0, "vz": -0.2},
            "attitude_rad": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            "armed": True,
            "flying": flying,
            "mode": "airsim_simpleflight",
            "landed_state": "flying",
            "has_collided": False,
        },
        "vehicles": [{"vehicle_name": "Drone1"}],
        "tool_internal": "must not be recorded",
    }


def _frame(x: float = 0.0) -> dict:
    return {"t": 0.0, "data": build_frame(_snapshot(x=x))}


def test_build_frame_keeps_only_telemetry():
    frame = build_frame(_snapshot())
    assert frame["backend"] == "airsim"
    assert frame["drone"]["position_ned"]["x"] == 0.0
    assert frame["vehicles"][0]["vehicle_name"] == "Drone1"
    assert "tool_internal" not in frame  # 工具内部字段不进飞行记录


def test_summary_computes_distance_altitude_and_speeds():
    frames = [_frame(0.0), _frame(3.0), _frame(6.0)]
    summary = summarize_frames(frames, duration_s=1.5)
    assert summary["frame_count"] == 3
    assert summary["duration_s"] == 1.5
    assert summary["distance_m"] == 6.0
    assert summary["max_altitude_m"] == 3.0
    assert summary["min_altitude_m"] == 3.0
    assert summary["max_horizontal_speed_mps"] == 2.0
    assert summary["flying_frames"] == 3
    assert summary["vehicles"] == ["Drone1"]


def test_writer_creates_versioned_meta_frames_and_summary(tmp_path: Path):
    writer = FlightLogWriter("mission_a", meta={"run_id": "run_1", "mode": "execute"}, data_dir=tmp_path)
    writer.start()
    writer.record_frame(build_frame(_snapshot(x=0.0)))
    writer.record_frame(build_frame(_snapshot(x=2.0)))
    summary = writer.stop()

    meta = json.loads((tmp_path / "mission_a" / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == FLIGHTLOG_SCHEMA_VERSION
    assert meta["run_id"] == "run_1"
    assert meta["sample_interval_s"] > 0

    lines = (tmp_path / "mission_a" / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["t"] >= 0 and first["data"]["backend"] == "airsim"

    on_disk = json.loads((tmp_path / "mission_a" / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == FLIGHTLOG_SCHEMA_VERSION
    assert on_disk["frame_count"] == 2
    assert summary["frame_count"] == 2


def test_session_records_periodically_and_updates_index(tmp_path: Path):
    session = FlightLogSession(
        "run_42",
        snapshot_provider=lambda: _snapshot(),
        interval=0.05,
        meta={"run_id": "run_42"},
        data_dir=str(tmp_path),
    )
    session.start()
    time.sleep(0.25)
    summary = session.stop()

    assert summary["name"] == "run_42"
    assert summary["frame_count"] >= 1, summary
    sessions = list_sessions(tmp_path)
    assert [row["name"] for row in sessions] == ["run_42"]
    assert sessions[0]["summary"]["frame_count"] == summary["frame_count"]
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["sessions"]


def test_read_session_returns_meta_summary_and_frames(tmp_path: Path):
    session = FlightLogSession(
        "run_7", snapshot_provider=lambda: _snapshot(), interval=0.05, data_dir=str(tmp_path)
    )
    session.start()
    time.sleep(0.15)
    session.stop()

    data = read_session("run_7", data_dir=tmp_path)
    assert data is not None
    assert data["meta"]["schema_version"] == FLIGHTLOG_SCHEMA_VERSION
    assert data["summary"]["frame_count"] >= 1
    assert data["frames"]
    assert read_session("does_not_exist", data_dir=tmp_path) is None


def test_read_session_rejects_path_traversal(tmp_path: Path):
    (tmp_path / ".." / "escape").mkdir(exist_ok=True)
    assert read_session("../escape", data_dir=tmp_path) is None
    assert read_session("run/../../etc", data_dir=tmp_path) is None


def test_retention_drops_oldest_sessions(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(flightlog_sessions, "DEFAULT_MAX_SESSIONS", 2)
    for name in ("old", "middle", "new"):
        session = FlightLogSession(
            name, snapshot_provider=lambda: _snapshot(), interval=0.05, data_dir=str(tmp_path)
        )
        session.start()
        session.stop()
        time.sleep(0.01)
    names = {row["name"] for row in list_sessions(tmp_path)}
    assert names == {"middle", "new"}, names
    assert not (tmp_path / "old").exists()
