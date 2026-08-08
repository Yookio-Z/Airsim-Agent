"""MAVLink serial port discovery helpers.

This mirrors the narrow part of QGroundControl auto-connect that matters for
USB flight controllers: identify Pixhawk-like boards from VID/PID or port
metadata, then try the serial link at the board-appropriate baud rate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PIXHAWK_DEFAULT_BAUD = 115200
SIK_DEFAULT_BAUD = 57600
COMMON_MAVLINK_BAUDS = (PIXHAWK_DEFAULT_BAUD, SIK_DEFAULT_BAUD, 921600)
UDP_PORT_LIKE_BAUDS = {14540, 14550, 14551, 14555, 14556, 14557, 14558, 14559, 18570}


@dataclass(frozen=True)
class SerialMavlinkCandidate:
    """A serial endpoint that is plausible for MAVLink."""

    device: str
    url: str
    baud: int
    board_type: str
    board_name: str
    score: int
    description: str = ""
    manufacturer: str = ""
    product: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "url": self.url,
            "baud": self.baud,
            "board_type": self.board_type,
            "board_name": self.board_name,
            "score": self.score,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "product": self.product,
            "hwid": self.hwid,
            "vid": self.vid,
            "pid": self.pid,
            "serial_number": self.serial_number,
        }


def normalize_serial_baud(value: Any, board_type: str = "Pixhawk") -> int:
    """Return a sane MAVLink baud, correcting common UI port/baud mixups."""

    default = SIK_DEFAULT_BAUD if board_type == "SiK Radio" else PIXHAWK_DEFAULT_BAUD
    try:
        baud = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if baud in UDP_PORT_LIKE_BAUDS or baud <= 0:
        return default
    return baud


def discover_serial_mavlink_candidates(
    preferred_device: str = "",
    preferred_baud: Any = None,
    include_unknown: bool = False,
) -> list[SerialMavlinkCandidate]:
    """List serial MAVLink candidates sorted by confidence.

    ``preferred_device`` is used for explicit Serial links: the selected port is
    returned even when its metadata is generic. Pure auto-connect avoids unknown
    ports unless their metadata looks like a flight controller.
    """

    try:
        from serial.tools import list_ports
    except Exception:
        return []

    preferred_device = _clean_device_name(preferred_device)
    ports = _filter_composite_ports(list(list_ports.comports()))
    candidates: list[SerialMavlinkCandidate] = []

    for port in ports:
        device = _clean_device_name(str(getattr(port, "device", "") or ""))
        if not device:
            continue
        if preferred_device and not _port_matches(port, preferred_device):
            continue

        board_type, board_name, score = identify_serial_board(port)
        if _is_bootloader(port, board_type):
            continue
        if not score:
            if not (preferred_device or include_unknown):
                continue
            score = 35
            board_type = "Unknown"
            board_name = str(getattr(port, "description", "") or device)

        for order, baud in enumerate(_baud_sequence(preferred_baud, board_type)):
            candidates.append(
                SerialMavlinkCandidate(
                    device=device,
                    url=f"serial:{device}:{baud}",
                    baud=baud,
                    board_type=board_type,
                    board_name=board_name,
                    score=max(1, score - order),
                    description=str(getattr(port, "description", "") or ""),
                    manufacturer=str(getattr(port, "manufacturer", "") or ""),
                    product=str(getattr(port, "product", "") or ""),
                    hwid=str(getattr(port, "hwid", "") or ""),
                    vid=_optional_int(getattr(port, "vid", None)),
                    pid=_optional_int(getattr(port, "pid", None)),
                    serial_number=str(getattr(port, "serial_number", "") or ""),
                )
            )

    unique: dict[str, SerialMavlinkCandidate] = {}
    for candidate in candidates:
        existing = unique.get(candidate.url)
        if existing is None or candidate.score > existing.score:
            unique[candidate.url] = candidate

    return sorted(
        unique.values(),
        key=lambda item: (-item.score, item.device.upper(), _baud_rank(item.baud)),
    )


def identify_serial_board(port: Any) -> tuple[str, str, int]:
    """Identify a serial port using QGC USB board info plus local heuristics."""

    vid = _optional_int(getattr(port, "vid", None))
    pid = _optional_int(getattr(port, "pid", None))
    description = str(getattr(port, "description", "") or "")
    manufacturer = str(getattr(port, "manufacturer", "") or "")
    product = str(getattr(port, "product", "") or "")
    hwid = str(getattr(port, "hwid", "") or "")

    board_info, desc_fallbacks, manufacturer_fallbacks = _load_qgc_usb_board_info()
    if vid is not None:
        for row in board_info:
            try:
                row_vid = int(row.get("vendorID"))
                row_pid = int(row.get("productID"))
            except (TypeError, ValueError):
                continue
            if vid == row_vid and (pid == row_pid or row_pid == 0):
                return str(row.get("boardClass") or "Pixhawk"), str(row.get("name") or "Pixhawk"), 100

    for row in desc_fallbacks:
        pattern = str(row.get("regExp") or "")
        if _regex_matches(pattern, description):
            return str(row.get("boardClass") or "Pixhawk"), str(row.get("name") or "Pixhawk"), 85

    for row in manufacturer_fallbacks:
        pattern = str(row.get("regExp") or "")
        if _regex_matches(pattern, manufacturer):
            return str(row.get("boardClass") or "Pixhawk"), str(row.get("name") or "Pixhawk"), 80

    haystack = " ".join([description, manufacturer, product, hwid]).lower()
    if any(token in haystack for token in ("pixhawk", "px4", "fmu", "ardupilot", "holybro", "cube", "cuav", "nxtpx4", "nextpx4")):
        return "Pixhawk", description or manufacturer or "Pixhawk", 65
    if any(token in haystack for token in ("sik radio", "3dr radio")):
        return "SiK Radio", description or manufacturer or "SiK Radio", 60
    return "", "", 0


@lru_cache(maxsize=1)
def _load_qgc_usb_board_info() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(__file__).resolve().parents[2]
    path = root / "third_party" / "qgroundcontrol" / "src" / "Comms" / "USBBoardInfo.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], [], []
    return (
        list(data.get("boardInfo") or []),
        list(data.get("boardDescriptionFallback") or []),
        list(data.get("boardManufacturerFallback") or []),
    )


def _filter_composite_ports(ports: list[Any]) -> list[Any]:
    seen: dict[tuple[int | None, int | None], set[str]] = {}
    filtered: list[Any] = []
    for port in ports:
        vid = _optional_int(getattr(port, "vid", None))
        pid = _optional_int(getattr(port, "pid", None))
        serial = str(getattr(port, "serial_number", "") or "")
        description = str(getattr(port, "description", "") or "")
        key = (vid, pid)
        if vid is not None and pid is not None and serial and serial != "0":
            serials = seen.setdefault(key, set())
            if serial in serials and "nmea" not in description.lower():
                continue
            serials.add(serial)
        filtered.append(port)
    return filtered


def _baud_sequence(preferred_baud: Any, board_type: str) -> list[int]:
    if preferred_baud not in (None, ""):
        return [normalize_serial_baud(preferred_baud, board_type)]
    default = SIK_DEFAULT_BAUD if board_type == "SiK Radio" else PIXHAWK_DEFAULT_BAUD
    values = [default, *COMMON_MAVLINK_BAUDS]
    unique: list[int] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _baud_rank(baud: int) -> int:
    try:
        return COMMON_MAVLINK_BAUDS.index(baud)
    except ValueError:
        return len(COMMON_MAVLINK_BAUDS)


def _is_bootloader(port: Any, board_type: str) -> bool:
    description = str(getattr(port, "description", "") or "")
    return board_type == "Pixhawk" and "BL" in description


def _regex_matches(pattern: str, text: str) -> bool:
    if not pattern or not text:
        return False
    try:
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    except re.error:
        return False


def _port_matches(port: Any, preferred_device: str) -> bool:
    preferred = _clean_device_name(preferred_device).lower()
    values = {
        _clean_device_name(str(getattr(port, attr, "") or "")).lower()
        for attr in ("device", "name")
    }
    values.add(_clean_device_name(str(getattr(port, "system_location", "") or "")).lower())
    return preferred in values


def _clean_device_name(value: str) -> str:
    value = value.strip()
    if value.startswith("\\\\.\\"):
        return value[4:]
    return value


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
