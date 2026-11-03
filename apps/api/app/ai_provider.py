import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx


class AIProviderError(Exception):
    def __init__(self, code: str, message: str, provider_attempts: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.provider_attempts = provider_attempts or []


@dataclass(frozen=True)
class Generation:
    output: dict[str, Any]
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usage_is_estimated: bool


class StructuredProvider(Protocol):
    name: str
    model: str

    def generate(self, agent_name: str, system_prompt: str, input_data: dict[str, Any]) -> Generation:
        ...

    def list_models(self) -> list[str]:
        ...

    def validate_api_key(self) -> bool:
        ...


class MockProvider:
    name = "mock"
    model = "deterministic-v1"

    def _research(self, data: dict[str, Any]) -> dict[str, Any]:
        findings = []
        for source in data["sources"]:
            excerpt = source["excerpt"].strip()
            if excerpt:
                findings.append({
                    "statement": f"Source {source['title']} contains a relevant observation.",
                    "evidence_summary": excerpt[:1000],
                    "source_ids": [source["id"]],
                    "confidence": 0.6,
                    "classification": "fact",
                })
        if not findings:
            findings.append({
                "statement": "There is not enough source material to establish a fact.",
                "evidence_summary": "Treat this research question as an unverified hypothesis until sources are added.",
                "source_ids": [],
                "confidence": 0.2,
                "classification": "hypothesis",
            })
        return {
            "findings": findings,
            "recommendations": ["Verify each source and its date before using the finding."],
            "limitations": ["The local mock summarizes only user-provided excerpts; it does not fetch or verify URLs."],
        }

    def _strategy(self, data: dict[str, Any]) -> dict[str, Any]:
        campaign = data["campaign"]
        audience = campaign.get("audience") or data["brand"].get("audience") or "The audience in the campaign brief"
        title = campaign["name"]
        return {
            "audience": audience,
            "message_pillars": [
                {"name": "Problem education", "rationale": f"Connect {title} to an audience need without overstating evidence.", "example_topics": ["A practical how-to", "Common misconceptions"]},
                {"name": "Product value", "rationale": "Explain the value proposition with supportable examples.", "example_topics": ["Product workflow", "Behind the scenes"]},
            ],
            "content_angles": ["Teach a useful concept", "Show a transparent process", "Invite a specific audience question"],
            "call_to_action": "Invite readers to learn more or share their perspective.",
            "success_metrics": ["Qualified engagement", "Relevant link clicks"],
            "cadence_suggestion": "Start with two useful posts per week and adjust using observed results.",
            "source_ids": data["source_ids"],
            "assumptions": ["Cadence is a starting hypothesis, not a performance guarantee."],
        }

    def _copy(self, data: dict[str, Any]) -> dict[str, Any]:
        idea = data["brief"].strip()
        campaign = data.get("campaign") or {}
        brand = data["brand"]
        title = campaign.get("name") or "A useful perspective"
        proposition = brand.get("value_proposition") or ""
        body = idea or proposition or "Share a specific lesson your audience can apply today."
        return {
            "title": title,
            "hook": "A practical idea for your next step:",
            "body": body[:3000],
            "call_to_action": "What would you add?",
            "hashtags": [],
            "source_ids": data["source_ids"],
            "assumptions": ["This is an editable starting draft. Verify all claims before approval."],
        }

    def _creative(self, data: dict[str, Any]) -> dict[str, Any]:
        brief = data["brief"].strip() or data.get("campaign", {}).get("name", "Campaign story")
        return {
            "concepts": [{
                "title": "Show the work",
                "description": f"Create a concise visual sequence that makes the brief tangible: {brief[:300]}",
                "visual_prompt": "Use original, rights-cleared brand assets. Show a clear before/process/after sequence without implying unsupported results.",
                "storyboard": ["Open with the audience problem", "Show one useful process step", "Close with a clear, low-pressure invitation"],
            }],
            "production_notes": ["Confirm rights for every asset.", "Do not depict customer endorsements or results without permission and evidence."],
            "source_ids": data["source_ids"],
        }

    def _review(self, data: dict[str, Any]) -> dict[str, Any]:
        text = data["body"]
        findings = []
        for prohibited in data["brand"].get("prohibited_claims", []):
            if prohibited and prohibited.casefold() in text.casefold():
                findings.append({
                    "severity": "blocking",
                    "code": "prohibited_claim",
                    "message": f"Content includes configured prohibited language: {prohibited}",
                    "remediation": "Remove or substantiate the claim and update the brand policy if appropriate.",
                })
        findings.append({
            "severity": "warning",
            "code": "human_fact_check",
            "message": "A human must verify factual claims, rights, and platform-specific requirements.",
            "remediation": "Add source references and review the exact preview before submitting for approval.",
        })
        return {
            "recommendation": "revise" if any(item["severity"] == "blocking" for item in findings) else "pass",
            "findings": findings,
            "claims_requiring_sources": [],
            "limitations": ["Automated review is advisory and cannot verify truth, legal compliance, rights, or platform policy."],
        }

    def generate(self, agent_name: str, system_prompt: str, input_data: dict[str, Any]) -> Generation:
        operations = {
            "market_intelligence": self._research,
            "content_strategist": self._strategy,
            "cross_platform_copywriter": self._copy,
            "creative_director": self._creative,
            "quality_brand_safety_reviewer": self._review,
        }
        operation = operations.get(agent_name)
        if operation is None:
            raise AIProviderError("unsupported_agent", "This AI workflow is not available.")
        output = operation(input_data)
        source_text = json.dumps({"system": system_prompt, "input": input_data}, ensure_ascii=False, default=str)
        output_text = json.dumps(output, ensure_ascii=False)
        return Generation(
            output=output,
            provider=self.name,
            model=self.model,
            input_tokens=max(1, len(source_text) // 4),
            output_tokens=max(1, len(output_text) // 4),
            usage_is_estimated=True,
        )

    def list_models(self) -> list[str]:
        return [self.model]

    def validate_api_key(self) -> bool:
        return True


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        provider_name: str = "openai-compatible",
        headers: dict[str, str] | None = None,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
            raise AIProviderError("invalid_provider_configuration", "The configured AI provider URL is invalid.")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise AIProviderError("invalid_provider_configuration", "Remote AI provider URLs must use HTTPS.")
        self.api_key = api_key
        self.model = model
        self.name = provider_name
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}", **(headers or {})}

    def generate(self, agent_name: str, system_prompt: str, input_data: dict[str, Any]) -> Generation:
        try:
            response = httpx.post(
                self.url,
                headers=self.headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(input_data, ensure_ascii=False, default=str)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "max_tokens": 1800,
                },
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except httpx.TimeoutException as error:
            raise AIProviderError("provider_timeout", "The configured AI provider timed out.") from error
        except httpx.RequestError as error:
            raise AIProviderError("provider_unavailable", "The configured AI provider could not be reached.") from error
        if response.is_error:
            code, message = _provider_rejection(
                response.status_code,
                _known_provider_error_code(response) if response.status_code == 429 else None,
            )
            raise AIProviderError(code, message)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            output = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise AIProviderError("invalid_provider_response", "The configured AI provider returned an invalid response.") from error
        usage = payload.get("usage") or {}
        try:
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
        except (ValueError, TypeError) as error:
            raise AIProviderError("invalid_provider_response", "The configured AI provider returned invalid usage data.") from error
        return Generation(
            output=output,
            provider=self.name,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_is_estimated=not bool(input_tokens and output_tokens),
        )

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(
                self.base_url + "/models",
                headers=self.headers,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        except httpx.TimeoutException as error:
            raise AIProviderError("provider_timeout", "The AI provider timed out.") from error
        except httpx.RequestError as error:
            raise AIProviderError("provider_unavailable", "The AI provider could not be reached.") from error
        if response.is_error:
            code, message = _provider_rejection(
                response.status_code,
                _known_provider_error_code(response) if response.status_code == 429 else None,
            )
            raise AIProviderError(code, message)
        try:
            payload = response.json()
            return sorted({
                entry["id"] for entry in payload["data"]
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            })
        except (ValueError, KeyError, TypeError) as error:
            raise AIProviderError("invalid_provider_response", "The AI provider returned an invalid model list.") from error

    def validate_api_key(self) -> bool:
        self.list_models()
        return True


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.url = f"{self.base_url}/models/{model}:generateContent"

    def generate(self, agent_name: str, system_prompt: str, input_data: dict[str, Any]) -> Generation:
        try:
            response = httpx.post(
                self.url,
                headers={"x-goog-api-key": self.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{
                        "role": "user",
                        "parts": [{"text": json.dumps(input_data, ensure_ascii=False, default=str)}],
                    }],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.3,
                        "maxOutputTokens": 1800,
                    },
                },
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except httpx.TimeoutException as error:
            raise AIProviderError("provider_timeout", "The configured AI provider timed out.") from error
        except httpx.RequestError as error:
            raise AIProviderError("provider_unavailable", "The configured AI provider could not be reached.") from error
        if response.is_error:
            code, message = _provider_rejection(
                response.status_code,
                _known_provider_error_code(response) if response.status_code == 429 else None,
            )
            raise AIProviderError(code, message)
        try:
            payload = response.json()
            content = payload["candidates"][0]["content"]["parts"][0]["text"]
            output = json.loads(content)
            usage = payload.get("usageMetadata") or {}
            input_tokens = int(usage.get("promptTokenCount") or 0)
            output_tokens = int(usage.get("candidatesTokenCount") or 0)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise AIProviderError("invalid_provider_response", "The configured AI provider returned an invalid response.") from error
        return Generation(
            output=output,
            provider=self.name,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_is_estimated=not bool(input_tokens and output_tokens),
        )

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(
                self.base_url + "/models",
                headers={"x-goog-api-key": self.api_key},
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        except httpx.TimeoutException as error:
            raise AIProviderError("provider_timeout", "The AI provider timed out.") from error
        except httpx.RequestError as error:
            raise AIProviderError("provider_unavailable", "The AI provider could not be reached.") from error
        if response.is_error:
            code, message = _provider_rejection(
                response.status_code,
                _known_provider_error_code(response) if response.status_code == 429 else None,
            )
            raise AIProviderError(code, message)
        try:
            payload = response.json()
            return sorted({
                entry["name"].removeprefix("models/")
                for entry in payload["models"]
                if isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and "generateContent" in entry.get("supportedGenerationMethods", [])
            })
        except (ValueError, KeyError, TypeError) as error:
            raise AIProviderError("invalid_provider_response", "The AI provider returned an invalid model list.") from error

    def validate_api_key(self) -> bool:
        self.list_models()
        return True


PROVIDER_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openai": "https://api.openai.com/v1",
}


def create_named_provider(
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> StructuredProvider:
    if not api_key or not model:
        raise AIProviderError("incomplete_provider_configuration", "Configure an API key and model for the selected provider.")
    if provider_name == "gemini":
        return GeminiProvider(api_key, model)
    if provider_name in PROVIDER_ENDPOINTS:
        headers = {"HTTP-Referer": "https://ai-content-manager.local", "X-Title": "AI Content Manager"} if provider_name == "openrouter" else None
        return OpenAICompatibleProvider(
            api_key,
            model,
            base_url or PROVIDER_ENDPOINTS[provider_name],
            provider_name=provider_name,
            headers=headers,
        )
    if provider_name == "openai-compatible":
        return OpenAICompatibleProvider(
            api_key,
            model,
            base_url or "https://api.openai.com/v1",
        )
    raise AIProviderError("unsupported_provider", "The configured AI provider is not supported.")


def _known_provider_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None
    error = payload["error"]
    for field in ("code", "type", "status"):
        if error.get(field) in ("insufficient_quota", "rate_limit_exceeded", "rate_limit_error"):
            return error[field]
    return None


def _provider_rejection(status_code: int, provider_error_code: str | None = None) -> tuple[str, str]:
    if status_code == 400 or status_code == 422:
        return (
            "provider_invalid_request",
            "The AI provider rejected the request format or model parameters. Check that the selected model supports chat completions and JSON mode.",
        )
    if status_code == 401:
        return (
            "provider_authentication_failed",
            "The AI provider rejected its configured API key. Verify the key is active and belongs to the configured provider.",
        )
    if status_code == 403:
        return (
            "provider_access_denied",
            "The AI provider denied access to the configured model or account. Check model access and project permissions.",
        )
    if status_code == 404:
        return (
            "provider_endpoint_or_model_not_found",
            "The AI provider endpoint or model was not found. Check the configured base URL and model identifier.",
        )
    if status_code == 408 or status_code == 504:
        return (
            "provider_timeout",
            "The AI provider timed out. Try again later.",
        )
    if status_code == 413:
        return (
            "provider_context_limit_exceeded",
            "The AI provider rejected the request because it exceeded a request or context limit. Shorten the brief or reduce supplied research.",
        )
    if status_code == 402 or provider_error_code in ("insufficient_quota", "QUOTA_EXCEEDED"):
        return (
            "provider_quota_exceeded",
            "The AI provider reports that the account has insufficient quota. Check billing, available credits, and project spending limits.",
        )
    if status_code == 429:
        if provider_error_code == "insufficient_quota":
            return (
                "provider_quota_exceeded",
                "The AI provider reports that the account has insufficient quota. Check billing, available credits, and project spending limits.",
            )
        if provider_error_code in ("rate_limit_exceeded", "rate_limit_error"):
            return (
                "provider_rate_limited",
                "The AI provider rate limit was reached. Wait before retrying or reduce request frequency.",
            )
        return (
            "provider_rate_limited",
            "The AI provider rate-limited the request or the account has insufficient quota. Check provider usage and retry after the limit resets.",
        )
    if status_code in (500, 502, 503):
        return (
            "provider_service_unavailable",
            "The AI provider is temporarily unavailable. Try again later.",
        )
    return (
        "provider_rejected_request",
        f"The AI provider rejected the request (HTTP {status_code}). Check provider configuration and account status.",
    )


def create_provider() -> StructuredProvider:
    provider_name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    if provider_name == "mock":
        return MockProvider()
    if provider_name not in {"openai-compatible", "gemini", "groq", "openrouter", "mistral", "openai"}:
        raise AIProviderError("unsupported_provider", "The configured AI provider is not supported.")
    prefix = provider_name.upper().replace("-", "_")
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip() or os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv(f"{prefix}_MODEL", "").strip() or os.getenv("LLM_MODEL", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip() if provider_name == "openai-compatible" else None
    return create_named_provider(provider_name, api_key, model, base_url)
