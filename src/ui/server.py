"""Stdlib HTTP server for the AirSim VLA command center."""

from __future__ import annotations

import argparse
import logging
import errno
import json
import mimetypes
import os
import queue
import re
import socket
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from src.agent import AgentRuntime
from src.agent.llm import ModelRegistry, derive_provider, list_provider_models, probe_model_capabilities
from src.agent.planner import _bounded_copy
from src.agent.skill_docs import parse_skill_doc


STATIC_DIR = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]

# 瓦片代理 + 磁盘缓存（参考 QGC QGCCachedTileSet：已下载瓦片存本地，下次秒开）
TILE_CACHE_DIR = STATIC_DIR / "tile_cache"
# 各图层源 URL 模板（{z}/{x}/{y} 顺序与源一致）及默认 content-type
TILE_SOURCES = {
    "satellite": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "image/jpeg",
    ),
    "street": (
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "image/png",
    ),
}
_TILE_CACHE_LOCK = threading.Lock()
# 延迟到 main() 创建，以便通过 --backend 参数选择后端
RUNTIME: AgentRuntime | None = None
MODEL_REGISTRY = ModelRegistry()


def _is_client_disconnect(exc: BaseException) -> bool:
    """Return True for expected browser/client disconnects.

    Windows browsers often close polling or SSE sockets while the stdlib server
    is still writing, which surfaces as WinError 10053/10054. Those are noisy
    transport events, not application failures.
    """
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
        return True
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in {10053, 10054, 10038}:
        return True
    return getattr(exc, "errno", None) in {
        errno.EPIPE,
        errno.ECONNRESET,
        getattr(errno, "ECONNABORTED", 10053),
    }


class AgentThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server that prevents multiple Windows runtimes sharing one port."""

    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if exc and _is_client_disconnect(exc):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# 路由表（单一事实来源）
#
# 约定：pattern 以 "*" 结尾表示前缀匹配（其余为精确匹配）；handler 是
# AgentRequestHandler 上的方法名；summary 是给人和脚本看的一句话说明——
# ``GET /api/endpoints`` 直接把这张表吐出来，所以新增端点必须先加一行。
# 匹配顺序：精确优先，其次前缀（长的优先），见 match_route()。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    handler: str
    summary: str

    def public(self) -> dict[str, str]:
        return {"method": self.method, "path": self.pattern, "summary": self.summary}


ROUTES: tuple[Route, ...] = (
    Route("GET", "/api/state", "_ep_api_state", "整体状态快照（会话、任务、工具、相机、记忆、Agent 说明）"),
    Route("GET", "/api/telemetry", "_ep_api_telemetry", "轻量遥测帧：顶部状态栏与地图的数据源"),
    Route("GET", "/api/stream", "_ep_api_stream", "SSE 实时事件流（会话消息、运行状态、任务列表）"),
    Route("GET", "/api/sessions", "_ep_api_sessions", "会话列表"),
    Route("GET", "/api/sessions/*", "_ep_api_sessions_tail", "单个会话：读取/加载/重命名/删除（动作在路径尾段）"),
    Route("GET", "/api/flightlog/sessions", "_ep_api_flightlog_sessions", "飞行记录列表（新→旧，含摘要索引）"),
    Route("GET", "/api/flightlog/sessions/*", "_ep_api_flightlog_sessions_tail", "读取一次飞行记录：meta + summary + 最后 N 帧"),
    Route("GET", "/api/tools", "_ep_api_tools", "工具清单（注册面：名称/参数/清单元数据）"),
    Route("GET", "/api/skills", "_ep_api_skills", "技能列表与详情（含 SKILL.md 原文）"),
    Route("GET", "/api/health", "_ep_api_health", "服务健康检查"),
    Route("GET", "/api/backends", "_ep_api_backends", "后端清单与各自能力位"),
    Route("GET", "/api/link", "_ep_api_link", "链路汇总：飞控链路 + 感知链路 + 遥测源"),
    Route("GET", "/api/settings/connections", "_ep_api_settings_connections", "连接配置读取（链路面板）"),
    Route("GET", "/api/settings/agent-prompt", "_ep_api_settings_agent_prompt", "常驻提示词 SYSTEM.md 读取（设置面板）"),
    Route("GET", "/api/settings/application", "_ep_api_settings_application", "应用设置读取"),
    Route("GET", "/api/settings/vehicle-info", "_ep_api_settings_vehicle_info", "载具信息（固件/状态，按需刷新）"),
    Route("GET", "/api/vehicle/mode", "_ep_api_vehicle_mode", "飞行模式信息（当前模式 + 可切换白名单）"),
    Route("GET", "/api/settings/vehicle-setup", "_ep_api_settings_vehicle_setup", "载具安装/调参快照"),
    Route("GET", "/api/settings/vehicle-telemetry", "_ep_api_settings_vehicle_telemetry", "载具遥测历史（波形用）"),
    Route("GET", "/api/settings/camera", "_ep_api_settings_camera", "相机设置读取"),
    Route("GET", "/api/camera/preview", "_ep_api_camera_preview", "单帧相机预览（优先感知轴缓存）"),
    Route("GET", "/api/gcs/mission", "_ep_api_gcs_mission", "任务草案读取（航线面板）"),
    Route("GET", "/api/gcs/mission/progress", "_ep_api_gcs_mission_progress", "任务执行进度"),
    Route("GET", "/api/models", "_ep_api_models", "模型配置列表（含能力探测结果）"),
    Route("GET", "/api/attachments/*", "_ep_api_attachments_tail", "会话附件二进制（图片等）"),
    Route("GET", "/tile/*", "_ep_tile_tail", "地图瓦片代理（带磁盘缓存）"),
    Route("GET", "/captures/*", "_ep_captures_tail", "本地截图/证据帧文件"),
    Route("POST", "/api/command", "_ep_api_command", "下发自然语言指令，启动一次 Agent 任务"),
    Route("POST", "/api/control", "_ep_api_control", "手动飞控动作：解锁/起飞/降落/悬停/返航/暂停/急停"),
    Route("POST", "/api/perception/view", "_ep_api_perception_view", "切换感知轴取景（相机名/图像类型）"),
    Route("POST", "/api/settings/connections", "_ep_api_settings_connections_post", "保存连接配置"),
    Route("POST", "/api/settings/agent-prompt", "_ep_api_settings_agent_prompt_post", "保存常驻提示词 SYSTEM.md"),
    Route("POST", "/api/settings/application", "_ep_api_settings_application_post", "保存应用设置"),
    Route("POST", "/api/settings/camera", "_ep_api_settings_camera_post", "保存相机设置"),
    Route("POST", "/api/vehicle/mode", "_ep_api_vehicle_mode_post", "切换飞行模式（仅白名单内的模式）"),
    Route("POST", "/api/settings/connections/activate", "_ep_api_settings_connections_activate", "激活一条连接（等价于切换后端并连接）"),
    Route("POST", "/api/settings/connections/deactivate", "_ep_api_settings_connections_deactivate", "断开当前连接"),
    Route("POST", "/api/gcs/mission", "_ep_api_gcs_mission_post", "保存任务草案"),
    Route("POST", "/api/gcs/mission/download", "_ep_api_gcs_mission_download", "从飞控下载当前任务到草案"),
    Route("POST", "/api/gcs/mission/upload", "_ep_api_gcs_mission_upload", "上传草案到飞控"),
    Route("POST", "/api/gcs/mission/start", "_ep_api_gcs_mission_start", "启动已上传的任务"),
    Route("POST", "/api/gcs/mission/start_multi", "_ep_api_gcs_mission_start_multi", "多机任务：后台任务非阻塞执行（每机起飞+航线）"),
    Route("POST", "/api/gcs/mission/progress", "_ep_api_gcs_mission_progress_post", "任务执行进度（POST 版）"),
    Route("POST", "/api/gcs/mission/clear", "_ep_api_gcs_mission_clear", "清空飞控任务"),
    Route("POST", "/api/approve", "_ep_api_approve", "批准待审批的高风险动作（真机审批门）"),
    Route("POST", "/api/reject", "_ep_api_reject", "拒绝待审批动作"),
    Route("POST", "/api/flightlog/record", "_ep_api_flightlog_record", "手动开始/停止飞行记录"),
    Route("POST", "/api/sessions*", "_ep_api_sessionstail", "会话操作：新建/加载/重命名/删除（路径尾段指定动作）"),
    Route("POST", "/api/skills", "_ep_api_skills_post", "技能管理：新建/编辑/删除/停用"),
    Route("POST", "/api/models", "_ep_api_models_post", "保存模型配置"),
    Route("POST", "/api/models/catalog", "_ep_api_models_catalog", "从模型厂商目录拉取可用模型列表"),
    Route("POST", "/api/models/*", "_ep_api_models_tail", "模型操作（连通性探测/删除等）"),
    Route("POST", "/api/tool", "_ep_api_tool", "直接执行一次工具调用（调试/运维用）"),
    Route("GET", "/api/endpoints", "_ep_api_endpoints", "本表：所有 HTTP 端点的方法、路径与说明（自文档）"),
)

_PREFIX_ROUTES: tuple[Route, ...] = tuple(
    sorted((r for r in ROUTES if r.pattern.endswith("*")), key=lambda r: -len(r.pattern))
)


def match_route(method: str, path: str) -> Route | None:
    """按方法 + 路径找路由：精确匹配优先，其次前缀（长的优先）。"""
    for route in ROUTES:
        if route.method == method and not route.pattern.endswith("*") and route.pattern == path:
            return route
    for route in _PREFIX_ROUTES:
        if route.method == method and path.startswith(route.pattern[:-1]):
            return route
    return None


class _Request:
    """一次请求的上下文：路由后的处理函数只从这里取输入。"""

    __slots__ = ("method", "path", "parsed", "payload")

    def __init__(self, method: str, path: str, parsed: Any, payload: dict[str, Any]) -> None:
        self.method = method
        self.path = path
        self.parsed = parsed
        self.payload = payload


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "AirSimAgentUI/0.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        """路由 → 端点方法；未命中时：GET 兜底静态文件，POST 返回 404。"""
        parsed = urlparse(self.path)
        path = unquote(parsed.path) if method == "GET" else parsed.path
        payload: dict[str, Any] = {}
        if method == "POST":
            try:
                payload = self._read_json()
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                return
        route = match_route(method, path)
        if route is not None:
            getattr(self, route.handler)(_Request(method, path, parsed, payload))
            return
        if method == "POST":
            self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        # GET 兜底：静态文件（/ → index.html）。未知路径由 _serve_file 报 404。
        if path == "/":
            path = "/index.html"
        self._serve_file(STATIC_DIR / path.lstrip("/"), base=STATIC_DIR)

    # ------------------------------------------------------------------
    # 端点处理器：与 ROUTES 一一对应（分支体与原实现逐字一致）
    # ------------------------------------------------------------------

    def _ep_api_state(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.state())
        return

    def _ep_api_telemetry(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.telemetry_state())
        return

    def _ep_api_stream(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_sse()
        return

    def _ep_api_sessions(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, "sessions": RUNTIME.list_sessions()})
        return

    def _ep_api_sessions_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._handle_session_get(path, parsed)
        return

    def _ep_api_flightlog_sessions(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, "sessions": RUNTIME.flightlog_sessions()})
        return

    def _ep_api_flightlog_sessions_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        name = unquote(path[len("/api/flightlog/sessions/"):])
        session = RUNTIME.flightlog_session(name)
        if session is None:
            self._send_json({"ok": False, "error": "flightlog session not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"ok": True, "session": session})
        return

    def _ep_api_tools(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"tools": RUNTIME.tools.list_tools()})
        return

    def _ep_api_skills(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, "skills": self._list_skills()})
        return

    def _ep_api_health(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, "service": "airsim-agent-ui"})
        return

    def _ep_api_backends(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({
            "ok": True,
            "backends": RUNTIME.tools.backend_registry.list_public(),
            "current": RUNTIME.tools.backend_id,
        })
        return

    def _ep_api_link(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        # Single end-point describing how the Agent is wired to the
        # vehicle: flight control link + perception axis link + telemetry
        # source. Surfaces connection strings, source type, health.
        self._send_json({"ok": True, **RUNTIME.link_summary()})
        return

    def _ep_api_settings_connections(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, **RUNTIME.connection_settings()})
        return

    def _ep_api_settings_agent_prompt(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, **RUNTIME.agent_instructions_payload()})
        return

    def _ep_api_settings_application(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({"ok": True, "application": RUNTIME.application_settings()})
        return

    def _ep_api_settings_vehicle_info(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        params = self._query_params(parsed)
        refresh = str(params.get("refresh", "")).lower() in {"1", "true", "yes", "force"}
        self._send_json(RUNTIME.vehicle_info(refresh=refresh))
        return

    def _ep_api_vehicle_mode(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.vehicle_mode_info())
        return

    def _ep_api_settings_vehicle_setup(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        params = self._query_params(parsed)
        include_history = str(params.get("history", "1")).lower() not in {"0", "false", "no"}
        try:
            history_limit = int(params.get("limit", 240) or 240)
        except (TypeError, ValueError):
            history_limit = 240
        self._send_json(RUNTIME.vehicle_setup_snapshot(
            include_history=include_history,
            history_limit=history_limit,
        ))
        return

    def _ep_api_settings_vehicle_telemetry(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        params = self._query_params(parsed)
        include_history = str(params.get("history", "1")).lower() not in {"0", "false", "no"}
        try:
            history_limit = int(params.get("limit", 240) or 240)
        except (TypeError, ValueError):
            history_limit = 240
        history_keys = [
            value.strip()
            for value in str(params.get("groups") or "").split(",")
            if value.strip()
        ]
        self._send_json(RUNTIME.vehicle_telemetry_snapshot(
            include_history=include_history,
            history_limit=history_limit,
            history_keys=history_keys or None,
        ))
        return

    def _ep_api_settings_camera(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({
            "ok": True,
            "camera": RUNTIME.camera_settings(),
            # 相机下拉的事实来源：AirSim settings.json 声明的相机，而不是前端写死的 0..4。
            "airsim_cameras": RUNTIME.airsim_camera_catalog(),
        })
        return

    def _ep_api_camera_preview(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        params = self._query_params(parsed)
        ok, body, mime_type, meta = RUNTIME.camera_preview_frame(params)
        if not ok:
            self._send_json({"ok": False, "error": meta.get("message", "camera preview unavailable"), "meta": meta}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._send_binary(
            body,
            mime_type,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-Camera-Meta": json.dumps(meta, ensure_ascii=True, separators=(",", ":")),
            },
        )
        return

    def _ep_api_gcs_mission(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.gcs_mission_get())
        return

    def _ep_api_gcs_mission_progress(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.gcs_mission_progress())
        return

    def _ep_api_models(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json({
            "ok": True,
            "models": MODEL_REGISTRY.list_public(),
            "default": MODEL_REGISTRY._default_id,
        })
        return

    def _ep_api_attachments_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        storage_key = path.rsplit("/", 1)[-1]
        item = RUNTIME.attachment_file(storage_key)
        if not item:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path, mime_type = item
        self._serve_binary(file_path, mime_type)
        return

    def _ep_tile_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._serve_tile(path)
        return

    def _ep_captures_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._serve_file(REPO_ROOT / path.lstrip("/"), base=REPO_ROOT / "captures")
        return

    def _ep_api_command(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        try:
            command = str(payload.get("command", ""))
            execute = bool(payload.get("execute", False))
            model_id = str(payload.get("model", ""))
            mode = str(payload.get("mode", ""))
            attachments = payload.get("attachments") or []
            if not isinstance(attachments, list):
                raise ValueError("attachments must be a list")
            self._send_json(RUNTIME.submit_command(
                command,
                execute=execute,
                model_id=model_id,
                mode=mode,
                attachments=attachments,
            ))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
        return

    def _ep_api_control(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        action = str(payload.get("action", ""))
        vehicles = payload.get("vehicles")
        self._send_json(RUNTIME.control(
            action,
            expected_backend=str(payload.get("expected_backend", "")),
            vehicles=vehicles if isinstance(vehicles, list) else None,
        ))
        return

    def _ep_api_perception_view(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        # 相机面板切相机/图像类型时走这里：把"看哪一路"变成感知轴的显式
        # 动作，而不是让面板另开一条取流各看各的。
        camera_name = str(payload.get("camera_name") or "").strip()
        if not camera_name:
            self._send_json({"ok": False, "error": "camera_name required"}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(RUNTIME.perception_set_view(camera_name))
        return

    def _ep_api_settings_connections_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.save_connection_settings(payload))
        return

    def _ep_api_settings_agent_prompt_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.save_agent_instructions(str(payload.get("content") or "")))
        return

    def _ep_api_settings_application_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.save_application_settings(payload))
        return

    def _ep_api_settings_camera_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.save_camera_settings(payload))
        return

    def _ep_api_vehicle_mode_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.set_vehicle_mode(str(payload.get("mode") or "")))
        return

    def _ep_api_settings_connections_activate(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        connection_id = str(payload.get("connection_id", ""))
        if not connection_id:
            self._send_json({"ok": False, "error": "connection_id required"}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(RUNTIME.activate_connection(connection_id))
        return

    def _ep_api_settings_connections_deactivate(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.deactivate_connection())
        return

    def _ep_api_gcs_mission_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
        self._send_json(RUNTIME.gcs_mission_set(draft_data))
        return

    def _ep_api_gcs_mission_download(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.gcs_mission_download(expected_backend=str(payload.get("expected_backend", ""))))
        return

    def _ep_api_gcs_mission_upload(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else (payload if payload else None)
        self._send_json(RUNTIME.gcs_mission_upload(
            draft_data,
            expected_backend=str(payload.get("expected_backend", "")),
        ))
        return

    def _ep_api_gcs_mission_start(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else (payload if payload else None)
        self._send_json(RUNTIME.gcs_mission_start(
            draft_data,
            expected_backend=str(payload.get("expected_backend", "")),
        ))
        return

    def _ep_api_gcs_mission_start_multi(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        assignments = payload.get("assignments")
        self._send_json(RUNTIME.gcs_mission_start_multi(
            assignments,
            expected_backend=str(payload.get("expected_backend", "")),
        ))
        return

    def _ep_api_gcs_mission_progress_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.gcs_mission_progress())
        return

    def _ep_api_gcs_mission_clear(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._send_json(RUNTIME.gcs_mission_clear(expected_backend=str(payload.get("expected_backend", ""))))
        return

    def _ep_api_approve(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        run_id = str(payload.get("run_id", ""))
        if not run_id:
            self._send_json({"ok": False, "error": "run_id required"}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(RUNTIME.approve_run(run_id))
        return

    def _ep_api_reject(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        run_id = str(payload.get("run_id", ""))
        if not run_id:
            self._send_json({"ok": False, "error": "run_id required"}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(RUNTIME.reject_run(run_id))
        return

    def _ep_api_flightlog_record(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        action = str(payload.get("action", "")).strip().lower()
        if action == "start":
            self._send_json(RUNTIME.start_manual_flightlog(name=str(payload.get("name", ""))))
        elif action == "stop":
            self._send_json(RUNTIME.stop_manual_flightlog())
        else:
            self._send_json({"ok": False, "error": "action must be start or stop"}, HTTPStatus.BAD_REQUEST)
        return

    def _ep_api_sessionstail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._handle_session_post(path, payload)
        return

    def _ep_api_skills_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._handle_skill_post(payload)
        return

    def _ep_api_models_post(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._handle_model_post(payload)
        return

    def _ep_api_models_catalog(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        result = list_provider_models(
            str(payload.get("base_url", "")),
            str(payload.get("api_type", "openai")),
            str(payload.get("api_key", "")),
        )
        self._send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
        return

    def _ep_api_models_tail(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        self._handle_model_action(path, payload)
        return

    def _ep_api_tool(self, req: "_Request") -> None:
        path, parsed, payload = req.path, req.parsed, req.payload
        tool = str(payload.get("tool", ""))
        params = payload.get("params", {})
        dry_run = bool(payload.get("dry_run", False))
        if not isinstance(params, dict):
            self._send_json({"ok": False, "error": "params must be an object"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self._send_json(RUNTIME.execute_tool(
                tool,
                params=params,
                dry_run=dry_run,
                expected_backend=str(payload.get("expected_backend", "")),
            ))
        except Exception as e:
            # 工具异常必须带 traceback 落日志，否则前端只看到一个干巴巴的
            # 错误字符串，无法定位（例如编码/模型返回格式问题）。
            import logging
            import traceback

            logging.getLogger("ui").warning(
                "tool_exception tool=%s: %s\n%s",
                tool, e, "\n".join(traceback.format_exc().splitlines()[-25:]),
            )
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return


    def _ep_api_endpoints(self, req: "_Request") -> None:
        self._send_json(
            {"ok": True, "count": len(ROUTES), "endpoints": [route.public() for route in ROUTES]}
        )

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 18 * 1024 * 1024:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _serve_binary(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def _query_params(self, parsed) -> dict:
        query = parse_qs(parsed.query, keep_blank_values=True)
        return {str(key): values[-1] if values else "" for key, values in query.items()}

    def _send_binary(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(str(key), str(value))
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        # bounded serialization: a pathologically deep payload (sick LLM
        # output) must never make json.dumps raise RecursionError and kill
        # the request thread mid-poll (frontend freezes on no updates)
        body = json.dumps(_bounded_copy(data), ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def _send_sse(self) -> None:
        subscriber = RUNTIME.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            self._write_sse("snapshot", RUNTIME.state())
            while True:
                try:
                    envelope = subscriber.get(timeout=15)
                    self._write_sse(str(envelope.get("type") or "message"), envelope.get("payload") or {})
                except queue.Empty:
                    self._write_sse("ping", {"ok": True})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            RUNTIME.unsubscribe(subscriber)

    def _write_sse(self, event: str, data: dict) -> None:
        payload = json.dumps(_bounded_copy(data), ensure_ascii=False, default=str)
        frame = f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        try:
            self.wfile.write(frame)
            self.wfile.flush()
        except OSError as exc:
            if _is_client_disconnect(exc):
                raise ConnectionAbortedError("SSE client disconnected") from exc
            raise

    def _handle_session_get(self, path: str, parsed) -> None:
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[-1] == "export":
            session_id = parts[2]
            params = self._query_params(parsed)
            result = RUNTIME.export_session(session_id, str(params.get("format") or "markdown"))
            if not result.get("ok"):
                self._send_json(result, HTTPStatus.NOT_FOUND)
                return
            self._send_binary(
                str(result.get("content") or "").encode("utf-8"),
                str(result.get("content_type") or "text/plain; charset=utf-8"),
                headers={
                    "Content-Disposition": f'attachment; filename="{result.get("filename") or "session.md"}"',
                    "Cache-Control": "no-store",
                },
            )
            return
        if len(parts) >= 4 and parts[-1] == "history":
            result = RUNTIME.session_history(parts[2])
            self._send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND)
            return
        if len(parts) < 4 or parts[-1] != "load":
            self._send_json({"ok": False, "error": "invalid session endpoint"}, HTTPStatus.NOT_FOUND)
            return
        session_id = parts[2]
        self._send_json(RUNTIME.load_session(session_id))

    def _handle_session_post(self, path: str, payload: dict) -> None:
        if path == "/api/sessions":
            name = str(payload.get("name", ""))
            self._send_json(RUNTIME.create_session(name))
            return

        parts = path.strip("/").split("/")
        if len(parts) < 4:
            self._send_json({"ok": False, "error": "invalid session endpoint"}, HTTPStatus.NOT_FOUND)
            return

        session_id = parts[2]
        action = parts[3]
        if action == "load":
            self._send_json(RUNTIME.load_session(session_id))
        elif action == "rename":
            name = str(payload.get("name", ""))
            self._send_json(RUNTIME.rename_session(session_id, name))
        elif action == "delete":
            self._send_json(RUNTIME.delete_session(session_id))
        else:
            self._send_json({"ok": False, "error": "unknown session action"}, HTTPStatus.NOT_FOUND)

    def _handle_model_post(self, payload: dict) -> None:
        action = str(payload.get("action", "add"))
        if action == "default":
            model_id = str(payload.get("id", ""))
            try:
                MODEL_REGISTRY.set_default(model_id)
                RUNTIME.planner.reload_config()
                self._send_json({"ok": True, "default": MODEL_REGISTRY._default_id})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        model = {
            "id": str(payload.get("id", "")).strip() or f"model_{int(time.time() * 1000)}",
            "name": str(payload.get("name", "")).strip() or "未命名模型",
            "model": str(payload.get("model", "")).strip(),
            "base_url": str(payload.get("base_url", "")).strip().rstrip("/"),
            "api_key": str(payload.get("api_key", "")),
            "api_type": str(payload.get("api_type", "openai")).strip() or "openai",
            "timeout_sec": float(payload["timeout_sec"]) if payload.get("timeout_sec") not in (None, "") else 25.0,
            # 能力一律自动识别，用户不需要手填
            "capability_mode": "auto",
        }
        # provider 只用于界面分组：用户没填就从 Base URL 推断
        model["provider"] = str(payload.get("provider", "")).strip() or derive_provider(
            model["base_url"], model["api_type"], model["model"]
        )
        # 保存路径上的能力探测是"锦上添花"：给它更短的预算，别让用户等太久
        probed = probe_model_capabilities(
            model["model"], model["api_type"], model["base_url"], model["api_key"], timeout=4.0
        )
        if probed.get("ok"):
            model["capabilities"] = {**probed["capabilities"], "fetched_at": _utc_now_iso()}
        try:
            MODEL_REGISTRY.add(model)
            RUNTIME.planner.reload_config()
            self._send_json({"ok": True, "model": self._public_model(model["id"])})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)

    def _handle_model_action(self, path: str, payload: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            self._send_json({"ok": False, "error": "invalid model endpoint"}, HTTPStatus.NOT_FOUND)
            return
        model_id = parts[2]
        sub_action = parts[3] if len(parts) >= 4 else ""

        if sub_action == "delete":
            try:
                MODEL_REGISTRY.delete(model_id)
                RUNTIME.planner.reload_config()
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
            return
        if sub_action == "reveal-key":
            model = MODEL_REGISTRY.get(model_id)
            if not model:
                self._send_json({"ok": False, "error": "model not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True, "api_key": str(model.get("api_key") or "")})
            return

        if sub_action == "detect":
            model = MODEL_REGISTRY.get(model_id)
            if not model:
                self._send_json({"ok": False, "error": "model not found"}, HTTPStatus.NOT_FOUND)
                return
            probed = probe_model_capabilities(
                str(model.get("model") or ""),
                str(model.get("api_type") or "openai"),
                str(model.get("base_url") or ""),
                str(model.get("api_key") or ""),
            )
            if not probed.get("ok"):
                self._send_json({
                    "ok": False,
                    "error": probed.get("error", "probe failed"),
                    "message": probed.get("message", ""),
                    "available_models": probed.get("available_models") or [],
                    "available_total": probed.get("available_total") or 0,
                }, HTTPStatus.BAD_REQUEST)
                return
            if probed.get("capabilities"):
                MODEL_REGISTRY.set_capabilities(model_id, {**probed["capabilities"], "fetched_at": _utc_now_iso()})
            RUNTIME.planner.reload_config()
            self._send_json({
                "ok": True,
                "model": self._public_model(model_id),
                "catalog_metadata": bool(probed.get("catalog_metadata")),
                "message": probed.get("message", ""),
            })
            return

        updates = {}
        for key in [
            "name",
            "model",
            "base_url",
            "api_key",
            "api_type",
            "timeout_sec",
            "thinking_mode",
            "reasoning_effort",
        ]:
            if key in payload:
                if key == "timeout_sec":
                    updates[key] = float(payload[key])
                elif key == "base_url":
                    updates[key] = str(payload[key]).strip().rstrip("/")
                else:
                    updates[key] = str(payload[key]).strip()
        updates["capability_mode"] = "auto"
        # 清除旧的参数化字段，避免残留配置影响模型能力；capability_mode 是
        # 用户的手动覆盖项，探测会重新执行，因此不在清除之列。
        for stale in ("max_tokens", "temperature", "generation_mode", "context_window"):
            updates[stale] = None
        # Base URL / 模型 ID 变了就重推 provider，保持分组标签跟着走
        if "base_url" in updates or "model" in updates:
            merged_for_provider = {**(MODEL_REGISTRY.get(model_id) or {}), **{k: v for k, v in updates.items() if v is not None}}
            updates["provider"] = derive_provider(
                str(merged_for_provider.get("base_url") or ""),
                str(merged_for_provider.get("api_type") or "openai"),
                str(merged_for_provider.get("model") or ""),
            )
        try:
            MODEL_REGISTRY.update(model_id, updates)
            merged = MODEL_REGISTRY.get(model_id) or {}
            probed = probe_model_capabilities(
                str(merged.get("model") or ""),
                str(merged.get("api_type") or "openai"),
                str(merged.get("base_url") or ""),
                str(merged.get("api_key") or ""),
            )
            if probed.get("ok"):
                MODEL_REGISTRY.set_capabilities(model_id, {**probed["capabilities"], "fetched_at": _utc_now_iso()})
            RUNTIME.planner.reload_config()
            self._send_json({"ok": True, "model": self._public_model(model_id)})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)

    def _public_model(self, model_id: str) -> dict[str, Any] | None:
        model = MODEL_REGISTRY.get(model_id)
        if not model:
            return None
        return MODEL_REGISTRY._public_model(model)

    def _list_skills(self) -> list[dict[str, Any]]:
        """List executable skills plus every user-editable SKILL.md document."""
        executable = {
            str(card.get("name")): card
            for card in RUNTIME.agent_loop.skills.all_cards()
            if isinstance(card, dict) and card.get("name")
        }
        docs = {
            str(card.get("name")): card
            for card in RUNTIME.agent_loop.skills.doc_cards()
            if isinstance(card, dict) and card.get("name")
        }
        names = sorted(set(executable) | set(docs))
        return [self._public_skill_card(name, executable.get(name), docs.get(name)) for name in names]

    def _public_skill_card(
        self,
        action_name: str,
        executable_card: dict[str, Any] | None,
        doc_card: dict[str, Any] | None,
    ) -> dict[str, Any]:
        card = {**(doc_card or {}), **(executable_card or {})}
        doc_path = str(card.get("doc_path") or (doc_card or {}).get("doc_path") or "")
        markdown = ""
        if doc_path:
            try:
                resolved = Path(doc_path).resolve()
                skills_root = (REPO_ROOT / ".airsim_agent" / "skills").resolve()
                if resolved == skills_root or skills_root in resolved.parents:
                    markdown = resolved.read_text(encoding="utf-8")
            except Exception:
                markdown = ""
        executable = bool(executable_card)
        doc_status = str(card.get("doc_status") or (doc_card or {}).get("doc_status") or "")
        # 界面只展示"装在哪"：给出相对仓库根的路径，用户可以直接去改文件
        try:
            doc_path_rel = str(Path(doc_path).resolve().relative_to(REPO_ROOT).as_posix()) if doc_path else ""
        except Exception:
            doc_path_rel = ""
        return {
            "doc_path_rel": doc_path_rel,
            "id": action_name,
            "name": action_name,
            "display_name": card.get("display_name") or action_name,
            "description": card.get("purpose") or card.get("description") or "",
            "purpose": card.get("purpose") or card.get("description") or "",
            "when_to_use": card.get("when_to_use") or "",
            "supported_actions": [action_name] if executable else [],
            "required_capabilities": list(card.get("required_capabilities") or []),
            "parameters": dict(card.get("inputs") or card.get("parameters") or {}),
            "subtools": list(card.get("subtools") or []),
            "cost": card.get("cost") or "medium",
            "risk": card.get("risk") or "medium",
            "status": "ready" if executable else (doc_status or "draft"),
            "doc_status": doc_status,
            "doc_type": card.get("doc_type") or "",
            "markdown": markdown,
            "executable": executable,
            "source": "workspace" if doc_path else "runtime",
            "enabled": executable or doc_status not in {"disabled", "archived"},
        }

    def _handle_skill_post(self, payload: dict) -> None:
        action = str(payload.get("action") or "update").strip().lower()
        if action == "create":
            try:
                card = self._create_skill_markdown(payload)
                self._send_json({"ok": True, "skill": card})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if action == "delete":
            try:
                self._delete_skill_document(str(payload.get("id", "")))
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        action_name = str(payload.get("id", "")).strip()
        updates = payload.get("updates") or {}
        markdown = payload.get("markdown")
        if not action_name:
            self._send_json({"ok": False, "error": "id required"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(updates, dict):
            self._send_json({"ok": False, "error": "updates must be an object"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            if isinstance(markdown, str):
                self._save_skill_markdown(action_name, markdown)
                RUNTIME.agent_loop.skills.reload_docs()
                skills = {item["id"]: item for item in self._list_skills()}
                self._send_json({"ok": True, "skill": skills.get(action_name)})
                return
            card = RUNTIME.agent_loop.skills.update_spec(action_name, updates)
            self._send_json({"ok": True, "skill": card})
        except KeyError as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.NOT_FOUND)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)

    def _delete_skill_document(self, action_name: str) -> None:
        """删除工作区里的 SKILL.md（只允许删 skills/ 目录下的文档）。"""
        name = str(action_name or "").strip()
        if not name:
            raise ValueError("id required")
        cards = {
            str(item.get("name")): item
            for item in RUNTIME.agent_loop.skills.doc_cards()
            if isinstance(item, dict) and item.get("name")
        }
        card = cards.get(name)
        if not card:
            raise KeyError(f"unknown skill: {name}")
        doc_path = str(card.get("doc_path") or "")
        if not doc_path:
            raise ValueError("skill has no SKILL.md document")
        resolved = Path(doc_path).resolve()
        skills_root = (REPO_ROOT / ".airsim_agent" / "skills").resolve()
        if skills_root not in resolved.parents:
            raise ValueError("only workspace skills can be deleted")
        resolved.unlink()
        RUNTIME.agent_loop.skills.reload_docs()

    def _save_skill_markdown(self, action_name: str, markdown: str) -> None:
        cards = {
            str(item.get("name")): item
            for item in RUNTIME.agent_loop.skills.doc_cards()
            if isinstance(item, dict) and item.get("name")
        }
        card = cards.get(action_name)
        if not card:
            raise KeyError(f"unknown skill: {action_name}")
        doc_path = str(card.get("doc_path") or "")
        if not doc_path:
            raise ValueError("skill has no SKILL.md document")
        resolved = Path(doc_path).resolve()
        skills_root = (REPO_ROOT / ".airsim_agent" / "skills").resolve()
        if resolved == skills_root or skills_root not in resolved.parents:
            raise ValueError("skill document path is outside skills/")
        text = markdown.replace("\r\n", "\n").strip() + "\n"
        # Parse before writing so malformed frontmatter fails without corrupting the file.
        tmp_path = resolved.with_suffix(resolved.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        try:
            parsed = parse_skill_doc(tmp_path)
            if parsed.action_name != action_name:
                raise ValueError(f"SKILL.md name must remain {action_name}")
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        resolved.write_text(text, encoding="utf-8")

    def _create_skill_markdown(self, payload: dict) -> dict[str, Any] | None:
        raw_name = str(payload.get("id") or payload.get("name") or "").strip().lower()
        skill_slug = re.sub(r"[^a-z0-9_-]+", "_", raw_name.replace("skill:", "")).strip("_")
        if not skill_slug:
            raise ValueError("skill name required")
        action_name = f"skill:{skill_slug}"
        skills_root = (REPO_ROOT / ".airsim_agent" / "skills").resolve()
        destination = (skills_root / skill_slug / "SKILL.md").resolve()
        if skills_root not in destination.parents:
            raise ValueError("invalid skill name")
        if destination.exists():
            raise ValueError(f"skill '{action_name}' already exists")
        display_name = str(payload.get("display_name") or skill_slug.replace("_", " ").title()).strip()
        description = str(payload.get("description") or "当操作员需要执行该工作流时使用本技能。").strip()
        markdown = payload.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            # Trae/Codex 风格：frontmatter 只保留 name/description；
            # 节标题用规范中文名（解析器把"使用时机/失败处理"读进卡片字段）。
            markdown = (
                "---\n"
                f"name: {skill_slug}\n"
                f"description: {description}\n"
                "---\n\n"
                f"# {display_name}\n\n"
                "## 目的\n\n"
                f"{description}\n\n"
                "## 使用时机\n\n"
                "描述应该在什么操作员意图下激活这份指导。\n\n"
                "## 操作规则\n\n"
                "- 下达指令前先读取飞行器当前状态。\n"
                "- 只使用当前后端暴露的工具。\n"
            )
        text = markdown.replace("\r\n", "\n").strip() + "\n"
        destination.parent.mkdir(parents=True, exist_ok=False)
        try:
            destination.write_text(text, encoding="utf-8")
            parsed = parse_skill_doc(destination)
            if parsed.action_name != action_name:
                raise ValueError(f"SKILL.md name must be {action_name}")
            RUNTIME.agent_loop.skills.reload_docs()
        except Exception:
            try:
                destination.unlink()
                destination.parent.rmdir()
            except OSError:
                pass
            raise
        cards = {item["id"]: item for item in self._list_skills()}
        return cards.get(action_name)

    def _serve_file(self, path: Path, base: Path) -> None:
        try:
            resolved = path.resolve()
            base_resolved = base.resolve()
            if base_resolved not in resolved.parents and resolved != base_resolved:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not resolved.exists() or not resolved.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            body = resolved.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            if _is_client_disconnect(e):
                return
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def _send_bytes(self, data: bytes, content_type: str, cache: bool = False) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            # 瓦片长期不变，浏览器可长期缓存（参考 QGC 磁盘缓存策略）
            if cache:
                self.send_header("Cache-Control", "public, max-age=604800, immutable")
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def _serve_tile(self, path: str) -> None:
        """瓦片代理 + 磁盘缓存（参考 QGC QGCCachedTileSet）。

        路由: /tile/{layer}/{z}/{x}/{y}
        首次从源拉取并写入本地缓存，后续直接返回本地文件（毫秒级）。
        """
        parts = path.strip("/").split("/")
        # 期望: ["tile", layer, z, x, y]
        if len(parts) != 5:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        _, layer, z, x, y = parts
        src = TILE_SOURCES.get(layer)
        if src is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        url_tmpl, default_ct = src
        cache_path = TILE_CACHE_DIR / layer / z / x / f"{y}.tile"

        # 命中本地缓存：直接返回（毫秒级，QGC 同款思路）
        if cache_path.exists():
            try:
                data = cache_path.read_bytes()
                self._send_bytes(data, default_ct, cache=True)
                return
            except Exception:
                pass

        # 未命中：从源拉取
        try:
            url = url_tmpl.format(z=z, x=x, y=y)
            req = urllib.request.Request(url, headers={"User-Agent": "AirSimAgentUI/0.1"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", default_ct)
        except Exception as e:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"tile fetch failed: {e}")
            return

        # 写入本地缓存（加锁防并发竞争）
        try:
            with _TILE_CACHE_LOCK:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
        except Exception:
            pass

        self._send_bytes(data, content_type, cache=True)


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_saved_backend() -> str:
    try:
        from src.paths import AGENT_HOME

        data = json.loads((AGENT_HOME / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("backend", ""))
    except Exception:
        return ""


def main() -> None:
    # INFO 级别：返航/降落监视线程的执行轨迹需要可见，否则无法诊断
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="AirSim VLA Agent Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--backend",
        default="",
        help="Execution backend: airsim, px4_mavlink, or px4_ros2 (default: px4_mavlink)",
    )
    args = parser.parse_args()

    # CLI > env > saved settings > default
    backend = (
        args.backend
        or os.environ.get("AIRSIM_AGENT_BACKEND")
        or _load_saved_backend()
        or "px4_mavlink"
    )
    # Set the backend before AgentRuntime builds the backend registry.
    os.environ["AIRSIM_AGENT_BACKEND"] = backend

    httpd = AgentThreadingHTTPServer((args.host, args.port), AgentRequestHandler)
    global RUNTIME
    try:
        RUNTIME = AgentRuntime()
    except Exception:
        httpd.server_close()
        raise

    url = f"http://{args.host}:{args.port}"
    print(f"AirSim VLA Agent UI running at {url} (backend: {backend})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # 先把在跑的任务停下来并把飞机放到悬停再关服务：worker 是 daemon 线程，
        # 直接退出会在任意点把它们杀掉（可能夹在一条飞控命令中间）。
        runtime = RUNTIME
        if runtime is not None:
            try:
                runtime.shutdown()
            except Exception as exc:
                print(f"runtime shutdown failed: {exc}", flush=True)
        httpd.server_close()


if __name__ == "__main__":
    main()
