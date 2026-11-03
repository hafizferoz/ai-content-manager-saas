import httpx
import pytest

from app.ai_provider import AIProviderError, MockProvider, OpenAICompatibleProvider, create_provider


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

    assert error.value.code == "provider_rejected_request"
    assert "secret provider details" not in str(error.value)


def test_provider_factory_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert create_provider().name == "mock"
