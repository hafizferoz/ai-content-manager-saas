def register_workspace(client, email="ai-owner@example.com"):
    response = client.post("/api/v1/auth/register", json={
        "name": "AI Owner",
        "email": email,
        "password": "secure-password-123",
        "organization_name": "AI Workspace",
    })
    assert response.status_code == 201, response.text
    result = response.json()
    client.headers.update({"Authorization": f"Bearer {result['access_token']}"})
    return result, result["workspace"]["id"]


def create_research(client, workspace_id, campaign_id=None):
    return client.post(f"/api/v1/workspaces/{workspace_id}/research", json={
        "question": "What should the campaign teach its audience?",
        "campaign_id": campaign_id,
        "sources": [{
            "title": "Product research note",
            "url": "https://example.com/research-note",
            "source_type": "document",
            "excerpt": "A verified internal note says customers want practical onboarding guidance.",
        }],
    })


def test_source_linked_research_strategy_and_creative_workflows(client):
    _, workspace_id = register_workspace(client)
    campaign = client.post(f"/api/v1/workspaces/{workspace_id}/campaigns", json={
        "name": "Onboarding education",
        "objective": "Help customers get started",
        "audience": "New product users",
    }).json()

    research_response = create_research(client, workspace_id, campaign["id"])
    assert research_response.status_code == 201, research_response.text
    research = research_response.json()
    assert research["status"] == "completed"
    assert research["sources"][0]["id"] in research["findings"][0]["source_ids"]
    assert research["agent_run"]["agent_name"] == "market_intelligence"
    assert research["agent_run"]["status"] == "succeeded"

    strategy = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign['id']}/strategy",
        json={"research_record_ids": [research["id"]]},
    )
    assert strategy.status_code == 200, strategy.text
    assert len(strategy.json()["strategy"]["message_pillars"]) >= 1
    assert research["sources"][0]["id"] in strategy.json()["strategy"]["source_ids"]
    strategy_record = strategy.json()["strategy_record"]
    assert strategy_record["campaign_id"] == campaign["id"]
    assert strategy_record["source_ids"] == [research["sources"][0]["id"]]
    assert strategy_record["output"] == strategy.json()["strategy"]
    strategy_history = client.get(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign['id']}/strategies"
    )
    assert strategy_history.status_code == 200
    assert strategy_history.json()[0]["id"] == strategy_record["id"]
    campaigns = client.get(f"/api/v1/workspaces/{workspace_id}/campaigns").json()
    assert campaigns[0]["latest_strategy"]["id"] == strategy_record["id"]

    creative = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign['id']}/creative-concepts",
        json={"brief": "Show a simple first-week onboarding workflow.",
              "research_record_ids": [research["id"]]},
    )
    assert creative.status_code == 200, creative.text
    assert creative.json()["concepts"]["concepts"][0]["storyboard"]


def test_copywriter_creates_editable_draft_and_preview(client):
    _, workspace_id = register_workspace(client)
    research = create_research(client, workspace_id).json()
    response = client.post(f"/api/v1/workspaces/{workspace_id}/content/generate", json={
        "platform": "x",
        "brief": "Explain a practical onboarding lesson using verified customer research.",
        "research_record_ids": [research["id"]],
    })
    assert response.status_code == 201, response.text
    generated = response.json()
    assert generated["status"] == "draft"
    assert generated["version"]["number"] == 1
    assert research["sources"][0]["id"] in generated["version"]["source_references"]
    assert generated["agent_run"]["agent_name"] == "cross_platform_copywriter"
    assert generated["agent_run"]["content_version_id"] == generated["version"]["id"]

    preview = client.get(
        f"/api/v1/workspaces/{workspace_id}/content/{generated['id']}/platform-preview"
    )
    assert preview.status_code == 200
    assert preview.json()["platform"] == "x"
    assert preview.json()["approximate"] is True
    assert preview.json()["character_count"] == len(generated["version"]["body"])

    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "x", "display_name": "X mock", "external_account_id": "x-source-preview",
    }).json()
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{generated['id']}/quality-check"
    ).json()["passed"] is True
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{generated['id']}/ai-review"
    ).json()["passed"] is True
    approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{generated['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "x-preview"},
    )
    assert approval.status_code == 201, approval.text
    approval_card = client.get(f"/api/v1/workspaces/{workspace_id}/approvals").json()[0]
    assert approval_card["source_references"][0]["id"] == research["sources"][0]["id"]
    assert {finding["review_type"] for finding in approval_card["quality_findings"]} == {"ai", "deterministic"}

    runs = client.get(f"/api/v1/workspaces/{workspace_id}/agent-runs")
    assert runs.status_code == 200
    assert any(run["id"] == generated["agent_run"]["id"] for run in runs.json())
    usage = client.get(f"/api/v1/workspaces/{workspace_id}/usage").json()
    assert usage["event_count"] >= 2
    assert usage["input_tokens"] > 0
    assert usage["estimated_events"] == usage["event_count"]

    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "x", "display_name": "X mock", "external_account_id": "x-preview",
    }).json()
    assert account["platform"] == "x"
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Long text", "platform": "x", "body": "x" * 290,
    }).json()
    long_preview = client.get(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/platform-preview"
    ).json()
    assert long_preview["within_limit"] is False


def test_deterministic_brand_review_blocks_prohibited_claim(client):
    _, workspace_id = register_workspace(client)
    client.put(f"/api/v1/workspaces/{workspace_id}/brand", json={
        "name": "Northwind",
        "prohibited_claims": ["guaranteed results"],
    })
    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin", "display_name": "Northwind", "external_account_id": "northwind",
    }).json()
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Claim",
        "platform": "linkedin",
        "body": "We offer guaranteed results.",
    }).json()

    ai_review = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/ai-review"
    )
    assert ai_review.status_code == 200, ai_review.text
    assert ai_review.json()["review_type"] == "ai"
    assert ai_review.json()["passed"] is False

    deterministic = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check"
    )
    assert deterministic.status_code == 200
    submission = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "northwind"},
    )
    assert submission.status_code == 409
    assert "quality review" in submission.json()["detail"]


def test_ai_review_blocking_finding_prevents_approval(client, monkeypatch):
    from app.ai_provider import Generation, MockProvider
    import app.ai_workflows as workflows

    class BlockingReviewer(MockProvider):
        def generate(self, agent_name, system_prompt, input_data):
            generation = super().generate(agent_name, system_prompt, input_data)
            if agent_name == "quality_brand_safety_reviewer":
                output = dict(generation.output)
                output["recommendation"] = "revise"
                output["findings"] = [{
                    "severity": "blocking",
                    "code": "unsupported_claim",
                    "message": "A claim needs evidence.",
                    "remediation": "Attach a source.",
                }]
                return Generation(
                    output=output,
                    provider=generation.provider,
                    model=generation.model,
                    input_tokens=generation.input_tokens,
                    output_tokens=generation.output_tokens,
                    usage_is_estimated=generation.usage_is_estimated,
                )
            return generation

    monkeypatch.setattr(workflows, "create_provider", BlockingReviewer)
    _, workspace_id = register_workspace(client)
    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin", "display_name": "Northwind", "external_account_id": "northwind",
    }).json()
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Claim", "platform": "linkedin", "body": "A new product update.",
    }).json()
    ai_review = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/ai-review"
    )
    assert ai_review.status_code == 200
    assert ai_review.json()["passed"] is False
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check"
    ).json()["passed"] is True
    submission = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "northwind"},
    )
    assert submission.status_code == 409
    assert "AI review" in submission.json()["detail"]


def test_research_and_source_access_are_tenant_scoped(client):
    _, workspace_a = register_workspace(client, "research-a@example.com")
    campaign = client.post(f"/api/v1/workspaces/{workspace_a}/campaigns", json={
        "name": "Private strategy campaign",
        "objective": "Keep campaign strategy private to its workspace",
    }).json()
    strategy = client.post(
        f"/api/v1/workspaces/{workspace_a}/campaigns/{campaign['id']}/strategy",
        json={"research_record_ids": []},
    )
    assert strategy.status_code == 200
    research = create_research(client, workspace_a).json()
    client.headers.pop("Authorization")
    _, workspace_b = register_workspace(client, "research-b@example.com")

    read = client.get(f"/api/v1/workspaces/{workspace_a}/research")
    assert read.status_code == 404
    strategy_read = client.get(
        f"/api/v1/workspaces/{workspace_b}/campaigns/{campaign['id']}/strategies"
    )
    assert strategy_read.status_code == 404
    generate = client.post(f"/api/v1/workspaces/{workspace_b}/content/generate", json={
        "platform": "linkedin",
        "brief": "Use a source outside this workspace.",
        "research_record_ids": [research["id"]],
    })
    assert generate.status_code == 404


def test_provider_configuration_failure_is_recorded_and_reported(client, monkeypatch):
    _, workspace_id = register_workspace(client)
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")

    response = client.post(f"/api/v1/workspaces/{workspace_id}/research", json={
        "question": "What is relevant to our audience?",
    })
    assert response.status_code == 502
    assert "unsupported_provider" in response.json()["detail"]
    runs = client.get(f"/api/v1/workspaces/{workspace_id}/agent-runs").json()
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_code"] == "unsupported_provider"
    research = client.get(f"/api/v1/workspaces/{workspace_id}/research").json()
    assert research[0]["status"] == "failed"


def test_copywriter_provider_rejection_reports_safe_actionable_error(client, monkeypatch):
    import httpx

    _, workspace_id = register_workspace(client)
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def rejected_request(url, headers, json, timeout):
        return httpx.Response(
            401,
            text="provider response containing sensitive details",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.ai_provider.httpx.post", rejected_request)
    response = client.post(f"/api/v1/workspaces/{workspace_id}/content/generate", json={
        "platform": "linkedin",
        "brief": "Draft a practical educational post.",
    })

    assert response.status_code == 502
    assert "provider_authentication_failed" in response.json()["detail"]
    assert "Verify the key is active" in response.json()["detail"]
    assert "sensitive details" not in response.json()["detail"]
    runs = client.get(f"/api/v1/workspaces/{workspace_id}/agent-runs").json()
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_code"] == "provider_authentication_failed"


def test_unscoped_model_source_reference_is_rejected(client, monkeypatch):
    from app.ai_provider import Generation, MockProvider
    import app.ai_workflows as workflows

    class InvalidSourceProvider(MockProvider):
        def generate(self, agent_name, system_prompt, input_data):
            generation = super().generate(agent_name, system_prompt, input_data)
            output = dict(generation.output)
            output["findings"] = [{
                "statement": "Unsupported external citation.",
                "evidence_summary": "No workspace source supports this.",
                "source_ids": [999999],
                "confidence": 0.9,
                "classification": "fact",
            }]
            return Generation(
                output=output,
                provider=generation.provider,
                model=generation.model,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                usage_is_estimated=generation.usage_is_estimated,
            )

    monkeypatch.setattr(workflows, "create_provider", InvalidSourceProvider)
    _, workspace_id = register_workspace(client)
    response = create_research(client, workspace_id)
    assert response.status_code == 502
    assert "unscoped_source_reference" in response.json()["detail"]
    runs = client.get(f"/api/v1/workspaces/{workspace_id}/agent-runs").json()
    assert runs[0]["status"] == "failed"
    assert runs[0]["output"] == {}


def test_research_rejects_credential_urls_and_excessive_excerpt_payload(client):
    _, workspace_id = register_workspace(client)
    credential_url = client.post(f"/api/v1/workspaces/{workspace_id}/research", json={
        "question": "Check a source",
        "sources": [{
            "title": "Credential URL",
            "url": "https://user:password@example.com/report",
            "excerpt": "A source excerpt.",
        }],
    })
    assert credential_url.status_code == 422

    excessive = client.post(f"/api/v1/workspaces/{workspace_id}/research", json={
        "question": "Check source text limits",
        "sources": [{
            "title": f"Source {index}",
            "url": f"https://example.com/{index}",
            "excerpt": "x" * 6000,
        } for index in range(3)],
    })
    assert excessive.status_code == 422
