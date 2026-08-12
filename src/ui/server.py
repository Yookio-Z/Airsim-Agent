"""Stdlib HTTP server for the AirSim VLA command center."""

from __future__ import annotations

import argparse
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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.agent import AgentRuntime
from src.agent.llm import ModelRegistry
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


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "AirSimAgentUI/0.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/state":
            self._send_json(RUNTIME.state())
            return
        if path == "/api/telemetry":
            self._send_json(RUNTIME.telemetry_state())
            return
        if path == "/api/stream":
            self._send_sse()
            return
        if path == "/api/sessions":
            self._send_json({"ok": True, "sessions": RUNTIME.list_sessions()})
            return
        if path.startswith("/api/sessions/"):
            self._handle_session_get(path, parsed)
            return
        if path == "/api/replay/sessions":
            self._send_json({"ok": True, "sessions": RUNTIME.replay_sessions()})
            return
        if path.startswith("/api/replay/sessions/"):
            name = unquote(path[len("/api/replay/sessions/"):])
            session = RUNTIME.get_replay_session(name)
            if session is None:
                self._send_json({"ok": False, "error": "replay session not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True, "session": session})
            return
        if path == "/api/tools":
            self._send_json({"tools": RUNTIME.tools.list_tools()})
            return
        if path == "/api/skills":
            self._send_json({"ok": True, "skills": self._list_skills()})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "service": "airsim-agent-ui"})
            return
        if path == "/api/backends":
            self._send_json({
                "ok": True,
                "backends": RUNTIME.tools.backend_registry.list_public(),
                "current": RUNTIME.tools.backend_id,
            })
            return

        if path == "/api/settings/connections":
            self._send_json({"ok": True, **RUNTIME.connection_settings()})
            return
        if path == "/api/settings/application":
            self._send_json({"ok": True, "application": RUNTIME.application_settings()})
            return
        if path == "/api/settings/vehicle-info":
            params = self._query_params(parsed)
            refresh = str(params.get("refresh", "")).lower() in {"1", "true", "yes", "force"}
            self._send_json(RUNTIME.vehicle_info(refresh=refresh))
            return
        if path == "/api/settings/vehicle-setup":
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
        if path == "/api/settings/vehicle-telemetry":
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
        if path == "/api/settings/vehicle-parameters":
            params = self._query_params(parsed)
            refresh = str(params.get("refresh", "")).lower() in {"1", "true", "yes", "force"}
            try:
                limit = int(params.get("limit", 200) or 200)
            except (TypeError, ValueError):
                limit = 200
            try:
                offset = int(params.get("offset", 0) or 0)
            except (TypeError, ValueError):
                offset = 0
            try:
                timeout = float(params.get("timeout", 20.0) or 20.0)
            except (TypeError, ValueError):
                timeout = 20.0
            self._send_json(RUNTIME.vehicle_parameters(
                refresh=refresh,
                query=str(params.get("q", "")),
                limit=limit,
                offset=offset,
                timeout=timeout,
            ))
            return
        if path == "/api/settings/camera":
            self._send_json({"ok": True, "camera": RUNTIME.camera_settings()})
            return
        if path == "/api/camera/preview":
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

        # P6: GCS MissionManager facade endpoints
        if path == "/api/gcs/mission":
            self._send_json(RUNTIME.gcs_mission_get())
            return
        if path == "/api/gcs/mission/progress":
            self._send_json(RUNTIME.gcs_mission_progress())
            return

        if path == "/api/models":
            self._send_json({
                "ok": True,
                "models": MODEL_REGISTRY.list_public(),
                "default": MODEL_REGISTRY._default_id,
            })
            return

        if path.startswith("/api/attachments/"):
            storage_key = path.rsplit("/", 1)[-1]
            item = RUNTIME.attachment_file(storage_key)
            if not item:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_path, mime_type = item
            self._serve_binary(file_path, mime_type)
            return

        if path.startswith("/tile/"):
            self._serve_tile(path)
            return

        if path.startswith("/captures/"):
            self._serve_file(REPO_ROOT / path.lstrip("/"), base=REPO_ROOT / "captures")
            return

        if path == "/":
            path = "/index.html"
        self._serve_file(STATIC_DIR / path.lstrip("/"), base=STATIC_DIR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/command":
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

        if path == "/api/control":
            action = str(payload.get("action", ""))
            self._send_json(RUNTIME.control(action, expected_backend=str(payload.get("expected_backend", ""))))
            return
        if path == "/api/camera/frame":
            params = payload.get("params", {})
            if not isinstance(params, dict):
                self._send_json({"ok": False, "error": "params must be an object"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.capture_camera_frame(params))
            return
        if path == "/api/backend":
            backend_id = str(payload.get("backend", ""))
            connect_params = payload.get("connect_params")
            connection_id = payload.get("connection_id")
            if not backend_id:
                self._send_json({"ok": False, "error": "backend required"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.set_backend(
                backend_id,
                connect_params=connect_params,
                connection_id=connection_id,
            ))
            return

        if path == "/api/settings/connections":
            self._send_json(RUNTIME.save_connection_settings(payload))
            return

        if path == "/api/settings/application":
            self._send_json(RUNTIME.save_application_settings(payload))
            return

        if path == "/api/settings/camera":
            self._send_json(RUNTIME.save_camera_settings(payload))
            return

        if path == "/api/settings/vehicle-parameters/set":
            name = str(payload.get("name", "")).strip()
            if not name:
                self._send_json({"ok": False, "error": "name required"}, HTTPStatus.BAD_REQUEST)
                return
            component_id = payload.get("component_id")
            param_type = payload.get("param_type")
            try:
                clean_component_id = int(component_id) if component_id not in (None, "") else None
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "component_id must be an integer"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                clean_param_type = int(param_type) if param_type not in (None, "") else None
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "param_type must be an integer"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                timeout = float(payload.get("timeout", 3.0) or 3.0)
            except (TypeError, ValueError):
                timeout = 3.0
            self._send_json(RUNTIME.set_vehicle_parameter(
                name=name,
                value=payload.get("value"),
                component_id=clean_component_id,
                param_type=clean_param_type,
                timeout=timeout,
            ))
            return

        if path == "/api/settings/connections/activate":
            connection_id = str(payload.get("connection_id", ""))
            if not connection_id:
                self._send_json({"ok": False, "error": "connection_id required"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.activate_connection(connection_id))
            return

        # P6: GCS MissionManager facade endpoints (UI and Agent unified path)
        if path == "/api/gcs/mission":
            draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
            self._send_json(RUNTIME.gcs_mission_set(draft_data))
            return
        if path == "/api/gcs/mission/download":
            self._send_json(RUNTIME.gcs_mission_download(expected_backend=str(payload.get("expected_backend", ""))))
            return
        if path == "/api/gcs/mission/upload":
            draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else (payload if payload else None)
            self._send_json(RUNTIME.gcs_mission_upload(
                draft_data,
                expected_backend=str(payload.get("expected_backend", "")),
            ))
            return
        if path == "/api/gcs/mission/start":
            draft_data = payload.get("draft") if isinstance(payload.get("draft"), dict) else (payload if payload else None)
            self._send_json(RUNTIME.gcs_mission_start(
                draft_data,
                expected_backend=str(payload.get("expected_backend", "")),
            ))
            return
        if path == "/api/gcs/mission/progress":
            self._send_json(RUNTIME.gcs_mission_progress())
            return
        if path == "/api/gcs/mission/clear":
            self._send_json(RUNTIME.gcs_mission_clear(expected_backend=str(payload.get("expected_backend", ""))))
            return

        # P5: operator approval endpoints for high-risk direct tool calls
        if path == "/api/approve":
            run_id = str(payload.get("run_id", ""))
            if not run_id:
                self._send_json({"ok": False, "error": "run_id required"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.approve_run(run_id))
            return

        if path == "/api/reject":
            run_id = str(payload.get("run_id", ""))
            if not run_id:
                self._send_json({"ok": False, "error": "run_id required"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.reject_run(run_id))
            return

        # Replay: manual recording start/stop (runs auto-record in the runtime).
        if path == "/api/replay/record":
            action = str(payload.get("action", "")).strip().lower()
            if action == "start":
                self._send_json(RUNTIME.start_manual_replay(name=str(payload.get("name", ""))))
            elif action == "stop":
                self._send_json(RUNTIME.stop_manual_replay())
            else:
                self._send_json({"ok": False, "error": "action must be start or stop"}, HTTPStatus.BAD_REQUEST)
            return

        if path.startswith("/api/sessions"):
            self._handle_session_post(path, payload)
            return

        if path == "/api/skills":
            self._handle_skill_post(payload)
            return

        if path == "/api/models":
            self._handle_model_post(payload)
            return

        if path.startswith("/api/models/"):
            self._handle_model_action(path, payload)
            return

        if path == "/api/tool":
            tool = str(payload.get("tool", ""))
            params = payload.get("params", {})
            dry_run = bool(payload.get("dry_run", False))
            if not isinstance(params, dict):
                self._send_json({"ok": False, "error": "params must be an object"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(RUNTIME.execute_tool(
                tool,
                params=params,
                dry_run=dry_run,
                expected_backend=str(payload.get("expected_backend", "")),
            ))
            return

        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

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
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
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
        payload = json.dumps(data, ensure_ascii=False, default=str)
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
            "provider": str(payload.get("provider", "")).strip() or "openai",
            "model": str(payload.get("model", "")).strip(),
            "base_url": str(payload.get("base_url", "")).strip().rstrip("/"),
            "api_key": str(payload.get("api_key", "")),
            "api_type": str(payload.get("api_type", "openai")).strip() or "openai",
            "timeout_sec": float(payload["timeout_sec"]) if payload.get("timeout_sec") not in (None, "") else 25.0,
        }
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

        updates = {}
        for key in ["name", "provider", "model", "base_url", "api_key", "api_type", "timeout_sec"]:
            if key in payload:
                if key == "timeout_sec":
                    updates[key] = float(payload[key])
                elif key == "base_url":
                    updates[key] = str(payload[key]).strip().rstrip("/")
                else:
                    updates[key] = str(payload[key]).strip()
        # 清除旧的参数化字段，避免残留配置影响模型能力
        for stale in ("max_tokens", "temperature", "capability_mode", "generation_mode", "context_window"):
            updates[stale] = None
        try:
            MODEL_REGISTRY.update(model_id, updates)
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
                skills_root = (REPO_ROOT / "skills").resolve()
                if resolved == skills_root or skills_root in resolved.parents:
                    markdown = resolved.read_text(encoding="utf-8")
            except Exception:
                markdown = ""
        executable = bool(executable_card)
        doc_status = str(card.get("doc_status") or (doc_card or {}).get("doc_status") or "")
        return {
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
        skills_root = (REPO_ROOT / "skills").resolve()
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
        skills_root = (REPO_ROOT / "skills").resolve()
        destination = (skills_root / skill_slug / "SKILL.md").resolve()
        if skills_root not in destination.parents:
            raise ValueError("invalid skill name")
        if destination.exists():
            raise ValueError(f"skill '{action_name}' already exists")
        display_name = str(payload.get("display_name") or skill_slug.replace("_", " ").title()).strip()
        description = str(payload.get("description") or "Operator guidance for a UAV workflow.").strip()
        markdown = payload.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            # Trae/Codex 风格：frontmatter 只保留 name/description
            markdown = (
                "---\n"
                f"name: {skill_slug}\n"
                f"description: {description}\n"
                "---\n\n"
                f"# {display_name}\n\n"
                "## Purpose\n\n"
                f"{description}\n\n"
                "## When to Use\n\n"
                "Describe the operator intent that should activate this guidance.\n\n"
                "## Operating Rules\n\n"
                "- Read current vehicle state before issuing commands.\n"
                "- Use only tools exposed by the active backend.\n"
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


def _load_saved_backend() -> str:
    try:
        data = json.loads((REPO_ROOT / "src" / "data" / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("backend", ""))
    except Exception:
        return ""


def main() -> None:
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
        httpd.server_close()


if __name__ == "__main__":
    main()
