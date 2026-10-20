"""相机下拉的事实来源：AirSim settings.json 里实际声明的相机。

事故现场：相机面板写死了 0..4 五个选项，而 settings.json 只声明了
``CameraImage`` / ``CameraDepth`` 两个相机；面板却同时显示 5 个假选项和
一个 "CameraImage · custom"，操作员无法判断哪一路真的存在。
"""

from __future__ import annotations

import json

import pytest

from src.agent.backends import read_airsim_camera_catalog


def _write_settings(tmp_path, payload: dict) -> str:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_named_cameras_are_the_only_options_and_empty_capture_is_dropped(tmp_path, monkeypatch) -> None:
    path = _write_settings(
        tmp_path,
        {
            "Vehicles": {
                "PX4": {
                    "Cameras": {
                        "CameraImage": {"CaptureSettings": [{"ImageType": 0}, {"ImageType": 1}]},
                        # 只留位、不采集的相机不出图，不该出现在下拉里
                        "CameraDepth": {"CaptureSettings": []},
                    }
                }
            }
        },
    )
    monkeypatch.setenv("AIRSIM_SETTINGS_PATH", path)

    catalog = read_airsim_camera_catalog()

    assert catalog["by_vehicle"] == {"PX4": ["CameraImage"]}
    assert catalog["default"] == ["CameraImage"]


def test_vehicle_without_cameras_gets_airsim_defaults(tmp_path, monkeypatch) -> None:
    path = _write_settings(tmp_path, {"Vehicles": {"PX4": {"VehicleType": "PX4Multirotor"}}})
    monkeypatch.setenv("AIRSIM_SETTINGS_PATH", path)

    catalog = read_airsim_camera_catalog()

    assert catalog["by_vehicle"] == {"PX4": ["0", "1", "2", "3", "4"]}
    assert catalog["default"] == ["0", "1", "2", "3", "4"]


def test_first_vehicle_is_the_panel_default(tmp_path, monkeypatch) -> None:
    path = _write_settings(
        tmp_path,
        {
            "Vehicles": {
                "Alpha": {"Cameras": {"Front": {"CaptureSettings": [{"ImageType": 0}]}}},
                "Beta": {"Cameras": {"Belly": {"CaptureSettings": [{"ImageType": 0}]}}},
            }
        },
    )
    monkeypatch.setenv("AIRSIM_SETTINGS_PATH", path)

    catalog = read_airsim_camera_catalog()

    assert catalog["by_vehicle"] == {"Alpha": ["Front"], "Beta": ["Belly"]}
    assert catalog["default"] == ["Front"]


def test_missing_settings_file_falls_back_to_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIRSIM_SETTINGS_PATH", str(tmp_path / "does-not-exist.json"))

    catalog = read_airsim_camera_catalog()

    assert catalog["by_vehicle"] == {}
    assert catalog["default"] == ["0", "1", "2", "3", "4"]


def test_cameras_block_that_captures_nothing_still_lists_its_names(tmp_path, monkeypatch) -> None:
    """整块都不能采集时保留名字：面板要如实展示配置，而不是给一个空下拉。"""
    path = _write_settings(
        tmp_path,
        {"Vehicles": {"PX4": {"Cameras": {"CameraDepth": {"CaptureSettings": []}}}}},
    )
    monkeypatch.setenv("AIRSIM_SETTINGS_PATH", path)

    catalog = read_airsim_camera_catalog()

    assert catalog["default"] == ["CameraDepth"]
