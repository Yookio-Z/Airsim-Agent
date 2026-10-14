"""取景一致性：感知轴是"哪一路画面"的唯一事实来源。

背景（这次修的就是它）：相机面板曾经拿 `camera_name in {"", "0"}` 猜自己看的是
不是感知轴那一路，而真实配置里相机叫 CameraImage——于是"面板看着 A、Agent 看着
B"在代码里分不出来；面板切到别的相机或深度通道时，它另开一条 AirSim 连接做单帧
检测、类别还写死成 car，结果既拖慢感知轴，Agent 也完全不知道操作员在看什么。

这些测试锁住四条不变量：
1. 取景只有一个来源（axis.view），面板与 Agent 读同一份；
2. 换相机是"把轴切过去"，不是各开一条流；
3. 面板旁看的画面会被记录（不混进 targets）；
4. 预览检测的类别跟随感知轴配置，不再写死。
"""

from __future__ import annotations

import numpy as np
import pytest

from _runtime_factories import tool_runtime
from src.modules.perception_axis import PerceptionAxis
from src.modules.perception_profile import PerceptionProfile


class StubFrameSource:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.last_error = ""

    def open(self) -> bool:
        self.opened = True
        return True

    def close(self) -> None:
        self.closed = True

    def get_frame(self):
        return np.zeros((60, 80, 3), dtype=np.uint8)


class StubAxis:
    """够 capture_camera_preview 用的最小感知轴。"""

    def __init__(self, camera_name: str = "CameraImage", target_class: str = "car", jpeg: bytes | None = None) -> None:
        self._camera_name = camera_name
        self.profile = PerceptionProfile(profile="sim_local", camera_name=camera_name, target_class=target_class)
        self.enabled = True
        self.preview_notes: list[dict] = []
        # 1x1 的合法 JPEG：编码路径要真的能解出来
        import cv2

        ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
        assert ok
        self._jpeg = jpeg if jpeg is not None else buf.tobytes()

    def view(self) -> dict:
        return {
            "camera_name": self._camera_name,
            "image_type": "scene",
            "frame_source": "airsim",
            "deploy": "local",
            "switchable": True,
        }

    def annotated_frame(self):
        return self._jpeg, [{"class": "car", "confidence": 0.8, "bbox": [1, 1, 4, 4], "track_id": 3}], 123.0

    def is_online(self) -> bool:
        return True

    def health(self) -> dict:
        return {"online": True, "fps": 5.0, "detect_fps": 2.0, "view": self.view()}

    def snapshot(self) -> dict:
        return {"targets": [], "primary": None, "timestamp": 0.0, "view": self.view()}

    def pop_events(self) -> list:
        return []

    def note_preview(self, **kwargs) -> None:
        self.preview_notes.append(dict(kwargs))


# ----------------------------------------------------------------------
# 1. 取景只有一处事实来源
# ----------------------------------------------------------------------

def test_view_marks_switchability_by_frame_source():
    airsim = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    assert airsim.view()["camera_name"] == "CameraImage"
    assert airsim.view()["image_type"] == "scene"
    assert airsim.view()["switchable"] is True

    # RTSP 是一路流、USB 是本机设备号：它们不由"相机名"决定，不能按名切换。
    rtsp = PerceptionAxis(profile=PerceptionProfile(frame_source="rtsp"))
    assert rtsp.view()["switchable"] is False
    remote = PerceptionAxis(profile=PerceptionProfile(frame_source="none", deploy="remote", remote_url="http://x"))
    assert remote.view()["switchable"] is False


def test_health_and_snapshot_carry_the_view():
    """取景随 health/snapshot 下发：面板据此对齐，Agent 据此判断看的是哪一路。"""
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    assert axis.health()["view"]["camera_name"] == "CameraImage"
    assert axis.snapshot()["view"]["camera_name"] == "CameraImage"


# ----------------------------------------------------------------------
# 2. 换相机 = 把轴切过去
# ----------------------------------------------------------------------

@pytest.fixture()
def started_axis(monkeypatch):
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    monkeypatch.setattr(axis, "_build_frame_source", lambda profile: StubFrameSource())
    assert axis.start() is True
    yield axis
    axis.stop()


def test_set_view_switches_camera_and_keeps_detection(started_axis):
    axis = started_axis
    axis.start_detection()
    assert axis._engine.detection_enabled is True

    result = axis.set_view("1")

    assert result["ok"] is True
    assert result["status"] == "switched"
    assert axis.view()["camera_name"] == "1"
    # 换相机不该把操作员开着的检测顺手关掉
    assert result["detection_preserved"] is True
    assert axis._engine is not None and axis._engine.detection_enabled is True


def test_set_view_is_a_noop_for_the_current_camera(started_axis):
    axis = started_axis
    engine_before = axis._engine
    result = axis.set_view("CameraImage")
    assert result["status"] == "unchanged"
    assert axis._engine is engine_before  # 没换相机就不重建


def test_set_view_defers_when_the_axis_has_not_started():
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    result = axis.set_view("2")
    assert result["ok"] is True
    assert result["status"] == "deferred"
    assert axis.view()["camera_name"] == "2"


def test_set_view_refuses_a_stream_that_has_no_camera_name():
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="rtsp"))
    result = axis.set_view("1")
    assert result["ok"] is False
    assert result["status"] == "not_switchable"


def test_set_view_rejects_empty_name(started_axis):
    result = started_axis.set_view("   ")
    assert result["ok"] is False
    assert result["status"] == "invalid_camera"


# ----------------------------------------------------------------------
# 3. 面板旁看的画面要留下记录
# ----------------------------------------------------------------------

def test_preview_records_match_and_mismatch_against_the_axis():
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))

    axis.note_preview(
        camera_name="CameraImage", image_type="scene", source="airsim",
        detection_source="axis", detections=[{"class": "car"}],
    )
    same = axis.preview()
    assert same["matches_axis_view"] is True
    assert same["stale"] is False
    assert axis.snapshot()["preview"]["matches_axis_view"] is True

    axis.note_preview(
        camera_name="1", image_type="depth", source="airsim",
        detection_source="", detections=[],
    )
    other = axis.preview()
    assert other["matches_axis_view"] is False


def test_preview_expires_instead_of_pretending_to_be_current():
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    axis.note_preview(camera_name="1", image_type="scene", source="airsim", detection_source="preview")
    axis._preview["timestamp"] -= axis._PREVIEW_TTL_S + 1.0
    assert axis.preview()["stale"] is True
    assert axis.preview()["matches_axis_view"] is False


def test_no_preview_record_is_not_an_error():
    axis = PerceptionAxis(profile=PerceptionProfile(frame_source="airsim", camera_name="CameraImage"))
    assert axis.preview() == {}
    assert "preview" not in axis.snapshot()


# ----------------------------------------------------------------------
# 5. 面板取帧：与轴那一路相同才回放轴的缓存
# ----------------------------------------------------------------------

def _runtime_with_axis(axis, controller=None):
    """ToolRuntime 需要相机设置提供者：预览的 source/camera 默认值从它来。"""
    return tool_runtime(
        perception_axis=axis,
        camera_settings_provider=lambda: {"source": "airsim", "camera_name": "CameraImage", "image_type": "scene"},
        controller=controller,
    )


def _preview(runtime, **params):
    return runtime.capture_camera_preview({"detect": "1", **params})


def test_axis_view_request_replays_the_axis_frame(monkeypatch):
    axis = StubAxis(camera_name="CameraImage")
    runtime = _runtime_with_axis(axis)
    monkeypatch.setattr(runtime, "_start_preview_reaper", lambda: None)

    ok, body, mime, meta = _preview(runtime, camera_name="CameraImage", source="airsim", image_type="scene")

    assert ok is True
    assert meta["detection_source"] == "axis"
    assert meta["camera"] == "CameraImage"      # meta 报的是轴实际在用的相机
    assert meta["detections"][0]["track_id"] == 3
    # 记录下"操作员正在看轴那一路"
    assert axis.preview_notes and axis.preview_notes[-1]["camera_name"] == "CameraImage"
    assert axis.preview_notes[-1]["detection_source"] == "axis"


def test_request_for_another_camera_does_not_replay_the_axis_frame(monkeypatch):
    """面板要看别的相机时，绝不能把轴那一路的标注帧当成它要的画面返回。"""
    axis = StubAxis(camera_name="CameraImage")
    runtime = _runtime_with_axis(axis)
    monkeypatch.setattr(runtime, "_start_preview_reaper", lambda: None)
    # 直连抓帧不可用 → 走到明确的错误，而不是悄悄回放轴缓存
    monkeypatch.setattr(runtime, "_perception_camera_controller", lambda: None)
    monkeypatch.setattr(runtime, "_ensure_preview_controller", lambda params: (None, "no camera"))

    ok, body, _mime, meta = _preview(runtime, camera_name="1", source="airsim", image_type="scene")

    assert ok is False
    assert meta.get("detection_source") != "axis"


def test_another_camera_view_never_runs_its_own_detection(monkeypatch):
    """预览路径不自己跑检测：框只有感知轴一个来源，也只有它这么快。

    这条路曾经在请求里跑一次性单帧检测（实测 1.3s，抖到 14s），叠上抓帧和感知
    轴的负载，整帧能拖到几十秒——前端每帧只等 3.6s，画面就永远刷不出来。
    """
    called: list[str] = []

    def boom(*args, **kwargs):
        called.append("detect")
        raise AssertionError("预览路径不该自己跑检测")

    monkeypatch.setattr("src.modules.yolo_detection.detect_objects_stateless", boom)

    axis = StubAxis(camera_name="CameraImage")
    runtime = _runtime_with_axis(axis)
    jpeg = _jpeg_bytes()

    class _Ctrl:
        is_connected = True
        _vehicles: list = []

        def capture_image(self, **kwargs):
            return jpeg

    monkeypatch.setattr(runtime, "_perception_camera_controller", lambda: _Ctrl())
    monkeypatch.setattr(runtime, "_start_preview_reaper", lambda: None)

    ok, _body, _mime, meta = _preview(runtime, camera_name="1", source="airsim", image_type="scene")

    assert ok is True, meta
    assert called == []
    assert meta["detections"] == []
    assert meta["detection_source"] == "none"
    assert meta["matches_axis_view"] is False


def _jpeg_bytes() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((16, 16, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


def test_non_scene_channel_never_replays_the_axis_frame(monkeypatch):
    axis = StubAxis(camera_name="CameraImage")
    runtime = _runtime_with_axis(axis)
    monkeypatch.setattr(runtime, "_start_preview_reaper", lambda: None)
    monkeypatch.setattr(runtime, "_perception_camera_controller", lambda: None)
    monkeypatch.setattr(runtime, "_ensure_preview_controller", lambda params: (None, "no camera"))

    ok, _body, _mime, meta = _preview(runtime, camera_name="CameraImage", source="airsim", image_type="depth")

    assert ok is False
    assert meta.get("detection_source") != "axis"


def test_axis_without_a_camera_name_still_serves_its_cache(monkeypatch):
    """轴报不出相机名时无法比对，按"要的就是轴那一路"处理，不去白开第二条取流。"""
    axis = StubAxis(camera_name="")
    runtime = _runtime_with_axis(axis)
    monkeypatch.setattr(runtime, "_start_preview_reaper", lambda: None)

    ok, _body, _mime, meta = _preview(runtime, camera_name="0", source="airsim", image_type="scene")

    assert ok is True
    assert meta["detection_source"] == "axis"


# ----------------------------------------------------------------------
# 6. 前端接线（静态断言：这些是"单一事实来源"的落点，删掉就又分叉了）
# ----------------------------------------------------------------------

def test_agent_tool_surface_does_not_grow_a_view_switch():
    """取景归操作员：Agent 读得到"在看哪一路"（走 perception_status），但不切它。

    这是刻意的克制——原子工具只做一件事，而且"系统背着操作员改他正在看的东西"
    与"系统偷偷替他换模型"是同一类错误。要换视角，Agent 自己转向/移动，或让
    操作员在相机面板上切。
    """
    import json

    from src.agent.tool_executor import ToolCollector
    from src.tools.perception_axis import register_perception_axis_tools

    collector = ToolCollector()
    axis = StubAxis(camera_name="CameraImage")
    register_perception_axis_tools(collector, axis, lambda data: json.dumps(data, ensure_ascii=False), vlm=None)

    names = set(collector.tools)
    assert "perception_set_view" not in names
    assert "perception_view" not in names
    # 读能力保留在既有的 perception_status 里：view 随状态一起返回。
    # 检测快照不再返回——产品已无目标检测（YOLO 移除），相机健康里也不该出现
    # 让模型以为"可以靠检测锁定"的字段。
    status = json.loads(collector.tools["perception_status"]())
    assert status["view"]["camera_name"] == "CameraImage"
    assert status["online"] is True
    assert "snapshot" not in status
    assert "detections" not in status


def test_camera_panel_wires_the_axis_view():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "ui" / "static" / "ui-camera.js").read_text(encoding="utf-8")

    # 面板切相机 → 切感知轴（而不是各开一条流）
    assert 'post("/api/perception/view"' in text
    # 面板开面板时加入轴当前的取景，而不是把它拽到面板默认的 0 号相机
    assert "axisViewCameraName" in text


# ----------------------------------------------------------------------
# 7. 目标检测已从产品中移除：工具面只剩"语义 + 深度"，几何靠点位投影
#    （2026-10-12 事故的根因是"检测锁定"成了飞行动作的前置条件）
# ----------------------------------------------------------------------

def test_runtime_guard_rows_are_not_rendered_as_model_thinking():
    """kind=guard 的行必须渲染成"运行时守卫"，不能被兜底归成"模型思考"。

    实测缺陷：normalizeProcessKind 的白名单漏了 "guard"，于是运行时的守卫/交回
    决定（"上一步要求先观察…"）被归成 reasoning，操作员看到的是
    "模型思考 · 运行… <运行时写的句子>"——把系统的话当成模型在想。
    """
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "ui" / "static" / "ui-chat.js").read_text(encoding="utf-8")
    match = re.search(r"function normalizeProcessKind\(item\) \{(.*?)\n\}", text, re.S)
    assert match, "normalizeProcessKind not found"
    body = match.group(1)
    whitelist = body.split(".includes(explicit)")[0]
    for kind in ('"reasoning"', '"tool"', '"skill"', '"guard"'):
        assert kind in whitelist, (kind, whitelist)
    # 分类与文案都认识 guard
    assert 'if (kind === "guard") return "guard"' in text
    assert 'if (cat === "guard") return "系统自动处理"' in text


def test_detection_tools_are_gone_and_point_locate_is_the_geometry_path():
    """检测类工具必须不存在；点位投影是替代它的几何路径。

    没有检测，Agent 照样要能靠近与降落：归一化点（视觉模型给的）+ 深度图 +
    位姿 = 距离与世界坐标。这是"不需要目标检测也可以"这句话的落点。
    """
    import json

    from src.agent.tool_executor import ToolCollector
    from src.tools.perception_axis import register_perception_axis_tools

    collector = ToolCollector()
    axis = StubAxis(camera_name="CameraImage")
    calls: list[tuple] = []

    def locate(x, y, vehicle_name="", hint_w=0.0, hint_h=0.0):
        calls.append((x, y, vehicle_name))
        return {"valid": True, "world_pos": {"x": 1.0, "y": 2.0, "z": -3.0}, "distance_m": 4.5}

    register_perception_axis_tools(
        collector, axis, lambda data: json.dumps(data, ensure_ascii=False), vlm=None, locate=locate
    )

    names = set(collector.tools)
    for gone in ("perception_start", "perception_stop", "airsim_detect_objects", "drone_approach_target"):
        assert gone not in names, f"{gone} 应随 YOLO 一起移除"
    assert {"perception_status", "inspect_current_frame", "airsim_locate_point"} <= names

    payload = json.loads(collector.tools["airsim_locate_point"](0.55, 0.4))
    assert payload["status"] == "ok"
    assert payload["valid"] is True
    assert payload["world_pos"]["z"] == -3.0
    assert payload["query_point"] == {"x": 0.55, "y": 0.4}
    assert calls == [(0.55, 0.4, "")]

    # 没注入投影回调时给一句可执行的话，而不是抛异常
    bare = ToolCollector()
    register_perception_axis_tools(bare, axis, lambda data: json.dumps(data, ensure_ascii=False))
    failed = json.loads(bare.tools["airsim_locate_point"](0.5, 0.5))
    assert failed["status"] == "error"
    assert "不支持" in failed["message"]
