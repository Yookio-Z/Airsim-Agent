"""模块级设置读写与共享路径/模板常量（原 runtime.py 顶部区）。

拆分自 runtime.py，不依赖运行时类。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.config import config
from src.modules.mavlink_autodiscovery import normalize_serial_baud



REPO_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = REPO_ROOT / "src" / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = REPO_ROOT / "src" / "data" / "settings.json"
SKILLS_OVERRIDES_PATH = REPO_ROOT / "src" / "data" / "skills.json"
ATTACHMENTS_DIR = REPO_ROOT / "src" / "data" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

# AirSim settings.json 通信模式模板（config/airsim_settings/）：
#   airsim_simpleflight_multirotor -> airsim 后端（本机直接 RPC 控制，3 机）
#   px4_mavlink_udp_sitl          -> px4_mavlink 后端（UDP 连本机/WSL PX4 SITL）
#   px4_ros2_tcp_edge             -> px4_ros2 后端（TCP 连 Jetson/边端 PX4 SITL）
AIRSIM_SETTINGS_TEMPLATES: dict[str, dict[str, str]] = {
    "airsim_simpleflight_multirotor": {
        "label": "AirSim 纯仿真（SimpleFlight · 3 机）",
        "description": "本机 AirSim 直接 API 控制，对应系统 airsim 后端。三架 SimpleFlight 用于多机验证。",
        "backend": "airsim",
        "file": "airsim_simpleflight_multirotor.json",
    },
    "px4_mavlink_udp_sitl": {
        "label": "PX4 MAVLink（UDP · 本机/虚拟机 SITL）",
        "description": "AirSim 作为 PX4 仿真器，UDP 14540/14580 连接本机或 WSL 的 PX4 SITL，对应 px4_mavlink 后端。",
        "backend": "px4_mavlink",
        "file": "px4_mavlink_udp_sitl.json",
    },
    "px4_ros2_tcp_edge": {
        "label": "PX4 ROS2（TCP · Jetson/边端 SITL）",
        "description": "TCP 4560 连接 Jetson 边端 PX4 SITL（ControlIp 按实际 IP 修改），对应 px4_ros2 后端。",
        "backend": "px4_ros2",
        "file": "px4_ros2_tcp_edge.json",
    },
}


def _default_connection_settings() -> dict[str, Any]:
    """Return factory defaults matching the UI's QGC Links panel."""
    return {
        "auto_connect": False,
        "active_connection_id": "",
        "connections": [
            {
                "id": "default_airsim",
                "name": "AirSim Local",
                "type": "airsim",
                "params": {"host": "127.0.0.1", "portNumber": "41452"},
            },
            {
                "id": "default_px4_auto",
                "name": "PX4 Auto",
                "type": "auto",
                "params": {
                    "host": "127.0.0.1",
                    "portNumber": "14550",
                    "remotePort": "18570",
                    "realVehicle": False,
                },
            },
            {
                "id": "default_px4_usb",
                "name": "PX4 USB Serial",
                "type": "serial",
                "params": {
                    "port": "",
                    "baud": "115200",
                    "realVehicle": True,
                },
            },
            {
                "id": "default_px4",
                "name": "PX4 SITL UDP",
                "type": "udp",
                "params": {
                    "host": "127.0.0.1",
                    "portNumber": "14550",
                    "remotePort": "18570",
                    "realVehicle": False,
                },
            },
            {
                "id": "default_px4_ros2",
                "name": "PX4 ROS2 Gateway",
                "type": "px4_ros2",
                "params": {
                    "url": config.ros_bridge_url,
                    "workspace": config.ros_workspace_path,
                },
            },
        ],
    }


def _default_camera_settings() -> dict[str, Any]:
    """Return default camera source settings for the UI camera viewer."""
    return {
        "source": "airsim",
        "host": "127.0.0.1",
        "port": 41452,
        "url": "",
        "camera_name": "0",
        "vehicle_name": "",
        "image_type": "scene",
        "timeout_sec": 30.0,
        "auto_save": False,
    }


def _default_application_settings() -> dict[str, Any]:
    return {
        "appearance": {
            "language": "zh-CN",
            "theme": "dark",
            "density": "comfortable",
        },
        "map": {
            "default_layer": "satellite",
            "follow_vehicle": True,
            "show_vehicle_track": False,
            "require_reliable_gps": True,
        },
        "telemetry": {
            "refresh_ms": 250,
            "setup_refresh_ms": 100,
            "history_seconds": 60,
            "chart_sample_hz": 20,
        },
        "safety": {
            "confirm_real_vehicle_actions": True,
            "require_gps_for_global_mission": True,
            "max_display_jump_m": 120.0,
        },
        "agent": {
            "show_context_usage": True,
            "auto_select_multimodal_model": True,
            "persist_full_session_history": True,
        },
    }


def _application_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if settings is None:
        settings = _load_settings()
    defaults = _default_application_settings()
    raw = settings.get("application")
    section = dict(raw) if isinstance(raw, dict) else {}
    merged: dict[str, Any] = {}
    for group, values in defaults.items():
        incoming = section.get(group)
        merged[group] = {
            **values,
            **(dict(incoming) if isinstance(incoming, dict) else {}),
        }
    try:
        merged["telemetry"]["refresh_ms"] = max(100, min(2000, int(merged["telemetry"]["refresh_ms"])))
        merged["telemetry"]["setup_refresh_ms"] = max(
            50,
            min(2000, int(merged["telemetry"]["setup_refresh_ms"])),
        )
        merged["telemetry"]["history_seconds"] = max(10, min(600, int(merged["telemetry"]["history_seconds"])))
        merged["telemetry"]["chart_sample_hz"] = max(5, min(100, int(merged["telemetry"]["chart_sample_hz"])))
        merged["safety"]["max_display_jump_m"] = max(10.0, min(5000.0, float(merged["safety"]["max_display_jump_m"])))
    except (TypeError, ValueError):
        return defaults
    return merged


def _load_settings() -> dict[str, Any]:
    """Load persisted agent settings from disk."""
    try:
        if not SETTINGS_PATH.exists():
            return {}
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist agent settings to disk."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _connection_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the connection subsection, merging with defaults."""
    if settings is None:
        settings = _load_settings()
    defaults = _default_connection_settings()
    conn_section = dict(settings.get("connections") or {})
    connections = list(conn_section.get("connections") or defaults["connections"])
    by_id = {str(item.get("id") or ""): item for item in connections if isinstance(item, dict)}
    for default_conn in defaults["connections"]:
        if default_conn["id"] not in by_id:
            connections.append(default_conn)
    merged = {
        "auto_connect": conn_section.get("auto_connect", defaults["auto_connect"]),
        "active_connection_id": conn_section.get("active_connection_id", defaults["active_connection_id"]),
        "connections": connections,
    }
    # Ensure default connections exist if the list was empty.
    if not merged["connections"]:
        merged["connections"] = defaults["connections"]
    backend_hint = str(settings.get("backend") or "").strip()
    if backend_hint:
        selected_id, _selected = _select_connection_for_backend(merged, backend_hint)
        if selected_id:
            merged["active_connection_id"] = selected_id
    return merged


def _camera_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return persisted camera source settings, merging with UI defaults."""
    if settings is None:
        settings = _load_settings()
    defaults = _default_camera_settings()
    raw_section = settings.get("camera")
    section = dict(raw_section) if isinstance(raw_section, dict) else {}
    airsim_link = next(
        (
            c for c in _connection_settings(settings).get("connections", [])
            if str(c.get("type") or "").lower() == "airsim"
        ),
        {},
    )
    airsim_params = dict(airsim_link.get("params") or {}) if isinstance(airsim_link, dict) else {}
    image_type = str(section.get("image_type") or defaults["image_type"]).lower()
    if image_type not in {"scene", "depth", "segmentation", "infrared"}:
        image_type = defaults["image_type"]
    try:
        timeout_sec = float(section.get("timeout_sec", defaults["timeout_sec"]))
    except (TypeError, ValueError):
        timeout_sec = float(defaults["timeout_sec"])
    timeout_sec = max(3.0, min(120.0, timeout_sec))
    host = str(section.get("host") or airsim_params.get("host") or defaults["host"]).strip() or defaults["host"]
    try:
        port = int(section.get("port") or airsim_params.get("portNumber") or airsim_params.get("port") or defaults["port"])
    except (TypeError, ValueError):
        port = int(defaults["port"])
    return {
        "source": str(section.get("source") or defaults["source"]).strip().lower() or defaults["source"],
        "host": host,
        "port": port,
        "url": str(section.get("url") or "").strip(),
        "camera_name": str(section.get("camera_name") or defaults["camera_name"]).strip() or defaults["camera_name"],
        "vehicle_name": str(section.get("vehicle_name") or defaults["vehicle_name"]).strip(),
        "image_type": image_type,
        "timeout_sec": timeout_sec,
        "auto_save": bool(section.get("auto_save", defaults["auto_save"])),
    }


def _build_connect_params(connection: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate a UI connection entry into backend_id + connect_params."""
    conn_type = str(connection.get("type") or "auto").lower()
    params = dict(connection.get("params") or {})
    real_vehicle = bool(params.get("realVehicle", False))

    if conn_type == "airsim":
        ip = str(params.get("host") or params.get("ip") or "127.0.0.1").strip()
        port = int(params.get("portNumber") or params.get("port") or "41452")
        return "airsim", {"ip": ip, "port": port}

    if conn_type in {"px4_ros2", "ros2", "ros", "px4_ros"}:
        url = str(
            params.get("url")
            or params.get("base_url")
            or params.get("host")
            or config.ros_bridge_url
            or "http://127.0.0.1:8766"
        ).strip()
        workspace = str(params.get("workspace") or config.ros_workspace_path or "").strip()
        payload = {"url": url}
        if workspace:
            payload["workspace"] = workspace
        return "px4_ros2", payload

    # Everything else maps to the PX4 MAVLink backend.
    if conn_type == "serial":
        port = str(params.get("port") or "").strip()
        baud = normalize_serial_baud(params.get("baud") or "115200")
        url = f"serial:{port}:{baud}" if port else "auto:serial"
        return "px4_mavlink", {"url": url, "real_vehicle": True}
    elif conn_type == "tcp":
        address = str(params.get("address") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "5760").strip()
        url = f"tcp:{address}:{port_number}"
        return "px4_mavlink", {"url": url, "real_vehicle": real_vehicle}
    elif conn_type == "auto":
        host = str(params.get("host") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "14550").strip()
        if ":" in host and any(host.startswith(p) for p in ("udp:", "udpout:", "udpin:", "tcp:", "serial:", "auto:")):
            fallback_url = host
        else:
            fallback_url = f"udp:{host}:{port_number}"
        connect_params = {
            "url": "auto:",
            "fallback_url": fallback_url,
            "real_vehicle": real_vehicle,
        }
        remote_port = str(params.get("remotePort") or "").strip()
        if remote_port:
            connect_params["remote_host"] = host
            connect_params["remote_port"] = int(remote_port)
        return "px4_mavlink", connect_params
    else:
        # udp — use bare ``udp:`` prefix so that the MAVLink
        # controller can try both listen (udpin) and send (udpout)
        # modes.  PX4 SITL's GCS link broadcasts heartbeats to a
        # well-known port (14550 by default), so binding (udpin) is
        # usually the fastest path.
        host = str(params.get("host") or "127.0.0.1").strip()
        port_number = str(params.get("portNumber") or "14550").strip()
        # Allow users to type the full mavlink URL prefix directly.
        if ":" in host and any(host.startswith(p) for p in ("udp:", "udpout:", "udpin:", "tcp:", "serial:")):
            url = host
        else:
            url = f"udp:{host}:{port_number}"
        connect_params: dict[str, Any] = {
            "url": url,
            "real_vehicle": real_vehicle,
        }
        remote_port = str(params.get("remotePort") or "").strip()
        if remote_port:
            connect_params["remote_host"] = host
            connect_params["remote_port"] = int(remote_port)
        return "px4_mavlink", connect_params


def _select_connection_for_backend(
    conn_section: dict[str, Any],
    backend_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Return an active connection id compatible with the requested backend."""

    connections = [c for c in conn_section.get("connections", []) if isinstance(c, dict)]
    active_id = str(conn_section.get("active_connection_id") or "")
    active = next((c for c in connections if str(c.get("id") or "") == active_id), None)
    if active is not None:
        try:
            active_backend, _ = _build_connect_params(active)
        except Exception:
            active_backend = ""
        if active_backend == backend_id:
            return active_id, active

    preferred_ids = {
        "px4_mavlink": ["default_px4_auto", "default_px4_usb", "default_px4"],
        "airsim": ["default_airsim"],
        "px4_ros2": ["default_px4_ros2"],
    }.get(backend_id, [])
    for preferred_id in preferred_ids:
        candidate = next((c for c in connections if str(c.get("id") or "") == preferred_id), None)
        if candidate is not None:
            try:
                candidate_backend, _ = _build_connect_params(candidate)
            except Exception:
                continue
            if candidate_backend == backend_id:
                return str(candidate.get("id") or ""), candidate

    for candidate in connections:
        try:
            candidate_backend, _ = _build_connect_params(candidate)
        except Exception:
            continue
        if candidate_backend == backend_id:
            return str(candidate.get("id") or ""), candidate
    return "", None


