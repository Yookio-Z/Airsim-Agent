"""相机面板 → RTSP 取流的配置落地测试。

覆盖"面板里配的 RTSP 参数真的会作用到 FFmpeg"这条链路：
传输协议（TCP/UDP）经 OPENCV_FFMPEG_CAPTURE_OPTIONS 传给 FFmpeg，握手超时
经 CAP_PROP_OPEN_TIMEOUT_MSEC 传进去，并且相机面板的 URL/协议优先于 .env。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

from src.modules.frame_source import RtspFrameSource
from src.modules.rtsp_camera_controller import RtspCameraController


class _FakeVideoCapture:
    def __init__(self, frame: np.ndarray | None, opened: bool = True) -> None:
        self._frame = frame
        self._opened = opened

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        if self._frame is None:
            return False, None
        return True, self._frame.copy()

    def release(self) -> None:
        self._opened = False


def _frame() -> np.ndarray:
    return np.full((64, 96, 3), 120, dtype=np.uint8)


@pytest.fixture(autouse=True)
def _clean_ffmpeg_env(monkeypatch):
    # OPENCV_FFMPEG_CAPTURE_OPTIONS 是进程级变量，测试之间必须隔离，
    # 否则上一个用例设的 rtsp_transport 会漏到下一个用例。
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    yield


def test_transport_and_timeout_reach_ffmpeg(monkeypatch) -> None:
    import cv2 as cv2_module

    seen: dict = {}

    def fake_capture(*args, **_kwargs):
        seen["args"] = args
        seen["env"] = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        return _FakeVideoCapture(_frame())

    monkeypatch.setattr(cv2_module, "VideoCapture", fake_capture)
    source = RtspFrameSource("rtsp://host/stream", transport="TCP", open_timeout_sec=5)

    assert source.open() is True
    assert seen["env"] == "rtsp_transport;tcp"
    assert seen["args"][0] == "rtsp://host/stream"
    assert list(seen["args"][2]) == [int(cv2_module.CAP_PROP_OPEN_TIMEOUT_MSEC), 5000]


def test_without_transport_opencv_default_is_kept(monkeypatch) -> None:
    import cv2 as cv2_module

    seen: dict = {}

    def fake_capture(*args, **_kwargs):
        seen["args"] = args
        seen["env"] = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        return _FakeVideoCapture(_frame())

    monkeypatch.setattr(cv2_module, "VideoCapture", fake_capture)
    source = RtspFrameSource("rtsp://host/stream")

    assert source.open() is True
    # 没指定协议就不碰环境变量，也不额外传构造参数（保持既有行为）
    assert seen["env"] is None
    assert len(seen["args"]) == 1


def test_controller_forwards_transport(monkeypatch) -> None:
    import cv2 as cv2_module

    monkeypatch.setattr(cv2_module, "VideoCapture", lambda *a, **k: _FakeVideoCapture(_frame()))
    controller = RtspCameraController("rtsp://host/stream", transport="tcp", open_timeout_sec=6)

    assert controller.connect().connected is True
    assert controller.get_connection_info()["transport"] == "tcp"
    assert controller._source.transport == "tcp"
    assert controller._source.open_timeout_sec == 6


def test_camera_settings_normalizes_transport() -> None:
    from src.agent.runtime import _camera_settings, _default_camera_settings

    assert _default_camera_settings()["transport"] == "tcp"
    assert _camera_settings({"camera": {"transport": "UDP"}})["transport"] == "udp"
    # 拼错/缺失时回落到默认，不能把无效值透给 FFmpeg
    assert _camera_settings({"camera": {"transport": "rtsp"}})["transport"] == "tcp"
    assert _camera_settings({"camera": {}})["transport"] == "tcp"


def test_perception_rtsp_config_prefers_camera_panel(monkeypatch) -> None:
    from src.agent import runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "_camera_settings",
        lambda settings=None: {"source": "rtsp", "url": "rtsp://panel/stream", "transport": "udp"},
    )
    assert runtime_module._perception_rtsp_config() == ("rtsp://panel/stream", "udp")

    # 面板没选 RTSP 时返回空，让感知轴回落到 .env 的配置
    monkeypatch.setattr(
        runtime_module,
        "_camera_settings",
        lambda settings=None: {"source": "airsim", "url": "rtsp://panel/stream", "transport": "tcp"},
    )
    assert runtime_module._perception_rtsp_config() == ("", "")


def test_perception_axis_prefers_provider_over_env(monkeypatch) -> None:
    from src.modules.perception_axis import PerceptionAxis

    axis = PerceptionAxis(
        profile=SimpleNamespace(frame_source="rtsp", deploy="local"),
        rtsp_url="rtsp://env/stream",
        rtsp_transport="tcp",
        rtsp_config_provider=lambda: ("rtsp://panel/stream", "udp"),
    )
    assert axis._rtsp_config() == ("rtsp://panel/stream", "udp")

    # provider 返回空（面板没选 RTSP）→ 用 .env 的值
    axis_empty = PerceptionAxis(
        profile=SimpleNamespace(frame_source="rtsp", deploy="local"),
        rtsp_url="rtsp://env/stream",
        rtsp_transport="tcp",
        rtsp_config_provider=lambda: ("", ""),
    )
    assert axis_empty._rtsp_config() == ("rtsp://env/stream", "tcp")


def test_rtsp_open_timeout_is_clamped() -> None:
    from src.agent.tool_executor import _rtsp_open_timeout

    assert _rtsp_open_timeout(30) == 20.0   # 面板允许到 120s，握手不能等这么久
    assert _rtsp_open_timeout(2) == 3.0
    assert _rtsp_open_timeout(None) == 10.0
    assert _rtsp_open_timeout("bad") == 10.0


def test_preview_button_path_honours_panel_rtsp_settings(monkeypatch) -> None:
    """「查看画面」走的整条路：面板设置 → RTSP 控制器 → 预览 JPEG。"""
    import cv2 as cv2_module

    from src.agent.tool_executor import ToolRuntime

    monkeypatch.setattr(cv2_module, "VideoCapture", lambda *a, **k: _FakeVideoCapture(_frame()))
    settings = {
        "source": "rtsp",
        "url": "rtsp://192.168.144.11:8554/main",
        "transport": "udp",
        "timeout_sec": 30,
        "camera_name": "0",
        "image_type": "scene",
    }
    runtime = ToolRuntime(backend_id="px4_mavlink", camera_settings_provider=lambda: dict(settings))

    controller, error = runtime._ensure_preview_controller({})
    assert controller is not None, error
    assert controller.transport == "udp"
    assert controller.url == "rtsp://192.168.144.11:8554/main"

    ok, body, mime, meta = runtime.capture_camera_preview({})
    assert ok is True
    assert mime == "image/jpeg"
    assert body[:2] == b"\xff\xd8"
    assert meta.get("source") == "rtsp"
