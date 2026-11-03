from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.linkedin_oauth import LinkedInToken, decrypt_token, encrypt_token
from app.models import (
    Approval,
    ContentItem,
    ContentVersion,
    LinkedInCredential,
    PublishJob,
    SocialAccount,
)
import app.publishing as publishing
from conftest import register


def configure_linkedin(monkeypatch, *, scopes=None):
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "LINKEDIN_REDIRECT_URI",
        "http://localhost:8000/api/v1/integrations/linkedin/callback",
    )
    monkeypatch.setenv("LINKEDIN_API_VERSION", "202610")
    monkeypatch.setenv("SOCIAL_TOKEN_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setenv("PUBLISHING_ENABLED", "true")
    token = LinkedInToken(
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        scopes=scopes if scopes is not None else ["openid", "profile", "w_member_social"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    return encryption_key, token


def start_connection(client, workspace_id):
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/integrations/linkedin/connect"
    )
    assert response.status_code == 200, response.text
    authorization_url = response.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split()) == {"openid", "profile", "w_member_social"}
    return query["state"][0]


def create_reconciliation_job(workspace_id, user_id, *, status="reconciliation_required", error_message=""):
    with publishing.SessionLocal() as db:
        account = SocialAccount(
            organization_id=workspace_id,
            platform="linkedin",
            display_name="LinkedIn reconciliation test",
            external_account_id="urn:li:person:reconciliation-test",
            is_mock=False,
            active=True,
        )
        db.add(account)
        db.flush()
        item = ContentItem(
            organization_id=workspace_id,
            title="Reconciliation test post",
            platform="linkedin",
            created_by=user_id,
        )
        db.add(item)
        db.flush()
        version = ContentVersion(
            content_item_id=item.id,
            number=1,
            body="Approved text for a reconciliation test.",
            fingerprint="a" * 64,
            created_by=user_id,
        )
        db.add(version)
        db.flush()
        item.current_version_id = version.id
        approval = Approval(
            organization_id=workspace_id,
            content_item_id=item.id,
            content_version_id=version.id,
            version_fingerprint=version.fingerprint,
            brand_fingerprint="b" * 64,
            social_account_id=account.id,
            destination=account.external_account_id,
            action="publish",
            status="approved",
            requested_by=user_id,
            decided_by=user_id,
            decided_at=datetime.now(timezone.utc),
        )
        db.add(approval)
        db.flush()
        job = PublishJob(
            organization_id=workspace_id,
            approval_id=approval.id,
            idempotency_key=f"reconcile-{workspace_id}-{status}-{user_id}-{account.id}",
            status=status,
            error_message=error_message,
        )
        db.add(job)
        db.commit()
        return job.id


def test_linkedin_oauth_state_is_single_use_and_tokens_are_encrypted(client, monkeypatch):
    from app import main

    encryption_key, token = configure_linkedin(monkeypatch)
    owner = register(client)
    workspace_id = owner["workspace"]["id"]
    state = start_connection(client, workspace_id)
    monkeypatch.setattr(main, "exchange_code", lambda settings, code: token)
    monkeypatch.setattr(main, "fetch_profile", lambda access_token: {
        "subject": "synthetic-member-id",
        "name": "LinkedIn Test Member",
    })

    callback = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "synthetic-authorization-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/?linkedin=connected")
    accounts = client.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()
    assert len(accounts) == 1
    account = accounts[0]
    assert account["platform"] == "linkedin"
    assert account["is_mock"] is False
    assert account["external_account_id"] == "urn:li:person:synthetic-member-id"
    capabilities = {item["capability"]: item for item in account["capabilities"]}
    assert capabilities["publish"]["enabled"] is True
    assert capabilities["oauth"]["status"] == "connected"
    assert "synthetic-access-token" not in callback.text
    assert "synthetic-refresh-token" not in callback.text
    with publishing.SessionLocal() as db:
        credential = db.scalar(select(LinkedInCredential).where(
            LinkedInCredential.social_account_id == account["id"],
        ))
        assert credential is not None
        assert credential.access_token_ciphertext != token.access_token
        assert credential.refresh_token_ciphertext != token.refresh_token
        assert decrypt_token(credential.access_token_ciphertext, encryption_key) == token.access_token

    replay = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "synthetic-authorization-code", "state": state},
        follow_redirects=False,
    )
    assert replay.status_code == 400

    disconnected = client.delete(
        f"/api/v1/workspaces/{workspace_id}/social-accounts/{account['id']}/connection"
    )
    assert disconnected.status_code == 200
    accounts_after_disconnect = client.get(
        f"/api/v1/workspaces/{workspace_id}/social-accounts"
    ).json()
    assert accounts_after_disconnect[0]["active"] is False
    assert all(not item["enabled"] for item in accounts_after_disconnect[0]["capabilities"])
    with publishing.SessionLocal() as db:
        assert db.scalar(select(LinkedInCredential).where(
            LinkedInCredential.social_account_id == account["id"],
        )) is None

    encrypted = encrypt_token(token.access_token, encryption_key)
    assert encrypted != token.access_token
    assert decrypt_token(encrypted, encryption_key) == token.access_token
    assert decrypt_token(encrypt_token(token.refresh_token, encryption_key), encryption_key) == token.refresh_token


def test_linkedin_publish_reconciliation_is_role_gated_and_audited(client):
    from app.models import AuditLog

    owner = register(client, "reconcile-owner@example.com", "Reconciliation Workspace")
    workspace_id = owner["workspace"]["id"]
    job_id = create_reconciliation_job(workspace_id, owner["user"]["id"])
    outsider = register(client, "reconcile-outsider@example.com", "Another Workspace")
    cross_tenant = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{job_id}/reconcile",
        json={
            "outcome": "confirmed_published",
            "provider_post_id": "urn:li:share:confirmed-post-123",
            "evidence_summary": "Verified the post in the LinkedIn member feed.",
        },
    )
    assert cross_tenant.status_code == 404
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})

    published = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{job_id}/reconcile",
        json={
            "outcome": "confirmed_published",
            "provider_post_id": "urn:li:share:confirmed-post-123",
            "evidence_summary": "Verified the post in the LinkedIn member feed.",
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["provider_post_id"] == "urn:li:share:confirmed-post-123"

    uncertain_retry_job = create_reconciliation_job(workspace_id, owner["user"]["id"])
    uncertain_retry = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{uncertain_retry_job}/reconcile",
        json={
            "outcome": "confirmed_not_published",
            "evidence_summary": "Checked the member feed and confirmed no post exists.",
            "retry_confirmation": "I confirmed no LinkedIn post was created",
        },
    )
    assert uncertain_retry.status_code == 200, uncertain_retry.text
    assert uncertain_retry.json()["status"] == "queued"
    duplicate_reconciliation = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{uncertain_retry_job}/reconcile",
        json={
            "outcome": "confirmed_not_published",
            "evidence_summary": "Checked the member feed and confirmed no post exists.",
            "retry_confirmation": "I confirmed no LinkedIn post was created",
        },
    )
    assert duplicate_reconciliation.status_code == 409

    retry_job_id = create_reconciliation_job(
        workspace_id,
        owner["user"]["id"],
        status="blocked",
        error_message=(
            "LinkedIn rate limited this publish request (HTTP 429); no automatic retry was made. "
            "Honor the provider Retry-After period before authorizing a retry."
        ),
    )
    bad_retry = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{retry_job_id}/reconcile",
        json={
            "outcome": "retry_after_rate_limit",
            "evidence_summary": "Checked the member feed after waiting.",
            "retry_confirmation": "I confirmed no LinkedIn post was created",
        },
    )
    assert bad_retry.status_code == 422
    retry = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{retry_job_id}/reconcile",
        json={
            "outcome": "retry_after_rate_limit",
            "evidence_summary": "Checked the member feed after waiting.",
            "retry_confirmation": "I waited for Retry-After and confirmed no LinkedIn post was created",
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "queued"

    approver = register(client, "reconcile-approver@example.com", "Separate Workspace")
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    assert client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": approver["user"]["email"],
        "role": "approver",
    }).status_code == 201
    client.headers.update({"Authorization": f"Bearer {approver['access_token']}"})
    denied = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{create_reconciliation_job(workspace_id, owner['user']['id'])}/reconcile",
        json={
            "outcome": "confirmed_not_published",
            "evidence_summary": "Checked LinkedIn and confirmed no post exists.",
            "retry_confirmation": "I confirmed no LinkedIn post was created",
        },
    )
    assert denied.status_code == 403

    client.headers.update({"Authorization": f"Bearer {approver['access_token']}"})
    with publishing.SessionLocal() as db:
        logs = db.scalars(select(AuditLog).where(
            AuditLog.organization_id == workspace_id,
            AuditLog.action.in_((
                "publish.reconciled_published",
                "publish.reconciled_retry_authorized",
                "publish.rate_limit_retry_authorized",
            )),
        )).all()
        assert len(logs) == 3
        assert all(log.actor_id == owner["user"]["id"] for log in logs)


def test_linkedin_without_member_publish_scope_is_connected_but_cannot_publish(client, monkeypatch):
    from app import main

    _, token = configure_linkedin(monkeypatch, scopes=["openid", "profile"])
    owner = register(client, "linkedin-readonly@example.com", "LinkedIn Readonly")
    workspace_id = owner["workspace"]["id"]
    state = start_connection(client, workspace_id)
    monkeypatch.setattr(main, "exchange_code", lambda settings, code: token)
    monkeypatch.setattr(main, "fetch_profile", lambda access_token: {
        "subject": "readonly-member-id",
        "name": "LinkedIn Readonly Member",
    })

    response = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "synthetic-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    account = client.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[0]
    publish = next(item for item in account["capabilities"] if item["capability"] == "publish")
    assert publish["enabled"] is False
    assert publish["status"] == "missing_permission"


def test_linkedin_publishing_remains_disabled_without_operator_opt_in(client, monkeypatch):
    from app import main

    _, token = configure_linkedin(monkeypatch)
    monkeypatch.setenv("PUBLISHING_ENABLED", "false")
    owner = register(client, "linkedin-disabled@example.com", "LinkedIn Disabled")
    workspace_id = owner["workspace"]["id"]
    state = start_connection(client, workspace_id)
    monkeypatch.setattr(main, "exchange_code", lambda settings, code: token)
    monkeypatch.setattr(main, "fetch_profile", lambda access_token: {
        "subject": "disabled-member-id",
        "name": "LinkedIn Disabled Member",
    })

    callback = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "synthetic-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    account = client.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[0]
    publish = next(item for item in account["capabilities"] if item["capability"] == "publish")
    assert publish["status"] == "disabled_by_operator"
    assert publish["enabled"] is False


def test_linkedin_post_adapter_uses_official_rest_headers_and_confirmed_id(client, monkeypatch):
    from app import main, social_adapters

    encryption_key, connected_token = configure_linkedin(monkeypatch)
    token = LinkedInToken(
        access_token=connected_token.access_token,
        refresh_token=connected_token.refresh_token,
        scopes=connected_token.scopes,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        refresh_expires_at=connected_token.refresh_expires_at,
    )
    owner = register(client, "linkedin-publisher@example.com", "LinkedIn Publisher")
    workspace_id = owner["workspace"]["id"]
    state = start_connection(client, workspace_id)
    monkeypatch.setattr(main, "exchange_code", lambda settings, code: token)
    monkeypatch.setattr(main, "fetch_profile", lambda access_token: {
        "subject": "publisher-member-id",
        "name": "LinkedIn Publisher",
    })
    connected = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "synthetic-code", "state": state},
        follow_redirects=False,
    )
    assert connected.status_code == 303
    account = client.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[0]
    draft = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "LinkedIn post",
        "platform": "linkedin",
        "body": "Approved text from a synthetic integration test.",
    }).json()
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{draft['id']}/quality-check"
    ).json()["passed"]
    approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{draft['id']}/submit-for-approval",
        json={
            "social_account_id": account["id"],
            "destination": account["external_account_id"],
        },
    )
    assert approval.status_code == 201, approval.text
    approval = approval.json()

    reviewer = client.post("/api/v1/auth/register", json={
        "name": "LinkedIn Approver",
        "email": "linkedin-approver@example.com",
        "password": "valid-approver-password-123",
        "organization_name": "Separate",
    }).json()
    client.headers.update({"Authorization": "Bearer " + owner["access_token"]})
    assert client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "linkedin-approver@example.com",
        "role": "approver",
    }).status_code == 201
    client.headers.update({"Authorization": "Bearer " + reviewer["access_token"]})
    assert client.post(
        f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision",
        json={"decision": "approved"},
    ).status_code == 200
    job = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"],
        "idempotency_key": "linkedin-synthetic-post",
    }).json()

    refreshed_token = LinkedInToken(
        access_token="synthetic-refreshed-access-token",
        refresh_token="synthetic-refreshed-refresh-token",
        scopes=token.scopes,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    refresh_calls = []
    monkeypatch.setattr(
        social_adapters,
        "refresh_access_token",
        lambda settings, refresh_token, scopes: refresh_calls.append(refresh_token) or refreshed_token,
    )

    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        import httpx

        return httpx.Response(
            201,
            headers={"x-restli-id": "urn:li:share:synthetic-post-id"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(social_adapters.httpx, "post", fake_post)
    client.headers.update({"Authorization": "Bearer " + owner["access_token"]})
    result = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{job['id']}/dispatch"
    )

    assert result.status_code == 200, result.text
    assert result.json()["provider_post_id"] == "urn:li:share:synthetic-post-id"
    assert seen["url"] == "https://api.linkedin.com/rest/posts"
    assert seen["headers"]["Authorization"] == "Bearer synthetic-refreshed-access-token"
    assert seen["headers"]["Linkedin-Version"] == "202610"
    assert seen["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert seen["payload"]["author"] == account["external_account_id"]
    assert seen["payload"]["commentary"] == draft["version"]["body"]
    assert "synthetic-access-token" not in result.text
    assert refresh_calls == ["synthetic-refresh-token"]
    with publishing.SessionLocal() as db:
        credential = db.scalar(select(LinkedInCredential).where(
            LinkedInCredential.social_account_id == account["id"],
        ))
        assert decrypt_token(credential.access_token_ciphertext, encryption_key) == refreshed_token.access_token
        assert decrypt_token(credential.refresh_token_ciphertext, encryption_key) == refreshed_token.refresh_token

    rate_limited_job = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"],
        "idempotency_key": "linkedin-synthetic-rate-limit",
    }).json()

    def fake_rate_limited_post(url, headers, json, timeout):
        import httpx

        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(social_adapters.httpx, "post", fake_rate_limited_post)
    rate_limit_result = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{rate_limited_job['id']}/dispatch"
    )
    assert rate_limit_result.status_code == 409
    rate_limited = client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()
    rate_limited = next(row for row in rate_limited if row["id"] == rate_limited_job["id"])
    assert rate_limited["status"] == "blocked"
    assert "HTTP 429" in rate_limited["error_message"]
    assert "Retry-After" in rate_limited["error_message"]

    uncertain_job = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"],
        "idempotency_key": "linkedin-synthetic-timeout",
    }).json()
    timeout_calls = []

    def fake_timeout_post(url, headers, json, timeout):
        import httpx

        timeout_calls.append(url)
        raise httpx.ReadTimeout("synthetic timeout", request=httpx.Request("POST", url))

    monkeypatch.setattr(social_adapters.httpx, "post", fake_timeout_post)
    uncertain_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{uncertain_job['id']}/dispatch"
    )
    assert uncertain_response.status_code == 409
    assert "uncertain" in uncertain_response.json()["detail"].lower()
    no_blind_retry = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{uncertain_job['id']}/dispatch"
    )
    assert no_blind_retry.status_code == 409
    assert timeout_calls == ["https://api.linkedin.com/rest/posts"]
    uncertain = client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()
    uncertain = next(row for row in uncertain if row["id"] == uncertain_job["id"])
    assert uncertain["status"] == "reconciliation_required"
