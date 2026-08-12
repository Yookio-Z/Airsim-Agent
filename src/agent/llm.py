"""LLM planner with multi-provider model registry for the AirSim VLA agent."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .command_slots import extract_command_slots
from .loop_types import LoopDecision
from .planner import MissionPlan, MissionPlanner, MissionStep


_VISION_MODEL_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "vision",
    "-vl",
    "vl-",
    "pixtral",
    "gemini",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "llava",
    "mimo-v2.5",
)

_CONTEXT_WINDOW_HINTS = (
    (("gpt-4.1", "gpt-5"), 1_000_000),
    (("gemini-2.5",), 1_000_000),
    (("claude-3", "claude-sonnet-4", "claude-opus-4"), 200_000),
    (("deepseek", "gpt-4o", "o1", "o3", "qwen", "mimo"), 128_000),
)


def infer_model_capabilities(model: str, provider: str = "", capability_mode: str = "auto") -> dict[str, Any]:
    """Infer stable UI capabilities without making provider-specific network calls."""
    mode = str(capability_mode or "auto").strip().lower()
    haystack = f"{provider} {model}".lower()
    if mode == "vision":
        multimodal = True
        source = "manual"
    elif mode == "text":
        multimodal = False
        source = "manual"
    else:
        multimodal = any(hint in haystack for hint in _VISION_MODEL_HINTS)
        source = "model_id"

    context_window = 64_000
    for hints, size in _CONTEXT_WINDOW_HINTS:
        if any(hint in haystack for hint in hints):
            context_window = size
            break
    return {
        "multimodal": multimodal,
        "capability_source": source,
        "context_window": context_window,
        "input_modes": ["text", "image"] if multimodal else ["text"],
    }


@dataclass
class LLMConfig:
    """Legacy single-model config. Kept for backward compatibility."""

    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    api_key: str = ""
    timeout_sec: float = 25.0
    max_tokens: int = 2200
    temperature: float = 0.1

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "enabled": self.enabled,
            "timeout_sec": self.timeout_sec,
        }


class LLMConfigStore:
    """Loads local LLM settings without exposing secrets to the frontend."""

    def __init__(self, path: Path | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.path = path or root / ".airsim_agent" / "secrets.json"

    def load(self) -> LLMConfig:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}

        return LLMConfig(
            provider=str(data.get("provider") or os.environ.get("LLM_PROVIDER") or "deepseek"),
            base_url=str(data.get("base_url") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"),
            model=str(data.get("model") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"),
            api_key=str(data.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or ""),
            timeout_sec=float(data.get("timeout_sec") or os.environ.get("DEEPSEEK_TIMEOUT_SEC") or 25.0),
            max_tokens=int(data.get("max_tokens") or os.environ.get("DEEPSEEK_MAX_TOKENS") or 2200),
            temperature=float(data.get("temperature") or os.environ.get("DEEPSEEK_TEMPERATURE") or 0.1),
        )


class ModelRegistry:
    """Persistent registry of user-configured LLM models."""

    def __init__(self, path: Path | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.path = path or root / "src" / "data" / "models.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._models: list[dict[str, Any]] = []
        self._default_id: str = ""
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._models = [m for m in data.get("models", []) if isinstance(m, dict)]
                self._default_id = str(data.get("default", ""))
            except Exception:
                self._models = []
                self._default_id = ""

        if not self._models:
            self._migrate_legacy()

        if not self._default_id or not any(m.get("id") == self._default_id for m in self._models):
            self._default_id = self._models[0].get("id", "") if self._models else ""

        self._save()

    def _migrate_legacy(self) -> None:
        try:
            legacy = LLMConfigStore().load()
            if legacy.enabled:
                model_id = re.sub(r"[^a-zA-Z0-9_-]", "", legacy.provider) or "deepseek"
                self._models.append({
                    "id": model_id,
                    "name": model_id.capitalize(),
                    "provider": legacy.provider,
                    "model": legacy.model,
                    "base_url": legacy.base_url,
                    "api_key": legacy.api_key,
                    "api_type": "openai",
                    "timeout_sec": legacy.timeout_sec,
                    "max_tokens": legacy.max_tokens,
                    "temperature": legacy.temperature,
                    "multimodal": False,
                })
                self._default_id = model_id
        except Exception:
            pass

        if not self._models:
            self._models.append({
                "id": "deepseek",
                "name": "DeepSeek",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "api_type": "openai",
                "timeout_sec": 25.0,
                "max_tokens": 2200,
                "temperature": 0.1,
                "multimodal": False,
            })
            self._default_id = "deepseek"

    def _save(self) -> None:
        data = {"default": self._default_id, "models": self._models}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_public(self) -> list[dict[str, Any]]:
        return [self._public_model(m) for m in self._models]

    def _public_model(self, model: dict[str, Any]) -> dict[str, Any]:
        capability_mode = str(model.get("capability_mode") or "auto")
        inferred = infer_model_capabilities(
            str(model.get("model") or ""),
            str(model.get("provider") or ""),
            capability_mode,
        )
        api_key = str(model.get("api_key") or "")
        generation_mode = str(model.get("generation_mode") or "auto")
        return {
            "id": model.get("id", ""),
            "name": model.get("name", ""),
            "provider": model.get("provider", ""),
            "model": model.get("model", ""),
            "api_type": model.get("api_type", "openai"),
            "base_url": model.get("base_url", ""),
            "timeout_sec": float(model.get("timeout_sec", 25.0)),
            "max_tokens": int(model.get("max_tokens", 2200)),
            "temperature": float(model.get("temperature", 0.1)),
            "generation_mode": generation_mode,
            "enabled": bool(api_key),
            "key_hint": f"••••{api_key[-4:]}" if api_key else "",
            "capability_mode": capability_mode,
            "multimodal": bool(inferred["multimodal"]),
            "capability_source": inferred["capability_source"],
            "input_modes": inferred["input_modes"],
            "context_window": int(model.get("context_window") or inferred["context_window"]),
        }

    def get(self, model_id: str) -> dict[str, Any] | None:
        for m in self._models:
            if m.get("id") == model_id:
                return dict(m)
        return None

    def get_default(self) -> dict[str, Any] | None:
        if self._default_id:
            return self.get(self._default_id)
        return self._models[0] if self._models else None

    def add(self, model: dict[str, Any]) -> dict[str, Any]:
        if not model.get("id"):
            model["id"] = f"model_{int(time.time() * 1000)}"
        if not model.get("name"):
            model["name"] = model["id"]
        if any(m.get("id") == model["id"] for m in self._models):
            raise ValueError(f"model id '{model['id']}' already exists")
        model.setdefault("capability_mode", "auto")
        model.setdefault("generation_mode", "auto")
        self._models.append(model)
        if not self._default_id:
            self._default_id = model["id"]
        self._save()
        return model

    def update(self, model_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        for m in self._models:
            if m.get("id") == model_id:
                for key, value in updates.items():
                    if value is None:
                        m.pop(key, None)
                    else:
                        m[key] = value
                self._save()
                return m
        raise ValueError("model not found")

    def delete(self, model_id: str) -> None:
        self._models = [m for m in self._models if m.get("id") != model_id]
        if self._default_id == model_id:
            self._default_id = self._models[0].get("id", "") if self._models else ""
        self._save()

    def set_default(self, model_id: str) -> None:
        if not self.get(model_id):
            raise ValueError("model not found")
        self._default_id = model_id
        self._save()


def _config_value(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, default)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    return text.strip()


class OpenAIClient:
    """OpenAI-compatible chat completions client (covers DeepSeek, OpenAI, OpenRouter, vLLM, Ollama, etc.)."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def _request(self, messages: list[dict[str, Any]], payload_extra: dict[str, Any]) -> urllib.request.Request:
        url = str(self.config.get("base_url", "https://api.openai.com")).rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.get("model", ""),
            "messages": messages,
            "stream": False,
        }
        if str(self.config.get("generation_mode") or "auto") == "custom":
            payload["temperature"] = float(self.config.get("temperature", 0.1))
            # 只有显式配置了 max_tokens 才传，让推理模型自由输出避免 JSON 被截断
            configured_max = self.config.get("max_tokens")
            if configured_max:
                payload["max_tokens"] = int(configured_max)
        payload.update(payload_extra)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.get('api_key', '')}",
            },
            method="POST",
        )

    def chat_json(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        # chat_json 不限制 max_tokens，让推理模型有足够空间输出完整 JSON
        req = self._request(messages, {"response_format": {"type": "json_object"}})
        response = self._do_request(req)
        content = self._first_content(response)
        if not content:
            raise RuntimeError("LLM returned empty content")
        try:
            parsed = json.loads(_extract_json(content))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM JSON parse failed: {e}: {content[:300]}") from e
        return parsed, response.get("usage") or {}

    def chat_text(self, messages: list[dict[str, Any]], max_tokens: int = 0) -> tuple[str, dict[str, Any]]:
        extra: dict[str, Any] = {}
        # max_tokens=0 表示不限制，让推理模型自由输出
        configured_max = self.config.get("max_tokens")
        effective_max = configured_max if configured_max else max_tokens
        if effective_max:
            extra["max_tokens"] = int(effective_max)
        req = self._request(messages, extra)
        response = self._do_request(req)
        content = self._first_content(response).strip()
        if not content:
            raise RuntimeError("LLM returned empty content")
        return content, response.get("usage") or {}

    @staticmethod
    def _stream_token(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item or ""))
            return "".join(parts)
        return str(value)

    def stream_events(self, messages: list[dict[str, Any]], max_tokens: int = 0):
        extra: dict[str, Any] = {"stream": True}
        # max_tokens=0 表示不限制，让推理模型自由输出
        configured_max = self.config.get("max_tokens")
        effective_max = configured_max if configured_max else max_tokens
        if effective_max:
            extra["max_tokens"] = int(effective_max)
        req = self._request(messages, extra)
        req.add_header("Accept", "text/event-stream")
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_sec", 25.0))) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    for key in ("reasoning_content", "reasoning", "thinking"):
                        token = self._stream_token(delta.get(key))
                        if token:
                            yield {"type": "reasoning", "token": token}
                    token = self._stream_token(delta.get("content"))
                    if token:
                        yield {"type": "content", "token": token}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM request failed: {e.reason}") from e

    def stream_text(self, messages: list[dict[str, Any]], max_tokens: int = 700):
        for event in self.stream_events(messages, max_tokens=max_tokens):
            if event.get("type") == "content":
                yield event.get("token", "")

    def _do_request(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_sec", 25.0))) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM request failed: {e.reason}") from e

    @staticmethod
    def _first_content(response: dict[str, Any]) -> str:
        choices = response.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")


class AnthropicClient:
    """Anthropic Messages API client."""

    API_VERSION = "2023-06-01"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def _build_body(self, messages: list[dict[str, Any]], max_tokens: int, stream: bool) -> tuple[str, dict[str, Any]]:
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            raw_content = m.get("content", "")
            if role == "system":
                system_parts.append(str(raw_content))
                continue
            content = self._anthropic_content(raw_content)
            if role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append({"role": "user", "content": content})
        system = "\n\n".join(system_parts)
        # Anthropic API 强制要求 max_tokens，取配置值或调用方传入值，默认 8192 给推理模型足够空间
        configured_max = int(self.config.get("max_tokens", 0) or 0)
        effective_max = configured_max if configured_max else (max_tokens if max_tokens else 8192)
        body = {
            "model": self.config.get("model", ""),
            "max_tokens": effective_max,
            "messages": anthropic_messages,
            "stream": stream,
        }
        if str(self.config.get("generation_mode") or "custom") == "custom":
            body["temperature"] = float(self.config.get("temperature", 0.1))
        return system, body

    @staticmethod
    def _anthropic_content(content: Any) -> str | list[dict[str, Any]]:
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                blocks.append({"type": "text", "text": str(item.get("text") or "")})
                continue
            if item.get("type") != "image_url":
                continue
            image_url = item.get("image_url") or {}
            url = str(image_url.get("url") if isinstance(image_url, dict) else image_url)
            if not url.startswith("data:image/") or ";base64," not in url:
                continue
            header, encoded = url.split(",", 1)
            media_type = header[5:].split(";", 1)[0]
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            })
        return blocks

    def _request(self, messages: list[dict[str, Any]], max_tokens: int, stream: bool):
        url = str(self.config.get("base_url", "https://api.anthropic.com")).rstrip("/") + "/v1/messages"
        system, body = self._build_body(messages, max_tokens, stream)
        if system:
            body["system"] = system
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.config.get("api_key", ""),
                "anthropic-version": self.API_VERSION,
            },
            method="POST",
        )
        return req

    def chat_json(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        # chat_json 不限制 max_tokens，_build_body 内部用默认值 8192 保证推理空间
        configured_max = int(self.config.get("max_tokens", 0) or 0)
        req = self._request(messages, configured_max, stream=False)
        response = self._do_request(req)
        content = self._first_text(response)
        if not content:
            raise RuntimeError("Anthropic returned empty content")
        try:
            parsed = json.loads(_extract_json(content))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Anthropic JSON parse failed: {e}: {content[:300]}") from e
        usage = response.get("usage") or {}
        return parsed, {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)}

    def chat_text(self, messages: list[dict[str, Any]], max_tokens: int = 700) -> tuple[str, dict[str, Any]]:
        req = self._request(messages, max_tokens, stream=False)
        response = self._do_request(req)
        content = self._first_text(response).strip()
        if not content:
            raise RuntimeError("Anthropic returned empty content")
        usage = response.get("usage") or {}
        return content, {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)}

    def stream_events(self, messages: list[dict[str, Any]], max_tokens: int = 700):
        req = self._request(messages, max_tokens, stream=True)
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_sec", 25.0))) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    event_type = chunk.get("type", "")
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta") or {}
                        delta_type = str(delta.get("type") or "")
                        if delta_type == "thinking_delta":
                            token = str(delta.get("thinking") or delta.get("text") or "")
                            if token:
                                yield {"type": "reasoning", "token": token}
                            continue
                        token = delta.get("text", "")
                        if token:
                            yield {"type": "content", "token": token}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Anthropic request failed: {e.reason}") from e

    def stream_text(self, messages: list[dict[str, Any]], max_tokens: int = 700):
        for event in self.stream_events(messages, max_tokens=max_tokens):
            if event.get("type") == "content":
                yield event.get("token", "")

    def _do_request(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_sec", 25.0))) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Anthropic request failed: {e.reason}") from e

    @staticmethod
    def _first_text(response: dict[str, Any]) -> str:
        content = response.get("content") or []
        if isinstance(content, list) and content:
            return str(content[0].get("text", ""))
        return ""


def _create_client(config: dict[str, Any]):
    api_type = config.get("api_type", "openai")
    if api_type == "anthropic":
        return AnthropicClient(config)
    return OpenAIClient(config)


class LLMUnavailableError(RuntimeError):
    """Raised when an interactive chat request cannot reach the selected model."""


class LLMMissionPlanner:
    """LLM-first planner with rule-based fallback and per-request model selection."""

    def __init__(self, config: LLMConfig | None = None, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.legacy_config = config
        self.fallback = MissionPlanner()
        self.last_error = ""
        self.last_usage: dict[str, Any] = {}

    def reload_config(self) -> None:
        self.registry = ModelRegistry()

    def _resolve_config(self, model_id: str | None = None) -> dict[str, Any] | None:
        if model_id:
            model = self.registry.get(model_id)
            if model:
                return model
        if self.legacy_config and self.legacy_config.enabled:
            return {
                "id": self.legacy_config.provider,
                "name": self.legacy_config.provider,
                "provider": self.legacy_config.provider,
                "model": self.legacy_config.model,
                "base_url": self.legacy_config.base_url,
                "api_key": self.legacy_config.api_key,
                "api_type": "openai",
                "timeout_sec": self.legacy_config.timeout_sec,
                "max_tokens": self.legacy_config.max_tokens,
                "temperature": self.legacy_config.temperature,
            }
        return self.registry.get_default()

    def _enabled(self, config: dict[str, Any] | None) -> bool:
        return bool(config and config.get("api_key"))

    def _chat_json_with_retries(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        *,
        attempts: int = 3,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                return client.chat_json(messages)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable_llm_error(exc):
                    raise
                time.sleep(0.35 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _retryable_llm_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            term in message
            for term in (
                "empty content",
                "unexpected_eof",
                "eof occurred",
                "connection reset",
                "temporarily unavailable",
                "timeout",
                # JSON 解析失败通常是输出被截断或格式偏差，重试有望成功
                "json parse failed",
                "jsondecodeerror",
                "unterminated string",
                "expecting value",
                "expecting property name",
            )
        )

    def supports_multimodal(self, model_id: str | None = None) -> bool:
        config = self._resolve_config(model_id)
        return self._enabled(config) and self._config_supports_multimodal(config)

    @staticmethod
    def _config_supports_multimodal(config: dict[str, Any] | None) -> bool:
        if not config:
            return False
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
        )
        return bool(inferred["multimodal"])

    def status(self) -> dict[str, Any]:
        default = self.registry.get_default()
        data: dict[str, Any] = {"models": self.registry.list_public()}
        if default:
            data.update({
                "provider": default.get("provider", ""),
                "base_url": default.get("base_url", ""),
                "model": default.get("model", ""),
                "enabled": self._enabled(default),
                "api_type": default.get("api_type", "openai"),
            })
        else:
            data.update({"provider": "", "base_url": "", "model": "", "enabled": False, "api_type": "openai"})
        data["last_error"] = self.last_error
        data["last_usage"] = self.last_usage
        return data

    def plan(
        self,
        command: str,
        tools: list[dict[str, Any]],
        safety: dict[str, Any],
        telemetry: dict[str, Any] | None,
        memory: dict[str, Any],
        model_id: str | None = None,
        backend: str = "",
        capabilities: dict[str, Any] | None = None,
        tool_cards: list[dict[str, Any]] | None = None,
        agent_state: dict[str, Any] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> MissionPlan:
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)

        available_tools = self._compact_tools(tools)
        capability_payload = capabilities or {}
        card_payload = self._compact_tool_cards(tool_cards or [])
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": self._content_with_images(json.dumps(
                    {
                        "operator_command": command,
                        "backend": backend,
                        "backend_capabilities": capability_payload,
                        "available_tools": available_tools,
                        "available_tool_cards": card_payload,
                        "safety_constraints": safety,
                        "current_telemetry": telemetry or {},
                        "agent_state": agent_state or {},
                        "skill_guidance": self._compact_skill_guidance(
                            (agent_state or {}).get("skill_guidance") or []
                        ),
                        "memory_snapshot": self._compact_memory(memory),
                        "conversation_context": self._compact_conversation(conversation_context or []),
                        "output_schema": self._schema_hint(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ), attachments or []),
            },
        ]

        try:
            client = _create_client(config)
            payload, usage = self._chat_json_with_retries(client, messages)
            self.last_error = ""
            self.last_usage = usage
            plan = self._plan_from_payload(command, payload, {t["name"] for t in available_tools})
            if config:
                plan.planner_source = str(config.get("provider", "llm"))
                plan.planner_model = str(config.get("model", ""))
            return plan
        except Exception as e:
            self.last_error = str(e)
            raise LLMUnavailableError(f"LLM mission planning failed: {e}") from e

    def decide_next_step(
        self,
        command: str,
        loop_state: dict[str, Any],
        observation: dict[str, Any],
        tool_cards: list[dict[str, Any]],
        capabilities: dict[str, Any] | None,
        memory: dict[str, Any],
        model_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        require_llm: bool = False,
        skill_guidance: list[dict[str, Any]] | None = None,
    ) -> LoopDecision:
        """Choose the next action for a lightweight observe-decide-act loop."""

        allowed_tools = self._loop_action_names(tool_cards)
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            if require_llm:
                message = self._chat_unavailable_message(config, "missing_api_key")
                self.last_error = message
                raise LLMUnavailableError(message)
            return self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})

        messages = [
            {"role": "system", "content": self._loop_decision_system_prompt()},
            {
                "role": "user",
                "content": self._content_with_images(json.dumps(
                    {
                        "operator_command": command,
                        "loop_state": self._compact_loop_state(loop_state),
                        "observation": self._compact_observation(observation),
                        "backend_capabilities": capabilities or {},
                        "available_tool_cards": self._compact_tool_cards(tool_cards),
                        "skill_guidance": self._compact_skill_guidance(skill_guidance or []),
                        "memory_snapshot": self._compact_memory(memory),
                        "output_schema": self._loop_decision_schema_hint(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ), attachments or []),
            },
        ]
        try:
            client = _create_client(config)
            payload, usage = self._chat_json_with_retries(client, messages)
            self.last_error = ""
            self.last_usage = usage
            return self._decision_from_payload(payload, allowed_tools)
        except Exception as e:
            self.last_error = str(e)
            if require_llm:
                raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e}") from e
            decision = self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})
            if decision.reflection:
                decision.reflection = f"{decision.reflection} LLM fallback: {e}"
            else:
                decision.reflection = f"LLM fallback: {e}"
            return decision

    def confirm_target_in_image(
        self,
        target_description: str,
        image_base64: str,
        context: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Use the selected multimodal model to verify a target in one image."""
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
            message = f"模型不可用：{model_name} 未启用多模态能力，无法进行图像目标确认。"
            self.last_error = message
            raise LLMUnavailableError(message)

        target = target_description.strip() or "目标"
        payload = {
            "target_description": target,
            "context": context or {},
            "output_schema": {
                "target_found": "boolean, whether the requested target is visible",
                "confidence": "number 0..1",
                "target_label": "short label for the observed target or empty",
                "evidence": ["short visible evidence from the image"],
                "relative_direction": "center/left/right/top/bottom/top-left/top-right/bottom-left/bottom-right/unknown",
                "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null"},
                "recommended_next_action": "continue_search|approach|hold|reposition|insufficient_image",
                "summary_zh": "one concise Chinese sentence for the operator",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是无人机视觉任务的多模态目标确认器。"
                    "只根据图片和给定上下文判断目标是否可见，不要编造传感器数据或飞控状态。"
                    "如果目标只是可能存在但不清楚，target_found=false 或 confidence<0.6。"
                    "返回 JSON only。"
                ),
            },
            {
                "role": "user",
                "content": self._content_with_images(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    [{"data_url": self._data_url_from_base64(image_base64)}],
                ),
            },
        ]
        try:
            client = _create_client(config)
            try:
                parsed, usage = self._chat_json_with_retries(client, messages)
            except Exception:
                text, usage = client.chat_text(messages, max_tokens=700)
                parsed = json.loads(_extract_json(text))
            self.last_error = ""
            self.last_usage = usage
            return self._normalize_vlm_confirmation(parsed, target)
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def analyze_image(
        self,
        question: str,
        image_base64: str,
        context: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Use the selected multimodal model for open-ended image understanding."""
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "current model")
            message = f"模型不可用：{model_name} 未启用多模态能力，无法分析图像。"
            self.last_error = message
            raise LLMUnavailableError(message)

        prompt = question.strip() or "请描述无人机当前摄像头画面中可见的信息。"
        payload = {
            "operator_question": prompt,
            "context": context or {},
            "output_schema": {
                "summary_zh": "one concise Chinese answer to the operator",
                "visible_objects": ["short object or region labels visible in the image"],
                "target_candidates": [
                    {
                        "label": "candidate object label",
                        "confidence": "number 0..1",
                        "relative_direction": "center/left/right/top/bottom/top-left/top-right/bottom-left/bottom-right/unknown",
                        "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null"},
                        "evidence": "short visible evidence",
                    }
                ],
                "navigation_hint": "hold|reposition|approach_possible|insufficient_depth|unsafe",
                "safety_notes": ["short safety-relevant notes; empty if none"],
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a UAV camera image analyst. Answer only from the provided image and context. "
                    "Do not invent telemetry, GPS, depth, or vehicle state. "
                    "For navigation, only provide a hint; do not claim a 3D target position unless context contains one. "
                    "Return JSON only, with concise Chinese operator-facing text in summary_zh."
                ),
            },
            {
                "role": "user",
                "content": self._content_with_images(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    [{"data_url": self._data_url_from_base64(image_base64)}],
                ),
            },
        ]
        try:
            client = _create_client(config)
            try:
                parsed, usage = self._chat_json_with_retries(client, messages)
            except Exception:
                text, usage = client.chat_text(messages, max_tokens=900)
                parsed = json.loads(_extract_json(text))
            self.last_error = ""
            self.last_usage = usage
            return self._normalize_vlm_analysis(parsed, prompt)
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def final_answer(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = self._answer_payload(command, run_status, plan, telemetry, failure_reason, verification)
        if not self._enabled(config):
            return self._fallback_answer(payload)

        messages = self._answer_messages(payload)
        try:
            client = _create_client(config)
            text, usage = client.chat_text(messages)
            self.last_error = ""
            self.last_usage = usage
            return text
        except Exception as e:
            self.last_error = str(e)
            return self._fallback_answer(payload)

    def final_answer_stream(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
        model_id: str | None = None,
        on_token=None,
        on_reasoning=None,
        force_fallback: bool = False,
        should_stop=None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = self._answer_payload(command, run_status, plan, telemetry, failure_reason, verification)
        if force_fallback or not self._enabled(config):
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer

        messages = self._answer_messages(payload)
        chunks: list[str] = []
        try:
            client = _create_client(config)
            stream_events = getattr(client, "stream_events", None)
            if callable(stream_events):
                for event in stream_events(messages):
                    if callable(should_stop) and should_stop():
                        break
                    token = str(event.get("token") or "")
                    if not token:
                        continue
                    if event.get("type") == "reasoning":
                        if on_reasoning:
                            on_reasoning(token)
                        continue
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            else:
                for token in client.stream_text(messages):
                    if callable(should_stop) and should_stop():
                        break
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            self.last_error = ""
            answer = "".join(chunks).strip()
            if answer:
                return answer
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer
        except Exception as e:
            self.last_error = str(e)
            answer = self._fallback_answer(payload)
            if on_token:
                on_token(answer)
            return answer

    def chat_response_stream(
        self,
        command: str,
        conversation: list[dict[str, Any]] | None,
        agent_state: dict[str, Any],
        memory: dict[str, Any],
        model_id: str | None = None,
        on_token=None,
        on_reasoning=None,
        attachments: list[dict[str, Any]] | None = None,
        should_stop=None,
    ) -> str:
        config = self._resolve_config(model_id)
        payload = {
            "operator_message": command,
            "agent_state": agent_state or {},
            "memory_snapshot": self._compact_memory(memory),
        }
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是这个控制台里的通用 Chat 助手。"
                    "优先回答用户当前问题；只有当问题明确涉及无人机、后端、任务、航点或状态时，才引用 agent_state。"
                    "不要把普通知识问答、图片理解、配置排错强行转成 AirSim/PX4 状态汇报。"
                    "Chat 模式不会执行飞控工具；如果用户要求起飞、移动、降落、搜索、跟踪等真实动作，提醒他切换到 Execute 模式。"
                    "如果用户上传图片并要求说明图片内容，直接根据图片作答；不要把用户附件当成无人机传感器画面，除非用户明确说明。"
                    "必须基于提供的信息作答，不要编造后端连接、车辆状态、位置或传感器结果。"
                    "可以参考 memory_snapshot.guidance 中的偏好和风险提示，但不要把历史任务当成当前事实。"
                    "中文回答，简洁自然。"
                ),
            }
        ]
        for item in self._compact_conversation(conversation or []):
            role = "assistant" if item.get("role") == "assistant" else "user"
            messages.append({
                "role": role,
                "content": self._content_with_images(
                    str(item.get("content") or ""),
                    item.get("attachments") or [],
                ),
            })
        messages.append({
            "role": "user",
            "content": self._content_with_images(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                attachments or [],
            ),
        })

        chunks: list[str] = []
        try:
            client = _create_client(config)
            stream_events = getattr(client, "stream_events", None)
            if callable(stream_events):
                for event in stream_events(messages):
                    if callable(should_stop) and should_stop():
                        break
                    token = str(event.get("token") or "")
                    if not token:
                        continue
                    if event.get("type") == "reasoning":
                        if on_reasoning:
                            on_reasoning(token)
                        continue
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            else:
                for token in client.stream_text(messages):
                    if callable(should_stop) and should_stop():
                        break
                    chunks.append(token)
                    if on_token:
                        on_token(token)
            self.last_error = ""
            return "".join(chunks).strip()
        except Exception as e:
            message = self._chat_unavailable_message(config, str(e))
            self.last_error = message
            raise LLMUnavailableError(message) from e

    def summarize_attempt(
        self,
        command: str,
        loop_state: dict[str, Any],
        model_id: str | None = None,
    ) -> str:
        """Model-generated final report when an agent loop exhausts its step
        budget (smolagents ``provide_final_answer`` pattern).

        Reviews the tool-call history, failure reason and current telemetry,
        then produces a concise operator-facing Chinese summary. Returns ""
        when the LLM is unavailable so callers keep the local template
        summary instead.
        """
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            return ""
        results = loop_state.get("results") or []
        tool_lines = []
        for item in results[-12:]:
            tool = str(item.get("tool") or "?")
            status = "成功" if item.get("ok") else "失败"
            tool_lines.append(f"- {tool}: {status}")
        world = loop_state.get("observations") or []
        latest_world = {}
        for observation in reversed(world):
            payload = observation.get("world_state") or {}
            if isinstance(payload, dict) and payload:
                latest_world = payload
                break
        drone = latest_world.get("drone") or {}
        position = drone.get("position_ned") or {}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是无人机地面站的执行总结助手。任务未在步数预算内完成，"
                    "请基于已完成/失败的工具调用和当前遥测，用简洁中文总结："
                    "已完成的部分、卡住的原因、当前飞行状态、建议的下一步。"
                    "不要虚构工具结果或遥测数据。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "operator_command": command,
                        "tool_call_history": tool_lines,
                        "failure_reason": loop_state.get("failure_reason", ""),
                        "current_telemetry": {
                            "position_ned": position,
                            "flying": drone.get("flying"),
                            "armed": drone.get("armed"),
                        },
                        "output_format": "2-4 句中文总结",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        try:
            client = _create_client(config)
            text, _usage = client.chat_text(messages, max_tokens=600)
            self.last_error = ""
            summary = (text or "").strip()
            return summary[:800] if summary else ""
        except Exception as exc:
            self.last_error = str(exc)
            return ""

    def _answer_payload(
        self,
        command: str,
        run_status: str,
        plan: MissionPlan | None,
        telemetry: dict[str, Any] | None,
        failure_reason: str = "",
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "operator_command": command,
            "run_status": run_status,
            "failure_reason": failure_reason,
            "plan_summary": plan.summary if plan else "",
            "intent": plan.intent if plan else "",
            "current_telemetry": telemetry or {},
            "tool_results": self._compact_step_results(plan),
            "verification": verification or {},
        }

    def _answer_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是 AirSim 无人机 VLA Agent 的对话层。"
                    "你只向操作员输出最终可读结果，不展示隐藏思维链。"
                    "必须基于给定 telemetry 和 tool_results 作答，不要编造传感器数据。"
                    "字段语义：armed=true 表示电机已解锁，armed=false 表示电机未解锁；"
                    "flying=true 表示无人机处于飞行中，has_collided=true 表示发生碰撞。"
                    "如果用户询问状态，直接汇报连接、电机/飞行状态、高度、NED位置、速度、航向、碰撞状态。"
                    "如果任务已执行，必须参考 verification，对比起始位置、最终位置和任务目标，判断是否真的完成。"
                    "如果 verification.status=failed，不要说任务完成，要直接说明偏差和当前状态；"
                    "如果 verification.status=passed_with_warnings，不要输出“未完成/目标不一致”，只说明有轻微误差或已回读。"
                    "如果 failure_reason 只是 agent loop reached max_steps，不要原样展示内部错误；"
                    "请基于 tool_results、telemetry 和 verification 生成任务报告，说明已完成项、未完成项和当前安全状态。"
                    "对于飞行+拍照/VLM任务，最终回答必须包含：执行链路、最终飞行状态/位置、图像或目标发现、未完成项或限制。"
                    "中文回复，简洁自然，3到8句话。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            },
        ]

    def _system_prompt(self) -> str:
        return (
            "You are the high-level planner of an AirSim UAV VLA agent. "
            "Convert the operator command into a safe, auditable JSON mission plan. "
            "Only choose tools from available_tools. Use NED coordinates: z must be negative in the air. "
            "Never bypass the safety layer. Prefer conservative altitude, velocity, and geofence behavior. "
            "Do not add objective-changing actions that the operator did not ask for. "
            "Do not add photo, search, tracking, landing, disarm, reset, or disconnect steps unless explicitly requested. "
            "Memory is advisory only; never copy old mission endings into the current plan. "
            "Use memory_snapshot.guidance to prefer historically reliable tools/skills and to add caution notes for repeated failures; safety rules still dominate. "
            "drone_fly_to uses absolute local NED coordinates only. "
            "For relative body-frame commands such as forward/back/left/right/up/down, use drone_move_relative instead of guessing absolute coordinates. "
            "Use current_telemetry position_ned and heading_deg when reasoning about where the drone is. "
            "Use agent_state for backend readiness, connection health, active vehicle state, and active run context. "
            "Use conversation_context only to resolve references from the current command; never execute old requests again. "
            "If the operator says return to the previous start point, prefer memory_snapshot.session.last_task_start_position_ned when available; do not assume home unless memory is missing. "
            "If the task depends on current position or flight state, include drone_get_status before the action. "
            "For any flight mission, include connection and state readback. "
            "For takeoff/search/patrol, include drone_arm before drone_takeoff. "
            "Use skill_guidance as Markdown operating knowledge, not as callable tools. "
            "For short ordered UAV workflows, follow the flight_sequence guidance when present while still choosing native tools from available_tool_cards. "
            "For visual tasks, follow any relevant Markdown guidance, then use airsim_take_photo plus airsim_vlm_analyze_image for open-ended image descriptions or airsim_vlm_confirm_target for named targets. "
            "For geometric path requests such as square, rectangle, orbit, circle, grid, 正方形, 矩形, 绕圈, or 航线, prefer drone_fly_path when available. Compute conservative local NED waypoints from the latest observed position and avoid rotate-to-heading plus repeated drone_move_relative unless the operator specifically asks to point the camera. "
            "Respect backend_capabilities and available_tool_cards. Never plan tools whose required capabilities are unavailable. "
            "Markdown skills are references, not executable shortcuts; do not prefer kind=skill unless a real executable skill card is explicitly available. "
            "For vague movement or scan requests, choose conservative distance, altitude, and velocity instead of expanding the mission. "
            "Treat kind=atomic tools as one direct operation. Tools with execution_mode=async only start an operation; the runtime owns task_id polling, timeout, cancellation, and terminal outcome. "
            "If a requested capability is unavailable, produce a safe status/readback plan and explain the limitation in risk_notes. "
            "Return JSON only."
        )

    def _schema_hint(self) -> dict[str, Any]:
        return {
            "intent": "short snake_case intent",
            "summary": "one concise Chinese summary",
            "reasoning": "brief non-sensitive planning rationale in Chinese",
            "assumptions": ["operator-visible assumptions"],
            "risk_notes": ["safety or ambiguity notes"],
            "execution_mode": (
                "auto | agent_loop. Use agent_loop when the task requires an "
                "observe-respond cycle that a fixed sequence cannot express: "
                "visual search/confirm targets, tracking, or any step whose "
                "outcome decides the next action. Use auto for fixed sequences "
                "such as takeoff -> waypoints -> land."
            ),
            "steps": [
                {
                    "label": "Chinese step label",
                    "tool": "one available tool name",
                    "layer": "tool|planning|perception|action|memory|safety",
                    "params": {"name": "value"},
                    "needs_observation": (
                        "true when this step's result must be observed before "
                        "the next step can be chosen (photo/VLM/detect steps)"
                    ),
                }
            ],
        }

    def _loop_decision_system_prompt(self) -> str:
        return (
            "You are the decision layer of a UAV Agent Loop using ReAct / plan-execute-observe. "
            "At each step, choose exactly one next tool action, or mark the task complete after the latest observation proves the goal is handled. "
            "Use only available_tool_cards. Do not call unavailable tools. "
            "Parse the operator's full instruction yourself, including multi-step Chinese or English commands. Do not rely on keyword routing. "
            "Never bypass safety, never invent telemetry, and do not perform low-level continuous control. "
            "skill_guidance contains Markdown skills that teach how to work; they are not actions. Never output action=skill:* unless that exact action appears in available_tool_cards. "
            "If the operator's command is a knowledge question, explanation request, or non-UAV task that does not require vehicle tools or backend state, mark is_complete=true in the first turn and put a concise Chinese natural-language answer in reason. Do not force a drone tool call for questions unrelated to UAV operation. "
            "If flight_sequence guidance is present and the request is a short ordered workflow such as status -> takeoff -> move -> photo/VLM -> return -> land, use that Markdown to choose the next native tool and keep the sequence concise. "
            "For vague scan/move wording such as '一点距离' or '简单扫描', keep horizontal movement to 1-2 meters, use velocity around 1.0-1.5 m/s, and take off to at least 3 m before horizontal movement. "
            "For open-ended 'what is in the photo' requests, use airsim_vlm_analyze_image; use airsim_vlm_confirm_target only for a named target. "
            "For return-to-start, use the first observed position when available instead of guessing home coordinates. "
            "For square/rectangle/orbit/circle/grid path tasks, prefer one drone_fly_path action with explicit local NED waypoints over many rotate-to-heading plus drone_move_relative turns. Use rotate_to only when camera orientation matters. "
            "For multi-step goals, call one available tool per turn, then use the next observation to decide the following tool. "
            "Prefer status/readback after uncertain results. After a photo or visual sweep produces an image, call airsim_vlm_confirm_target with source=last_image before declaring a target found. "
            "Use memory_snapshot.guidance to prefer historically reliable skills/tools and to avoid repeating known failure patterns. "
            "An async tool is not complete when it returns started/running; wait for the runtime-provided terminal observation before declaring completion. "
            "If the backend lacks a requested capability, stop safely and explain the limitation in reflection. "
            "The reason/reflection fields are displayed live to the operator as public process text; write concise, useful, non-sensitive decision text there. "
            "When is_complete=true, put a concise Chinese task report in reason. Include executed sequence, final state/position, image finding, and any limitation/failure. "
            "Return JSON only."
        )

    def _loop_decision_schema_hint(self) -> dict[str, Any]:
        return {
            "action": "one available tool name, or empty string when complete",
            "params": {"name": "value"},
            "reason": "brief public Chinese process text explaining what you decided to do next",
            "is_complete": False,
            "needs_replan": False,
            "reflection": "optional brief public reflection on the latest observation/result",
        }

    def _decision_from_payload(self, payload: dict[str, Any], allowed_tools: set[str]) -> LoopDecision:
        action = str(payload.get("action") or "").strip()
        is_complete = bool(payload.get("is_complete", False))
        if is_complete:
            action = ""
        elif action not in allowed_tools:
            return LoopDecision(
                action="",
                reason=f"Model selected unavailable tool: {action}",
                is_complete=False,
                reflection="Stopped before executing an unavailable tool.",
            )
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        return LoopDecision(
            action=action,
            params=params,
            reason=str(payload.get("reason") or action or "complete"),
            is_complete=is_complete,
            needs_replan=bool(payload.get("needs_replan", False)),
            reflection=str(payload.get("reflection") or ""),
        )

    def _compact_loop_state(self, loop_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": loop_state.get("run_id", ""),
            "status": loop_state.get("status", ""),
            "step_count": len(loop_state.get("decisions") or []),
            "recent_decisions": (loop_state.get("decisions") or [])[-5:],
            "recent_results": (loop_state.get("results") or [])[-5:],
            "failure_reason": loop_state.get("failure_reason", ""),
        }

    def _compact_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        world = observation.get("world_state") or {}
        return {
            "step_index": observation.get("step_index", 0),
            "elapsed_since_start": observation.get("elapsed_since_start", 0),
            "backend": world.get("backend", ""),
            "connected": world.get("connected", False),
            "stale_connection": world.get("stale_connection", False),
            "drone": self._trim_data(world.get("drone") or {}),
            "last_action_result": self._trim_data(observation.get("last_action_result") or {}),
        }

    def _fallback_loop_decision(
        self,
        command: str,
        loop_state: dict[str, Any],
        observation: dict[str, Any],
        allowed_tools: set[str],
        capabilities: dict[str, Any],
    ) -> LoopDecision:
        lower = command.lower()
        slots = extract_command_slots(command)
        world = observation.get("world_state") or {}
        drone = world.get("drone") or {}
        connected = bool(world.get("connected", False)) and not bool(world.get("stale_connection", False))
        last = observation.get("last_action_result") or {}
        if last and last.get("ok") is False:
            return LoopDecision(
                action="drone_get_status" if "drone_get_status" in allowed_tools and not self._loop_has_action(loop_state, "drone_get_status", after_failure=True) else "",
                reason="Previous action failed; refresh status before stopping.",
                is_complete="drone_get_status" not in allowed_tools or self._loop_has_action(loop_state, "drone_get_status", after_failure=True),
                reflection=str((last.get("data") or {}).get("message") or "previous action failed"),
            )

        wants_search = any(k in lower for k in ["search", "find", "detect", "locate", "搜索", "寻找", "识别", "检测"])
        wants_track = any(k in lower for k in ["track", "follow", "追踪", "跟踪", "跟随"])
        wants_photo = any(k in lower for k in ["photo", "capture", "image", "screenshot", "拍照", "截图", "图像"])
        wants_visual = wants_search or wants_track or wants_photo
        wants_return = any(k in lower for k in ["return", "rtl", "go home", "return home", "返航", "返回", "回家"])

        wants_search = wants_search or any(k in lower for k in ["搜索", "寻找", "查找", "识别", "检测", "找"])
        wants_track = wants_track or any(k in lower for k in ["跟踪", "追踪", "跟随"])
        wants_photo = wants_photo or any(k in lower for k in ["拍照", "截图", "图像"])
        wants_return = wants_return or slots.land is True
        wants_visual = wants_search or wants_track or wants_photo
        wants_navigation = (
            slots.ned_target is not None
            or slots.relative_move is not None
            or any(k in lower for k in ["takeoff", "fly", "move", "go to", "起飞", "飞", "移动", "前进", "后退", "左移", "右移"])
        )

        completed_navigation_skill = self._loop_has_action(loop_state, "skill:navigation") or self._loop_has_action(loop_state, "skill:return_home")
        completed_search_skill = self._loop_has_action(loop_state, "skill:search")
        if completed_search_skill and wants_track:
            result_text = json.dumps(loop_state.get("results") or [], ensure_ascii=False, default=str).lower()
            if "not_found" in result_text:
                return LoopDecision(
                    action="",
                    reason="Search completed without a target, so tracking cannot start.",
                    is_complete=False,
                    reflection="The target must be found and identified before a tracking operation is allowed.",
                )
        completed_vlm_confirm = self._loop_has_action(loop_state, "airsim_vlm_confirm_target")
        if completed_navigation_skill or (completed_search_skill and completed_vlm_confirm and not wants_track):
            return LoopDecision(
                action="",
                reason="The selected high-level visual workflow has already run and produced a confirmation.",
                is_complete=True,
                reflection="Skill result is available in the previous observation.",
            )
        if completed_search_skill and not completed_vlm_confirm and not self._loop_has_recent_image(loop_state):
            return LoopDecision(
                action="",
                reason="Search did not produce an image for multimodal target confirmation.",
                is_complete=False,
                reflection="No recent image is available for airsim_vlm_confirm_target.",
            )

        if not connected and "drone_connect" in allowed_tools and not self._loop_has_action(loop_state, "drone_connect"):
            return LoopDecision("drone_connect", {}, "Connect before advanced task execution.")
        if "drone_get_status" in allowed_tools and not self._loop_has_action(loop_state, "drone_get_status"):
            return LoopDecision("drone_get_status", {}, "Observe current vehicle state before acting.")

        if wants_visual and not (capabilities.get("target_search") or capabilities.get("image_capture") or capabilities.get("target_tracking")):
            return LoopDecision(
                action="",
                reason="Requested visual capability is unavailable on this backend.",
                is_complete=True,
                reflection="No visual/search/tracking tool is exposed for the active backend.",
            )

        target = slots.target_class or self._loop_target_class(command)
        altitude = slots.altitude or self._loop_altitude(command)
        radius = slots.radius or 25.0
        velocity = slots.velocity or 2.0
        if (
            wants_visual
            and "airsim_vlm_confirm_target" in allowed_tools
            and not completed_vlm_confirm
            and self._loop_has_recent_image(loop_state)
        ):
            return LoopDecision(
                "airsim_vlm_confirm_target",
                {"target_description": target, "source": "last_image"},
                "Use the multimodal model to confirm whether the latest captured frame contains the requested target.",
            )
        if wants_search and "skill:search" in allowed_tools and not completed_search_skill:
            return LoopDecision(
                "skill:search",
                {
                    "target_class": target,
                    "search_altitude": altitude,
                    "search_radius": radius,
                    "scene_description": command,
                },
                "Use the high-level search skill to reduce loop turns.",
            )
        if wants_return and "skill:return_home" in allowed_tools:
            return LoopDecision(
                "skill:return_home",
                {"altitude": altitude, "velocity": velocity, "land": True if slots.land is None else slots.land},
                "Use the return-home skill for a deterministic recovery sequence.",
            )
        if not wants_visual and wants_navigation and "skill:navigation" in allowed_tools:
            params: dict[str, Any] = {
                "altitude": altitude,
                "velocity": velocity,
                "hover_after": True if slots.hover_after is None else slots.hover_after,
            }
            if slots.ned_target:
                params.update(slots.ned_target)
            if slots.relative_move:
                params.update(slots.relative_move)
            return LoopDecision("skill:navigation", params, "Use the navigation skill for the requested movement.")

        needs_flight = wants_visual or any(k in lower for k in ["takeoff", "fly", "patrol", "起飞", "飞", "巡检"])
        if needs_flight and not drone.get("armed") and "drone_arm" in allowed_tools and not self._loop_has_action(loop_state, "drone_arm"):
            return LoopDecision("drone_arm", {}, "Arm before airborne task execution.")
        if needs_flight and not drone.get("flying") and "drone_takeoff" in allowed_tools and not self._loop_has_action(loop_state, "drone_takeoff"):
            return LoopDecision("drone_takeoff", {"altitude": altitude}, "Take off to task altitude.")

        if wants_search and "skill:search" in allowed_tools and not self._loop_has_action(loop_state, "skill:search"):
            return LoopDecision(
                "skill:search",
                {"target_class": target, "search_altitude": altitude, "search_radius": radius},
                "Run a bounded visual search skill using atomic perception tools.",
            )
        if wants_track and "skill:track_object" in allowed_tools and not self._loop_has_action(loop_state, "skill:track_object"):
            return LoopDecision("skill:track_object", {"target_class": target}, "Track the requested target class through the skill layer.")
        if wants_photo and "airsim_take_photo" in allowed_tools and not self._loop_has_action(loop_state, "airsim_take_photo"):
            params: dict[str, Any] = {}
            if target:
                params["verify_target_class"] = target
            return LoopDecision("airsim_take_photo", params, "Capture a visual frame for verification.")

        return LoopDecision(
            action="",
            reason="The loop has completed all deterministic fallback actions.",
            is_complete=True,
            reflection="No further safe action is required.",
        )

    def _loop_action_names(self, tool_cards: list[dict[str, Any]]) -> set[str]:
        return {str(card.get("name")) for card in tool_cards if isinstance(card, dict) and card.get("name")}

    def _loop_has_action(self, loop_state: dict[str, Any], action: str, after_failure: bool = False) -> bool:
        decisions = loop_state.get("decisions") or []
        if not after_failure:
            return any((item or {}).get("action") == action for item in decisions if isinstance(item, dict))
        results = loop_state.get("results") or []
        last_failed_index = 0
        for item in results:
            if isinstance(item, dict) and item.get("ok") is False:
                last_failed_index = int(item.get("step_index") or 0)
        for index, item in enumerate(decisions, 1):
            if isinstance(item, dict) and item.get("action") == action and index > last_failed_index:
                return True
        return False

    def _loop_has_recent_image(self, loop_state: dict[str, Any]) -> bool:
        text = json.dumps(loop_state.get("results") or [], ensure_ascii=False, default=str).lower()
        return any(marker in text for marker in ["image_base64", "image_saved_to", "saved_to", "has_image", "last_image"])

    def _loop_altitude(self, command: str) -> float:
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:m|meter|meters|米|公尺)", command.lower())
        if not match:
            return 3.0
        try:
            return max(1.0, min(30.0, abs(float(match.group(1)))))
        except ValueError:
            return 3.0

    def _loop_target_class(self, command: str) -> str:
        lower = command.lower()
        if any(k in lower for k in ["person", "human", "pedestrian", "人", "行人", "人员"]):
            return "person"
        if any(k in lower for k in ["truck", "lorry", "卡车", "货车"]):
            return "truck"
        if any(k in lower for k in ["bus", "公交", "巴士"]):
            return "bus"
        if any(k in lower for k in ["car", "vehicle", "汽车", "车辆", "车"]):
            return "car"
        return "target"

    def _compact_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep = []
        for tool in tools:
            name = tool.get("name", "")
            if not name:
                continue
            params = tool.get("parameters", {})
            keep.append(
                {
                    "name": name,
                    "category": tool.get("category", ""),
                    "description": tool.get("description", ""),
                    "parameters": {
                        key: {"default": val.get("default"), "annotation": val.get("annotation", "")}
                        for key, val in list(params.items())[:12]
                        if isinstance(val, dict)
                    },
                }
            )
        return keep[:40]

    def _compact_tool_cards(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("name"):
                continue
            keep.append(
                {
                    "name": card.get("name", ""),
                    "purpose": card.get("purpose", ""),
                    "when_to_use": card.get("when_to_use", ""),
                    "inputs": card.get("inputs", {}),
                    "outputs": card.get("outputs", ""),
                    "preconditions": card.get("preconditions", []),
                    "required_capabilities": card.get("required_capabilities", []),
                    "subtools": card.get("subtools", []),
                    "failure_policy": card.get("failure_policy", ""),
                    "verification": card.get("verification", ""),
                    "not_for": card.get("not_for", ""),
                    "cost": card.get("cost", ""),
                    "risk": card.get("risk", ""),
                    "kind": card.get("kind", "atomic"),
                    "execution_mode": card.get("execution_mode", "immediate"),
                }
            )
        return keep[:40]

    def _compact_skill_guidance(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("name"):
                continue
            markdown = str(card.get("markdown") or "")
            if len(markdown) > 5000:
                markdown = markdown[:5000] + "\n..."
            keep.append(
                {
                    "name": card.get("name", ""),
                    "display_name": card.get("display_name", ""),
                    "description": card.get("description", ""),
                    "when_to_use": card.get("when_to_use", ""),
                    "required_capabilities": list(card.get("required_capabilities") or []),
                    "subtools": list(card.get("subtools") or []),
                    "markdown": markdown,
                    "executable": False,
                }
            )
        return keep[:4]

    def _compact_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": memory.get("session", {}),
            "recent_missions": memory.get("missions", [])[:5],
            "lessons": memory.get("lessons", [])[:5],
            "risk_events": memory.get("risk_events", [])[:5],
            "skill_candidates": memory.get("skill_candidates", [])[:5],
            "guidance": memory.get("guidance", {}),
        }

    def _chat_unavailable_message(self, config: dict[str, Any] | None, reason: str) -> str:
        model_name = "未选择模型"
        if config:
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
        reason = self._sanitize_model_error(reason)
        if reason == "missing_api_key":
            return f"模型不可用：{model_name} 未配置或未启用 API Key。请检查模型设置或切换到可用模型。"
        return f"模型不可用：{model_name} 调用失败。{reason}"

    def _sanitize_model_error(self, reason: str) -> str:
        text = str(reason or "").strip()
        if not text:
            return "未返回具体错误。"
        match = re.match(r"LLM HTTP\s+(\d+):\s*(.*)", text, flags=re.S)
        if match:
            code, detail = match.group(1), match.group(2).strip()
            detail_text = detail
            try:
                parsed = json.loads(detail)
                error = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(error, dict):
                    parts = [str(error.get("message") or "").strip()]
                    if error.get("code"):
                        parts.append(f"code={error.get('code')}")
                    detail_text = "；".join(part for part in parts if part)
            except Exception:
                detail_text = detail
            detail_text = detail_text or "HTTP 请求失败"
            return f"HTTP {code}: {detail_text[:500]}"
        return text[:500]

    def _compact_conversation(self, conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in conversation[-10:]:
            if not isinstance(item, dict):
                continue
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            rows.append({
                "role": role,
                "content": content[:1600],
                "attachments": list(item.get("attachments") or [])[:4] if role == "user" else [],
            })
        return rows

    def _content_with_images(self, text: str, attachments: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        images = [
            item for item in attachments[:4]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]
        if not images:
            return text
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": str(item.get("data_url")), "detail": "auto"},
            })
        return content

    def _compact_step_results(self, plan: MissionPlan | None) -> list[dict[str, Any]]:
        if not plan:
            return []
        rows: list[dict[str, Any]] = []
        for step in plan.steps:
            rows.append(
                {
                    "id": step.id,
                    "label": step.label,
                    "tool": step.tool,
                    "params": step.params,
                    "status": step.status,
                    "result": self._trim_data(step.result or {}),
                }
            )
        return rows

    def _trim_data(self, data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, dict) and self._contains_image_data(data):
            trimmed = self._trim_image_payload(data)
            text = json.dumps(trimmed, ensure_ascii=False, default=str)
            if len(text) <= 1200:
                return trimmed
            return {"summary": text[:1200] + "...", "has_image": True}
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) <= 1200:
            return data
        return {"summary": text[:1200] + "..."}

    def _contains_image_data(self, value: Any) -> bool:
        if isinstance(value, dict):
            if any(key in value for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._contains_image_data(item) for item in value.values())
        if isinstance(value, list):
            return any(self._contains_image_data(item) for item in value)
        return False

    def _trim_image_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            keep: dict[str, Any] = {}
            for key, item in value.items():
                if key == "image_base64":
                    keep["has_image"] = bool(item)
                    keep["image_base64_bytes"] = len(str(item or ""))
                elif key in {
                    "status",
                    "message",
                    "target",
                    "target_class",
                    "target_description",
                    "vehicle",
                    "camera",
                    "image_type",
                    "image_saved_to",
                    "saved_to",
                    "approach_image_saved_to",
                    "selected_view",
                    "current_position",
                    "search_progress",
                    "detections",
                    "all_detections",
                    "target_world_position",
                    "target_depth_meters",
                    "target_distance_meters",
                    "vlm_confirmation",
                }:
                    keep[key] = self._trim_image_payload(item)
            return keep or {"has_image": self._contains_image_data(value)}
        if isinstance(value, list):
            return [self._trim_image_payload(item) for item in value[:5]]
        return value

    @staticmethod
    def _data_url_from_base64(image_base64: str) -> str:
        text = str(image_base64 or "").strip()
        if text.startswith("data:image/"):
            return text
        return f"data:image/png;base64,{text}"

    def _normalize_vlm_confirmation(self, payload: dict[str, Any], target: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        confidence = payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        found = bool(payload.get("target_found")) and confidence >= 0.5
        action = str(payload.get("recommended_next_action") or "").strip().lower()
        if action not in {"continue_search", "approach", "hold", "reposition", "insufficient_image"}:
            action = "approach" if found and confidence >= 0.65 else "continue_search"
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        bbox = payload.get("bbox_hint") or {}
        if not isinstance(bbox, dict):
            bbox = {}
        summary = str(payload.get("summary_zh") or "").strip()
        if not summary:
            summary = f"图像中{'发现' if found else '未确认发现'}目标“{target}”，置信度 {confidence:.2f}。"
        return {
            "status": "target_confirmed" if found else "target_not_confirmed",
            "target_found": found,
            "confidence": round(confidence, 3),
            "target_description": target,
            "target_label": str(payload.get("target_label") or ""),
            "evidence": [str(item)[:160] for item in evidence[:5]],
            "relative_direction": str(payload.get("relative_direction") or "unknown"),
            "bbox_hint": {
                "x": bbox.get("x"),
                "y": bbox.get("y"),
            },
            "recommended_next_action": action,
            "summary_zh": summary,
        }

    def _normalize_vlm_analysis(self, payload: dict[str, Any], question: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        visible = payload.get("visible_objects") or []
        if not isinstance(visible, list):
            visible = [str(visible)]
        candidates = payload.get("target_candidates") or []
        if not isinstance(candidates, list):
            candidates = []
        normalized_candidates: list[dict[str, Any]] = []
        for item in candidates[:6]:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence", 0.0)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.0
            bbox = item.get("bbox_hint") or {}
            if not isinstance(bbox, dict):
                bbox = {}
            normalized_candidates.append({
                "label": str(item.get("label") or "")[:80],
                "confidence": round(confidence, 3),
                "relative_direction": str(item.get("relative_direction") or "unknown"),
                "bbox_hint": {"x": bbox.get("x"), "y": bbox.get("y")},
                "evidence": str(item.get("evidence") or "")[:220],
            })
        safety_notes = payload.get("safety_notes") or []
        if not isinstance(safety_notes, list):
            safety_notes = [str(safety_notes)]
        hint = str(payload.get("navigation_hint") or "hold").strip().lower()
        if hint not in {"hold", "reposition", "approach_possible", "insufficient_depth", "unsafe"}:
            hint = "hold"
        summary = str(payload.get("summary_zh") or "").strip()
        if not summary:
            summary = "已获取摄像头画面，但模型没有返回明确的图像描述。"
        return {
            "status": "image_analyzed",
            "question": question,
            "summary_zh": summary,
            "message": summary,
            "visible_objects": [str(item)[:100] for item in visible[:10]],
            "target_candidates": normalized_candidates,
            "navigation_hint": hint,
            "safety_notes": [str(item)[:180] for item in safety_notes[:5]],
        }

    def _fallback_chat_answer(self, payload: dict[str, Any]) -> str:
        command = str(payload.get("operator_message") or "")
        state = payload.get("agent_state") or {}
        vehicle = state.get("vehicle") or {}
        lower = command.lower()
        wants_status = any(word in lower for word in ["status", "state", "telemetry", "状态", "汇报", "连接", "在哪", "位置"])
        wants_action = any(word in lower for word in [
            "takeoff", "land", "move", "fly", "search", "track", "起飞", "降落", "移动", "飞到", "搜索", "跟踪", "执行"
        ])

        backend = state.get("backend_name") or state.get("backend") or "unknown"
        connected = "已连接" if state.get("connected") else "未连接"
        if state.get("stale_connection"):
            connected = "连接异常或状态过期"
        pos = vehicle.get("position_ned") if isinstance(vehicle, dict) else None
        pos_text = ""
        if isinstance(pos, dict):
            pos_text = f"，NED 位置 x={pos.get('x', '?')}, y={pos.get('y', '?')}, z={pos.get('z', '?')}"
        flight_bits: list[str] = []
        if isinstance(vehicle, dict):
            if "armed" in vehicle:
                flight_bits.append(f"armed={vehicle.get('armed')}")
            if "flying" in vehicle:
                flight_bits.append(f"flying={vehicle.get('flying')}")
            if "has_collided" in vehicle:
                flight_bits.append(f"has_collided={vehicle.get('has_collided')}")
        vehicle_text = f"，车辆状态: {', '.join(flight_bits)}" if flight_bits else ""

        if wants_status:
            return f"当前后端是 {backend}，状态：{connected}{vehicle_text}{pos_text}。这是 Chat 模式汇报，没有执行任何飞控工具。"
        if wants_action:
            return "我现在处于 Chat 模式，不会执行飞控动作。要真正起飞、移动、搜索或降落，请切换到 Execute 模式后再发送指令。"
        return f"我在 Chat 模式，可以帮你解释任务、检查会话上下文和汇报当前状态；当前后端 {backend}，{connected}。"

    def _fallback_answer(self, payload: dict[str, Any]) -> str:
        telemetry = payload.get("current_telemetry") or {}
        command = str(payload.get("operator_command") or "")
        status = str(payload.get("run_status") or "")
        summary = str(payload.get("plan_summary") or "任务")
        failure = str(payload.get("failure_reason") or "")
        verification = payload.get("verification") or {}
        tool_results = payload.get("tool_results") or []
        is_agent_loop = str(payload.get("intent") or "") == "agent_loop"
        tool_names = [
            str(row.get("tool") or "")
            for row in tool_results
            if isinstance(row, dict)
        ] if isinstance(tool_results, list) else []
        has_flight_action = any(
            name.startswith("drone_") and name not in {"drone_connect", "drone_get_status", "drone_list_vehicles"}
            for name in tool_names
        )

        if not is_agent_loop and not has_flight_action:
            for row in reversed(tool_results if isinstance(tool_results, list) else []):
                if not isinstance(row, dict):
                    continue
                if row.get("tool") not in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target"}:
                    continue
                result = row.get("result") if isinstance(row.get("result"), dict) else {}
                message = str(result.get("summary_zh") or result.get("message") or "").strip()
                if message:
                    return message

        wants_status = any(word in command.lower() for word in ["status", "state", "telemetry", "状态", "汇报"])
        if wants_status and isinstance(telemetry, dict):
            pos = telemetry.get("position_ned") or {}
            vel = telemetry.get("velocity_ned") or {}
            altitude = abs(float(pos.get("z") or 0))
            speed = (
                float(vel.get("vx") or 0) ** 2
                + float(vel.get("vy") or 0) ** 2
                + float(vel.get("vz") or 0) ** 2
            ) ** 0.5
            armed = "已解锁" if telemetry.get("armed") else "未解锁"
            flying = "空中飞行" if telemetry.get("flying") else "地面/未飞行"
            collision = "发生碰撞" if telemetry.get("has_collided") else "未检测到碰撞"
            heading = float(telemetry.get("heading_deg") or 0)
            return (
                f"当前无人机{armed}，状态为{flying}。"
                f"高度约 {altitude:.1f} m，NED 位置为 N {float(pos.get('x') or 0):.1f} / "
                f"E {float(pos.get('y') or 0):.1f} / D {float(pos.get('z') or 0):.1f}，"
                f"速度约 {speed:.1f} m/s，航向 {heading:.1f}°。"
                f"{collision}。"
            )

        if (is_agent_loop or has_flight_action) and isinstance(tool_results, list) and tool_results:
            report = self._fallback_agent_loop_report(command, status, summary, failure, verification, telemetry, tool_results)
            if report:
                return report

        if status == "completed":
            if str(payload.get("intent") or "") == "agent_loop":
                summary_text = summary.strip()
                if summary_text and summary_text.lower() not in {"agent loop task", "l3 agent loop task", "agent loop completed", "done", "complete"}:
                    return summary_text
            if verification.get("status") == "failed":
                return f"{summary}执行后校验未通过：{verification.get('summary', '最终状态与任务目标不一致')}。"
            return f"{summary}已完成。所有关键工具调用已经结束，当前状态已回读并写入任务记录。"
        if status in {"failed", "blocked"}:
            return f"{summary}未完成：{failure or '执行被中断或安全层阻止'}。"
        if status == "planned":
            return f"我已经完成规划：{summary}。当前尚未执行仿真动作。"
        return f"我已收到任务：{summary}，正在推进执行。"

    def _fallback_agent_loop_report(
        self,
        command: str,
        status: str,
        summary: str,
        failure: str,
        verification: dict[str, Any],
        telemetry: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        label_map = {
            "drone_get_status": "读取状态",
            "drone_set_mode": "切换模式",
            "drone_arm": "解锁电机",
            "drone_takeoff": "起飞",
            "drone_move_relative": "相对移动",
            "drone_fly_to": "返航/飞至目标点",
            "drone_fly_path": "路径飞行",
            "drone_land": "降落",
            "drone_hover": "悬停",
            "airsim_take_photo": "拍摄图像",
            "airsim_vlm_analyze_image": "图像分析",
            "airsim_vlm_confirm_target": "目标确认",
        }
        completed: list[str] = []
        failed: list[str] = []
        image_text = ""
        for row in tool_results:
            if not isinstance(row, dict):
                continue
            tool = str(row.get("tool") or "")
            label = label_map.get(tool, tool)
            if row.get("status") == "completed":
                completed.append(label)
            elif row.get("status") == "failed":
                failed.append(label)
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if tool in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target", "airsim_take_photo"}:
                image_text = str(
                    result.get("summary_zh")
                    or result.get("message")
                    or result.get("summary")
                    or image_text
                ).strip()
        chain = "、".join(dict.fromkeys(completed)) or "已有工具调用"
        failed_text = f"失败或未确认步骤：{'、'.join(dict.fromkeys(failed))}。" if failed else ""

        pos = telemetry.get("position_ned") if isinstance(telemetry, dict) else {}
        pos = pos if isinstance(pos, dict) else {}
        altitude = abs(self._safe_float(pos.get("z"), self._safe_float(telemetry.get("altitude_m"), 0.0)) if isinstance(telemetry, dict) else 0.0)
        armed = "已解锁" if isinstance(telemetry, dict) and telemetry.get("armed") else "未解锁"
        flying = "飞行中" if isinstance(telemetry, dict) and telemetry.get("flying") else "未飞行/已落地"
        collision = "发生碰撞" if isinstance(telemetry, dict) and telemetry.get("has_collided") else "未检测到碰撞"
        final_state = (
            f"最终状态：{armed}，{flying}，高度约 {altitude:.1f} m，"
            f"NED 位置 N {self._safe_float(pos.get('x')):.1f} / E {self._safe_float(pos.get('y')):.1f} / D {self._safe_float(pos.get('z')):.1f}，{collision}。"
        )

        limit_note = ""
        if "max_steps" in failure:
            limit_note = "Agent 已停止继续决策，并根据已收集结果生成报告。"
        elif status in {"failed", "blocked"} and failure:
            limit_note = f"任务未完全收口：{failure}。"

        verify_note = ""
        if isinstance(verification, dict):
            if verification.get("level") == "failed":
                verify_note = f"校验提示：{verification.get('summary') or '关键状态未达到目标'}。"
            elif verification.get("level") == "warning":
                verify_note = "状态已回读，存在轻微误差但未发现阻断性失败。"

        image_text = image_text.rstrip("。.!！?？")
        image_note = f"图像结果：{image_text}。" if image_text else "图像结果：没有拿到明确的视觉分析摘要。"
        done_prefix = "任务已完成。" if status == "completed" else "任务执行已停止在当前阶段。"
        return " ".join(part for part in [
            done_prefix,
            f"执行链路：{chain}。",
            image_note if any(word in command for word in ["拍照", "图像", "目标", "扫描", "看看"]) else "",
            final_state,
            failed_text,
            verify_note,
            limit_note,
        ] if part).strip()

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _plan_from_payload(self, command: str, payload: dict[str, Any], known_tools: set[str]) -> MissionPlan:
        run_id = f"run_{int(time.time() * 1000)}"
        steps: list[MissionStep] = []
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list):
            raise RuntimeError("LLM plan field 'steps' must be a list")

        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool", "")).strip()
            if tool not in known_tools and tool != "memory_store":
                continue
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            steps.append(
                MissionStep(
                    id=f"s{len(steps) + 1:02d}",
                    label=str(raw.get("label") or tool),
                    tool=tool,
                    params=params,
                    layer=str(raw.get("layer") or "tool"),
                    needs_observation=bool(raw.get("needs_observation")),
                )
            )

        steps = self._normalize_steps(command, steps, known_tools)
        if not steps:
            raise RuntimeError("LLM produced no executable known-tool steps")

        return MissionPlan(
            run_id=run_id,
            command=command,
            intent=str(payload.get("intent") or "llm_mission"),
            summary=str(payload.get("summary") or "LLM 生成任务计划"),
            steps=steps,
            assumptions=[str(x) for x in payload.get("assumptions", []) if isinstance(x, (str, int, float))],
            planner_source="llm",
            planner_model="",
            reasoning=str(payload.get("reasoning") or ""),
            risk_notes=[str(x) for x in payload.get("risk_notes", []) if isinstance(x, (str, int, float))],
            execution_mode=str(payload.get("execution_mode") or "auto").strip().lower()
            if str(payload.get("execution_mode") or "").strip().lower() in {"auto", "agent_loop"}
            else "auto",
        )

    def _normalize_steps(self, command: str, steps: list[MissionStep], known_tools: set[str]) -> list[MissionStep]:
        steps = self._filter_unsolicited_steps(command, steps)
        normalized: list[MissionStep] = []
        for step in steps:
            step.params = dict(step.params or {})
            if step.tool == "drone_takeoff":
                step.params["altitude"] = self._positive_float(step.params.get("altitude"), 3.0)
            elif step.tool == "drone_fly_to":
                if "z" in step.params:
                    step.params["z"] = self._ned_z(step.params.get("z"), -3.0)
                step.params.setdefault("velocity", 2.0)
            elif step.tool == "drone_move_relative":
                step.params["forward_m"] = float(step.params.get("forward_m", 0.0) or 0.0)
                step.params["right_m"] = float(step.params.get("right_m", 0.0) or 0.0)
                step.params["up_m"] = float(step.params.get("up_m", 0.0) or 0.0)
                step.params["velocity"] = self._positive_float(step.params.get("velocity"), 2.0)
            elif step.tool == "drone_fly_path":
                waypoints = step.params.get("waypoints_json")
                if not isinstance(waypoints, str):
                    waypoints = step.params.get("waypoints")
                if not isinstance(waypoints, str):
                    try:
                        waypoints = json.dumps(waypoints or [], ensure_ascii=False, separators=(",", ":"))
                    except (TypeError, ValueError):
                        waypoints = "[]"
                step.params["waypoints_json"] = waypoints
                step.params["velocity"] = self._positive_float(step.params.get("velocity"), 1.5)
            elif step.tool in {"skill:search", "airsim_search_target"}:
                if step.tool == "airsim_search_target" and "skill:search" in known_tools:
                    step.tool = "skill:search"
                    step.layer = "planning"
                step.params["search_altitude"] = self._positive_float(step.params.get("search_altitude"), 3.0)
                step.params["search_radius"] = self._positive_float(step.params.get("search_radius"), 25.0)
                target_class = self._command_target_class(command)
                if target_class:
                    step.params.setdefault("target_class", target_class)
                step.params.setdefault("max_steps", 4)
                step.params.setdefault("scene_description", command)
            elif step.tool == "airsim_vlm_confirm_target":
                target_class = self._command_target_class(command)
                step.params.setdefault("target_description", target_class or command)
                step.params.setdefault("source", "last_image")
            normalized.append(step)

        memory_steps = [s for s in normalized if s.tool == "memory_store"]
        normalized = [s for s in normalized if s.tool != "memory_store"]
        visual_tools = {"airsim_take_photo", "airsim_detect_objects"}
        wants_open_image_analysis = self._command_has_any(
            command,
            ["what", "describe", "scene", "see", "look", "有什么", "看到", "看看", "画面", "照片", "图像内容"],
        )
        needs_vlm_analyze = (
            "airsim_vlm_analyze_image" in known_tools
            and not any(s.tool in {"airsim_vlm_analyze_image", "airsim_vlm_confirm_target"} for s in normalized)
            and any(s.tool in visual_tools for s in normalized)
            and wants_open_image_analysis
        )
        needs_vlm_confirm = (
            "airsim_vlm_confirm_target" in known_tools
            and not any(s.tool == "airsim_vlm_confirm_target" for s in normalized)
            and not needs_vlm_analyze
            and any(s.tool in visual_tools for s in normalized)
            and self._command_has_any(command, ["search", "find", "detect", "识别", "搜索", "寻找", "找", "目标", "photo", "image", "拍照", "图像"])
        )
        if needs_vlm_analyze:
            normalized.append(
                MissionStep(
                    "s00",
                    "Analyze camera image",
                    "airsim_vlm_analyze_image",
                    {"question": command, "source": "last_image"},
                    "perception",
                )
            )
        if needs_vlm_confirm:
            target_class = self._command_target_class(command)
            normalized.append(
                MissionStep(
                    "s00",
                    "Confirm visual target",
                    "airsim_vlm_confirm_target",
                    {"target_description": target_class or command, "source": "last_image"},
                    "perception",
                )
            )
        has_control = any(s.tool.startswith("drone_") or s.tool.startswith("airsim_") or s.tool.startswith("skill:") for s in normalized)
        core_tools = [s.tool for s in normalized if s.tool != "memory_store"]
        if has_control and "drone_get_status" in known_tools and (not core_tools or core_tools[-1] != "drone_get_status"):
            normalized.append(MissionStep("s00", "Read final status", "drone_get_status", {}, "perception"))
        if memory_steps:
            normalized.append(memory_steps[0])
        else:
            normalized.append(MissionStep("s00", "Store mission memory", "memory_store", {"source": "mission"}, "memory"))

        for index, step in enumerate(normalized, 1):
            step.id = f"s{index:02d}"
        return normalized

    def _command_has_any(self, command: str, words: list[str]) -> bool:
        lower = command.lower()
        return any(word in lower for word in words)

    def _positive_float(self, value: Any, default: float) -> float:
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            return default
        return number or default

    def _ned_z(self, value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return -abs(number) if number > 0 else number

    def _mission_takeoff_altitude(self, steps: list[MissionStep]) -> float:
        for step in steps:
            if step.tool in {"skill:search", "airsim_search_target"}:
                return self._positive_float(step.params.get("search_altitude"), 3.0)
            if step.tool == "drone_takeoff":
                return self._positive_float(step.params.get("altitude"), 3.0)
        return 3.0

    def _command_target_class(self, command: str) -> str:
        lower = command.lower()
        if any(word in lower for word in ["卡车", "truck"]):
            return "truck"
        if any(word in lower for word in ["公交", "巴士", "bus"]):
            return "bus"
        if any(word in lower for word in ["车辆", "汽车", "可疑车", "车", "car", "vehicle"]):
            return "car"
        if any(word in lower for word in ["行人", "人员", "目标人", "person", "pedestrian", "human"]):
            return "person"
        return ""

    def _filter_unsolicited_steps(self, command: str, steps: list[MissionStep]) -> list[MissionStep]:
        lower = command.lower()

        def has_any(words: list[str]) -> bool:
            return any(w in lower for w in words)

        wants_photo = has_any(["photo", "capture", "image", "拍照", "图像", "截图", "照片"])
        wants_search = has_any(["search", "find", "detect", "识别", "搜索", "寻找", "找", "目标"])
        wants_track = has_any(["track", "follow", "跟踪", "追踪"])
        wants_land = has_any(["land", "降落", "落地", "返航", "结束任务"])
        wants_takeoff = has_any(["takeoff", "起飞", "升空"])
        wants_disarm = has_any(["disarm", "锁定电机", "锁电机"])
        wants_disconnect = has_any(["disconnect", "断开"])

        filtered: list[MissionStep] = []
        for step in steps:
            tool = step.tool
            if tool == "drone_takeoff" and wants_land and not (wants_takeoff or wants_search or wants_photo or wants_track):
                continue
            if tool == "airsim_take_photo" and not wants_photo:
                continue
            if tool in {"airsim_detect_objects", "airsim_get_depth_map"} and not (wants_photo or wants_search):
                continue
            if tool == "airsim_search_target":
                continue
            if tool == "skill:search" and not wants_search:
                continue
            if tool in {"airsim_track_object", "airsim_approach_target"}:
                continue
            if tool == "drone_land" and not wants_land:
                continue
            if tool == "drone_disarm" and not (wants_land or wants_disarm):
                continue
            if tool == "drone_disconnect" and not wants_disconnect:
                continue
            filtered.append(step)
        return filtered
