"""LLM 多厂商客户端层：配置/注册表/请求重试与工具调用错误（拆分自 llm.py）。"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm_protocol import (
    IMAGE_TOKEN_QUOTA,
    ContextBudget,
    TokenMeter,
    estimate_messages,
    function_tool_schema,
    openai_tools_to_anthropic,
    tool_schema_from_spec,
)

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

# Providers whose OpenAI/Anthropic-compatible endpoints support native
# function/tool calling. Unknown or local endpoints default to JSON mode
# (zero risk); users can force "tools" via capability_mode.
_NATIVE_TOOL_PROVIDERS = (
    "openai", "deepseek", "anthropic", "openrouter", "qwen", "aliyun", "dashscope",
    "moonshot", "zhipu", "glm", "mistral", "groq", "together", "siliconflow",
    "azure", "ollama", "vllm", "lmstudio", "localai", "xai", "cohere", "minimax",
    "stepfun", "volcengine", "ark", "hunyuan", "kimi", "baichuan", "ernie",
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

    if mode == "tools":
        native_tools = True
        tools_source = "manual"
    elif mode == "text":
        native_tools = False
        tools_source = "manual"
    else:
        native_tools = any(name in provider.lower() for name in _NATIVE_TOOL_PROVIDERS)
        tools_source = "provider"

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
        "native_tools": native_tools,
        "native_tools_source": tools_source,
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
            "reasoning_effort": str(model.get("reasoning_effort") or ""),
            "thinking_mode": str(model.get("thinking_mode") or ""),
            "multimodal": bool(inferred["multimodal"]),
            "capability_source": inferred["capability_source"],
            "input_modes": inferred["input_modes"],
            "native_tools": bool(inferred["native_tools"]),
            "native_tools_source": inferred["native_tools_source"],
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


# ---------------------------------------------------------------------------
# LLM transport retry (transient failures only)
# ---------------------------------------------------------------------------
#
# 429 (rate limit) and 5xx (server) are transient; other 4xx responses (auth,
# bad request, context overflow) are not — retrying them cannot help, and a
# context-overflow retry would just fail again. Timeouts and connection
# errors (URLError) are transient at the transport level.
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 0.5
_RETRY_MAX_DELAY_S = 8.0
_RETRY_AFTER_CAP_S = 30.0


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Exponential backoff with jitter; prefers the provider's Retry-After."""
    if retry_after:
        try:
            seconds = float(str(retry_after).strip())
            if seconds > 0:
                return min(seconds, _RETRY_AFTER_CAP_S)
        except ValueError:
            pass
    delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
    jitter = random.uniform(0.0, delay * 0.3)
    return min(delay + jitter, _RETRY_MAX_DELAY_S)


def _request_with_retry(
    req: urllib.request.Request,
    timeout: float,
    label: str = "LLM",
    retry_box: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST once, retrying transient HTTP/transport errors with backoff.

    Returns the parsed JSON body. Raises RuntimeError for non-retryable
    errors and when all attempts are exhausted. ``retry_box`` (optional)
    receives ``{"attempts": n}`` for observability.
    """
    last_error: Exception | None = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if retry_box is not None:
                    retry_box["attempts"] = attempt - 1
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            message = f"{label} HTTP {e.code}: {detail}"
            if e.code not in _RETRYABLE_HTTP_CODES or attempt == _RETRY_MAX_ATTEMPTS:
                raise RuntimeError(message) from e
            last_error = RuntimeError(message)
            delay = _retry_delay(attempt, e.headers.get("Retry-After"))
        except urllib.error.URLError as e:
            message = f"{label} request failed: {e.reason}"
            if attempt == _RETRY_MAX_ATTEMPTS:
                raise RuntimeError(message) from e
            last_error = RuntimeError(message)
            delay = _retry_delay(attempt)
        time.sleep(delay)
    raise last_error  # pragma: no cover — loop always returns or raises above


# ---------------------------------------------------------------------------
# Context-overflow detection (recovery path)
# ---------------------------------------------------------------------------
#
# When a provider rejects the request because the prompt exceeds its context
# window, the planner rebuilds the request with a tighter budget and retries
# once (see LLMMissionPlanner._chat_with_overflow_recovery). Markers cover
# OpenAI/DeepSeek/OpenRouter ("maximum context length", "context_length_
# exceeded"), Anthropic ("prompt is too long"), and local endpoints.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "context length",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "reduce the length",
    "reduce your message",
    "token limit",
)


def is_context_overflow_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _CONTEXT_OVERFLOW_MARKERS)


def _retryable_stream_error(exc: Exception) -> bool:
    """Stream retry policy: retry transport errors, 429 and 5xx; never retry
    other 4xx responses (auth, bad request, context overflow)."""
    message = str(exc).lower()
    if "http 4" in message and "http 429" not in message:
        return False
    return True


def _stream_events_with_first_chunk_retry(
    client: Any,
    messages: list[dict[str, Any]],
    max_tokens: int = 0,
    attempts: int = 2,
):
    """Yield stream events, retrying once only when the FIRST event fails to
    arrive — nothing has been delivered yet, so a retry cannot duplicate
    output. After the first token, errors propagate as-is (a retry would
    produce a duplicated partial answer)."""
    for attempt in range(max(1, attempts)):
        stream = client.stream_events(messages, max_tokens=max_tokens)
        try:
            first = next(stream)
        except StopIteration:
            return
        except Exception as exc:
            # thinking models can take a long time before the first token:
            # a timeout on attempt 1 should not burn a second full timeout —
            # the caller (streamed planning) falls back to the non-streaming
            # path, which is a single bounded request. 4xx also never retries.
            if attempt >= attempts - 1 or not _retryable_stream_error(exc) or _stream_timeout_error(exc):
                raise
            continue
        yield first
        yield from stream
        return


def _stream_timeout_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "timeout" in message or "timed out" in message or "超时" in message


class OpenAIClient:
    """OpenAI-compatible chat completions client (covers DeepSeek, OpenAI, OpenRouter, vLLM, Ollama, etc.)."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.last_retries = 0
        self.last_reasoning = ""

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
        # DeepSeek V4 / OpenAI reasoning-effort models: thinking mode and
        # effort level are request-level settings (temperature etc. are
        # ignored while thinking is enabled on DeepSeek).
        thinking_mode = str(self.config.get("thinking_mode") or "").strip().lower()
        reasoning_effort = str(self.config.get("reasoning_effort") or "").strip().lower()
        if thinking_mode in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking_mode}
        if reasoning_effort in {"low", "medium", "high", "max"}:
            payload["reasoning_effort"] = reasoning_effort
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
        # 推理模型的思考内容（DeepSeek reasoning_content）透出到 client 属性，
        # 供流式回退路径也能把思考归档进前端思考块
        message = ((response.get("choices") or [{}])[0].get("message")) or {}
        self.last_reasoning = str(message.get("reasoning_content") or "")
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

    def chat_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        """Native function-calling round trip.

        Returns (tool_calls, text, usage); tool_calls items are
        {id, name, arguments(dict)}. Raises ToolCallUnsupportedError when the
        endpoint rejects the tools parameter so the caller can degrade to JSON
        mode.
        """
        req = self._request(messages, {"tools": tools, "tool_choice": tool_choice})
        try:
            response = self._do_request(req)
        except Exception as exc:
            if is_tool_unsupported_error(exc):
                raise ToolCallUnsupportedError(str(exc)) from exc
            raise
        message = ((response.get("choices") or [{}])[0].get("message")) or {}
        text = str(message.get("content") or "")
        # DeepSeek-style reasoning models return chain-of-thought alongside
        # tool calls; surface it so the agent loop can show the decision
        # rationale in the operator's event stream
        self.last_reasoning = str(message.get("reasoning_content") or "")
        tool_calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            arguments = str(function.get("arguments") or "").strip()
            try:
                parsed = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError as e:
                raise RuntimeError(f"LLM tool arguments JSON parse failed: {e}: {arguments[:200]}") from e
            if not isinstance(parsed, dict):
                parsed = {}
            tool_calls.append({"id": str(call.get("id") or ""), "name": name, "arguments": parsed})
        return tool_calls, text, response.get("usage") or {}

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
            # Streaming (especially with thinking enabled) can take much
            # longer than a normal request before the FIRST token arrives;
            # use a wider read timeout so planning is not killed early.
            stream_timeout = max(float(self.config.get("timeout_sec", 25.0)), 60.0)
            with urllib.request.urlopen(req, timeout=stream_timeout) as resp:
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
        box: dict[str, Any] = {}
        try:
            data = _request_with_retry(req, float(self.config.get("timeout_sec", 25.0)), label="LLM", retry_box=box)
            self.last_retries = int(box.get("attempts") or 0)
            return data
        except Exception:
            self.last_retries = int(box.get("attempts") or 0)
            raise

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
        self.last_retries = 0

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

    def _request(self, messages: list[dict[str, Any]], max_tokens: int, stream: bool, extra: dict[str, Any] | None = None):
        url = str(self.config.get("base_url", "https://api.anthropic.com")).rstrip("/") + "/v1/messages"
        system, body = self._build_body(messages, max_tokens, stream)
        if extra:
            body.update(extra)
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

    def chat_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        """Native tool_use round trip for the Anthropic Messages API.

        ``tools`` must already be in Anthropic shape ({name, description,
        input_schema}). Returns (tool_calls, text, usage); tool_calls items are
        {id, name, arguments(dict)}.
        """
        configured_max = int(self.config.get("max_tokens", 0) or 0)
        extra: dict[str, Any] = {"tools": tools, "tool_choice": tool_choice or {"type": "auto"}}
        req = self._request(messages, configured_max, stream=False, extra=extra)
        try:
            response = self._do_request(req)
        except Exception as exc:
            if is_tool_unsupported_error(exc):
                raise ToolCallUnsupportedError(str(exc)) from exc
            raise
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": block.get("input") or {},
                })
        usage = response.get("usage") or {}
        return tool_calls, "".join(text_parts), {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        }

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
        box: dict[str, Any] = {}
        try:
            data = _request_with_retry(req, float(self.config.get("timeout_sec", 25.0)), label="Anthropic", retry_box=box)
            self.last_retries = int(box.get("attempts") or 0)
            return data
        except Exception:
            self.last_retries = int(box.get("attempts") or 0)
            raise

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


class ToolCallUnsupportedError(RuntimeError):
    """The provider rejected the tools parameter. The caller should fall back
    to JSON-schema prompting instead of retrying the native path."""


def is_tool_unsupported_error(exc: Exception) -> bool:
    """Distinguish 'provider does not support tools' from our own bugs.

    The authoritative signal is the OpenAI-style error.param field; message
    keywords are a conservative fallback. A schema bug in our own tools payload
    usually surfaces as 'invalid schema for function X' and is NOT treated as
    unsupported, so it stays visible instead of being silently degraded.
    """
    text = str(exc)
    match = re.match(r"LLM HTTP (\d+):\s*(.*)", text, flags=re.S)
    if not match:
        return False
    code = int(match.group(1))
    if code not in {400, 404, 422}:
        return False
    detail = match.group(2)
    try:
        parsed = json.loads(detail)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            param = str(error.get("param") or "")
            if param and any(marker in param.lower() for marker in ("tool", "function")):
                return True
    except Exception:
        pass
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "does not support",
            "not supported",
            "unsupported parameter",
            "unknown parameter",
            "unexpected parameter",
            "extra field",
            "unknown field",
            "unexpected field",
        )
    )


class LLMUnavailableError(RuntimeError):
    """Raised when an interactive chat request cannot reach the selected model."""


