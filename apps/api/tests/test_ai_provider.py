import httpx
import pytest

from app.ai_provider import (
    AIProviderError,
    GeminiProvider,
    MockProvider,
    OpenAICompatibleProvider,
    _known_provider_error_code,
    _provider_rejection,
    create_provider,
    create_named_provider,
)


def test_mock_provider_returns_structured_research_and_estimated_usage():
    result = MockProvider().generate(
        "market_intelligence",
        "Return a source-linked summary.",
        {"question": "What matters?", "sources": []},
    )
    assert result.provider == "mock"
    assert result.usage_is_estimated is True
    assert result.output["findings"][0]["classification"] == "hypothesis"
    assert result.output["findings"][0]["source_ids"] == []


def test_unknown_agent_is_reported_by_mock_provider():
    with pytest.raises(AIProviderError) as error:
        MockProvider().generate("unavailable_agent", "", {})
    assert error.value.code == "unsupported_agent"


def test_remote_provider_url_requires_https():
    with pytest.raises(AIProviderError) as error:
        OpenAICompatibleProvider("secret", "model", "http://provider.example/v1")
    assert error.value.code == "invalid_provider_configuration"
    assert OpenAICompatibleProvider("secret", "model", "http://localhost:8001/v1")


def test_openai_compatible_adapter_sends_json_mode_and_reads_usage(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = json
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.ai_provider.httpx.post", fake_post)
    provider = OpenAICompatibleProvider("test-only-key", "test-model", "https://provider.example/v1")
    result = provider.generate("agent", "Schema instructions", {"brief": "hello"})

    assert seen["url"] == "https://provider.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-only-key"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert result.output == {"result": "ok"}
    assert result.input_tokens == 12
    assert result.output_tokens == 4
    assert result.usage_is_estimated is False


def test_gemini_adapter_uses_json_generation_and_reads_usage(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update({"url": url, "headers": headers, "payload": json})
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"result":"ok"}'}]}}],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.ai_provider.httpx.post", fake_post)
    provider = GeminiProvider("test-only-key", "current-model")
    result = provider.generate("agent", "Schema instructions", {"brief": "hello"})

    assert seen["url"].endswith("/models/current-model:generateContent")
    assert seen["headers"] == {"x-goog-api-key": "test-only-key"}
    assert seen["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert result.output == {"result": "ok"}
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.usage_is_estimated is False


@pytest.mark.parametrize(
    ("name", "expected_base"),
    [
        ("groq", "https://api.groq.com/openai/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("mistral", "https://api.mistral.ai/v1"),
        ("openai", "https://api.openai.com/v1"),
    ],
)
def test_named_openai_compatible_providers_use_official_api_endpoints(name, expected_base):
    provider = create_named_provider(name, "test-only-key", "account-model")

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == name
    assert provider.base_url == expected_base
    assert provider.model == "account-model"
    assert provider.headers["Authorization"] == "Bearer test-only-key"


def test_provider_errors_do_not_expose_provider_response_body(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return httpx.Response(
            401,
            text="authorization failed for secret provider details",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.ai_provider.httpx.post", fake_post)
    provider = OpenAICompatibleProvider("test-only-key", "test-model", "https://provider.example/v1")
    with pytest.raises(AIProviderError) as error:
        provider.generate("agent", "prompt", {})

    assert error.value.code == "provider_authentication_failed"
    assert "API key" in str(error.value)
    assert "secret provider details" not in str(error.value)


@pytest.mark.parametrize(
    ("provider_code", "expected_code", "message_fragment"),
    [
        ("insufficient_quota", "provider_quota_exceeded", "billing"),
        ("rate_limit_exceeded", "provider_rate_limited", "Wait before retrying"),
        ("rate_limit_error", "provider_rate_limited", "Wait before retrying"),
    ],
)
def test_provider_429_errors_are_safely_classified(provider_code, expected_code, message_fragment):
    sensitive_detail = "internal provider detail"
    response = httpx.Response(
        429,
        json={"error": {"code": provider_code, "message": sensitive_detail}},
    )

    safe_code = _known_provider_error_code(response)
    code, message = _provider_rejection(response.status_code, safe_code)

    assert code == expected_code
    assert message_fragment in message
    assert sensitive_detail not in message


def test_unknown_provider_429_details_are_not_retained():
    response = httpx.Response(
        429,
        json={"error": {"code": "unknown", "message": "sensitive provider detail"}},
    )

    assert _known_provider_error_code(response) is None


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "provider_invalid_request"),
        (403, "provider_access_denied"),
        (404, "provider_endpoint_or_model_not_found"),
        (408, "provider_timeout"),
        (413, "provider_context_limit_exceeded"),
        (429, "provider_rate_limited"),
        (503, "provider_service_unavailable"),
        (418, "provider_rejected_request"),
    ],
)
def test_provider_rejection_status_maps_to_safe_actionable_error(status_code, expected_code):
    code, message = _provider_rejection(status_code)

    assert code == expected_code
    assert message
    assert str(status_code) not in message or status_code == 418


def test_provider_factory_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert create_provider().name == "mock"
