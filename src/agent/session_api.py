"""Chat sessions, attachments and the operator-editable agent instructions.

Session CRUD on top of session_store, the attachment blobs, and the application
settings the UI edits. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from . import (
    session_store,
    settings_store,
)
from .run_state import ChatMessage
from .session_store import (
    SESSION_MEMORY_DIR,
    _REPLACE_RETRIES,
    _SESSION_WRITE_LOCK,
    _sessions_by_mtime,
    _trim_session_message,
)
from .settings_store import (
    _application_settings,
)
from pathlib import Path
from src.logging_config import get_logger
from typing import Any
import base64
import hashlib
import json
import os
import re
import tempfile
import time


logger = get_logger(__name__)


class SessionMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def _store_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        if len(attachments) > 4:
            raise ValueError("每条消息最多附加 4 张图片")
        stored: list[dict[str, Any]] = []
        total_size = 0
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise ValueError("attachment must be an object")
            mime_type = str(item.get("mime_type") or item.get("type") or "").lower()
            data_url = str(item.get("data_url") or "")
            prefix = f"data:{mime_type};base64,"
            if mime_type not in allowed or not data_url.startswith(prefix):
                raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片")
            try:
                raw = base64.b64decode(data_url[len(prefix):], validate=True)
            except Exception as exc:
                raise ValueError(f"图片数据无法解析: {exc}") from exc
            if not raw:
                raise ValueError("图片不能为空")
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("单张图片不能超过 5 MB")
            total_size += len(raw)
            if total_size > 12 * 1024 * 1024:
                raise ValueError("单条消息图片总大小不能超过 12 MB")
            digest = hashlib.sha256(raw).hexdigest()
            storage_key = f"{digest}{allowed[mime_type]}"
            path = settings_store.attachments_dir() / storage_key
            if not path.exists():
                path.write_bytes(raw)
            stored.append({
                "id": digest[:16],
                "name": str(item.get("name") or f"image-{index + 1}{allowed[mime_type]}")[:120],
                "mime_type": mime_type,
                "size": len(raw),
                "storage_key": storage_key,
                "url": f"/api/attachments/{storage_key}",
            })
        return stored

    def _hydrate_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hydrated: list[dict[str, Any]] = []
        for item in attachments[:4]:
            if not isinstance(item, dict):
                continue
            key = Path(str(item.get("storage_key") or "")).name
            path = settings_store.attachments_dir() / key
            mime_type = str(item.get("mime_type") or "")
            if not key or not path.is_file() or mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                continue
            raw = path.read_bytes()
            hydrated.append({
                **item,
                "data_url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
            })
        return hydrated

    def attachment_file(self, storage_key: str) -> tuple[Path, str] | None:
        key = Path(storage_key).name
        if not key or key != storage_key:
            return None
        path = settings_store.attachments_dir() / key
        mime_by_suffix = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
        mime_type = mime_by_suffix.get(path.suffix.lower())
        if not mime_type or not path.is_file():
            return None
        return path, mime_type

    @classmethod
    def agent_instructions_payload(self) -> dict[str, Any]:
        """给设置面板读取：规范文件路径 + 当前内容（完整，不截断）。

        注入提示词时才会截断（见 _agent_instructions）：设置面板里显示/保存的必须
        是文件原文，否则面板一保存就把超出部分写没了。
        """
        try:
            path = self._AGENT_INSTRUCTIONS_PATH
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
        except Exception:
            content = ""
        return {"path": str(self._AGENT_INSTRUCTIONS_PATH), "content": content}

    def save_agent_instructions(self, content: str) -> dict[str, Any]:
        """保存操作规范（设置面板可编辑）。空内容 = 不注入任何额外规范。"""
        try:
            path = self._AGENT_INSTRUCTIONS_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content or ""), encoding="utf-8")
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _agent_instructions(cls) -> str:
        """读取可编辑的 Agent 操作规范；文件不存在或为空就不注入。"""
        try:
            path = cls._AGENT_INSTRUCTIONS_PATH
            if not path.is_file():
                return ""
            return path.read_text(encoding="utf-8").strip()[:4000]
        except Exception:
            return ""

    def application_settings(self) -> dict[str, Any]:
        """Return operator-facing application preferences."""
        return _application_settings()

    def save_application_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist validated application preferences."""
        try:
            settings = settings_store._load_settings()
            current = _application_settings(settings)
            incoming = payload if isinstance(payload, dict) else {}
            for group in current:
                update = incoming.get(group)
                if isinstance(update, dict):
                    current[group].update(update)
            settings["application"] = _application_settings({"application": current})
            settings_store._save_settings(settings)
            return {"ok": True, "application": settings["application"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出会话元数据。

        会话文件动辄几 MB（内含跑批状态），而 UI 每秒都会轮询 /api/state，
        每次都全量解析所有文件会让整个界面发卡。这里按 (mtime, size) 缓存
        元数据，只有文件真的变了才重新解析。
        """
        cache = getattr(self, "_session_meta_cache", None)
        if cache is None:
            cache = {}
            self._session_meta_cache = cache
        sessions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in _sessions_by_mtime():
            key = str(path)
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = cache.get(key)
            generation = getattr(self, "_session_write_generation", 0)
            if (cached and cached["mtime"] == stat.st_mtime and cached["size"] == stat.st_size
                    and cached.get("generation", -1) == generation):
                sessions.append(dict(cached["meta"]))
                continue
            data = session_store.read_session_file(path)
            if data is None:
                logger.warning("session_file_unreadable", path=key)
                continue
            messages = data.get("messages") or []
            meta = {
                "id": data.get("id", path.stem),
                "name": data.get("name", "未命名对话"),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(messages),
                # 左侧导航条是"一条输入一根线"，这里一并给出输入条数，
                # 免得会话列表写 120 条、导航条只有 60 根，看起来像丢了数据
                "input_count": sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user"),
            }
            cache[key] = {"mtime": stat.st_mtime, "size": stat.st_size, "meta": meta, "generation": generation}
            sessions.append(dict(meta))
        for stale_key in [k for k in cache if k not in seen]:
            cache.pop(stale_key, None)
        return sessions

    def session_history(self, session_id: str) -> dict[str, Any]:
        """Return the complete persisted session without changing the active session."""
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        data = session_store.read_session_file(path)
        if data is None:
            return {"ok": False, "error": "failed to load session: unreadable session file"}
        return {"ok": True, "session": self._session_public_dict(data)}

    def create_session(self, name: str = "") -> dict[str, Any]:
        # 执行中切会话会把运行态从 _current 里抹掉：任务继续飞，但界面上看不到
        # 它，暂停/停止也点不到。要求先结束任务。
        blocked = self._run_in_progress_error("新建对话")
        if blocked:
            return blocked
        now = time.time()
        session_id = f"session_{int(now * 1000)}"
        session = {
            "id": session_id,
            "name": name.strip() or "新对话",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        self._save_session(session)
        self._current_session_id = session_id
        self._bind_session_memory(session_id)
        with self._lock:
            self._messages.clear()
            self._current = None
            self._pending_run_ids.clear()
        self._publish("snapshot", self.state())
        return {"ok": True, "session": session}

    def load_session(self, session_id: str) -> dict[str, Any]:
        blocked = self._run_in_progress_error("切换对话")
        if blocked:
            return blocked
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        data = session_store.read_session_file(path)
        if data is None:
            return {"ok": False, "error": "failed to load session: unreadable session file"}

        messages = []
        for raw in data.get("messages", []):
            if not isinstance(raw, dict):
                continue
            messages.append(ChatMessage(
                id=str(raw.get("id", f"msg_{int(time.time() * 1000)}_")),
                role=str(raw.get("role", "assistant")),
                content=str(raw.get("content", "")),
                attachments=list(raw.get("attachments") or []),
                run_id=str(raw.get("run_id", "")),
                status=str(raw.get("status", "complete")),
                details=raw.get("details") or {},
                created_at=float(raw.get("created_at", time.time())),
                updated_at=float(raw.get("updated_at", time.time())),
            ))

        with self._lock:
            self._current_session_id = session_id
            self._bind_session_memory(session_id)
            self._messages = messages
            self._current = None
            self._pending_run_ids.clear()
            changed = self._mark_orphan_running_messages_locked()
        if changed:
            self._persist_current_session()
        self._publish("snapshot", self.state())
        return {"ok": True, "session": self._session_public_dict(data)}

    def rename_session(self, session_id: str, name: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            data = session_store.read_session_file(path)
            if data is None:
                return {"ok": False, "error": "rename failed: unreadable session file"}
            data["name"] = name.strip() or data.get("name", "未命名对话")
            data["updated_at"] = time.time()
            self._save_session(data)
            self._publish("snapshot", self.state())
            return {"ok": True, "session": data}
        except Exception as e:
            return {"ok": False, "error": f"rename failed: {e}"}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        try:
            path.unlink()
            if self._current_session_id == session_id:
                self._load_or_create_default_session()
            else:
                self._publish("snapshot", self.state())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"delete failed: {e}"}

    def export_session(self, session_id: str, export_format: str = "markdown") -> dict[str, Any]:
        """Export the complete persisted conversation without the model context filter."""
        path = self._session_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "session not found"}
        data = session_store.read_session_file(path)
        if data is None:
            return {"ok": False, "error": "failed to read session: unreadable session file"}

        clean_format = str(export_format or "markdown").strip().lower()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(data.get("name") or session_id)).strip("_") or session_id
        if clean_format == "json":
            content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            return {
                "ok": True,
                "filename": f"{safe_name}.json",
                "content_type": "application/json; charset=utf-8",
                "content": content,
            }

        lines = [
            f"# {data.get('name') or 'Conversation'}",
            "",
            f"- Session: `{data.get('id') or session_id}`",
            f"- Messages: {len(data.get('messages') or [])}",
            "",
        ]
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "assistant").capitalize()
            lines.extend((f"## {role}", "", str(message.get("content") or ""), ""))
            attachments = message.get("attachments") or []
            if attachments:
                lines.extend(("Attachments:", ""))
                for item in attachments:
                    if isinstance(item, dict):
                        label = item.get("name") or item.get("filename") or item.get("url") or "attachment"
                    else:
                        label = str(item)
                    lines.append(f"- {label}")
                lines.append("")
        return {
            "ok": True,
            "filename": f"{safe_name}.md",
            "content_type": "text/markdown; charset=utf-8",
            "content": "\n".join(lines).rstrip() + "\n",
        }

    def _load_or_create_default_session(self) -> None:
        sessions = self.list_sessions()
        if sessions:
            self.load_session(sessions[0]["id"])
            return
        self.create_session("新对话")

    def _persist_current_session(self) -> None:
        if not self._current_session_id:
            return
        path = self._session_path(self._current_session_id)
        try:
            if path.exists():
                data = session_store.read_session_file(path)
                if data is None:
                    # 文件存在但暂时读不出来（并发替换/损坏）：宁可跳过这次保存，
                    # 也不能用"新对话"覆盖掉原有的名字与创建时间
                    logger.warning("session_persist_skipped_unreadable", session=self._current_session_id)
                    return
            else:
                data = {"id": self._current_session_id, "name": "新对话", "created_at": time.time()}
            with self._lock:
                data["messages"] = [m.to_dict() for m in self._messages]
            data["updated_at"] = time.time()
            self._save_session(data)
        except Exception:
            pass

    def _bind_session_memory(self, session_id: str) -> None:
        """把 Agent 记忆切换到指定会话的独立存储。"""
        sid = str(session_id or "").strip() or "default"
        try:
            self.memory.rebind(SESSION_MEMORY_DIR / sid)
        except Exception:
            pass

    def _session_public_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """会话发给前端前的瘦身：剥掉 details 里的大字段。

        原始文件里 loop_state.observations 一条就能有 500KB，49 条消息的会话
        超过 14MB——切一次会话要把这堆东西序列化、传输、再解析，界面自然卡。
        """
        payload = dict(data)
        payload["messages"] = [
            _trim_session_message(message, for_payload=True) for message in (data.get("messages") or [])
        ]
        return payload

    def _save_session(self, data: dict[str, Any]) -> None:
        path = self._session_path(data["id"])
        # 落盘前剥掉大字段：否则每条消息都把 500KB 的观测原文写一遍
        payload = dict(data)
        payload["messages"] = [
            _trim_session_message(message) for message in (data.get("messages") or [])
        ]
        # 原子写：先写临时文件再替换，避免中断/并发把文件拼成两份 JSON
        # （历史上有 3 个会话就是这样坏掉、并从列表里消失的）。
        # 临时名必须逐次唯一：/api/state 的 HTTP 线程、运行线程与兜底线程都会
        # 保存同一个会话，只用 pid 命名会让并发写撞在同一个临时文件上，
        # 失败又被上层 except 静默吞掉 —— 表现为"最新内容没落盘"。
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with _SESSION_WRITE_LOCK:
            handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
            temp_path = Path(temp_name)
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                for attempt in range(_REPLACE_RETRIES):
                    try:
                        os.replace(temp_path, path)
                        break
                    except PermissionError:
                        # 目标文件此刻被读线程打开：短暂退让后重试
                        if attempt == _REPLACE_RETRIES - 1:
                            raise
                        time.sleep(0.02 * (attempt + 1))
                # 元数据缓存按 (mtime,size) 判定，同尺寸快速重写可能落在同一个
                # 时间戳刻度上；这里推进代次，保证刚写的内容立刻可见。
                self._session_write_generation = getattr(self, "_session_write_generation", 0) + 1
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _session_path(self, session_id: str) -> Path:
        # through the accessor: one patch seam for tests that redirect sessions
        return session_store.sessions_dir() / f"{session_id}.json"
