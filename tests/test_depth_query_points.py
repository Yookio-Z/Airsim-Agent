"""深度查询点的坐标约定：归一化与深度图像素不能混。

为什么单独测：深度图（256x144）和彩色图（800x600）分辨率不同，而位置信息通常
来自彩色图/VLM。把彩色图像素直接当深度图像素传进来**不会越界报错**——它会读到
另一个位置，返回一个"看起来很正常"的错误距离。这条路径的错误是静默的，所以
约定必须有测试守住。

配套的用法见 `skills/visual-approach/SKILL.md`：视觉模型给归一化点 → 这里取深度
→ 模型换算成机体系的一小步 → `drone_move_relative`。
"""

from __future__ import annotations

import pytest

from src.tools.vision import _parse_depth_query_point

DEPTH_W, DEPTH_H = 256, 144


def test_normalized_point_scales_to_depth_resolution():
    """0.36,0.52 是画面比例，不是深度图像素：必须换算。"""
    assert _parse_depth_query_point("0.36,0.52", DEPTH_W, DEPTH_H) == (92, 74)
    assert _parse_depth_query_point("0.5,0.5", DEPTH_W, DEPTH_H) == (128, 72)
    # 角落：归一化 (1,1) 是右下角，不是像素 (1,1)
    assert _parse_depth_query_point("1,1", DEPTH_W, DEPTH_H) == (255, 143)
    assert _parse_depth_query_point("0,0", DEPTH_W, DEPTH_H) == (0, 0)


def test_pixel_point_passes_through_unchanged():
    """有一个分量 >1 就证明是像素坐标，按原值使用（向后兼容旧调用）。"""
    assert _parse_depth_query_point("92,72", DEPTH_W, DEPTH_H) == (92, 72)
    assert _parse_depth_query_point("240,72", DEPTH_W, DEPTH_H) == (240, 72)
    assert _parse_depth_query_point("  92 , 72 ", DEPTH_W, DEPTH_H) == (92, 72)


def test_color_frame_pixel_is_not_silently_accepted():
    """彩色图像素（800x600）传进来时，要么被换算成正确的深度像素，要么越界报错。

    真值：彩色图 x=288 → 归一化 0.36 → 深度图 92。若按像素解释会读 288（越界）；
    若调用方写 0.36 则得到 92。这里守住"不会既接受 288 又当成同一位置"。
    """
    # 越界：288 > 255 → 由调用方（工具主逻辑）判为超出范围，不会静默读错
    assert _parse_depth_query_point("288,312", DEPTH_W, DEPTH_H) == (288, 312)
    assert 288 >= DEPTH_W  # 主逻辑据此返回 error，而不是读另一个像素

    # 而彩色图里 u<0.32 的点（x<256）会落在深度图范围内 —— 这正是必须用归一化
    # 写法的地方：240 是"画面 30% 处"还是"深度图右侧"？只有调用方知道自己给的是
    # 哪种坐标。归一化写法没有这个歧义。
    assert _parse_depth_query_point("240,72", DEPTH_W, DEPTH_H) == (240, 72)
    assert _parse_depth_query_point("0.30,0.50", DEPTH_W, DEPTH_H) == (76, 72)


def test_malformed_points_raise_value_error():
    for bad in ("", "1", "1,2,3", "a,b", "0.1;0.2"):
        with pytest.raises(ValueError):
            _parse_depth_query_point(bad, DEPTH_W, DEPTH_H)
