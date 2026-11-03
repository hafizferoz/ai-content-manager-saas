from sqlalchemy import select

from app.models import SocialAccountCapability
import app.publishing as publishing
from conftest import register


def test_platform_capability_catalog_is_scoped_and_real_apis_are_disabled(client):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]

    response = client.get(
        f"/api/v1/workspaces/{workspace_id}/integrations/capabilities"
    )

    assert response.status_code == 200
    registry = response.json()
    assert {item["platform"] for item in registry} == {
        "linkedin", "instagram", "facebook", "youtube", "x", "tiktok",
    }
    for platform in registry:
        expected_status = "not_configured" if platform["platform"] == "linkedin" else "not_implemented"
        assert platform["connection_mode"] == "mock_only"
        assert platform["real_connection_status"] == expected_status
        publish = next(capability for capability in platform["capabilities"] if capability["name"] == "publish")
        assert publish["mock_status"] == "available"
        assert publish["real_status"] == "not_implemented"
        assert publish["required_permissions"] == (
            ["w_member_social"] if platform["platform"] == "linkedin" else []
        )

    client.headers.pop("Authorization")
    other_owner = register(client, "other-capability-owner@example.com", "Other workspace")
    denied = client.get(
        f"/api/v1/workspaces/{owner['workspace']['id']}/integrations/capabilities"
    )
    assert other_owner["workspace"]["id"] != owner["workspace"]["id"]
    assert denied.status_code == 404


def test_mock_account_persists_capabilities_and_rejects_real_or_unknown_accounts(client):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]

    created = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin",
        "display_name": "Local LinkedIn",
        "external_account_id": "local-page",
        "is_mock": True,
    })
    assert created.status_code == 201, created.text
    capabilities = {entry["capability"]: entry for entry in created.json()["capabilities"]}
    assert capabilities["publish"]["status"] == "available"
    assert capabilities["publish"]["enabled"] is True
    assert capabilities["analytics"]["enabled"] is False
    assert capabilities["oauth"]["enabled"] is False

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()
    listed_capabilities = {entry["capability"]: entry for entry in listed[0]["capabilities"]}
    assert {
        name: (entry["status"], entry["enabled"])
        for name, entry in listed_capabilities.items()
    } == {
        name: (entry["status"], entry["enabled"])
        for name, entry in capabilities.items()
    }

    real_account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin",
        "display_name": "External account",
        "external_account_id": "external-page",
        "is_mock": False,
    })
    assert real_account.status_code == 400

    unsupported_platform = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "unlisted-network",
        "display_name": "Unknown",
        "external_account_id": "unknown",
    })
    assert unsupported_platform.status_code == 422


def test_worker_rechecks_publish_capability_immediately_before_adapter(client, monkeypatch):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]
    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin",
        "display_name": "Local page",
        "external_account_id": "local-page",
    }).json()
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Post",
        "platform": "linkedin",
        "body": "An approved draft.",
    }).json()
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check"
    ).json()["passed"]
    approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "local-page"},
    ).json()

    reviewer = client.post("/api/v1/auth/register", json={
        "name": "Approver",
        "email": "capability-approver@example.com",
        "password": "valid-approver-password-123",
        "organization_name": "Separate",
    }).json()
    client.headers.update({"Authorization": "Bearer " + owner["access_token"]})
    assert client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "capability-approver@example.com",
        "role": "approver",
    }).status_code == 201
    client.headers.update({"Authorization": "Bearer " + reviewer["access_token"]})
    assert client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved",
    }).status_code == 200

    job = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"],
        "idempotency_key": "capability-test-key",
    }).json()

    original_validate = publishing.validate_publish_authorization
    validation_calls = 0

    def revoke_after_dispatch_validation(db, publish_job):
        nonlocal validation_calls
        validation_calls += 1
        result = original_validate(db, publish_job)
        if validation_calls == 1:
            capability = db.scalar(select(SocialAccountCapability).where(
                SocialAccountCapability.social_account_id == account["id"],
                SocialAccountCapability.capability == "publish",
            ))
            capability.enabled = False
            capability.status = "revoked"
            db.flush()
        return result

    monkeypatch.setattr(publishing, "validate_publish_authorization", revoke_after_dispatch_validation)
    dispatched = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{job['id']}/dispatch"
    )
    assert validation_calls == 2
    assert dispatched.status_code == 409
    assert "publish capability" in dispatched.json()["detail"]
    jobs = client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()
    assert jobs[0]["status"] == "blocked"
