"""Shared waypoint parsing for local NED paths.

Both the pre-flight safety validator and ``drone_fly_path`` consume the same
``waypoints`` parameter. They must agree on the accepted shapes, otherwise the
safety layer can reject a command the flight tool would execute (or crash on a
list item with ``'list' object has no attribute 'get'``).

Accepted shapes:

- compact string: ``"x,y,z;x,y,z"``
- JSON array of objects: ``[{"x": 10, "y": 0, "z": -5}, ...]``
- JSON array of triples: ``[[10, 0, -5], ...]``

The parser is deliberately dependency-free: tools and the agent safety layer
both import it, and it must not pull either side into the other.
"""

from __future__ import annotations

import json
import math
from typing import Any


class WaypointFormatError(ValueError):
    """Waypoints are missing, malformed, or contain non-finite numbers."""


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise WaypointFormatError(f"{label} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise WaypointFormatError(f"{label} must be finite, got {value!r}")
    return number


def _point(item: Any, index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        missing = [axis for axis in ("x", "y", "z") if axis not in item]
        if missing:
            raise WaypointFormatError(
                f"waypoint #{index} must be an object with x, y, z "
                f"(missing {', '.join(missing)})"
            )
        return {
            **item,
            "x": _number(item.get("x"), f"waypoint #{index}.x"),
            "y": _number(item.get("y"), f"waypoint #{index}.y"),
            "z": _number(item.get("z"), f"waypoint #{index}.z"),
        }
    if isinstance(item, (list, tuple)):
        if len(item) != 3:
            raise WaypointFormatError(
                f"waypoint #{index} must be [x, y, z] or {{x, y, z}}, got {item!r}"
            )
        return {
            "x": _number(item[0], f"waypoint #{index}.x"),
            "y": _number(item[1], f"waypoint #{index}.y"),
            "z": _number(item[2], f"waypoint #{index}.z"),
        }
    raise WaypointFormatError(
        f"waypoint #{index} must be [x, y, z] or {{x, y, z}}, got {item!r}"
    )


def _parse_compact(text: str) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for index, segment in enumerate(text.split(";")):
        segment = segment.strip()
        if not segment:
            raise WaypointFormatError(
                f"waypoint #{index} is empty; expected 'x,y,z'"
            )
        parts = [part.strip() for part in segment.split(",")]
        if len(parts) != 3:
            raise WaypointFormatError(
                f"waypoint #{index} must be 'x,y,z' (or pass a JSON array): {segment!r}"
            )
        points.append(
            {
                "x": _number(parts[0], f"waypoint #{index}.x"),
                "y": _number(parts[1], f"waypoint #{index}.y"),
                "z": _number(parts[2], f"waypoint #{index}.z"),
            }
        )
    return points


def normalize_waypoints(value: Any) -> list[dict[str, Any]]:
    """Return a non-empty list of ``{x, y, z}`` dicts with finite coordinates.

    ``value`` may be a compact string, a JSON string, or an already-decoded
    list. Errors are raised as ``WaypointFormatError`` with a message that is
    safe to show to the operator/model.
    """

    if value is None:
        raise WaypointFormatError("waypoints must not be empty")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise WaypointFormatError("waypoints must not be empty")
        if text[0] in "[{":
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise WaypointFormatError(f"invalid waypoint JSON: {exc}") from exc
        else:
            return _parse_compact(text)
    if not isinstance(value, (list, tuple)) or not value:
        raise WaypointFormatError(
            "waypoints must be a non-empty list of {x, y, z} objects or [x, y, z] triples"
        )
    return [_point(item, index) for index, item in enumerate(value)]
