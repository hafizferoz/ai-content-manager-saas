import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx


class AIProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(self, api_key: str, model: str, base_url: str):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
            raise AIProviderError("invalid_provider_configuration", "The configured AI provider URL is invalid.")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise AIProviderError("invalid_provider_configuration", "Remote AI provider URLs must use HTTPS.")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"

    def generate(self, agent_name: str, system_prompt: str, input_data: dict[str, Any]) -> Generation:
        try:
            response = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
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
            raise AIProviderError("provider_rejected_request", "The configured AI provider rejected the request.")
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


def create_provider() -> StructuredProvider:
    provider_name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "openai-compatible":
        api_key = os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
        if not api_key or not model:
            raise AIProviderError("incomplete_provider_configuration", "Configure LLM_API_KEY and LLM_MODEL for the selected provider.")
        return OpenAICompatibleProvider(api_key, model, base_url)
    raise AIProviderError("unsupported_provider", "The configured AI provider is not supported.")
