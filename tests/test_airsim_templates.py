"""AirSim settings.json template tests: listing and one-click apply with backup."""

from __future__ import annotations

from src.agent.runtime import AIRSIM_SETTINGS_TEMPLATES, AgentRuntime


def _runtime(tmp_path, monkeypatch) -> AgentRuntime:
    rt = object.__new__(AgentRuntime)
    target = tmp_path / "Documents" / "AirSim" / "settings.json"
    monkeypatch.setattr(AgentRuntime, "_airsim_settings_path", staticmethod(lambda: target))
    return rt


def test_info_lists_all_three_templates(tmp_path, monkeypatch) -> None:
    info = _runtime(tmp_path, monkeypatch).airsim_settings_info()
    ids = {t["id"] for t in info["templates"]}
    assert ids == set(AIRSIM_SETTINGS_TEMPLATES)
    assert all(t["exists"] for t in info["templates"])
    assert info["target_path"].endswith("settings.json")
    assert info["target_exists"] is False


def test_apply_writes_template_and_backs_up(tmp_path, monkeypatch) -> None:
    rt = _runtime(tmp_path, monkeypatch)
    target = rt._airsim_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"SettingsVersion": 1.2, "SimMode": "old"}', encoding="utf-8")

    result = rt.apply_airsim_settings_template("airsim_simpleflight_multirotor")

    assert result["ok"] is True
    assert result["backup_path"] is not None
    written = target.read_text(encoding="utf-8")
    assert '"SimMode": "Multirotor"' in written
    assert '"Drone3"' in written  # 多机模板
    backup = __import__("pathlib").Path(result["backup_path"])
    assert backup.is_file()
    assert '"SimMode": "old"' in backup.read_text(encoding="utf-8")


def test_apply_creates_dir_when_missing(tmp_path, monkeypatch) -> None:
    rt = _runtime(tmp_path, monkeypatch)
    result = rt.apply_airsim_settings_template("px4_mavlink_udp_sitl")
    assert result["ok"] is True
    written = rt._airsim_settings_path().read_text(encoding="utf-8")
    assert '"VehicleType": "PX4Multirotor"' in written
    assert '"UseUdp": true' in written


def test_apply_unknown_template_fails(tmp_path, monkeypatch) -> None:
    result = _runtime(tmp_path, monkeypatch).apply_airsim_settings_template("nope")
    assert result["ok"] is False
