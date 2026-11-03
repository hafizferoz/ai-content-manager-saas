from datetime import datetime, timedelta, timezone

from conftest import register


def create_mock_account(client, workspace_id, *, display_name="Phase 4 account"):
    response = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin",
        "display_name": display_name,
        "external_account_id": f"mock-{display_name.lower().replace(' ', '-')}",
        "is_mock": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_analytics_snapshot_import_definitions_filters_export_and_tenant_scope(client):
    owner = register(client, "phase4-analytics-owner@example.com", "Analytics Workspace")
    workspace_id = owner["workspace"]["id"]
    account = create_mock_account(client, workspace_id)
    campaign = client.post(f"/api/v1/workspaces/{workspace_id}/campaigns", json={
        "name": "Snapshot campaign",
        "objective": "Measure audience engagement",
    })
    assert campaign.status_code == 201, campaign.text
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Analytics content",
        "platform": "linkedin",
        "body": "A post connected to an analytics snapshot.",
        "campaign_id": campaign.json()["id"],
    })
    assert content.status_code == 201, content.text

    start = datetime.now(timezone.utc) - timedelta(days=2)
    end = datetime.now(timezone.utc) - timedelta(days=1)
    payload = {
        "social_account_id": account["id"],
        "content_item_id": content.json()["id"],
        "campaign_id": campaign.json()["id"],
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "metrics": {"impressions": 1250, "clicks": 28, "engagement_rate": 2.24},
        "unavailable_metrics": ["reach"],
        "source": "mock",
        "source_reference": "phase4-analytics-run-1",
        "source_note": "=untrusted spreadsheet content",
    }
    created = client.post(f"/api/v1/workspaces/{workspace_id}/analytics/snapshots", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["platform"] == "linkedin"
    assert created.json()["source"] == "mock"

    duplicate = client.post(f"/api/v1/workspaces/{workspace_id}/analytics/snapshots", json=payload)
    assert duplicate.status_code == 409
    definitions = client.get(f"/api/v1/workspaces/{workspace_id}/analytics/definitions").json()
    engagement = next(item for item in definitions["definitions"] if item["key"] == "engagement_rate")
    assert engagement["aggregation"] == "not_comparable"
    assert "do not compare" in engagement["definition"]

    result = client.get(f"/api/v1/workspaces/{workspace_id}/analytics").json()
    assert len(result["snapshots"]) == 1
    assert result["snapshots"][0]["metrics"]["impressions"] == 1250
    assert result["snapshots"][0]["unavailable_metrics"] == ["reach"]
    assert result["freshness"]["snapshot_count"] == 1
    filtered = client.get(
        f"/api/v1/workspaces/{workspace_id}/analytics",
        params={"campaign_id": campaign.json()["id"] + 1},
    )
    assert filtered.status_code == 200
    assert filtered.json()["snapshots"] == []

    export = client.get(f"/api/v1/workspaces/{workspace_id}/analytics/export.csv")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    assert "engagement_rate" in export.text
    assert "1250" in export.text
    assert "'=untrusted spreadsheet content" in export.text

    outsider = register(client, "phase4-analytics-outsider@example.com", "Other Analytics Workspace")
    foreign_snapshot = client.post(
        f"/api/v1/workspaces/{outsider['workspace']['id']}/analytics/snapshots",
        json=payload,
    )
    assert foreign_snapshot.status_code == 404


def test_experiment_log_is_tenant_scoped_validated_and_audited(client):
    owner = register(client, "phase4-experiment-owner@example.com", "Experiment Workspace")
    workspace_id = owner["workspace"]["id"]
    campaign = client.post(f"/api/v1/workspaces/{workspace_id}/campaigns", json={
        "name": "Experiment campaign",
        "objective": "Learn from performance",
    }).json()
    start = datetime.now(timezone.utc).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    payload = {
        "campaign_id": campaign["id"],
        "platform": "linkedin",
        "name": "Short vs long opening",
        "hypothesis": "A shorter opening may improve clicks.",
        "variant": "Use a one-sentence opening.",
        "primary_metric": "clicks",
        "period_start": start,
        "period_end": end,
        "status": "planned",
        "outcome": "",
        "next_action": "",
    }
    created = client.post(f"/api/v1/workspaces/{workspace_id}/experiments", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "planned"
    listed = client.get(f"/api/v1/workspaces/{workspace_id}/experiments").json()
    assert [row["id"] for row in listed] == [created.json()["id"]]

    invalid_metric = {**payload, "primary_metric": "made_up_metric"}
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/experiments",
        json=invalid_metric,
    ).status_code == 422
    invalid_period = {**payload, "period_start": end, "period_end": start}
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/experiments",
        json=invalid_period,
    ).status_code == 422

    outsider = register(client, "phase4-experiment-outsider@example.com", "Other Experiment Workspace")
    foreign_campaign = client.post(
        f"/api/v1/workspaces/{outsider['workspace']['id']}/experiments",
        json={**payload, "campaign_id": campaign["id"]},
    )
    assert foreign_campaign.status_code == 404


def test_community_inbox_draft_review_assignment_and_no_external_send(client):
    owner = register(client, "phase4-community-owner@example.com", "Community Workspace")
    workspace_id = owner["workspace"]["id"]
    account = create_mock_account(client, workspace_id, display_name="Community mock account")
    message_payload = {
        "social_account_id": account["id"],
        "external_message_id": "mock-comment-001",
        "conversation_id": "mock-thread-001",
        "author_name": "Community member",
        "body": "Can you share more about this post?",
        "source": "mock",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    message = client.post(f"/api/v1/workspaces/{workspace_id}/community", json=message_payload)
    assert message.status_code == 201, message.text
    message_id = message.json()["id"]
    assert message.json()["external_send_available"] is False
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/community",
        json=message_payload,
    ).status_code == 409

    assignment = client.patch(
        f"/api/v1/workspaces/{workspace_id}/community/{message_id}/assignment",
        json={"assigned_to": owner["user"]["id"]},
    )
    assert assignment.status_code == 200
    assert assignment.json()["assigned_to"] == owner["user"]["id"]

    reply = client.post(
        f"/api/v1/workspaces/{workspace_id}/community/{message_id}/replies",
        json={"body": "Thanks for asking. Here is more detail."},
    )
    assert reply.status_code == 201
    assert reply.json()["status"] == "draft"
    submitted = client.post(
        f"/api/v1/workspaces/{workspace_id}/community/replies/{reply.json()['id']}/submit"
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"

    approver = register(client, "phase4-community-approver@example.com", "Separate Workspace")
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    added = client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": approver["user"]["email"],
        "role": "approver",
    })
    assert added.status_code == 201
    client.headers.update({"Authorization": f"Bearer {approver['access_token']}"})
    decision = client.post(
        f"/api/v1/workspaces/{workspace_id}/community/replies/{reply.json()['id']}/decision",
        json={"decision": "approved", "comment": "Reviewed as appropriate."},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"

    inbox = client.get(f"/api/v1/workspaces/{workspace_id}/community?status=open")
    assert inbox.status_code == 200
    assert inbox.json()[0]["replies"][0]["status"] == "approved"
    assert inbox.json()[0]["external_send_available"] is False
    resolved = client.patch(
        f"/api/v1/workspaces/{workspace_id}/community/{message_id}/status",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    outsider = register(client, "phase4-community-outsider@example.com", "Other Community Workspace")
    foreign_read = client.get(f"/api/v1/workspaces/{workspace_id}/community")
    assert outsider["workspace"]["id"] != workspace_id
    assert foreign_read.status_code == 404


def test_community_reply_author_cannot_approve_and_mock_inbox_rejects_live_account(client):
    from app.models import SocialAccount
    import app.publishing as publishing

    owner = register(client, "phase4-community-author@example.com", "Community Roles Workspace")
    workspace_id = owner["workspace"]["id"]
    account = create_mock_account(client, workspace_id, display_name="Role test account")
    message = client.post(f"/api/v1/workspaces/{workspace_id}/community", json={
        "social_account_id": account["id"],
        "external_message_id": "role-test-message",
        "conversation_id": "role-test-thread",
        "author_name": "Member",
        "body": "A question",
        "source": "mock",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }).json()
    reply = client.post(
        f"/api/v1/workspaces/{workspace_id}/community/{message['id']}/replies",
        json={"body": "A drafted response."},
    ).json()
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/community/{message['id']}/replies"
    ).status_code == 422
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/community/replies/{reply['id']}/decision",
        json={"decision": "approved"},
    ).status_code == 403

    with publishing.SessionLocal() as db:
        live_account = SocialAccount(
            organization_id=workspace_id,
            platform="linkedin",
            display_name="Connected account",
            external_account_id="urn:li:person:phase4-test",
            is_mock=False,
            active=True,
        )
        db.add(live_account)
        db.commit()
        live_account_id = live_account.id
    rejected = client.post(f"/api/v1/workspaces/{workspace_id}/community", json={
        "social_account_id": live_account_id,
        "external_message_id": "fake-live-source",
        "conversation_id": "fake-live-thread",
        "author_name": "Member",
        "body": "A fabricated provider response should not be marked mock.",
        "source": "mock",
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    assert rejected.status_code == 409
