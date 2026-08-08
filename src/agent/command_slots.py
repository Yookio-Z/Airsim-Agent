"""Conservative slot extraction for lightweight agent decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_NUMBER = r"([-+]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)"
_UNIT_M = r"(?:m|meter|meters|米|公尺)?"
_MOVE_VERBS = r"(?:飞行|飞|移动|走|前进|后退|平移)?"


@dataclass(frozen=True)
class CommandSlots:
    """Structured values extracted from an operator command."""

    altitude: float | None = None
    radius: float | None = None
    velocity: float | None = None
    target_class: str = ""
    ned_target: dict[str, float] | None = None
    relative_move: dict[str, float] | None = None
    relative_moves: list[dict[str, float]] = field(default_factory=list)
    land: bool | None = None
    return_to_start: bool = False
    hover_after: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "altitude": self.altitude,
            "radius": self.radius,
            "velocity": self.velocity,
            "target_class": self.target_class,
            "ned_target": dict(self.ned_target) if self.ned_target else None,
            "relative_move": dict(self.relative_move) if self.relative_move else None,
            "relative_moves": [dict(item) for item in self.relative_moves],
            "land": self.land,
            "return_to_start": self.return_to_start,
            "hover_after": self.hover_after,
        }


def extract_command_slots(command: str) -> CommandSlots:
    """Extract safe, auditable task parameters from free-form text."""

    text = command or ""
    lower = text.lower()
    altitude = _extract_altitude(text)
    radius = _extract_radius(text)
    velocity = _extract_velocity(text)
    target_class = _extract_target_class(lower)
    ned_target = _extract_ned_target(text, altitude)
    relative_moves = _extract_relative_moves(text)
    relative_move = relative_moves[0] if relative_moves else _extract_relative_move(text)
    land = True if _has_any(lower, ("land", "landing", "降落", "落地")) else None
    return_to_start = _has_any(
        lower,
        (
            "return to start",
            "return to initial",
            "back to start",
            "回到初始",
            "返回初始",
            "回到起点",
            "返回起点",
            "回到出发点",
            "返回出发点",
            "回到原点",
            "返回原点",
            "返航",
            "返回",
            "回家",
        ),
    )
    hover_after = False if _has_any(lower, ("do not hover", "no hover", "不用悬停", "不要悬停")) else None
    return CommandSlots(
        altitude=altitude,
        radius=radius,
        velocity=velocity,
        target_class=target_class,
        ned_target=ned_target,
        relative_move=relative_move,
        relative_moves=relative_moves,
        land=land,
        return_to_start=return_to_start,
        hover_after=hover_after,
    )


def _extract_altitude(text: str) -> float | None:
    patterns = [
        rf"(?:altitude|height|takeoff to|climb to|rise to)\s*{_NUMBER}\s*{_UNIT_M}",
        rf"(?:高度|高|起飞到|升到|爬升到)\s*{_NUMBER}\s*{_UNIT_M}",
        rf"(?:take\s*off|takeoff|起飞|升空|爬升)\s*{_NUMBER}\s*{_UNIT_M}",
        rf"(?:起飞|升空|爬升|飞)\D{{0,8}}{_NUMBER}\s*{_UNIT_M}\s*(?:高度|高)",
        rf"{_NUMBER}\s*{_UNIT_M}\s*(?:altitude|height|high|高度|高空|高)",
    ]
    for pattern in patterns:
        value = _first_float(pattern, text)
        if value is not None:
            return _clamp(abs(value), 0.5, 120.0)
    return None


def _extract_radius(text: str) -> float | None:
    patterns = [
        rf"(?:radius|range|within)\s*{_NUMBER}\s*{_UNIT_M}",
        rf"(?:半径|范围|方圆)\s*{_NUMBER}\s*{_UNIT_M}",
        rf"{_NUMBER}\s*{_UNIT_M}\s*(?:radius|range|范围|半径)",
    ]
    for pattern in patterns:
        value = _first_float(pattern, text)
        if value is not None:
            return _clamp(abs(value), 1.0, 500.0)
    return None


def _extract_velocity(text: str) -> float | None:
    patterns = [
        rf"(?:speed|velocity)\s*{_NUMBER}\s*(?:m/s|米/秒|mps)?",
        rf"(?:速度|速率|以)\s*{_NUMBER}\s*(?:m/s|米/秒|mps)?",
        rf"{_NUMBER}\s*(?:m/s|米/秒|mps)",
    ]
    for pattern in patterns:
        value = _first_float(pattern, text)
        if value is not None:
            return _clamp(abs(value), 0.2, 20.0)
    return None


def _extract_ned_target(text: str, altitude: float | None) -> dict[str, float] | None:
    explicit = re.search(
        rf"x\s*[:=]?\s*{_NUMBER}\D+"
        rf"y\s*[:=]?\s*{_NUMBER}\D+"
        rf"z\s*[:=]?\s*{_NUMBER}",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return {
            "x": float(explicit.group(1)),
            "y": float(explicit.group(2)),
            "z": float(explicit.group(3)),
        }

    grouped = re.search(
        rf"[（(]\s*{_NUMBER}\s*[,，\s]+{_NUMBER}\s*[,，\s]+{_NUMBER}\s*[）)]",
        text,
    )
    if grouped:
        return {
            "x": float(grouped.group(1)),
            "y": float(grouped.group(2)),
            "z": float(grouped.group(3)),
        }

    x_value = _signed_direction_value(text, positive=("north", "n", "北", "向北", "往北"), negative=("south", "s", "南", "向南", "往南"))
    y_value = _signed_direction_value(text, positive=("east", "e", "东", "向东", "往东"), negative=("west", "w", "西", "向西", "往西"))
    z_value = _axis_value(text, ("z", "down", "d", "向下", "往下", "下降"))
    if z_value is None and altitude is not None and (x_value is not None or y_value is not None):
        z_value = -abs(altitude)
    if x_value is None and y_value is None and z_value is None:
        return None
    return {
        "x": float(x_value or 0.0),
        "y": float(y_value or 0.0),
        "z": float(z_value if z_value is not None else -abs(altitude or 3.0)),
    }


def _extract_relative_move(text: str) -> dict[str, float] | None:
    forward = _signed_direction_value(
        text,
        positive=("forward", "ahead", "前进", "向前", "往前"),
        negative=("backward", "back", "后退", "向后", "往后"),
    )
    right = _signed_direction_value(
        text,
        positive=("right", "右移", "向右", "往右"),
        negative=("left", "左移", "向左", "往左"),
    )
    up = _signed_direction_value(
        text,
        positive=("up", "ascend", "上升", "向上", "往上"),
        negative=("down", "descend", "下降", "向下", "往下"),
    )
    if forward is None and right is None and up is None:
        return None
    return {
        "forward_m": float(forward or 0.0),
        "right_m": float(right or 0.0),
        "up_m": float(up or 0.0),
    }


def _extract_relative_moves(text: str) -> list[dict[str, float]]:
    """Extract ordered body-frame relative moves from chained commands."""

    moves: list[dict[str, float]] = []
    consumed_spans: set[tuple[int, int]] = set()
    direction_specs = (
        (("forward", "ahead", "前进", "向前", "往前"), (1.0, 0.0, 0.0)),
        (("backward", "back", "后退", "向后", "往后"), (-1.0, 0.0, 0.0)),
        (("right", "右移", "向右", "往右"), (0.0, 1.0, 0.0)),
        (("left", "左移", "向左", "往左"), (0.0, -1.0, 0.0)),
        (("up", "ascend", "上升", "向上", "往上"), (0.0, 0.0, 1.0)),
        (("down", "descend", "下降", "向下", "往下"), (0.0, 0.0, -1.0)),
    )
    matches: list[tuple[int, dict[str, float]]] = []
    for words, vector in direction_specs:
        for word in sorted(words, key=len, reverse=True):
            escaped = _token_pattern(word)
            patterns = (
                rf"{escaped}\s*{_MOVE_VERBS}\s*{_NUMBER}\s*{_UNIT_M}",
                rf"{_NUMBER}\s*{_UNIT_M}\s*{_MOVE_VERBS}\s*{escaped}",
            )
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    try:
                        distance = abs(_parse_number(match.group(1)))
                    except (TypeError, ValueError):
                        continue
                    if distance <= 0:
                        continue
                    span = (match.start(), match.end())
                    if any(not (span[1] <= existing[0] or span[0] >= existing[1]) for existing in consumed_spans):
                        continue
                    consumed_spans.add(span)
                    forward = vector[0] * distance
                    right = vector[1] * distance
                    up = vector[2] * distance
                    matches.append((
                        match.start(),
                        {
                            "forward_m": float(forward),
                            "right_m": float(right),
                            "up_m": float(up),
                        },
                    ))
    for _, move in sorted(matches, key=lambda item: item[0]):
        moves.append(move)
    return moves


def _signed_direction_value(text: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> float | None:
    pos = _direction_value(text, positive)
    if pos is not None:
        return abs(pos)
    neg = _direction_value(text, negative)
    if neg is not None:
        return -abs(neg)
    return None


def _direction_value(text: str, words: tuple[str, ...]) -> float | None:
    for word in sorted(words, key=len, reverse=True):
        escaped = _token_pattern(word)
        patterns = [
            rf"{escaped}\s*[:=]?\s*{_NUMBER}\s*{_UNIT_M}",
            rf"{escaped}\s*{_MOVE_VERBS}\s*{_NUMBER}\s*{_UNIT_M}",
            rf"{_NUMBER}\s*{_UNIT_M}\s*{escaped}",
            rf"{_NUMBER}\s*{_UNIT_M}\s*{_MOVE_VERBS}\s*{escaped}",
        ]
        for pattern in patterns:
            value = _first_float(pattern, text)
            if value is not None:
                return value
    return None


def _axis_value(text: str, words: tuple[str, ...]) -> float | None:
    for word in sorted(words, key=len, reverse=True):
        value = _first_float(rf"{_token_pattern(word)}\s*[:=]?\s*{_NUMBER}\s*{_UNIT_M}", text, flags=re.IGNORECASE)
        if value is not None:
            return value
    return None


def _token_pattern(word: str) -> str:
    escaped = re.escape(word)
    if word.isascii() and word.isalpha() and len(word) <= 2:
        return rf"\b{escaped}\b"
    return escaped


def _extract_target_class(lower: str) -> str:
    aliases = {
        "person": ("person", "human", "pedestrian", "行人", "人员"),
        "truck": ("truck", "lorry", "卡车", "货车"),
        "bus": ("bus", "公交", "巴士"),
        "car": ("car", "vehicle", "auto", "汽车", "车辆", "小车"),
        "drone": ("drone", "uav", "无人机"),
    }
    for canonical, words in aliases.items():
        if _has_any(lower, words):
            return canonical
    return ""


def _first_float(pattern: str, text: str, flags: int = re.IGNORECASE) -> float | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    try:
        return _parse_number(match.group(1))
    except (TypeError, ValueError):
        return None


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _parse_number(value: str) -> float:
    value = str(value or "").strip()
    if not value:
        raise ValueError("empty number")
    try:
        return float(value)
    except ValueError:
        pass

    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return float(digits[value])
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char == "十":
            section += (number or 1) * 10
            number = 0
        elif char == "百":
            section += (number or 1) * 100
            number = 0
        else:
            raise ValueError(f"unsupported Chinese number: {value}")
    total += section + number
    if total <= 0:
        raise ValueError(f"unsupported Chinese number: {value}")
    return float(total)
