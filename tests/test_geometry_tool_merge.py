"""几何工具合并后的契约：一个工具，点→距离+世界坐标，多点一次取帧。

背景：产品原本有两个相机源工具（airsim_get_depth_map 多点测距 +
locate_image_point 单点投影）。2026-10-14 起合并为一个几何入口
`locate_image_point(points="x,y;x,y;...")`：第一个点是主点（距离+世界坐标），
其余点在同一张深度图上逐点读数（中位数/最小值），净空判断与目标取样共用一条
路径。这组用例锁住合并后的三件事：参数解析、附加点读数、多点统计量。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from src.modules.perception import airsim_adapter
from src.tools.perception_axis import _parse_locate_points


# ---------------------------------------------------------------- 参数解析
def test_parse_locate_points_accepts_normalised_single_and_multi():
    assert _parse_locate_points("0.5,0.5") == [(0.5, 0.5)]
    assert _parse_locate_points("0.36,0.52;0.355,0.525;0.365,0.515") == [
        (0.36, 0.52),
        (0.355, 0.525),
        (0.365, 0.515),
    ]
    # 中文全角标点也要能用（操作员/模型都可能敲全角）
    assert _parse_locate_points("0.5，0.5；0.6,0.6") == [(0.5, 0.5), (0.6, 0.6)]


def test_parse_locate_points_rejects_pixels_and_junk():
    pixels = _parse_locate_points("440,312")
    assert isinstance(pixels, str) and "归一化" in pixels
    too_many = _parse_locate_points(";".join(["0.5,0.5"] * 9))
    assert isinstance(too_many, str) and "最多" in too_many
    assert isinstance(_parse_locate_points("0.5"), str)
    assert isinstance(_parse_locate_points(""), str)
    assert isinstance(_parse_locate_points("abc"), str)


# ---------------------------------------------------------------- 附加点读数
def _stub_controller() -> SimpleNamespace:
    def _rpc(fn: Any = None, timeout: float = 0.0, vehicle_name: str = "") -> Any:
        return SimpleNamespace(
            kinematics_estimated=SimpleNamespace(
                position=SimpleNamespace(x_val=0.0, y_val=0.0, z_val=-3.0),
                orientation=SimpleNamespace(w_val=1.0, x_val=0.0, y_val=0.0, z_val=0.0),
            )
        )

    return SimpleNamespace(
        _client=SimpleNamespace(getMultirotorState=lambda **kwargs: None),
        _rpc=_rpc,
        camera_stabilization_status=lambda *a, **k: {"enabled": False},
    )


def _uniform_depth(value: float = 5.0, height: int = 144, width: int = 256) -> np.ndarray:
    return np.full((height, width), float(value), dtype=np.float32)


def test_one_call_reads_the_primary_point_and_extra_points(monkeypatch):
    monkeypatch.setattr(airsim_adapter, "get_depth_image", lambda *a, **k: _uniform_depth(5.0))
    result = airsim_adapter.project_point_to_world(
        frame_provider=_stub_controller(),
        x_norm=0.5,
        y_norm=0.5,
        extra_points=[(0.4, 0.5), (0.6, 0.5), (0.5, 0.4)],
    )
    assert result["valid"] is True
    assert result["distance_m"] == 5.0
    assert len(result["extra_measures"]) == 3
    assert all(item.get("distance_m") == 5.0 for item in result["extra_measures"])
    assert result["distance_median_m"] == 5.0
    assert result["distance_min_m"] == 5.0  # 净空判断用最小值


def test_extra_points_that_hit_the_sky_are_excluded(monkeypatch):
    depth = _uniform_depth(5.0)
    depth[:, :60] = 15000.0  # 左侧一条"天空"：无有效深度
    monkeypatch.setattr(airsim_adapter, "get_depth_image", lambda *a, **k: depth)
    result = airsim_adapter.project_point_to_world(
        frame_provider=_stub_controller(),
        x_norm=0.5,
        y_norm=0.5,
        extra_points=[(0.05, 0.5), (0.6, 0.5)],
    )
    measures = result["extra_measures"]
    assert len(measures) == 2
    sky_item = next(item for item in measures if item["point"]["x"] < 0.1)
    assert "error" in sky_item and "distance_m" not in sky_item
    # 统计量只用可信读数：天空点不参与中位数/最小值
    assert result["distance_median_m"] == 5.0
    assert result["distance_min_m"] == 5.0


def test_primary_point_only_path_stays_unchanged(monkeypatch):
    """没有附加点时不多出统计键（老调用方读 distance_m 的行为不变）。"""
    monkeypatch.setattr(airsim_adapter, "get_depth_image", lambda *a, **k: _uniform_depth(8.0))
    result = airsim_adapter.project_point_to_world(
        frame_provider=_stub_controller(), x_norm=0.5, y_norm=0.5
    )
    assert result["valid"] is True
    assert result["distance_m"] == 8.0
    assert "extra_measures" not in result
    assert "distance_median_m" not in result
    assert "distance_min_m" not in result
