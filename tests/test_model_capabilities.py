"""Provider-catalog capability probing and multimodal inference priority."""

import json

from src.agent.llm import (
    ModelRegistry,
    _catalog_urls,
    derive_provider,
    list_provider_models,
    infer_model_capabilities,
    parse_catalog_capabilities,
    probe_model_capabilities,
    reasoning_profile,
)


def test_sanitize_bounded_on_self_referential_state():
    # 回归：带图消息进入 run 状态后，共享可变字典可能形成循环引用，
    # _sanitize_for_frontend 必须被深度上限拦住而不是打爆递归。
    from src.agent.runtime import AgentRuntime

    runtime = AgentRuntime.__new__(AgentRuntime)
    node: dict = {"data_url": "data:image/png;base64,AAAA"}
    node["self"] = node
    result = runtime._sanitize_for_frontend({"messages": [node]})
    assert result["messages"][0]["data_url_omitted"] is True
    assert "[bounded]" in str(result)


def test_llm_image_walkers_bounded_on_self_referential_result():
    # 回归：工具结果里共享的可变字典形成循环引用时，
    # _contains_image_data / _trim_image_payload 不能打爆 agent loop 线程。
    from src.agent.llm import LLMMissionPlanner

    planner = LLMMissionPlanner.__new__(LLMMissionPlanner)
    node: dict = {"saved_to": "captures/target.png"}
    node["self"] = node
    assert planner._contains_image_data({"result": node}) is True
    # detections 在白名单里，自引用会真正走递归路径
    loop: dict = {"detections": None}
    loop["detections"] = loop
    trimmed = planner._trim_image_payload(loop)
    assert "[bounded]" in str(trimmed)


def _public(registry: ModelRegistry, model_id: str) -> dict:
    return next(m for m in registry.list_public() if m["id"] == model_id)


def test_manual_override_beats_provider_catalog():
    caps = infer_model_capabilities(
        "gpt-4o", "openai", "text", {"input_modalities": ["text", "image"]}
    )
    assert caps["multimodal"] is False
    assert caps["capability_source"] == "manual"


def test_provider_catalog_beats_model_id_hints():
    # "gpt-4o" matches the keyword hints, but the catalog says text-only.
    caps = infer_model_capabilities(
        "gpt-4o", "openai", "auto", {"input_modalities": ["text"]}
    )
    assert caps["multimodal"] is False
    assert caps["capability_source"] == "provider_catalog"


def test_provider_catalog_marks_dots_model_visual():
    caps = infer_model_capabilities(
        "dots-studio/dots-3-note-preview:free",
        "openrouter",
        "auto",
        {"input_modalities": ["text", "image"], "context_length": 512_000},
    )
    assert caps["multimodal"] is True
    assert caps["capability_source"] == "provider_catalog"
    assert caps["input_modes"] == ["text", "image"]
    assert caps["context_window"] == 512_000


def test_missing_catalog_falls_back_to_model_id():
    caps = infer_model_capabilities("dots-studio/dots-3-note-preview:free", "openrouter")
    assert caps["multimodal"] is False
    assert caps["capability_source"] == "model_id"


def test_catalog_context_window_wins_over_hints():
    caps = infer_model_capabilities(
        "dots-studio/dots-3-note-preview:free",
        "openrouter",
        "auto",
        {"input_modalities": ["text", "image"], "context_length": 512_000},
    )
    assert caps["context_window"] == 512_000


def test_parse_openrouter_entry():
    entry = {
        "id": "dots-studio/dots-3-note-preview:free",
        "architecture": {
            "modality": "text+image->text",
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
        },
        "context_length": 512_000,
    }
    parsed = parse_catalog_capabilities(entry, model_id=entry["id"])
    assert parsed["input_modalities"] == ["text", "image"]
    assert parsed["output_modalities"] == ["text"]
    assert parsed["context_length"] == 512_000
    assert parsed["source"] == "provider_catalog"


def test_parse_vllm_entry_context_only():
    entry = {"id": "meta-llama/Llama-3-8B", "max_model_len": 8192}
    parsed = parse_catalog_capabilities(entry)
    assert parsed == {"source": "provider_catalog", "context_length": 8192}


def test_parse_uninformative_entry_returns_empty():
    assert parse_catalog_capabilities({"id": "plain-model"}) == {}
    assert parse_catalog_capabilities("not-a-dict") == {}


def test_parse_anthropic_entry_maps_claude_to_image():
    parsed = parse_catalog_capabilities({"id": "claude-sonnet-4-5"}, model_id="claude-sonnet-4-5", api_type="anthropic")
    assert parsed["input_modalities"] == ["text", "image"]
    legacy = parse_catalog_capabilities({"id": "claude-2.1"}, model_id="claude-2.1", api_type="anthropic")
    assert "input_modalities" not in legacy


def test_probe_detects_image_input(monkeypatch):
    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    catalog = {
        "data": [
            {
                "id": "dots-studio/dots-3-note-preview:free",
                "architecture": {"input_modalities": ["text", "image"]},
                "context_length": 512_000,
            }
        ]
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResponse(catalog))
    result = probe_model_capabilities(
        "dots-studio/dots-3-note-preview:free", "openai", "https://openrouter.ai/api/v1", "sk-test"
    )
    assert result["ok"] is True
    assert result["capabilities"]["input_modalities"] == ["text", "image"]


def test_probe_never_raises_on_network_failure(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    result = probe_model_capabilities("m", "openai", "https://127.0.0.1:9", "k")
    assert result["ok"] is False


def test_probe_reports_model_missing_from_catalog(monkeypatch):
    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResponse({"data": [{"id": "other-model"}]})
    )
    result = probe_model_capabilities("m", "openai", "https://x.example/v1", "k")
    assert result["ok"] is False
    assert "not listed" in result["error"]


def test_registry_uses_cached_capabilities(tmp_path):
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({
        "id": "dots",
        "model": "dots-studio/dots-3-note-preview:free",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "k",
        "api_type": "openai",
    })
    public = _public(registry, "dots")
    assert public["multimodal"] is False  # no catalog data yet, keyword miss

    registry.set_capabilities("dots", {"input_modalities": ["text", "image"], "context_length": 512_000})
    public = _public(registry, "dots")
    assert public["multimodal"] is True
    assert public["capability_source"] == "provider_catalog"
    assert public["context_window"] == 512_000
    assert public["capabilities"]["input_modalities"] == ["text", "image"]


def test_registry_manual_override_wins_over_catalog(tmp_path):
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({
        "id": "m",
        "model": "gpt-4o",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "k",
        "api_type": "openai",
        "capability_mode": "text",
    })
    registry.set_capabilities("m", {"input_modalities": ["text", "image"]})
    public = _public(registry, "m")
    assert public["multimodal"] is False
    assert public["capability_source"] == "manual"


def test_registry_persists_thinking_settings(tmp_path):
    """思考模式 / 思考等级是请求级参数，必须真的存下来并回读。"""
    path = tmp_path / "models.json"
    registry = ModelRegistry(path=path)
    registry.add({
        "id": "m",
        "model": "deepseek-v4",
        "provider": "deepseek",
        "api_key": "k",
        "thinking_mode": "enabled",
        "reasoning_effort": "high",
    })

    reloaded = ModelRegistry(path=path)
    entry = reloaded.get("m")

    assert entry["thinking_mode"] == "enabled"
    assert entry["reasoning_effort"] == "high"
    assert _public(reloaded, "m")["reasoning_effort"] == "high"


def test_registry_drops_invalid_thinking_settings(tmp_path):
    """界面填错的值不能原样转发给上游，否则请求会被模型端 400 拒绝。"""
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({
        "id": "m",
        "model": "gpt-4o",
        "api_key": "k",
        "thinking_mode": "banana",
        "reasoning_effort": "ultra",
    })

    entry = registry.get("m")

    assert "thinking_mode" not in entry
    assert "reasoning_effort" not in entry


def test_registry_update_can_set_and_clear_thinking_settings(tmp_path):
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({"id": "m", "model": "deepseek-v4", "api_key": "k"})

    registry.update("m", {"thinking_mode": "enabled", "reasoning_effort": "max"})
    assert registry.get("m")["reasoning_effort"] == "max"

    # 空字符串 = 回到模型默认，字段要被移除而不是留个空值
    registry.update("m", {"reasoning_effort": "", "thinking_mode": "disabled"})
    entry = registry.get("m")
    assert "reasoning_effort" not in entry
    assert entry["thinking_mode"] == "disabled"

    # 非法值同样按"清除"处理
    registry.update("m", {"thinking_mode": "nonsense"})
    assert "thinking_mode" not in registry.get("m")


def test_reasoning_profile_uses_catalog_supported_efforts():
    """厂商目录声明了哪些档位，界面就只该给哪些档位。"""
    profile = reasoning_profile({
        "id": "m",
        "model": "openai/gpt-astra-latest",
        "capabilities": {
            "reasoning": {"mandatory": True, "supported_efforts": ["max", "xhigh", "high", "medium", "low"], "default_effort": "medium"},
            "source": "provider_catalog",
        },
    })

    assert profile["levels"] == ["low", "medium", "high", "max"]  # xhigh 不在我方请求体取值域内
    assert profile["default"] == "medium"
    assert profile["mandatory"] is True
    assert profile["source"] == "provider_catalog"


def test_reasoning_profile_falls_back_to_reasoning_capable_models():
    profile = reasoning_profile({
        "id": "m",
        "model": "dots-studio/dots-3",
        "capabilities": {"supported_parameters": ["reasoning", "tools"], "reasoning": {"mandatory": False}},
    })

    assert profile["levels"] == ["low", "medium", "high"]
    assert profile["mandatory"] is False


def test_reasoning_profile_uses_model_family_when_catalog_is_silent():
    """读不到厂商目录时按模型族兜底：DeepSeek V4 官方思考强度是 low/high/max 三档。"""
    profile = reasoning_profile({"id": "deepseek", "model": "deepseek-v4-flash", "capabilities": None})

    assert profile["levels"] == ["low", "high", "max"]
    assert profile["source"] == "model_id_heuristic"
    assert profile["recognized"] is True


def test_reasoning_profile_marks_unknown_model_as_unrecognized():
    """识别不出依据时要能区分"未识别"与"不支持"，别让用户误判模型能力。"""
    profile = reasoning_profile({"id": "m", "model": "vendor-mystery-xl", "capabilities": None})

    assert profile["recognized"] is False
    assert profile["levels"] == []


def test_reasoning_profile_reports_unsupported_model():
    profile = reasoning_profile({"id": "local", "model": "some-local-llama", "capabilities": None})

    assert profile["supports_thinking"] is False
    assert profile["levels"] == []


def test_public_model_exposes_reasoning_profile(tmp_path):
    registry = ModelRegistry(path=tmp_path / "models.json")
    registry.add({"id": "m", "model": "deepseek-v4-flash", "api_key": "k"})

    public = _public(registry, "m")

    assert public["reasoning"]["supports_thinking"] is True
    assert public["reasoning"]["levels"] == ["low", "high", "max"]


def test_derive_provider_from_base_url():
    """用户只填 Base URL，provider 由主机名推断（仅用于界面分组）。"""
    assert derive_provider("https://openrouter.ai/api/v1", "openai", "dots-3") == "openrouter"
    assert derive_provider("https://api.deepseek.com/v1", "openai", "deepseek-v4-flash") == "deepseek"
    assert derive_provider("https://api.openai.com/v1", "openai", "gpt-4o") == "openai"
    assert derive_provider("http://127.0.0.1:8000/v1", "openai", "qwen3-32b") == "local"
    assert derive_provider("https://my-llm.company.com/v1", "openai", "custom") == "company"


def test_derive_provider_falls_back_to_api_type():
    assert derive_provider("", "anthropic", "claude-sonnet-4-20250514") == "anthropic"
    assert derive_provider("", "openai", "whatever") == "openai"


def _catalog_response(payload):
    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self):
            return json.dumps(self._body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _FakeResponse(payload)


def test_catalog_urls_fall_back_to_the_other_compat_dialect():
    """DeepSeek 的 Anthropic 入口没有 /models，必须兜底试 OpenAI 入口。"""
    urls = _catalog_urls("https://api.deepseek.com/anthropic", "anthropic")

    assert "https://api.deepseek.com/models" in urls
    assert urls[0] == "https://api.deepseek.com/anthropic/v1/models"


def test_list_provider_models_reads_official_names(monkeypatch):
    """模型命名以厂商目录为准：官方返回什么名字就用什么名字。"""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _catalog_response({
            "object": "list",
            "data": [
                {"id": "deepseek-flash", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
            ],
        }),
    )

    result = list_provider_models("https://api.deepseek.com/anthropic", "anthropic", "sk-x")

    assert result["ok"] is True
    assert [m["id"] for m in result["models"]] == ["deepseek-flash", "deepseek-v4-pro"]
    assert result["models"][1]["reasoning_levels"] == ["low", "high", "max"]


def test_probe_succeeds_when_catalog_only_lists_the_id(monkeypatch):
    """目录里只有 id（DeepSeek 就是这样）也算识别成功，不该报错。"""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _catalog_response({"data": [{"id": "deepseek-flash"}]}),
    )

    result = probe_model_capabilities("deepseek-flash", "openai", "https://api.deepseek.com/v1", "k")

    assert result["ok"] is True
    assert result["catalog_metadata"] is False
    assert "按模型 ID 推断" in result["message"]


def test_probe_missing_model_reports_available_ids(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _catalog_response({"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}),
    )

    result = probe_model_capabilities("deepseek-nope", "openai", "https://api.deepseek.com/v1", "k")

    assert result["ok"] is False
    assert result["available_models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert "deepseek-flash" in result["message"]
