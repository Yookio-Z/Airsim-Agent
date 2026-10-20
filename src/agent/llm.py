"""LLM planner with multi-provider model registry for the AirSim VLA agent."""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .command_slots import (
    FLIGHT_TERMS,
    NAVIGATION_TERMS,
    extract_command_slots,
    extract_intents,
    extract_target_class,
)
from .llm_protocol import (
    IMAGE_TOKEN_QUOTA,
    ContextBudget,
    TokenMeter,
    estimate_messages,
    function_tool_schema,
    openai_tools_to_anthropic,
    tool_schema_from_spec,
)
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
    "minimax-m3",
    "minimax-m2",
    "minimax-m1",
    "qwen2.5-vl",
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


def infer_model_capabilities(
    model: str,
    provider: str = "",
    capability_mode: str = "auto",
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer stable UI capabilities.

    Priority: manual capability_mode override, then modalities reported by the
    provider's model catalog (cached on the model entry), then model-ID
    keyword hints as the offline fallback.
    """
    mode = str(capability_mode or "auto").strip().lower()
    haystack = f"{provider} {model}".lower()
    catalog = capabilities if isinstance(capabilities, dict) else {}
    catalog_mods = _clean_modality_list(catalog.get("input_modalities"))
    if mode == "vision":
        multimodal = True
        source = "manual"
    elif mode == "text":
        multimodal = False
        source = "manual"
    elif catalog_mods:
        multimodal = "image" in catalog_mods
        source = "provider_catalog"
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
    catalog_context = _positive_int(catalog.get("context_length"))
    if catalog_context:
        context_window = catalog_context
    return {
        "multimodal": multimodal,
        "capability_source": source,
        "context_window": context_window,
        "input_modes": ["text", "image"] if multimodal else ["text"],
        "native_tools": native_tools,
        "native_tools_source": tools_source,
    }


def _clean_modality_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


_CATALOG_CAPABILITY_KEYS = (
    "input_modalities",
    "output_modalities",
    "context_length",
    "supported_parameters",
    "reasoning",
    "source",
    "fetched_at",
)


def _catalog_capabilities(model: dict[str, Any]) -> dict[str, Any]:
    """Cached provider-catalog capabilities stored on a model entry."""
    caps = model.get("capabilities")
    if not isinstance(caps, dict):
        return {}
    return {key: caps[key] for key in _CATALOG_CAPABILITY_KEYS if key in caps}


# 思考模式 / 思考等级是请求级参数（DeepSeek V4、OpenAI reasoning 系列等），
# 空值表示"用模型默认"，此时该字段不会出现在请求体里。
THINKING_MODES = ("enabled", "disabled")
REASONING_EFFORTS = ("low", "medium", "high", "max")
_THINKING_ALLOWED = {
    "thinking_mode": THINKING_MODES,
    "reasoning_effort": REASONING_EFFORTS,
}


def sanitize_thinking_value(key: str, value: Any) -> str:
    """把 thinking_mode / reasoning_effort 归一成合法取值，非法值返回空串。

    界面上填错的值直接丢弃，而不是原样转发给上游——否则模型端会以 400 拒绝，
    表现为"改完思考等级整个请求就失败"。
    """
    allowed = _THINKING_ALLOWED.get(key)
    if not allowed:
        return ""
    text = str(value or "").strip().lower()
    return text if text in allowed else ""


def _apply_thinking_settings(entry: dict[str, Any]) -> None:
    """就地清洗 model 条目上的思考参数，空值直接删掉。"""
    for key in _THINKING_ALLOWED:
        if key not in entry:
            continue
        value = sanitize_thinking_value(key, entry.get(key))
        if value:
            entry[key] = value
        else:
            entry.pop(key, None)


# 请求体只会发送这几个档位，所以界面也只该提供这些
_SENDABLE_EFFORTS = ("low", "medium", "high", "max")
# 没有厂商目录时按模型族推断档位；没把握的模型一律返回空，宁可只给"开/关思考"
# 仅在"读不到厂商目录"时兜底。DeepSeek 按官方更新日志：V4 系列思考强度为
# low/high/max 三档；deepseek-chat / deepseek-reasoner 是映射到 v4-flash 的旧名
# （2026-07-24 下线）。
_FAMILY_REASONING_LEVELS = (
    (("deepseek-v4", "deepseek-flash", "deepseek-pro", "deepseek-reasoner", "deepseek-r1"), ("low", "high", "max")),
    (("deepseek-chat",), ()),
    (("gpt-5", "o1", "o3", "o4"), ("low", "medium", "high")),
    (("gemini-2.5", "gemini-3"), ("low", "high")),
    (("claude-4", "sonnet-4", "opus-4", "claude-3-7", "claude-3.7"), ("low", "medium", "high")),
)


def derive_provider(base_url: str, api_type: str = "openai", model_id: str = "") -> str:
    """用户只填 Base URL 时，从主机名推断一个 provider 标签。

    这个标签只用于界面上的分组/展示，不影响请求；认不出就退回 API 类型或
    模型 ID 前缀，保证永远有个可读的值。
    """
    host = ""
    text = str(base_url or "").strip().lower()
    if text:
        without_scheme = text.split("://", 1)[-1]
        host = without_scheme.split("/", 1)[0].split("@")[-1].split(":")[0]
    for token, label in (
        ("openrouter", "openrouter"),
        ("deepseek", "deepseek"),
        ("moonshot", "moonshot"),
        ("siliconflow", "siliconflow"),
        ("dashscope", "dashscope"),
        ("bigmodel", "zhipu"),
        ("volces", "volcengine"),
        ("anthropic", "anthropic"),
        ("openai", "openai"),
    ):
        if token in host:
            return label
    if host:
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"} or host.endswith(".local"):
            return "local"
        labels = [part for part in host.split(".") if part and part not in {"api", "www", "com", "cn", "ai"}]
        if labels:
            return labels[-1]
    # 没填 Base URL 时不做猜测，直接用 API 类型当标签，行为可预期
    return str(api_type or "openai").strip().lower() or "openai"


def reasoning_profile(entry: dict[str, Any]) -> dict[str, Any]:
    """这个模型真正可设置的思考档位，供界面只显示可用选项。

    优先用厂商目录（OpenRouter 的 ``reasoning.supported_efforts`` /
    ``supported_parameters``）；目录没给就只能按模型族推断，推不出来就只暴露
    "是否思考"、不显示档位——不拿通用四档硬套所有模型。
    """
    capabilities = _catalog_capabilities(entry)
    reasoning = capabilities.get("reasoning") if isinstance(capabilities.get("reasoning"), dict) else {}
    supported_params = capabilities.get("supported_parameters")
    declared = {
        str(item).strip().lower()
        for item in (reasoning.get("supported_efforts") or [])
    }
    catalog_efforts = [value for value in _SENDABLE_EFFORTS if value in declared]
    model_id = str(entry.get("model") or entry.get("id") or "").lower()
    levels: list[str] = []
    source = ""
    if catalog_efforts:
        levels, source = catalog_efforts, "provider_catalog"
    elif reasoning or (isinstance(supported_params, list)
                       and "reasoning" in {str(item).strip().lower() for item in supported_params}):
        levels, source = ["low", "medium", "high"], "provider_catalog"
    else:
        for family, family_levels in _FAMILY_REASONING_LEVELS:
            if any(token in model_id for token in family):
                levels, source = list(family_levels), "model_id_heuristic"
                break
    default_effort = str(reasoning.get("default_effort") or "").strip().lower()
    recognized = bool(source)
    return {
        "supports_thinking": bool(levels) or bool(reasoning),
        # recognized=False 表示"完全没查到依据"，界面应显示"未能识别"而不是
        # "不支持"——后者会让用户以为模型确实没有推理能力
        "recognized": recognized,
        "levels": levels,
        "default": default_effort if default_effort in levels else "",
        "source": source,
        "mandatory": bool(reasoning.get("mandatory")),
    }


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def parse_catalog_capabilities(entry: dict[str, Any], model_id: str = "", api_type: str = "openai") -> dict[str, Any]:
    """Extract capabilities from one provider /models entry.

    Handles OpenRouter's ``architecture`` block, top-level ``input_modalities``
    and the context-length spellings used by OpenRouter, vLLM and LM Studio.
    Anthropic's catalog publishes no modalities, so Claude 3+ ids are mapped to
    text+image there. Returns {} when the entry carries nothing usable.
    """
    if not isinstance(entry, dict):
        return {}
    arch = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
    capabilities: dict[str, Any] = {"source": "provider_catalog"}
    input_mods = _clean_modality_list(arch.get("input_modalities") or entry.get("input_modalities"))
    if not input_mods and str(api_type or "").strip().lower() == "anthropic":
        mid = str(model_id or entry.get("id") or "").lower()
        if mid.startswith("claude-") and not mid.startswith(("claude-2", "claude-instant")):
            input_mods = ["text", "image"]
    if input_mods:
        capabilities["input_modalities"] = input_mods
    output_mods = _clean_modality_list(arch.get("output_modalities") or entry.get("output_modalities"))
    if output_mods:
        capabilities["output_modalities"] = output_mods
    context_length = _positive_int(
        entry.get("context_length") or entry.get("max_model_len")
        or entry.get("max_context_length") or entry.get("context_window")
    )
    if context_length:
        capabilities["context_length"] = context_length
    reasoning = _parse_catalog_reasoning(entry)
    if reasoning:
        capabilities["reasoning"] = reasoning
    supported = entry.get("supported_parameters")
    if isinstance(supported, list) and supported:
        cleaned_supported = [str(item).strip() for item in supported if str(item).strip()]
        if cleaned_supported:
            capabilities["supported_parameters"] = cleaned_supported
    return capabilities if len(capabilities) > 1 else {}


def _parse_catalog_reasoning(entry: dict[str, Any]) -> dict[str, Any]:
    """OpenRouter 风格的能力块：是否强制思考、可选档位与默认档位。"""
    block = entry.get("reasoning") if isinstance(entry.get("reasoning"), dict) else {}
    parsed: dict[str, Any] = {}
    if "mandatory" in block:
        parsed["mandatory"] = bool(block.get("mandatory"))
    if "default_enabled" in block:
        parsed["default_enabled"] = bool(block.get("default_enabled"))
    efforts = block.get("supported_efforts")
    if isinstance(efforts, list):
        cleaned = [str(item).strip().lower() for item in efforts if str(item).strip()]
        if cleaned:
            parsed["supported_efforts"] = cleaned
    default_effort = str(block.get("default_effort") or "").strip().lower()
    if default_effort:
        parsed["default_effort"] = default_effort
    return parsed


def _find_catalog_entry(catalog: Any, model_id: str) -> dict[str, Any] | None:
    entries = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("id") or "") == str(model_id):
            return entry
    lowered = str(model_id or "").lower()
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("id") or "").lower() == lowered:
            return entry
    return None


# 单个候选地址的等待上限。探测会按不同写法串行试多个地址，
# 时间过长会直接卡住"保存模型"这次请求，所以收紧到 5 秒。
_CAPABILITY_PROBE_TIMEOUT_S = 5.0


def _catalog_urls(root: str, kind: str) -> list[str]:
    """厂商模型目录的常见写法。

    base_url 有的带 /v1 有的不带，还有的（如 DeepSeek 的 anthropic 兼容入口）
    把服务挂在子路径下——只试一种写法就会出现"明明配了 Key 却 404"。
    """
    root = str(root or "").strip().rstrip("/")
    if kind == "anthropic":
        candidates = [f"{root}/v1/models", f"{root}/models"]
    else:
        candidates = [f"{root}/models", f"{root}/v1/models"]
    if root.endswith("/v1"):
        candidates.append(f"{root[: -len('/v1')]}/models")
    # 不同兼容方言的目录位置不一样：DeepSeek 的 Anthropic 入口没有 /models，
    # 只有 OpenAI 入口有。用户把 API 类型选错时不该直接判"读不到"，所以最后
    # 再把另一种方言的写法也试一遍。
    alt_root = root
    if root.endswith("/anthropic"):
        alt_root = root[: -len("/anthropic")]
    alt = [f"{alt_root}/models", f"{alt_root}/v1/models"] if kind == "anthropic" else [f"{root}/v1/models"]
    candidates.extend(alt)
    urls: list[str] = []
    for url in candidates:
        if url not in urls:
            urls.append(url)
    return urls


def fetch_provider_catalog(
    base_url: str,
    api_type: str = "openai",
    api_key: str = "",
    timeout: float = _CAPABILITY_PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    """读取厂商的模型目录（``GET /models``）。

    厂商会随版本更新模型命名（DeepSeek 就把 deepseek-chat/reasoner 换成了
    deepseek-v4-*），所以"当前有哪些模型"必须以官方目录为准，不能靠代码里写死的
    名字猜。这里返回原始目录与最终使用的地址，供上层复用。
    """
    kind = "anthropic" if str(api_type or "").strip().lower() == "anthropic" else "openai"
    root = str(base_url or "").strip().rstrip("/")
    if not root:
        root = "https://api.anthropic.com" if kind == "anthropic" else "https://api.openai.com/v1"
    tried: list[str] = []
    last_error = ""
    for url in _catalog_urls(root, kind):
        tried.append(url)
        if kind == "anthropic":
            req = urllib.request.Request(
                url, headers={"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
            )
        else:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or ''}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            continue
        except Exception as e:
            last_error = str(e)
            continue
        if isinstance(payload, dict):
            return {"ok": True, "catalog": payload, "url": url, "tried_urls": tried}
    return {
        "ok": False,
        "error": f"catalog probe failed: {last_error or 'unreachable'}",
        "error_code": "catalog_unreachable",
        "message": (
            f"读不到厂商模型目录（{last_error or '无法连接'}）。"
            f"请检查 Base URL 是否需要以 /v1 结尾、API 类型是否与厂商一致；"
            f"目录读不到不影响使用，能力会按模型 ID 推断。"
        ),
        "tried_urls": tried,
    }


def list_provider_models(
    base_url: str,
    api_type: str = "openai",
    api_key: str = "",
    timeout: float = _CAPABILITY_PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    """给界面用：返回厂商当前实际提供的模型清单（含可识别的能力）。"""
    fetched = fetch_provider_catalog(base_url, api_type, api_key, timeout=timeout)
    if not fetched.get("ok"):
        return fetched
    entries = (fetched.get("catalog") or {}).get("data")
    if not isinstance(entries, list):
        return {
            "ok": False,
            "error": "catalog has no model list",
            "error_code": "catalog_empty",
            "message": "厂商目录格式无法识别（没有 models 列表）。",
        }
    models: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        caps = parse_catalog_capabilities(entry, model_id=str(entry.get("id")), api_type=api_type)
        effort = reasoning_profile({"id": entry.get("id"), "model": entry.get("id"), "capabilities": caps})
        models.append({
            "id": str(entry.get("id")),
            "name": str(entry.get("name") or entry.get("id")),
            "context_length": _positive_int(caps.get("context_length")) or None,
            "input_modalities": caps.get("input_modalities") or [],
            "reasoning_levels": effort.get("levels") or [],
        })
    models.sort(key=lambda item: item["id"])
    return {"ok": True, "models": models, "source_url": fetched.get("url"), "count": len(models)}


def probe_model_capabilities(
    model: str,
    api_type: str = "openai",
    base_url: str = "",
    api_key: str = "",
    timeout: float = _CAPABILITY_PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    """Ask the provider's model catalog what this model accepts.

    Best effort: network failures, auth errors and catalogs without modality
    metadata all return ``{"ok": False}`` — callers keep previously cached
    capabilities or fall back to model-ID heuristics. Never raises.
    """
    kind = "anthropic" if str(api_type or "").strip().lower() == "anthropic" else "openai"
    fetched = fetch_provider_catalog(base_url, api_type, api_key, timeout=timeout)
    if not fetched.get("ok"):
        return {k: v for k, v in fetched.items() if k != "tried_urls"} | {"tried_urls": fetched.get("tried_urls", [])}
    catalog = fetched.get("catalog")
    entry = _find_catalog_entry(catalog, str(model or ""))
    if entry is None:
        # 把目录里真实存在的模型 ID 带回去：否则用户只看到一句
        # "model not listed in provider catalog"，不知道模型 ID 到底错在哪
        entries = catalog.get("data") if isinstance(catalog, dict) else None
        available = [
            str(item.get("id"))
            for item in (entries if isinstance(entries, list) else [])
            if isinstance(item, dict) and item.get("id")
        ]
        return {
            "ok": False,
            # error 保持原来那句英文（对外契约），新增字段给界面做可操作提示
            "error": f"model not listed in provider catalog: '{model}'",
            "error_code": "catalog_missing_model",
            "message": (
                f"厂商目录里没有 '{model}' 这个模型 ID；"
                + (f"该目录有 {len(available)} 个模型，例如：{'、'.join(available[:4])}" if available else "该目录没有列出任何模型")
            ),
            "available_models": available[:12],
            "available_total": len(available),
        }
    capabilities = parse_catalog_capabilities(entry, model_id=str(model or ""), api_type=kind)
    # 模型在目录里就算成功：一些厂商（如 DeepSeek）只给 id，不给模态/上下文，
    # 这时候不该报错——按模型 ID 推断即可，报错反而像"这个模型不能用"。
    return {
        "ok": True,
        "capabilities": capabilities,
        "catalog_metadata": bool(capabilities),
        "message": "" if capabilities else "厂商目录只提供了模型 ID，模态/上下文按模型 ID 推断",
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
        from src.paths import AGENT_HOME

        self.path = path or AGENT_HOME / "models.json"
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
            _catalog_capabilities(model),
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
            "capabilities": _catalog_capabilities(model),
            "reasoning_effort": str(model.get("reasoning_effort") or ""),
            "thinking_mode": str(model.get("thinking_mode") or ""),
            "reasoning": reasoning_profile(model),
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
        _apply_thinking_settings(model)
        self._models.append(model)
        if not self._default_id:
            self._default_id = model["id"]
        self._save()
        return model

    def update(self, model_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        for m in self._models:
            if m.get("id") == model_id:
                for key, value in updates.items():
                    if key in _THINKING_ALLOWED:
                        value = sanitize_thinking_value(key, value) or None
                    if value is None:
                        m.pop(key, None)
                    else:
                        m[key] = value
                self._save()
                return m
        raise ValueError("model not found")

    def set_capabilities(self, model_id: str, capabilities: dict[str, Any]) -> dict[str, Any]:
        """Cache provider-catalog capabilities on a model entry."""
        for m in self._models:
            if m.get("id") == model_id:
                m["capabilities"] = _catalog_capabilities({"capabilities": capabilities})
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


def _as_bool(value: Any) -> bool:
    """严格布尔解析。

    模型在 JSON 模式下经常把布尔值写成字符串，而 bool("false") 是 True：
    {"is_complete": "false"} 会被判成"任务已完成"（一个动作都不执行就收工），
    {"target_found": "false"} 会被判成"目标已确认"（于是照着一个没确认的目标
    继续抵近）。两个方向都是危险的，所以只认真正的真值表示。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on", "是", "真"}
    return False


def _finite_float(value: Any) -> float | None:
    """float(value)，非数字或非有限值时返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unit_or_none(value: Any) -> float | None:
    """归一化尺寸字段（bbox_hint 的 w/h）：0~1 之外或非数字一律当"没给"。

    视觉模型回的尺寸是粗值，但"粗"不等于"可以随便"：负数、百分比写法、超界
    的值都是解析错误而不是"大目标"，直接丢掉比传下去安全。
    """
    number = _finite_float(value)
    if number is None or number <= 0.0:
        return None
    return round(min(1.0, number), 3)


# 轨迹摘要保留的步数上限（一行一步，最老的先丢）。见 _steps_digest。
_STEPS_DIGEST_LIMIT = 24

# 摘要里值得留的"关键数值"字段：距离 / 坐标 / 成败 / 状态。其余（图片、逐点深度
# 数组、原始结构）对"下一步做什么"没有价值。
_DIGEST_VALUE_KEYS = (
    "distance_median_m",
    "depth_m",
    "world_pos",
    "target_found",
    "status",
    "message",
    "reached",
)


def _steps_digest(results: list[Any], limit: int = _STEPS_DIGEST_LIMIT) -> list[str]:
    """把已执行的每一步压成一行：`序号. 工具 ok (参数; 关键数值)`。

    循环模型每轮都要知道"前面做过什么"，而完整结果会随步数线性膨胀。这里只留
    工具名、成败、至多两个入参和两个关键数值；图片与大字结构一律不进摘要（最近
    几步的完整形状由 ``_compact_loop_result`` 单独负责）。这是 Pi Harness 的
    "压缩成摘要而不是丢掉历史"在无 LLM 条件下的确定性版本。

    ``limit`` 由调用方按上下文压力给（见 ``_compact_loop_state`` 的 tight 模式）。
    """
    lines: list[str] = []
    for index, row in enumerate(results or [], 1):
        if not isinstance(row, dict):
            continue
        tool = str(row.get("tool") or "?")
        ok = "ok" if row.get("ok") else "FAIL"
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        bits: list[str] = []
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        if params:
            shown = {key: params[key] for key in list(params)[:2]}
            bits.append(f"params={json.dumps(shown, ensure_ascii=False, default=str)[:44]}")
        for key in _DIGEST_VALUE_KEYS:
            value = data.get(key)
            if value in (None, "", [], {}):
                continue
            text = json.dumps(value, ensure_ascii=False, default=str)
            bits.append(f"{key}={text[:44]}")
            if len(bits) >= 3:
                break
        lines.append(f"{index}. {tool} {ok}" + (f" ({'; '.join(bits)})" if bits else ""))
    return lines[-max(1, int(limit)):]


# 上下文吃紧时的收紧档：摘要行数与完整结果条数都减半再减半。
_STEPS_DIGEST_TIGHT_LIMIT = 8
_RECENT_RESULTS_DEFAULT = 3
_RECENT_RESULTS_TIGHT = 1

# 上下文压力阈值：上一轮 provider 报的用量超过窗口这个比例，就切到收紧档，
# 并让运行层发一条可见事件（Pi 的 shouldCompact 的确定性版本：只压缩，不调模型）。
_CONTEXT_PRESSURE_RATIO = 0.7

# "任务类型"过滤的安全边界：这些工具与链路/安全/基本机动有关，任何任务都保留。
# 其余工具只在"被技能声明或被实际用过"时才进提示词（见 _relevant_tool_cards）。
_ALWAYS_AVAILABLE_TOOLS = {
    "drone_connect",
    "drone_get_status",
    "drone_arm",
    "drone_takeoff",
    "drone_land",
    "drone_hover",
    "drone_move_relative",
    "drone_rotate_to",
    "drone_fly_to",
    "drone_fly_path",
    "locate_image_point",
    "inspect_current_frame",
    "perception_status",
}


def _extract_json(text: str) -> str:
    """从模型回复里取出 JSON 主体。

    以前只在文本"以 ``` 开头"时剥围栏——名字叫 extract 却并不提取。模型回复
    前面带一句解释、或者写成单行 ```json{...}``` 都会让 json.loads 失败，而
    这是确定性失败：重试三次照样失败，然后要么降级到规则决策（可能选出不同的
    飞行动作），要么直接把 run 判为 LLM 不可用。这里改成先剥围栏，再从第一个
    { 或 [ 取到最后一个对应的 } 或 ]。
    """
    text = (text or "").strip()
    if text.startswith("```"):
        # 去掉首行围栏标记（``` 或 ```json），以及结尾的 ```
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline >= 0 else text
        text = text.strip()
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()
    # 单行 ```json{...}``` 剥完围栏后可能仍是 ```json{...} 的形式
    if text.startswith("```"):
        text = text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()

    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        return text
    start = min(starts)
    closing = "}" if text[start] == "{" else "]"
    end = text.rfind(closing)
    if end <= start:
        return text
    return text[start : end + 1]


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
        # 推理内容在不同网关下的字段名不一样：DeepSeek 风格是 reasoning_content，
        # 另一些是 reasoning / thinking。只认一个名字的后果是"模型明明想了，界面
        # 一片空白"（实测反馈）。三个都读。
        self.last_reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("thinking")
            or ""
        )
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
        # 推理内容在不同网关下的字段名不一样：DeepSeek 风格是 reasoning_content，
        # 另一些是 reasoning / thinking。只认一个名字的后果是"模型明明想了，界面
        # 一片空白"（实测反馈）。三个都读。
        self.last_reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("thinking")
            or ""
        )
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


class LLMEmptyResponseError(LLMUnavailableError):
    """模型返回了空回复：既没有内容，也没有工具调用。

    单独成一个类型，是为了让调用方**重试**它：这类空回复多为瞬时（内容过滤、
    首 token 前截断、服务端抖动），和"key 没配 / 模型不存在"这类持久故障不同。
    实测教训：一次空回复在没有重试的情况下直接把一次已经起飞的悬停任务送进了
    失败流程并降落。
    """


class LLMMissionPlanner:
    """LLM-first planner with rule-based fallback and per-request model selection."""

    # 空回复（既无内容也无工具调用）的重试次数，见 _native_decision 与
    # LLMEmptyResponseError。
    _NATIVE_EMPTY_RETRIES = 2

    def __init__(self, config: LLMConfig | None = None, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.legacy_config = config
        self.fallback = MissionPlanner()
        self.last_error = ""
        self.last_usage: dict[str, Any] = {}
        self.token_meter = TokenMeter()
        # 上下文压力（上一轮实测 prompt 用量超窗口阈值）：非空时说明本轮已切到
        # 收紧档，运行层据此发一条可见事件，见 _context_pressure()。
        self.last_context_pressure: dict[str, Any] | None = None

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
        """Call ``chat_json`` with retries for recoverable outputs.

        Slow models often return an empty/truncated response first; a short
        backoff (0.35s) then retries into the same still-running window and
        fails a second time. Use a longer, per-attempt backoff so the retry
        lands after the model has actually finished generating."""
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                return client.chat_json(messages)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable_llm_error(exc):
                    raise
                time.sleep(1.5 * (attempt + 1))
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

    def _fit_sections(
        self,
        config: dict[str, Any],
        sections: list[dict[str, Any]],
        images: int = 0,
        window_scale: float = 1.0,
    ) -> dict[str, str]:
        """Fit prompt sections to the context budget, optionally scaled down.

        ``window_scale < 1`` is used by the context-overflow recovery path: the
        provider's real window is smaller than our estimate, so the whole
        budget is tightened instead of only the low-priority sections.
        """
        budget = self._context_budget(config)
        if window_scale < 1.0:
            budget = ContextBudget(
                context_window=max(1024, int(budget.context_window * window_scale)),
                output_reserve=2048,
                meter=self._token_meter(),
            )
        budget = budget.with_reserve(images * IMAGE_TOKEN_QUOTA)
        fitted = budget.fit(sections)
        # 记下这次实际用的预算对象：溢出重试前要据此判断"减半是否有意义"
        # （必需段自己就超预算时，减半不可能生效）。
        self._last_budget = budget
        return fitted

    def _chat_with_overflow_recovery(
        self,
        client: Any,
        build_messages: Callable[[float], list[dict[str, Any]]],
        call: Callable[[Any, list[dict[str, Any]]], tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Call the LLM, retrying once with a halved context budget when the
        provider reports a context-length overflow.

        ``call`` receives ``(client, messages)`` — the same shape as
        ``client.chat_json(messages)`` / ``client.chat_tools(...)`` so a
        bound method like ``self._chat_json_with_retries`` (which takes
        ``client`` first) can be passed directly.
        """
        try:
            return call(client, build_messages(1.0))
        except Exception as exc:
            if not is_context_overflow_error(exc):
                raise
            budget = getattr(self, "_last_budget", None)
            if budget is not None and getattr(budget, "essential_overflow", 0) > 0:
                # 指令/观察段自己就超了预算，减半 context_window 不可能削减它们，
                # 这一次重试注定以同样的溢出失败告终——直接说明原因，别浪费一次
                # 昂贵的往返再报一个更难懂的错。
                self.last_error = (
                    "context overflow: 指令/观察等必需段本身超出上下文预算 "
                    f"约 {budget.essential_overflow} tokens，无法通过裁剪其它段解决"
                )
                raise LLMUnavailableError(self.last_error) from exc
            self.last_error = f"context overflow; retrying with a tighter budget: {exc}"
            return call(client, build_messages(0.5))

    def _stream_plan(
        self,
        client: Any,
        build_messages: Callable[[float], list[dict[str, Any]]],
        on_reasoning: Callable[[str], None],
        window_scale: float = 1.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Plan through the streaming endpoint so reasoning tokens are
        delivered to the operator while the model thinks.

        Falls back to nothing itself: empty content / parse errors raise so
        callers can retry or degrade to the non-streaming path.
        """
        content: list[str] = []
        for event in _stream_events_with_first_chunk_retry(client, build_messages(window_scale)):
            token = str(event.get("token") or "")
            if not token:
                continue
            if event.get("type") == "reasoning":
                on_reasoning(token)
            else:
                content.append(token)
        text = "".join(content).strip()
        if not text:
            raise RuntimeError("LLM returned empty content")
        try:
            parsed = json.loads(_extract_json(text))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM JSON parse failed: {e}: {text[:300]}") from e
        return parsed, {}

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
            _catalog_capabilities(config),
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
        on_reasoning: Callable[[str], None] | None = None,
    ) -> MissionPlan:
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)

        available_tools = self._compact_tools(tools)
        capability_payload = capabilities or {}
        card_payload = self._compact_tool_cards(tool_cards or [])
        images = self._image_attachments(attachments)
        sections = [
            {"key": "operator_command", "value": command, "priority": "command"},
            {"key": "output_schema", "value": json.dumps(self._schema_hint(), ensure_ascii=False, default=str), "priority": "command"},
            {"key": "backend", "value": str(backend or ""), "priority": "observation"},
            {"key": "backend_capabilities", "value": json.dumps(capability_payload, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "safety_constraints", "value": json.dumps(safety, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "current_telemetry", "value": json.dumps(telemetry or {}, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "agent_state", "value": json.dumps(agent_state or {}, ensure_ascii=False, default=str), "priority": "observation"},
            {"key": "available_tools", "value": json.dumps(available_tools, ensure_ascii=False, default=str), "priority": "tool_cards"},
            {"key": "available_tool_cards", "value": json.dumps(card_payload, ensure_ascii=False, default=str), "priority": "tool_cards"},
            {
                "key": "skill_guidance",
                "value": json.dumps(self._compact_skill_guidance((agent_state or {}).get("skill_guidance") or []), ensure_ascii=False, default=str),
                "priority": "guidance",
            },
            {"key": "memory_snapshot", "value": self._dumps_safe(self._compact_memory(memory)), "priority": "memory"},
            {
                "key": "conversation_context",
                "value": json.dumps(self._compact_conversation(conversation_context or []), ensure_ascii=False, default=str),
                "priority": "memory",
            },
        ]
        sent_messages: list[list[dict[str, Any]]] = []

        def build_messages(window_scale: float = 1.0) -> list[dict[str, Any]]:
            payload_parts = self._sections_to_payload(self._fit_sections(config, sections, len(images), window_scale))
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": self._content_with_images(
                        json.dumps(payload_parts, ensure_ascii=False, indent=2, default=str),
                        attachments or [],
                    ),
                },
            ]
            sent_messages.append(messages)
            return messages

        try:
            client = _create_client(config)
            if on_reasoning is not None:
                # streamed planning: reasoning tokens reach the operator UI
                # while the model thinks (execute mode otherwise shows only
                # the static "正在理解指令..." placeholder for minutes)
                try:
                    parsed, usage = self._stream_plan(client, build_messages, on_reasoning)
                except Exception as exc:
                    if not is_context_overflow_error(exc):
                        # stream failed mid-flight (rare): fall back to the
                        # non-streaming path so planning is not lost
                        parsed, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
                    else:
                        self.last_error = f"context overflow; retrying with a tighter budget: {exc}"
                        parsed, usage = self._stream_plan(client, build_messages, on_reasoning, window_scale=0.5)
                self.last_error = ""
                self.last_usage = usage
                self._record_usage(sent_messages[-1] if sent_messages else [], usage)
                plan = self._plan_from_payload(command, parsed, {t["name"] for t in available_tools})
                if config:
                    plan.planner_source = str(config.get("provider", "llm"))
                    plan.planner_model = str(config.get("model", ""))
                return plan
            payload, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
            self.last_error = ""
            self.last_usage = usage
            self._record_usage(sent_messages[-1] if sent_messages else [], usage)
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
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        fallback_enabled: bool = True,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> LoopDecision:
        """Choose the next action for a lightweight observe-decide-act loop.

        Uses the native function-calling protocol when the selected model
        supports it. The protocol is deliberately single-round and stateless:
        tool results are folded into the next observation JSON instead of being
        appended as tool messages, so native and JSON modes can interleave
        without history-format conflicts. On ToolCallUnsupportedError the
        planner degrades to JSON-schema prompting; other model errors fall back
        to the rule decision unless require_llm is set (or fallback_enabled is
        False, used by sub-agents, in which case errors always raise).
        """

        allowed_tools = self._loop_action_names(tool_cards)
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            if require_llm:
                message = self._chat_unavailable_message(config, "missing_api_key")
                self.last_error = message
                raise LLMUnavailableError(message)
            return self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})

        prompt = system_prompt or self._loop_decision_system_prompt()
        images = self._image_attachments(attachments)
        # 原生工具调用模式：工具名/描述/参数 schema 已经由 API 的 tools 参数下发，
        # 再塞一份完整的 available_tool_cards（实测 ~5.8k tokens/轮）是纯重复。
        # 这符合"能力声明只发一次、历史才需要压缩"的原则（借 Pi Harness 的上下文
        # 设计）。JSON 模式下 tools 参数不可用，仍需要精简过的卡片。
        native = self._config_native_tools(config)
        # 上下文压力（上一轮实测用量）+ 按任务类型收窄的提示词卡片。
        # 注意 allowed_tools 仍是**全量**卡片算出来的（见上方注释）：收窄只影响
        # 菜单，不影响"模型叫得出名字就允许执行"。
        tight, pressure = self._context_pressure(config)
        self.last_context_pressure = pressure or None
        prompt_cards = self._relevant_tool_cards(tool_cards, loop_state)
        sections = [
            {"key": "operator_command", "value": command, "priority": "command"},
            {"key": "output_schema", "value": json.dumps(self._loop_decision_schema_hint(), ensure_ascii=False, default=str), "priority": "command"},
            {"key": "observation", "value": self._dumps_safe(self._compact_observation(observation)), "priority": "observation"},
            {"key": "backend_capabilities", "value": self._dumps_safe(capabilities or {}), "priority": "observation"},
            {"key": "loop_state", "value": self._dumps_safe(self._compact_loop_state(loop_state, tight=tight)), "priority": "recent"},
        ]
        if not native:
            sections.append(
                {"key": "available_tool_cards", "value": self._dumps_safe(self._compact_tool_cards(prompt_cards)), "priority": "tool_cards"}
            )
        sections.extend(
            [
                {"key": "skill_guidance", "value": json.dumps(self._compact_skill_guidance(skill_guidance or []), ensure_ascii=False, default=str), "priority": "guidance"},
                {"key": "memory_snapshot", "value": json.dumps(self._compact_memory(memory), ensure_ascii=False, default=str), "priority": "memory"},
            ]
        )
        if conversation_context:
            sections.append(
                {
                    "key": "conversation_context",
                    "value": json.dumps(self._compact_conversation(conversation_context), ensure_ascii=False, default=str),
                    "priority": "memory",
                }
            )
        sent_messages: list[list[dict[str, Any]]] = []

        def build_messages(window_scale: float = 1.0) -> list[dict[str, Any]]:
            payload = self._sections_to_payload(self._fit_sections(config, sections, len(images), window_scale))
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": self._content_with_images(
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        attachments or [],
                    ),
                },
            ]
            sent_messages.append(messages)
            return messages

        def fallback(error: Exception) -> LoopDecision:
            if not fallback_enabled:
                raise LLMUnavailableError(f"Agent Loop LLM decision failed: {error}") from error
            decision = self._fallback_loop_decision(command, loop_state, observation, allowed_tools, capabilities or {})
            message = str(error)
            if decision.reflection:
                decision.reflection = f"{decision.reflection} LLM fallback: {message}"
            else:
                decision.reflection = f"LLM fallback: {message}"
            return decision

        openai_tools = self._loop_tool_schemas(prompt_cards, tools) if native else []
        if native and openai_tools:
            try:
                decision = self._native_decision(config, build_messages(1.0), openai_tools, allowed_tools)
                self._record_usage(sent_messages[-1] if sent_messages else [], self.last_usage)
                return decision
            except ToolCallUnsupportedError as exc:
                self.last_error = f"native tool calling unavailable; using JSON mode: {exc}"
            except Exception as e:
                if is_context_overflow_error(e):
                    # tighter budget, then the JSON path below as the final fallback
                    try:
                        decision = self._native_decision(config, build_messages(0.5), openai_tools, allowed_tools)
                        self._record_usage(sent_messages[-1] if sent_messages else [], self.last_usage)
                        return decision
                    except ToolCallUnsupportedError as exc:
                        self.last_error = f"native tool calling unavailable; using JSON mode: {exc}"
                    except Exception as e2:
                        self.last_error = str(e2)
                        if require_llm and fallback_enabled:
                            raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e2}") from e2
                        return fallback(e2)
                self.last_error = str(e)
                if require_llm and fallback_enabled:
                    raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e}") from e
                return fallback(e)
        try:
            client = _create_client(config)
            parsed, usage = self._chat_with_overflow_recovery(client, build_messages, self._chat_json_with_retries)
            self.last_error = ""
            self.last_usage = usage
            self._record_usage(sent_messages[-1] if sent_messages else [], usage)
            return self._decision_from_payload(parsed, allowed_tools)
        except Exception as e:
            self.last_error = str(e)
            if require_llm and fallback_enabled:
                raise LLMUnavailableError(f"Agent Loop LLM decision failed: {e}") from e
            return fallback(e)

    def _image_attachments(self, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [
            item
            for item in (attachments or [])[:4]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]

    def _context_budget(self, config: dict[str, Any]) -> ContextBudget:
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
            _catalog_capabilities(config),
        )
        context_window = int(config.get("context_window") or inferred.get("context_window") or 64000)
        return ContextBudget(context_window=context_window, output_reserve=2048, meter=self._token_meter())

    def _token_meter(self) -> TokenMeter:
        meter = getattr(self, "token_meter", None)
        if meter is None:
            meter = TokenMeter()
            self.token_meter = meter
        return meter

    def _record_usage(self, messages: list[dict[str, Any]], usage: dict[str, Any], images: int = 0) -> None:
        """Recalibrate the token meter from the provider's real prompt count."""
        try:
            estimated = estimate_messages(messages, images=images)
            actual = int((usage or {}).get("prompt_tokens") or 0)
            self._token_meter().recalibrate(estimated, actual)
        except Exception:
            pass

    @staticmethod
    def _sections_to_payload(fitted: dict[str, str]) -> dict[str, Any]:
        """Rebuild a prompt payload dict from budgeted section strings.

        Truncated sections may no longer be valid JSON; they are passed through
        as raw text so the model still sees the available information.
        """
        payload: dict[str, Any] = {}
        for key, text in fitted.items():
            if not text or text == "[omitted]":
                payload[key] = text
                continue
            try:
                payload[key] = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                payload[key] = text
        return payload

    def _config_native_tools(self, config: dict[str, Any] | None) -> bool:
        """Per-model gate for the native function-calling path."""
        if not config:
            return False
        explicit = config.get("native_tools")
        if explicit is not None:
            return bool(explicit)
        inferred = infer_model_capabilities(
            str(config.get("model") or ""),
            str(config.get("provider") or ""),
            str(config.get("capability_mode") or "auto"),
        )
        return bool(inferred["native_tools"])

    def _loop_tool_schemas(
        self,
        tool_cards: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Synthesize function-calling schemas from tool cards + runtime specs."""
        specs = {str(t.get("name") or ""): t for t in (tools or []) if isinstance(t, dict)}
        schemas: list[dict[str, Any]] = []
        for card in tool_cards[:40]:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "")
            if not name:
                continue
            spec = specs.get(name)
            parameters = spec.get("parameters") if spec else None
            inputs = card.get("inputs") if isinstance(card.get("inputs"), dict) else {}
            schema = tool_schema_from_spec(name, parameters or {}, inputs)
            description = str(card.get("purpose") or (spec or {}).get("description") or name)
            schemas.append(function_tool_schema(name, description, schema))
        return schemas

    def _native_decision(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        openai_tools: list[dict[str, Any]],
        allowed_tools: set[str],
    ) -> LoopDecision:
        client = _create_client(config)
        if str(config.get("api_type") or "openai") == "anthropic":
            tools_payload = openai_tools_to_anthropic(openai_tools)
        else:
            tools_payload = openai_tools
        # 空回复重试：模型偶发"既无内容也无工具调用"（内容过滤/首 token 截断/抖动）。
        # 一次就判失败会把已经起飞的任务直接推进失败流程——重试两次再放弃。
        for attempt in range(self._NATIVE_EMPTY_RETRIES + 1):
            tool_calls, text, usage = client.chat_tools(messages, tools_payload)
            # surface the model's decision rationale (reasoning_content) for the
            # operator's event stream
            self.last_reasoning = str(getattr(client, "last_reasoning", "") or "")
            self.last_usage = usage
            try:
                decision = self._decision_from_tool_calls(tool_calls, text, allowed_tools)
            except LLMEmptyResponseError:
                if attempt >= self._NATIVE_EMPTY_RETRIES:
                    raise
                self.last_error = f"empty response, retry {attempt + 1}/{self._NATIVE_EMPTY_RETRIES}"
                continue
            self.last_error = ""
            return decision
        raise LLMEmptyResponseError("native tool-calling model returned an empty response (no content, no tool calls)")

    def _decision_from_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        text: str,
        allowed_tools: set[str],
    ) -> LoopDecision:
        """Native protocol: tool calls => actions; no tool calls => complete.

        Unavailable tool calls are skipped (noted in reflection) instead of
        failing the whole turn — one hallucinated name must not kill the run.
        """
        if not tool_calls:
            # 空回复不能当成"任务完成"。原生协议下 content 为空且没有 tool_calls
            # 是内容过滤、拒答、首 token 前截断这类情况的典型形状；以前直接返回
            # is_complete=True，于是 run 被标成 completed、summary 就是 "complete"，
            # 而一个动作都没执行过。有文字说明才接受隐式完成。
            summary = (text or "").strip()
            if not summary:
                raise LLMEmptyResponseError(
                    "模型返回了空回复（没有内容、也没有工具调用），已自动重试仍为空。"
                    "这通常是内容过滤、首 token 前截断或模型服务抖动——请重试；"
                    "若持续出现，检查该模型服务是否可用。"
                    " [native tool-calling model returned an empty response (no content, no tool calls)]"
                )
            return LoopDecision(action="", reason=summary, is_complete=True)
        decisions: list[LoopDecision] = []
        rejected: list[str] = []
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            if name not in allowed_tools:
                rejected.append(name)
                continue
            decisions.append(
                LoopDecision(
                    action=name,
                    params=dict(arguments),
                    # 模型只回了 tool_call、没有正文时 reason 保持为空：合成一个
                    # "Call <tool>" 会让时间线把工具名当成模型思考显示出来
                    # （调用方用 decision.action 兜底，见 runtime 的 thought_trace）。
                    reason=text.strip(),
                    is_complete=False,
                )
            )
        if not decisions:
            if rejected:
                return LoopDecision(
                    action="",
                    reason=f"Model selected unavailable tools: {', '.join(rejected)}",
                    is_complete=False,
                    reflection="Stopped before executing unavailable tools.",
                )
            return LoopDecision(action="", reason=text.strip() or "complete", is_complete=True)
        main = decisions[0]
        if rejected:
            main.reflection = f"skipped unavailable tool calls: {', '.join(rejected)}"
        main.parallel_actions = [item.to_dict() for item in decisions[1:]]
        return main

    def confirm_target_in_image(
        self,
        target_description: str,
        image_base64: str,
        context: dict[str, Any] | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Use the configured model to verify a target in one image.

        与规划同一个模型：调用方传当前任务的 model_id，不能留空去回落默认值。
        """
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            message = self._multimodal_required_message(config, "进行图像目标确认")
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
                "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null", "w": "rough 0..1 object width or null", "h": "rough 0..1 object height or null"},
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
                    "bbox_hint 的 x/y（目标中心）与 w/h（目标宽高占画面的比例）都要给出，"
                    "归一化 0~1、粗估即可——它们会被用来算距离和朝向，缺失会让后续动作无法执行。"
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
        """Use the configured model for open-ended image understanding.

        规划与看图共用同一个模型：调用方必须传当前任务的 model_id。
        """
        config = self._resolve_config(model_id)
        if not self._enabled(config):
            message = self._chat_unavailable_message(config, "missing_api_key")
            self.last_error = message
            raise LLMUnavailableError(message)
        if not self._config_supports_multimodal(config):
            message = self._multimodal_required_message(config, "分析图像")
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
                        "bbox_hint": {"x": "rough 0..1 center x or null", "y": "rough 0..1 center y or null", "w": "rough 0..1 object width or null", "h": "rough 0..1 object height or null"},
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
                    "Return ONE JSON object with these exact top-level keys: "
                    "summary_zh, visible_objects, target_candidates, navigation_hint, safety_notes. "
                    "Do NOT echo the request payload and do NOT wrap the answer in output_schema/context; "
                    "put your Chinese operator-facing answer directly in summary_zh. "
                    "For every target candidate give bbox_hint x/y (centre) AND w/h (object width/height as a "
                    "fraction of the frame), normalised 0..1 — rough is fine, they are used to compute distance "
                    "and heading, and a missing size makes the next move impossible to size. Return JSON only."
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
            except Exception as exc:
                text, usage = client.chat_text(messages, max_tokens=900)
                import logging

                logging.getLogger(__name__).warning(
                    "vlm_json_retry_failed error=%s raw=%s", str(exc)[:200], text[:500]
                )
                parsed = json.loads(_extract_json(text))
            self.last_error = ""
            self.last_usage = usage
            if not str((parsed or {}).get("summary_zh") or "").strip():
                # 模型返回了 JSON 但没有 summary_zh（或返回了别的结构）——把原始
                # 结构落日志，避免前端只看到"没有返回明确的图像描述"却无从排查。
                import logging

                logging.getLogger(__name__).warning(
                    "vlm_missing_summary keys=%s raw=%s",
                    list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
                    json.dumps(parsed, ensure_ascii=False)[:500] if isinstance(parsed, (dict, list)) else str(parsed)[:500],
                )
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
        readonly_tools: list[dict[str, Any]] | None = None,
        execute_readonly_tool: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        on_tool_call: Callable[[str], None] | None = None,
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
                    "你可以调用提供的只读查询工具（状态/车辆列表）获取实时数据；"
                    "凡是涉及当前无人机状态、位置、数量的问题，必须先调用工具读取实时数据再回答，禁止仅凭上下文推测。"
                    "必须基于提供的信息作答，不要编造后端连接、车辆状态、位置或传感器结果。"
                    "可以参考 memory_snapshot.guidance 中的偏好和风险提示，但不要把历史任务当成当前事实。"
                    "中文回答，简洁自然。思考过程（reasoning）也用中文。"
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

        # Read-only tool pre-round: let the model pull live data (status /
        # vehicle list) through function calling, fold the results back into
        # the context, then answer. Chat still never executes control tools —
        # the whitelist is enforced by the caller.
        if readonly_tools and execute_readonly_tool:
            try:
                probe_client = _create_client(config)
                tool_calls, _text, _usage = probe_client.chat_tools(messages, readonly_tools)
            except Exception:
                tool_calls = []
            executed: list[str] = []
            for call in (tool_calls or [])[:3]:
                name = str(call.get("name") or "").strip()
                if not name:
                    continue
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                if on_tool_call:
                    try:
                        on_tool_call(name)
                    except Exception:
                        pass
                try:
                    result = execute_readonly_tool(name, args)
                except Exception as exc:
                    result = {"ok": False, "data": {"status": "error", "message": str(exc)[:200]}}
                executed.append(f"{name}: {json.dumps(result, ensure_ascii=False, default=str)[:1600]}")
            if executed:
                messages.append({
                    "role": "user",
                    "content": "[只读工具实时查询结果]\n" + "\n".join(executed) + "\n请基于以上实时数据回答用户问题。",
                })

        chunks: list[str] = []
        try:
            client = _create_client(config)
            stream_events = getattr(client, "stream_events", None)
            if callable(stream_events):
                for event in _stream_events_with_first_chunk_retry(client, messages):
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
                    "你是无人机任务助手，向操作员汇报任务执行结果。"
                    "基于给定的 telemetry 和 tool_results 作答，不要编造传感器数据。"
                    "用中文自然地总结任务执行情况与当前状态，风格自由发挥，"
                    "不要套用固定格式或模板，给出对操作员真正有用的总结即可。"
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
            "MULTI-VEHICLE RULE: when the operator's command targets several or every vehicle (全部/所有/每架/三架无人机/机群), set vehicle_name='all' on EVERY flight step (arm, takeoff, move, land, hover) so all vehicles act together; a missing or empty vehicle_name only moves the default (first) vehicle. After multi-vehicle actions, add a readback step and verify EVERY vehicle reached the expected state before reporting success. "
            "SKILL RULE (do this first): skill_guidance lists available skills, and EVERY skill IS a callable action named skill:<name> — it appears in available_tool_cards too. "
            "When a skill's description matches the operator's task, the FIRST step of your plan MUST be that skill activation ({\"tool\": \"skill:<name>\", \"params\": {}}); it returns the full operating steps for this kind of task, and you then plan the native tool steps accordingly. "
            "Do not merely read the guidance as background text: activating the skill is what loads the detail. Skipping the activation means planning from memory and missing the preconditions and coordinate rules it carries. "
            "For visual tasks, follow any relevant Markdown guidance, then use inspect_current_frame for image questions (open-ended descriptions and named-target confirmation); save_frame=true 可同时留存证据帧（拍照已并入该工具）。 "
            "For geometric path requests such as square, rectangle, orbit, circle, grid, 正方形, 矩形, 绕圈, or 航线, prefer drone_fly_path when available. Compute conservative local NED waypoints from the latest observed position and avoid rotate-to-heading plus repeated drone_move_relative unless the operator specifically asks to point the camera. "
            "Respect backend_capabilities and available_tool_cards. Never plan tools whose required capabilities are unavailable. "
            "Markdown skills are references, not executable shortcuts; do not prefer kind=skill unless a real executable skill card is explicitly available. "
            "For vague movement or scan requests, choose conservative distance, altitude, and velocity instead of expanding the mission. "
            "Treat kind=atomic tools as one direct operation. Tools with execution_mode=async only start an operation; the runtime owns task_id polling, timeout, cancellation, and terminal outcome. "
            "If a requested capability is unavailable, produce a safe status/readback plan and explain the limitation in risk_notes. "
            "The plan summary must state concrete numbers: vehicle count, coordinates, or altitude reached — never a bare '已完成/任务完成'. "
            # 输出纪律：规划模型的 reasoning 常花大量篇幅纠结输出格式/是否用循环，
            # 既慢又没信息量。这里明确约束，减少无效推理与重复步骤。
            "PLAN SHAPE (hard rules): at most ONE drone_takeoff and ONE drone_land per plan. "
            "Never place drone_land before the target is confirmed: for search/approach tasks the landing step must be the LAST step, after the approach steps. "
            "The plan is a CHAIN: the runtime executes its steps without asking the model after each one, so put deterministic steps (status/arm/takeoff/rotate) into the plan and only mark genuinely visual steps needs_observation. "
            "goal.success_criteria is MANDATORY and must be machine-checkable: for search/approach tasks include "
            "{\"metric\": \"target_confirmed\"} and {\"metric\": \"target_distance\", \"max_m\": 3.0}; if the operator asks to land, also add {\"metric\": \"landed\"}. "
            "The runtime will NOT invent criteria for you. "
            "OUTPUT DISCIPLINE: do not deliberate about the output format, tool list, or JSON schema in reasoning — the schema is fixed. "
            "Do NOT unroll loops: list a step ONCE even if it repeats (the runtime's agent loop handles repetition); 跟踪/搜索类任务只写一轮典型步骤，不要重复 2~3 遍。 "
            "Keep reasoning to 2-4 short sentences. Keep steps <= 12. Return JSON only. "
            "Think and reason in Chinese (中文思考)."
        )

    def _schema_hint(self) -> dict[str, Any]:
        return {
            "intent": "short snake_case intent",
            "summary": "one concise Chinese summary",
            "reasoning": "REQUIRED. 2-4 句中文规划理由：任务理解、关键决策依据（为什么选这些工具/参数）",
            "assumptions": ["operator-visible assumptions"],
            "risk_notes": ["safety or ambiguity notes"],
            "execution_mode": (
                "auto | agent_loop. Use agent_loop when the task requires an "
                "observe-respond cycle that a fixed sequence cannot express: "
                "visual search/confirm targets, tracking, or any step whose "
                "outcome decides the next action. Use auto for fixed sequences "
                "such as takeoff -> waypoints -> land."
            ),
            "keep_reacting": (
                "true when the task has no natural stopping point after the listed "
                "steps — continuous tracking, shadowing, keeping a target locked "
                "until the operator says otherwise, OR when the operator's stopping "
                "condition is a QUALITY judged from fresh observations rather than "
                "a step count (\"直到满意\", \"够近为止\", \"看清楚为止\", \"一直盯着\", "
                "\"反复检查直到...\"). In that case list the first cycle of steps and "
                "set keep_reacting=true: the model keeps observing and acting until it "
                "judges the condition met, then reports. One-shot tasks (take a photo, "
                "fly to a point, inspect once and report, a single approach-and-confirm "
                "pass) are false: the runtime should converge once the steps are done."
            ),
            "steps": [
                {
                    "label": "Chinese step label",
                    "tool": "one available tool name",
                    "layer": "tool|planning|perception|action|memory|safety",
                    "params": {"name": "value"},
                    "needs_observation": (
                        "true when this step's result must be observed before "
                        "the next step can be chosen (photo/VLM steps)"
                    ),
                }
            ],
        }

    def _loop_decision_system_prompt(self) -> str:
        return (
            "You are the decision layer of a UAV Agent Loop using ReAct / plan-execute-observe. "
            "At each step, choose exactly one next FLIGHT action (or mark the task complete once the latest observation proves the goal is handled) AND, when useful, chain up to 2 read-only calls to verify it in the SAME response. "
            "The actions array runs after the main action, in order, and all results come back together in one observation — this saves a round trip. Typical chains: drone_move_relative + locate_image_point (step, then re-measure); drone_rotate_to + inspect_current_frame (turn, then look); drone_takeoff + drone_get_status. "
            "Only flight actions are one-per-turn: never put two flight-control tools in one response. "
            "CORRECTION DISCIPLINE: when entering a correction loop, FIRST read the current vehicle status (drone_get_status) and verify from the observation whether the goal action actually failed — never blindly repeat an action that the previous step already reported as completed (e.g. landing completed means the vehicle is on the ground; calling land again is wrong). If the observation shows the goal is already satisfied, mark is_complete=true instead of re-executing. "
            "NEVER declare is_complete=true while any planned motion step (takeoff/hover/move/fly/land/rotate) is still failed or unexecuted from a previous attempt: you must first successfully re-execute that action (fixing its cause first, e.g. climb to the safe altitude before a blocked horizontal move), or prove with fresh drone_get_status telemetry that the target state is genuinely reached. Declaring the task complete while a goal motion is still missing is a hard error and the run will be marked failed. "
            "You may additionally batch up to 2 independent read-only tool calls (drone_get_status, airsim_get_sensors, locate_image_point, inspect_current_frame, perception_status) "
            "in the 'actions' array (JSON mode) or as extra tool calls (native mode). "
            "Never place flight-control tools (arm, disarm, takeoff, land, hover, fly, move, rotate, set_mode, mission upload/start) in that batch — one flight tool per turn. "
            "TOOL SELECTION — pick by what you already know, not by habit. There is NO object detection in this system: semantics come from the "
            "vision model, metric distance from the depth image. To go to something you can see: (1) inspect_current_frame once — read the "
            "normalised point (and rough size) of the target from answer.target_candidates[].bbox_hint; (2) locate_image_point at that point "
            "for the metric distance and world position — PASS the rough w/h; it cross-checks the box against the depth image (same_surface "
            "means you drew a continuous surface, not an object, so do not fly there) and you may add extra points (\"x,y;x,y;x,y\") to "
            "median-average the range; (3) drone_rotate_to to centre it if |x-0.5| > 0.15 (Δheading ≈ (x-0.5)×90°); (4) drone_move_relative for ONE "
            "step SIZED FROM THAT DISTANCE — forward ≈ Z×0.97 − 2 m standoff, capped at 12 m and at one third of the remaining distance while the "
            "target is still small in frame; never a fixed number of metres — then re-check (distance drops as you close in, so the step shrinks by "
            "itself; stop at the standoff). Use locate_image_point when you need the target's absolute NED position (to drone_fly_to it, or "
            "to remember where it is); local NED coordinate already known → drone_fly_to; only the heading matters → drone_rotate_to; a waypoint "
            "sequence → drone_fly_path; keep 2-3 m altitude for close-range work. Before ANY forward move toward a target, read depth again: a "
            "missing or implausible depth reading means do not move. "
            "NEVER drone_fly_to a coordinate you derived from a single depth point on a small target: that point is often the "
            "GROUND or the background in front of it (measured: a 9-degrees-down ray hit the road 30 m away, not the car) — flying "
            "there overshoots the target and it leaves the frame. Close in with bounded drone_move_relative steps and re-check; if you "
            "do use drone_fly_to, stop SHORT of the target (>= 2 m standoff) and keep it in frame. After losing the target, turn back "
            "toward its last known position before looking again. "
            "STOPPING CRITERION for approach tasks: you are done closing in once the target's normalised height h ≥ 0.25 "
            "(a quarter of the frame) or the depth says ≤ ~2 m standoff — then drone_hover and report. Do NOT keep creeping "
            "closer: the runtime refuses further approach steps after that, and a target that fills the whole frame is no longer "
            "usable for identification. "
            "PROVENANCE: your own position/heading/attitude come from the flight controller telemetry, not from vision — use them "
            "freely. A target's world_pos from locate_image_point is an ESTIMATE (one depth ray + your pose), not a sensor fix: "
            "treat it as approximate, never fly straight at it, and always keep visual confirmation as the authority. "
            "NEVER repeat a call that was just refused with a precondition error — the error text names what to fix; do that instead. "
            "Never treat a single vision-model answer as a measurement: its point is rough and its size is ±30%; one look per position, and if the "
            "answer is vague, move or turn rather than asking again. "
            "STOPPING CRITERION IS PART OF THE PLAN: in goal.success_criteria declare the machine-checkable criterion "
            "the runtime should stop at, with numbers. Examples: \"靠近后悬停\" → {\"metric\": \"target_fill\", \"min_fill\": 0.25} "
            "or {\"metric\": \"target_distance\", \"max_m\": 3.0}; \"降落在 H 点上\" → {\"metric\": \"position_reached\", \"x\": …, \"y\": …, "
            "\"z\": -3.0, \"tolerance\": 0.5} plus {\"metric\": \"landed\"}; 定点悬停 → {\"metric\": \"flying_at\", \"altitude\": 3.0}. "
            "Declaring it is how the operator gets a criterion that matches THIS task; the runtime never invents one. "
            "Use only the tools provided to you (the native tool list, or available_tool_cards in JSON mode). Do not call unavailable tools. "
            "Parse the operator's full instruction yourself, including multi-step Chinese or English commands. Do not rely on keyword routing. "
            "Never bypass safety, never invent telemetry, and do not perform low-level continuous control. "
            "skill_guidance is a CATALOG of skills: each entry has a name and a description saying what it covers and when to use it. It teaches how to work; a catalog entry is not itself an action. "
            "When an entry's description matches the operator's task, your first action should be that skill's action name (for example skill:region-search). That loads the full Markdown guidance as an observation; follow it while choosing native tools. Skills whose description does not match the task must be ignored. "
            "Never output action=skill:* for a name that is not in skill_guidance. "
            "If the operator's command is a knowledge question, explanation request, or non-UAV task that does not require vehicle tools or backend state, mark is_complete=true in the first turn and put a concise Chinese natural-language answer in reason. Do not force a drone tool call for questions unrelated to UAV operation. "
            "For vague scan/move wording such as '一点距离' or '简单扫描', keep horizontal movement to 1-2 meters, use velocity around 1.0-1.5 m/s, and take off to at least 3 m before horizontal movement. "
            "For open-ended 'what is in the photo' requests, use inspect_current_frame (vision-model analysis of the current perception frame). Use it whenever the user asks what the drone sees, the color/type/size of an object, or any '看看画面里.../描述当前画面' question. "
            "For return-to-start, use the first observed position when available instead of guessing home coordinates. "
            "For square/rectangle/orbit/circle/grid path tasks, prefer one drone_fly_path action with explicit local NED waypoints over many rotate-to-heading plus drone_move_relative turns. Use rotate_to only when camera orientation matters. "
            "For multi-step goals, decide the next step only when it depends on a NEW observation; otherwise chain the action with its verification reads in the same response instead of spending a turn on each. "
            "Prefer status/readback after uncertain results. After a photo or visual sweep produces an image, call inspect_current_frame with a direct confirmation question before declaring a target found. "
            "Use memory_snapshot.guidance to prefer historically reliable skills/tools and to avoid repeating known failure patterns. "
            "An async tool is not complete when it returns started/running; wait for the runtime-provided terminal observation before declaring completion. "
            "If the backend lacks a requested capability, stop safely and explain the limitation in reflection. "
            "The reason/reflection fields are displayed live to the operator as public process text; write concise, useful, non-sensitive decision text there. "
            "When is_complete=true, put a concise Chinese task report in reason. Include executed sequence, final state/position, image finding, and any limitation/failure. "
            "Think and reason in Chinese (中文思考). Return JSON only."
        )

    def _loop_decision_schema_hint(self) -> dict[str, Any]:
        return {
            "action": "one available tool name, or empty string when complete",
            "params": {"name": "value"},
            "reason": "brief public Chinese process text explaining what you decided to do next",
            "is_complete": False,
            "needs_replan": False,
            "reflection": "optional brief public reflection on the latest observation/result",
            "actions": [
                {
                    "action": "read-only verification call executed AFTER the main action, results returned together (drone_get_status, airsim_get_sensors, locate_image_point, inspect_current_frame) — never flight-control tools",
                    "params": {"name": "value"},
                    "reason": "optional short text",
                }
            ],
        }

    def _decision_from_payload(self, payload: dict[str, Any], allowed_tools: set[str]) -> LoopDecision:
        action = str(payload.get("action") or "").strip()
        if not action:
            # Models occasionally nest the tool call inside the reason field
            # (e.g. a fenced ```json block that failed to lift to the top
            # level). Recover the inner decision so a valid action is not
            # dropped and the run does not die on step one.
            try:
                reason_text = str(payload.get("reason") or "")
                stripped = reason_text.strip()
                if stripped.startswith("{") or stripped.startswith("```"):
                    inner = json.loads(_extract_json(reason_text))
                    if isinstance(inner, dict):
                        inner_action = str(inner.get("action") or "").strip()
                        if inner_action:
                            merged = dict(inner)
                            outer_reflection = str(payload.get("reflection") or "").strip()
                            if outer_reflection:
                                merged["reflection"] = outer_reflection
                            payload = merged
                            action = inner_action
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        is_complete = _as_bool(payload.get("is_complete", False))
        if is_complete:
            action = ""
        elif action and action not in allowed_tools:
            return LoopDecision(
                action="",
                reason=f"Model selected unavailable tool: {action}",
                is_complete=False,
                reflection="Stopped before executing an unavailable tool.",
            )
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        decision = LoopDecision(
            action=action,
            params=params,
            reason=str(payload.get("reason") or action or "complete"),
            is_complete=is_complete,
            needs_replan=_as_bool(payload.get("needs_replan", False)),
            reflection=str(payload.get("reflection") or ""),
        )
        raw_actions = payload.get("actions")
        if isinstance(raw_actions, list) and not is_complete:
            extras: list[LoopDecision] = []
            for raw in raw_actions:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("action") or "").strip()
                if not name or name not in allowed_tools or name == action:
                    continue
                raw_params = raw.get("params") or {}
                if not isinstance(raw_params, dict):
                    raw_params = {}
                extras.append(
                    LoopDecision(
                        action=name,
                        params=dict(raw_params),
                        reason=str(raw.get("reason") or ""),
                        is_complete=False,
                        needs_replan=False,
                        reflection=str(raw.get("reflection") or ""),
                    )
                )
            if not action and extras:
                first = extras.pop(0)
                decision = LoopDecision(
                    action=first.action,
                    params=first.params,
                    reason=first.reason or "complete",
                    is_complete=False,
                    reflection=first.reflection,
                )
            decision.parallel_actions = [item.to_dict() for item in extras]
        return decision

    def _compact_loop_state(self, loop_state: dict[str, Any], tight: bool = False) -> dict[str, Any]:
        # 工具结果里可能带着整张图的 base64（拍照 1.2MB ≈ 30 万 token）。以前这里
        # 只做"深度上限"的安全拷贝，不做图片裁剪，于是 loop_state 一节把整份预算
        # 吃光：ContextBudget 截断后，available_tool_cards / skill_guidance /
        # memory_snapshot 全变成 "[omitted]"——恰好是模型必须选下一步动作的那一轮。
        # 观察一节（_compact_observation）早就有这个裁剪，这里补齐同一套。
        #
        # ``tight``：上下文压力档（上一轮用量超过窗口 70%）。摘要与完整结果的条数
        # 同时收缩——这是"自动压缩"的确定性版本，见 _context_pressure()。
        digest_limit = _STEPS_DIGEST_TIGHT_LIMIT if tight else _STEPS_DIGEST_LIMIT
        recent_limit = _RECENT_RESULTS_TIGHT if tight else _RECENT_RESULTS_DEFAULT
        return {
            "run_id": loop_state.get("run_id", ""),
            "status": loop_state.get("status", ""),
            "step_count": len(loop_state.get("decisions") or []),
            # 决策/结果里可能含共享或自引用的数据结构（工具结果里嵌了
            # world_state/感知快照等），直接交给 json.dumps 会抛
            # "Circular reference detected" 把整个 run 打断。这里先做有深度
            # 上限的安全拷贝再放进提示词。
            "recent_decisions": self._safe_payload((loop_state.get("decisions") or [])[-4:]),
            # 轨迹摘要：每步一行（工具、成败、一个关键数值），最老的先丢。
            # 借 Pi Harness 的做法——历史不该被"丢掉"或原样重发，而是压缩成摘要：
            # 循环到第 20 步时模型仍需要知道前面做过什么，但不需要每步的完整返回。
            "steps_digest": _steps_digest(loop_state.get("results") or [], limit=digest_limit),
            "recent_results": [
                self._compact_loop_result(row) for row in (loop_state.get("results") or [])[-recent_limit:]
            ],
            "failure_reason": loop_state.get("failure_reason", ""),
        }

    def _context_pressure(self, config: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """上下文压力 → (是否收紧, 说明)。

        用的是 **provider 报的**上一轮 prompt 用量，而不是我们自己估的"这次要发多少"：
        报的值包含我们看不到的部分（工具 schema、服务端包装），更接近真相——这正是
        Pi Harness 的 estimateContextTokens 思路（usage + 尾部估算）。
        超过窗口 ``_CONTEXT_PRESSURE_RATIO`` 时切收紧档：摘要与完整结果条数同时收缩，
        并把这件事交给运行层发事件（压缩必须是可见的，否则下次又变成"莫名其妙变慢"）。
        """
        try:
            window = int(self._context_budget(config).context_window)
        except Exception:
            window = 64000
        used = int((self.last_usage or {}).get("prompt_tokens") or 0)
        if window <= 0 or used <= 0:
            return False, {}
        ratio = used / window
        if ratio < _CONTEXT_PRESSURE_RATIO:
            return False, {}
        return True, {"prompt_tokens": used, "context_window": window, "ratio": round(ratio, 2)}

    def _relevant_tool_cards(
        self,
        cards: list[dict[str, Any]],
        loop_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """按"任务类型"收窄进提示词的工具卡片（保守版）。

        任务类型的信号是**被激活过的技能**：技能声明了自己的 `subtools`，那就是这类
        任务需要的工具集。规则：

        - 链路/安全/基本机动/基本感知（`_ALWAYS_AVAILABLE_TOOLS`）任何任务都保留；
        - 被实际用过的工具必须保留（否则模型会"忘了自己刚用过什么"）；
        - 已激活技能的 `subtools` 全部保留；
        - 没有技能激活时**不过滤**（任务类型未知，宁可多发也不藏工具）。

        注意：这里只收窄**提示词与 API tools 菜单**。决策白名单（allowed_tools）仍按
        全量卡片算，所以即使某个工具没进菜单、模型仍然叫得出名字，也不会被误判成
        "不可用工具"而拒绝执行。
        """
        if not cards:
            return cards
        activated = {
            str(row.get("tool"))
            for row in (loop_state.get("results") or [])
            if isinstance(row, dict) and str(row.get("tool") or "").startswith("skill:")
        }
        if not activated:
            return cards
        keep = set(_ALWAYS_AVAILABLE_TOOLS)
        for row in loop_state.get("results") or []:
            if isinstance(row, dict) and row.get("tool"):
                keep.add(str(row["tool"]))
        for card in cards:
            name = str(card.get("name") or "")
            if name in activated:
                keep.update(str(item) for item in (card.get("subtools") or []) if item)
        filtered = [card for card in cards if str(card.get("name") or "") in keep]
        # 防御：过滤后太小说明判断有误，宁可不收窄
        if len(filtered) < 5:
            return cards
        return filtered

    def _compact_loop_result(self, row: Any) -> Any:
        """循环结果一行：裁掉图片负载，其余保持可读（参数/返回/成败）。"""
        safe = self._safe_payload(row, limit=8)
        if isinstance(safe, dict) and isinstance(safe.get("data"), dict):
            try:
                safe["data"] = self._trim_data(safe["data"])
            except Exception:
                safe["data"] = {"summary": "[unserializable tool result]"}
        return safe

    @staticmethod
    def _safe_payload(value: Any, limit: int = 12) -> Any:
        """有深度上限的循环安全拷贝（防止 json.dumps 遇到自引用抛错）。"""
        from .planner import _bounded_copy

        try:
            return _bounded_copy(value, _limit=limit)
        except Exception:
            return "[unserializable]"

    @staticmethod
    def _dumps_safe(value: Any) -> str:
        """json.dumps 的"永不抛错"包装：遇到自引用/不可序列化时降级为字符串。

        提示词组装不该因为某个工具结果里的循环引用把整个任务打断。
        """
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (ValueError, TypeError):
            try:
                from .planner import _bounded_copy

                return json.dumps(_bounded_copy(value, _limit=12), ensure_ascii=False, default=str)
            except Exception:
                return json.dumps({"value": "[unserializable]"}, ensure_ascii=False)

    def _compact_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        world = observation.get("world_state") or {}
        return {
            "step_index": observation.get("step_index", 0),
            "elapsed_since_start": observation.get("elapsed_since_start", 0),
            "frame_age_s": observation.get("frame_age_s"),
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
        intents = extract_intents(command)
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

        wants_search = intents["search"]
        wants_track = intents["track"]
        wants_photo = intents["photo"]
        wants_visual = wants_search or wants_track or wants_photo
        wants_return = intents["return_home"] or slots.land is True
        wants_navigation = (
            slots.ned_target is not None
            or slots.relative_move is not None
            or any(k in lower for k in NAVIGATION_TERMS)
        )

        completed_frame_inspection = self._loop_has_action(loop_state, "inspect_current_frame")

        if not connected and "drone_connect" in allowed_tools and not self._loop_has_action(loop_state, "drone_connect"):
            return LoopDecision("drone_connect", {}, "Connect before advanced task execution.")
        # Only observe the vehicle when the task is going to act on it. This line
        # used to fire unconditionally, so the fallback could never answer a pure
        # question -- it inserted a status read first and the operator saw vehicle
        # telemetry in reply to "what does that car look like". An information-only
        # request (visual question or named-target confirmation) with no flight
        # intent is finished by answering, not by flying.
        # An explicit request for vehicle state is honoured on its own terms. This
        # branch used to be missing: the unconditional read below was what served
        # "查看状态", so gating that line without adding this one would have broken
        # an explicit status request.
        if intents["status"]:
            return LoopDecision("drone_get_status", {}, "Operator asked for vehicle state.")
        # For everything else, decide by the absence of any *action* intent rather
        # than by a list of question phrasings: enumerating ways to ask a question
        # is the same keyword-matching trap that caused this bug. If nothing is
        # going to be flown, there is nothing to observe first.
        action_intents = ("motion", "takeoff", "land", "hover", "return_home",
                          "search", "track", "patrol", "visual_approach", "connect")
        information_only = not any(intents[k] for k in action_intents)
        if (
            not information_only
            and "drone_get_status" in allowed_tools
            and not self._loop_has_action(loop_state, "drone_get_status")
        ):
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
            and "inspect_current_frame" in allowed_tools
            and not completed_frame_inspection
            and self._loop_has_recent_image(loop_state)
        ):
            return LoopDecision(
                "inspect_current_frame",
                {"question": f"画面中是否有{target}？请确认目标是否存在，并简述其位置和外观。"},
                "Use the multimodal model to confirm whether the latest captured frame contains the requested target.",
            )

        needs_flight = wants_visual or any(k in lower for k in FLIGHT_TERMS)
        if needs_flight and not drone.get("armed") and "drone_arm" in allowed_tools and not self._loop_has_action(loop_state, "drone_arm"):
            return LoopDecision("drone_arm", {}, "Arm before airborne task execution.")
        if needs_flight and not drone.get("flying") and "drone_takeoff" in allowed_tools and not self._loop_has_action(loop_state, "drone_takeoff"):
            return LoopDecision("drone_takeoff", {"altitude": altitude}, "Take off to task altitude.")

        if wants_photo and "inspect_current_frame" in allowed_tools and not self._loop_has_action(loop_state, "inspect_current_frame"):
            # 拍照/看图统一走 inspect_current_frame（save_frame=True 留存证据帧）：
            # 原 airsim_take_photo 已并入该工具，模型面只有一个视觉入口。
            question = command
            if target or wants_search or wants_track:
                question = (
                    f"画面里有没有{target or '目标'}？它在画面的什么位置"
                    "（归一化坐标 x,y），大概多大（归一化宽高 w,h）？"
                )
            return LoopDecision(
                "inspect_current_frame",
                {"question": question, "save_frame": True},
                "把画面交给视觉模型（拍照意图：同时留存证据帧）。",
            )

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
        return extract_target_class(command) or "target"

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
                    # 只保留"怎么选、怎么调"需要的字段。outputs / failure_policy /
                    # verification / subtools / execution_mode 等对"选哪个工具"没有
                    # 决策价值，实测它们占了卡片体积的一半以上；工具返回什么，
                    # 结果里就能看到。
                    "when_to_use": str(card.get("when_to_use", ""))[:280],
                    "inputs": card.get("inputs", {}),
                    "preconditions": card.get("preconditions", []),
                    "not_for": str(card.get("not_for", ""))[:160],
                    "kind": card.get("kind", "atomic"),
                }
            )
        return keep[:40]

    def _compact_skill_guidance(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compact the skill CATALOG for the prompt.

        Entries are metadata only (name + description), so the cap is generous:
        hiding a skill from the catalog means the model can never discover it.
        The markdown truncation is retained defensively in case a caller passes
        full doc cards.
        """
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
        return keep[:20]

    def _compact_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        """每轮注入的记忆：只给"这次决策用得上"的，不给档案。

        借 Pi Harness 的上下文原则：能力与当前观察永不裁剪，历史与记忆都必须有界。
        这里原先每轮注入 5.6k tokens（guidance 全量 + 各类列表各 5 条 + facts/
        lessons 原文），而循环到第 N 步时真正有用的只有三件事：这类工具/技能以前
        表现如何、最近踩过什么坑、上次做到哪。
        原始 facts/lessons 是自然语言档案：既长又容易过期（旧记录里还写着"用目标
        检测确认"），不再逐轮注入。要看全文去哪里：`.airsim_agent/NOTES.md`（操作员
        维护的长期笔记，自动注入）与运行记录面板——记忆不是一个模型工具。
        """
        guidance = memory.get("guidance") if isinstance(memory.get("guidance"), dict) else {}
        missions = [item for item in (memory.get("missions") or [])[:3]]
        return {
            "session": self._safe_payload(memory.get("session") or {}, limit=4),
            "recent_missions": [
                {
                    "summary": str((item.get("summary") if isinstance(item, dict) else item) or "")[:120],
                    "status": str((item.get("status") if isinstance(item, dict) else "") or "")[:16],
                }
                for item in missions
            ],
            "guidance": {
                "preferred_tools": list(guidance.get("preferred_tools") or [])[:3],
                "caution_tools": list(guidance.get("caution_tools") or [])[:3],
                "preferred_skills": list(guidance.get("preferred_skills") or [])[:2],
                "recent_risk_events": list(guidance.get("recent_risk_events") or [])[:2],
            },
        }

    def _chat_unavailable_message(self, config: dict[str, Any] | None, reason: str) -> str:
        model_name = "未选择模型"
        if config:
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
        reason = self._sanitize_model_error(reason)
        if reason == "missing_api_key":
            return f"模型不可用：{model_name} 未配置或未启用 API Key。请检查模型设置或切换到可用模型。"
        return f"模型不可用：{model_name} 调用失败。{reason}"

    @staticmethod
    def _multimodal_required_message(config: dict[str, Any] | None, action: str) -> str:
        """配的模型看不了图时的说明。

        这里必须点明"规划与看图是同一个模型"：否则用户会去找一个并不存在的
        "视觉模型"设置项，而真正要改的是他正在用的那个模型。
        """
        model_name = "未选择模型"
        if config:
            model_name = str(config.get("name") or config.get("model") or config.get("id") or "当前模型")
        return (
            f"模型不可用：{model_name} 不支持图像输入，无法{action}。"
            "规划与看图共用同一个模型，系统不会另选一个视觉模型——"
            "请把当前模型换成支持图像的模型，或在模型设置里为它开启图像输入能力。"
        )

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
        # 工具结果可能含共享/自引用结构（感知快照、循环引用的 world_state
        # 等）。先做有深度上限的拷贝再序列化，避免 json.dumps 抛
        # "Circular reference detected" 把整个 run 打断。
        from .planner import _bounded_copy

        try:
            safe = _bounded_copy(data, _limit=12)
        except Exception:
            safe = {"[unserializable]": True}
        if isinstance(safe, dict) and self._contains_image_data(safe):
            trimmed = self._trim_image_payload(safe)
            try:
                text = json.dumps(trimmed, ensure_ascii=False, default=str)
            except (ValueError, TypeError):
                return {"summary": "[unserializable tool result]", "has_image": True}
            if len(text) <= 1200:
                return trimmed
            return {"summary": text[:1200] + "...", "has_image": True}
        try:
            text = json.dumps(safe, ensure_ascii=False, default=str)
        except (ValueError, TypeError):
            return {"summary": "[unserializable tool result]"}
        if len(text) <= 1200:
            return safe
        return self._truncate_keeping_long_text(safe, text)

    # 技能正文（SKILL.md）是"这类任务该怎么做"的操作知识，模型必须读到全文才
    # 能照着做；4 个内置技能都在 4~9KB。它以前和普通工具结果一起被 1200 字符
    # 截断，模型拿到的是半截 JSON——渐进披露对每个技能都是坏的。
    _LONG_TEXT_FIELDS = ("markdown", "guidance", "skill_markdown")
    _LONG_TEXT_CAP = 12000

    def _truncate_keeping_long_text(self, safe: dict[str, Any], text: str) -> dict[str, Any]:
        """超长结果的结构化截断：长文本字段保真，其余字段摘掉。

        直接切序列化后的字符串会得到非法 JSON，模型既看不到正文也拿不到字段名。
        这里改成保留 markdown/guidance 这类长文本字段（上限 _LONG_TEXT_CAP），
        其它字段用一行说明替代，保证输出仍是合法 JSON。
        """
        long_text = {
            key: value
            for key, value in safe.items()
            if key in self._LONG_TEXT_FIELDS and isinstance(value, str) and value
        }
        if not long_text:
            return {"summary": text[:1200] + "..."}
        kept: dict[str, Any] = {}
        for key, value in long_text.items():
            if len(value) <= self._LONG_TEXT_CAP:
                kept[key] = value
            else:
                kept[key] = value[: self._LONG_TEXT_CAP] + "\n...[truncated]"
        for key, value in safe.items():
            if key in kept or key in self._LONG_TEXT_FIELDS:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                rendered = json.dumps(value, ensure_ascii=False, default=str)
                if len(rendered) <= 400:
                    kept[key] = value
        kept["_truncated_fields"] = [
            key for key in safe if key not in kept and key != "_truncated_fields"
        ]
        return kept

    def _contains_image_data(self, value: Any, _depth: int = 0) -> bool:
        # 深度上限：与 runtime._sanitize_for_frontend 同理，工具结果里共享的
        # 可变字典可能形成循环引用，无上限会把 agent loop 线程打爆。
        if _depth > 24:
            return False
        if isinstance(value, dict):
            if any(key in value for key in ("image_base64", "image_saved_to", "saved_to", "approach_image_saved_to")):
                return True
            return any(self._contains_image_data(item, _depth + 1) for item in value.values())
        if isinstance(value, list):
            return any(self._contains_image_data(item, _depth + 1) for item in value)
        return False

    def _trim_image_payload(self, value: Any, _depth: int = 0) -> Any:
        if _depth > 24:
            return {"[bounded]": True}
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
                    keep[key] = self._trim_image_payload(item, _depth + 1)
            return keep or {"has_image": self._contains_image_data(value, _depth)}
        if isinstance(value, list):
            return [self._trim_image_payload(item, _depth + 1) for item in value[:5]]
        return value

    @staticmethod
    def _data_url_from_base64(image_base64: str) -> str:
        text = str(image_base64 or "").strip()
        if text.startswith("data:image/"):
            return text
        return f"data:image/png;base64,{text}"

    @staticmethod
    def _visual_result_text(result: dict[str, Any]) -> str:
        """从 inspect_current_frame 的结果里取可读的视觉摘要。"""
        if not isinstance(result, dict):
            return ""
        answer = result.get("answer") if isinstance(result.get("answer"), dict) else result
        for key in ("summary_zh", "message", "summary"):
            text = str(answer.get(key) or "").strip()
            if text:
                return text
        return ""

    def _normalize_vlm_confirmation(self, payload: dict[str, Any], target: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        confidence = payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        found = _as_bool(payload.get("target_found")) and confidence >= 0.5
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
                "w": _unit_or_none(bbox.get("w")),
                "h": _unit_or_none(bbox.get("h")),
            },
            "recommended_next_action": action,
            "summary_zh": summary,
        }

    def _normalize_vlm_analysis(self, payload: dict[str, Any], question: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        # 模型有时会把请求体原样回显，并把真正的答案塞进 output_schema/answer
        # 等嵌套字段里（顶层没有 summary_zh）。这里向下展开一层，取第一个含
        # summary_zh/summary 的字典作为实际结果，避免前端显示"模型没有返回
        # 明确的图像描述"。
        if not str(payload.get("summary_zh") or payload.get("summary") or "").strip():
            for key in ("output_schema", "result", "data", "answer", "response", "output", "analysis"):
                nested = payload.get(key)
                if isinstance(nested, dict) and str(
                    nested.get("summary_zh") or nested.get("summary") or ""
                ).strip():
                    payload = nested
                    break
            else:
                for value in payload.values():
                    if isinstance(value, dict) and str(
                        value.get("summary_zh") or value.get("summary") or ""
                    ).strip():
                        payload = value
                        break
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
                "bbox_hint": {
                    "x": bbox.get("x"),
                    "y": bbox.get("y"),
                    "w": _unit_or_none(bbox.get("w")),
                    "h": _unit_or_none(bbox.get("h")),
                },
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
            name.startswith("drone_") and name not in {"drone_connect", "drone_get_status"}
            for name in tool_names
        )

        if not is_agent_loop and not has_flight_action:
            for row in reversed(tool_results if isinstance(tool_results, list) else []):
                if not isinstance(row, dict):
                    continue
                if row.get("tool") != "inspect_current_frame":
                    continue
                result = row.get("result") if isinstance(row.get("result"), dict) else {}
                message = self._visual_result_text(result)
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
            "inspect_current_frame": "画面分析",
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
            if tool == "inspect_current_frame":
                image_text = self._visual_result_text(result) or image_text
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

    @staticmethod
    def _collapse_repeated_blocks(steps: list[MissionStep], max_steps: int = 14) -> list[MissionStep]:
        """折叠 LLM 展开的重复步骤块，并限制总步数。

        规划模型常把"检测→确认→抵近"这类循环**手工展开 2~3 遍**（见过 36 步
        的计划 = 同一 12 步块重复 3 次），既拖慢执行又让计划面板难以阅读。
        这里检测最小重复周期，只保留一份；循环交给 Agent Loop 在运行时处理。
        """
        if len(steps) <= 1:
            return steps
        key = lambda s: (s.tool, tuple(sorted((s.params or {}).items(), key=lambda kv: kv[0])))
        keys = [key(s) for s in steps]
        for period in range(1, len(keys) // 2 + 1):
            if len(keys) % period != 0:
                continue
            if all(keys[i] == keys[i % period] for i in range(len(keys))):
                steps = steps[:period]
                break
        return steps[:max_steps]

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
            # skill:* 永远允许：激活技能只是把该技能的步骤说明取回来（只读，
            # 不含飞控指令）。以前这里把"不在原生工具表里"的计划步骤一律丢弃，
            # 模型即使写了 skill:* 也会被静默删掉。
            if tool not in known_tools and not tool.startswith("skill:"):
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
                    needs_observation=_as_bool(raw.get("needs_observation")),
                )
            )

        steps = self._normalize_steps(command, steps, known_tools)
        steps = self._collapse_repeated_blocks(steps)
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
            keep_reacting=_as_bool(payload.get("keep_reacting")),
            goal=self._goal_from_payload(payload.get("goal"), command),
        )

    VERIFY_METRICS = {
        "target_confirmed",
        # 近距任务的停止判据（可机械评估）：目标占画面比例 / 距离。放在这里意味着
        # "到位标准由任务声明"，而不是由运行时对某一种任务写死一个常数。
        "target_fill",
        "target_distance",
        "position_reached",
        "flying_at",
        "landed",
        "photo_taken",
        "status_ok",
        "mission_progress_complete",
        "formation_stable",
    }

    def _goal_from_payload(self, raw_goal: Any, command: str) -> dict[str, Any]:
        """Parse the LLM-proposed task contract, keeping only verifiable metrics.

        Unknown metrics are dropped; LLM proposals never widen the verifier's
        vocabulary (fail-safe: a metric the runtime cannot check is not a
        completion gate).
        """
        if not isinstance(raw_goal, dict):
            raw_goal = {}
        criteria: list[dict[str, Any]] = []
        raw_criteria = raw_goal.get("success_criteria") or []
        if isinstance(raw_criteria, list):
            for item in raw_criteria:
                if not isinstance(item, dict):
                    continue
                metric = str(item.get("metric") or "").strip()
                if metric not in self.VERIFY_METRICS:
                    continue
                cleaned: dict[str, Any] = {"metric": metric}
                if metric == "position_reached":
                    try:
                        cleaned.update(
                            {
                                "x": float(item.get("x") or 0.0),
                                "y": float(item.get("y") or 0.0),
                                "z": self._ned_z(item.get("z"), -3.0),
                                "tolerance": float(item.get("tolerance") or 1.5),
                            }
                        )
                    except (TypeError, ValueError):
                        continue
                elif metric == "flying_at":
                    try:
                        cleaned["altitude"] = abs(float(item.get("altitude") or 3.0))
                        cleaned["tolerance"] = float(item.get("tolerance") or 1.0)
                    except (TypeError, ValueError):
                        continue
                elif metric == "target_fill":
                    try:
                        cleaned["min_fill"] = max(0.05, min(0.9, float(item.get("min_fill") or 0.25)))
                    except (TypeError, ValueError):
                        cleaned["min_fill"] = 0.25
                elif metric == "target_distance":
                    try:
                        cleaned["max_m"] = max(0.5, min(50.0, abs(float(item.get("max_m") or 3.0))))
                    except (TypeError, ValueError):
                        cleaned["max_m"] = 3.0
                elif metric == "target_confirmed":
                    cleaned["target"] = str(item.get("target") or "")
                # required（默认 true）：必需判据未满足就不接受完成；可选判据只记录、
                # 不当闸门（文献里的 typed criteria：required 是任务自己写的）。
                cleaned["required"] = bool(item.get("required", True))
                if metric in {"target_fill", "target_distance", "position_reached", "flying_at"}:
                    # 判据要"持续成立"多少秒才算数（默认 0 = 单次成立即可）。
                    # 这是文献里的 hold_for：防抖——既防一次噪声读数让我们提前收工，
                    # 也防"满足/不满足"来回跳导致收尾判定反复。
                    try:
                        cleaned["hold_s"] = max(0.0, min(30.0, float(item.get("hold_s") or 0.0)))
                    except (TypeError, ValueError):
                        cleaned["hold_s"] = 0.0
                criteria.append(cleaned)
        # 判据必须存在：LLM 经常不写 success_criteria，于是任务只能撞步数上限
        # （实测一次 17 分钟的任务就这样以 blocked 收尾）。这里只把**指令里
        # 已经说出的目标**转成可校验项，不是按任务类型发明标准：
        #   搜索/接近 → 目标确认 + 目标距离；明确说降落 → 已落地。
        derived = self._criteria_from_command(command)
        existing_metrics = {item["metric"] for item in criteria}
        for item in derived:
            if item["metric"] not in existing_metrics:
                criteria.append(item)
                existing_metrics.add(item["metric"])
        return {
            "objective": str(raw_goal.get("objective") or command)[:200],
            "target": str(raw_goal.get("target") or ""),
            "success_criteria": criteria,
        }

    def _criteria_from_command(self, command: str) -> list[dict[str, Any]]:
        """把指令里明确写出的完成目标转成可校验判据（规划器的确定性兜底）。

        运行时仍然只评估任务声明的判据；这里做的是"把任务自己的话变成判据"。
        """
        try:
            intents = extract_intents(command)
        except Exception:
            return []
        criteria: list[dict[str, Any]] = []
        wants_target = bool(intents.get("visual") and (intents.get("visual_approach") or intents.get("search")))
        if wants_target:
            criteria.append(
                {
                    "metric": "target_confirmed",
                    "target": self._command_target_class(command),
                    "required": True,
                }
            )
            criteria.append({"metric": "target_distance", "max_m": 3.0, "required": True, "hold_s": 0.0})
        if intents.get("land"):
            criteria.append({"metric": "landed", "required": True})
        return criteria

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
                waypoints = step.params.get("waypoints")
                if not isinstance(waypoints, str):
                    waypoints = step.params.get("waypoints_json")
                if not isinstance(waypoints, str):
                    try:
                        waypoints = json.dumps(waypoints or [], ensure_ascii=False, separators=(",", ":"))
                    except (TypeError, ValueError):
                        waypoints = "[]"
                step.params["waypoints"] = waypoints
                step.params["velocity"] = self._positive_float(step.params.get("velocity"), 1.5)
            elif step.tool == "inspect_current_frame":
                # 目标确认参数收口成 inspect_current_frame 的提问（该工具只收 question）。
                step.params.pop("source", None)
                step.params.pop("target_description", None)
                if not str(step.params.get("question") or "").strip():
                    target_class = self._command_target_class(command)
                    step.params["question"] = (
                        f"画面中是否有{target_class}？请确认目标是否存在，并简述其位置和外观。"
                        if target_class
                        else command
                    )
            normalized.append(step)

        # 目标识别/追踪类任务定高 2~3m：机载相机前视 15°，这个高度才能平视
        # 目标；飞高会俯视过度、目标变小、检出率下降。这是安全/性能策略而非
        # 语义隐藏处理，因此即使 LLM 或被复述的指令写了 5m 也在此收口。
        if self._is_close_range_visual_command(command):
            for step in normalized:
                if step.tool == "drone_takeoff":
                    alt = self._positive_float(step.params.get("altitude"), 3.0)
                    step.params["altitude"] = max(2.0, min(3.2, alt))
                elif step.tool == "drone_fly_to" and "z" in step.params:
                    z = self._ned_z(step.params.get("z"), -3.0)
                    step.params["z"] = -max(2.0, min(3.2, abs(z)))

            # 追踪/搜索类任务的目的是"找到并持续锁定"，不是飞完就收工。除非
            # 操作员明确要求降落/返航，否则不要把 drone_land 排进计划——之前
            # 出现"追踪任务执行到一半就降落"的任务理解错误。任务结束时的安全
            # 落地由 runtime 的收尾逻辑负责，不需要写进计划。
            explicit_landing = self._command_has_any(
                command, ["降落", "着陆", "落地", "返航", "返场", "回家", "land", "rtl", "return home", "return to launch"]
            )
            if not explicit_landing:
                normalized = [step for step in normalized if step.tool != "drone_land"]

        # 计划形状收口（确定性规则，与是哪个模型规划无关）：
        #  1) 一次任务最多一次起飞；降落也只保留一次，并放到最后——
        #     "目标还没确认就降落"会让任务失败，还会白白多一次起降
        #     （实测一次 17 分钟的任务里出现了 降落 → 再起飞）。
        intents = extract_intents(command)
        needs_confirmation = bool(intents["visual"] and (intents["visual_approach"] or intents["search"]))
        lands = [step for step in normalized if step.tool == "drone_land"]
        if lands:
            if needs_confirmation and not intents["land"]:
                normalized = [step for step in normalized if step.tool != "drone_land"]
            else:
                normalized = [step for step in normalized if step.tool != "drone_land"] + [lands[-1]]
        first_takeoff = next(
            (index for index, step in enumerate(normalized) if step.tool == "drone_takeoff"), None
        )
        if first_takeoff is not None:
            normalized = [
                step
                for index, step in enumerate(normalized)
                if step.tool != "drone_takeoff" or index == first_takeoff
            ]

        normalized = self._plan_visual_guidance(command, normalized, known_tools)

        # "看画面"意图：计划里没有观察步骤就补一步 inspect（拍照已并入 inspect，
        # 不再依赖计划里是否已有拍照步骤）。
        visual_tools = set()
        wants_open_image_analysis = self._command_has_any(
            command,
            ["what", "describe", "scene", "see", "look", "有什么", "看到", "看看", "画面", "照片", "图像内容"],
        )
        wants_target_look = self._command_has_any(
            command,
            ["search", "find", "detect", "识别", "搜索", "寻找", "找", "目标", "photo", "image", "拍照", "图像"],
        )
        needs_frame_inspection = (
            "inspect_current_frame" in known_tools
            and not any(s.tool == "inspect_current_frame" for s in normalized)
            and (wants_open_image_analysis or wants_target_look)
        )
        if needs_frame_inspection:
            target_class = self._command_target_class(command)
            question = (
                f"画面中是否有{target_class}？请确认目标是否存在，并简述其位置和外观。"
                if target_class and wants_target_look and not wants_open_image_analysis
                else command
            )
            normalized.append(
                MissionStep(
                    "s00",
                    "Inspect camera frame",
                    "inspect_current_frame",
                    {"question": question},
                    "perception",
                )
            )
        has_control = any(s.tool.startswith("drone_") or s.tool.startswith("airsim_") or s.tool.startswith("skill:") for s in normalized)
        core_tools = [s.tool for s in normalized]
        if has_control and "drone_get_status" in known_tools and (not core_tools or core_tools[-1] != "drone_get_status"):
            normalized.append(MissionStep("s00", "Read final status", "drone_get_status", {}, "perception"))

        for index, step in enumerate(normalized, 1):
            step.id = f"s{index:02d}"
        return normalized

    def _command_has_any(self, command: str, words: list[str]) -> bool:
        lower = command.lower()
        return any(word in lower for word in words)

    def _is_close_range_visual_command(self, command: str) -> bool:
        """近距视觉任务（搜索/识别/靠近某个看得见的东西）→ 定高 2~3m。

        判据分两路，刻意不误伤纯坐标飞行：
        - 意图：同时有 ``visual`` 与 ``visual_approach``——"飞向画面里那辆浅蓝色
          汽车"、"靠近那辆白车"都会命中。单独的 ``visual_approach`` **不够**：
          "飞到坐标 (10, 0, -5)" 也会命中它，而那是长距离绝对坐标任务。
        - 关键词兜底（原判据）：搜索/识别/跟踪/目标类的中英文说法。
        """
        text = command or ""
        try:
            intents = extract_intents(text)
            if intents.get("visual") and intents.get("visual_approach"):
                return True
        except Exception:
            pass
        return self._command_has_any(
            text,
            [
                "track", "follow", "search", "find", "detect", "target",
                "跟踪", "追踪", "跟随", "锁定", "搜索", "寻找", "识别", "检测", "目标",
            ],
        )

    def _plan_visual_guidance(
        self,
        command: str,
        steps: list[MissionStep],
        known_tools: set[str],
    ) -> list[MissionStep]:
        """识别/靠近类任务里，保证计划至少有一次"把画面交给视觉模型"的步骤。

        以前这里做的是"确定性补齐检测步骤"：往计划里插 perception_start +
        airsim_detect_objects。检测（YOLO）已从产品中移除，语义只能来自视觉
        模型——所以现在补齐的是 inspect_current_frame：没有它，计划会飞完却
        从未真正看过目标，后面的靠近没有任何依据。
        """
        if "inspect_current_frame" not in known_tools:
            return steps
        if not self._is_close_range_visual_command(command):
            return steps
        if any(step.tool == "inspect_current_frame" for step in steps):
            return steps
        target = self._command_target_class(command) or "目标"
        injected = [
            MissionStep(
                "s00",
                f"视觉确认目标({target})",
                "inspect_current_frame",
                {"question": f"画面里有没有{target}？它在画面的什么位置（归一化坐标 x,y），大致多大（归一化宽高 w,h）？"},
                "perception",
                needs_observation=True,
            )
        ]
        # 插在第一个机动步骤之前；否则插在降落之前；再否则插在末尾只读回读之前。
        insert_at = len(steps)
        for index, step in enumerate(steps):
            if step.tool in {"drone_move_relative", "drone_fly_to", "drone_land"}:
                insert_at = index
                break
        else:
            while insert_at > 0 and steps[insert_at - 1].tool == "drone_get_status":
                insert_at -= 1
        return steps[:insert_at] + injected + steps[insert_at:]

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
            if step.tool == "drone_takeoff":
                return self._positive_float(step.params.get("altitude"), 3.0)
        return 3.0

    def _command_target_class(self, command: str) -> str:
        return extract_target_class(command)

    def _filter_unsolicited_steps(self, command: str, steps: list[MissionStep]) -> list[MissionStep]:
        lower = command.lower()
        intents = extract_intents(command)

        def has_any(words: list[str]) -> bool:
            return any(w in lower for w in words)

        wants_photo = intents["photo"]
        wants_search = intents["search"]
        wants_track = intents["track"]
        wants_land = intents["land"] or intents["return_home"]
        wants_takeoff = intents["takeoff"]
        # 看得见的东西 + 要飞过去 = 必须量距离。以前这里只看 photo/search，于是
        # "飞向画面里那辆浅蓝色汽车"这条指令的深度步骤被静默删掉——模型既没有
        # 米制距离，也就无从推算该走多远。
        wants_visual_approach = bool(intents.get("visual") and intents.get("visual_approach"))
        wants_disarm = has_any(["disarm", "锁定电机", "锁电机"])
        wants_disconnect = has_any(["disconnect", "断开"])

        filtered: list[MissionStep] = []
        for step in steps:
            tool = step.tool
            if tool == "drone_takeoff" and wants_land and not (wants_takeoff or wants_search or wants_photo or wants_track):
                continue
            if tool == "locate_image_point" and not (wants_photo or wants_search or wants_visual_approach):
                continue
            if tool == "airsim_search_target":
                continue
            if tool == "skill:search" and not wants_search:
                continue
            if tool in {"airsim_track_object", "airsim_approach_target", "drone_approach_target",
                        "airsim_detect_objects", "perception_start", "perception_stop"}:
                continue
            if tool == "drone_land" and not wants_land:
                continue
            if tool == "drone_disarm" and not (wants_land or wants_disarm):
                continue
            if tool == "drone_disconnect" and not wants_disconnect:
                continue
            filtered.append(step)
        return filtered
