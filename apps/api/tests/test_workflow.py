from conftest import register


def setup_publish(client):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]
    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin", "display_name": "Northwind Demo",
        "external_account_id": "northwind-demo",
    }).json()
    campaign = client.post(f"/api/v1/workspaces/{workspace_id}/campaigns", json={
        "name": "Launch", "objective": "Introduce product",
    }).json()
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Launch note", "platform": "linkedin", "body": "A thoughtful product update.",
        "campaign_id": campaign["id"],
    }).json()
    review = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check"
    ).json()
    assert review["passed"] is True
    approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "company-page"},
    ).json()
    return owner, workspace_id, content, approval


def test_complete_mock_publish_requires_human_approval(client):
    owner, workspace_id, content, approval = setup_publish(client)
    review_card = client.get(f"/api/v1/workspaces/{workspace_id}/approvals").json()[0]
    assert review_card["content_title"] == "Launch note"
    assert review_card["content_body"] == "A thoughtful product update."
    assert review_card["version_number"] == 1
    assert review_card["account_name"] == "Northwind Demo"
    assert review_card["destination"] == "company-page"
    assert review_card["action"] == "publish"

    before_approval = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "publish-key-001",
    })
    assert before_approval.status_code == 409
    own_decision = client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved",
    })
    assert own_decision.status_code == 403

    # A separate registered user is added as an approver.
    client.headers.pop("Authorization")
    reviewer = client.post("/api/v1/auth/register", json={
        "name": "Reviewer", "email": "reviewer@example.com", "password": "reviewer-password-123",
        "organization_name": "Ignored",
    }).json()
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    member = client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "reviewer@example.com", "role": "approver",
    })
    assert member.status_code == 201, member.text
    client.headers.update({"Authorization": f"Bearer {reviewer['access_token']}"})
    decision = client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved", "comment": "Reviewed",
    })
    assert decision.status_code == 200, decision.text

    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    queued = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "publish-key-001",
    })
    assert queued.status_code == 201, queued.text
    job = queued.json()
    result = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs/{job['id']}/dispatch")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "published"
    duplicate = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "publish-key-001",
    })
    assert duplicate.json()["id"] == job["id"]
    jobs = client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "published"


def test_edit_invalidates_pending_approval(client):
    owner, workspace_id, content, approval = setup_publish(client)
    revision = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/versions",
        json={"title": "Updated", "platform": "linkedin", "body": "Changed copy."},
    )
    assert revision.status_code == 201
    approvals = client.get(f"/api/v1/workspaces/{workspace_id}/approvals").json()
    assert next(a for a in approvals if a["id"] == approval["id"])["status"] == "invalidated"


def test_workspace_isolation_blocks_reads_and_writes(client):
    first = register(client, "first@example.com", "First")
    first_workspace = first["workspace"]["id"]
    client.headers.pop("Authorization")
    second = register(client, "second@example.com", "Second")
    second_workspace = second["workspace"]["id"]
    response = client.get(f"/api/v1/workspaces/{first_workspace}/brand")
    assert response.status_code == 404
    denied = client.post(f"/api/v1/workspaces/{first_workspace}/campaigns", json={"name": "Nope"})
    assert denied.status_code == 404


def test_quality_review_blocks_configured_prohibited_claim(client):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]
    client.put(f"/api/v1/workspaces/{workspace_id}/brand", json={
        "name": "Northwind", "prohibited_claims": ["guaranteed results"],
    })
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Claim", "platform": "linkedin", "body": "We offer guaranteed results.",
    }).json()
    review = client.post(f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check")
    assert review.status_code == 200
    assert review.json()["passed"] is False


def test_brand_change_requires_fresh_quality_review(client):
    owner = register(client)
    workspace_id = owner["workspace"]["id"]
    account = client.post(f"/api/v1/workspaces/{workspace_id}/social-accounts", json={
        "platform": "linkedin", "display_name": "Company", "external_account_id": "company-page",
    }).json()
    content = client.post(f"/api/v1/workspaces/{workspace_id}/content", json={
        "title": "Update", "platform": "linkedin", "body": "A safe update.",
    }).json()
    first_review = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/quality-check"
    )
    assert first_review.json()["passed"] is True
    client.put(f"/api/v1/workspaces/{workspace_id}/brand", json={
        "name": "Northwind", "prohibited_claims": ["safe update"],
    })
    stale = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/submit-for-approval",
        json={"social_account_id": account["id"], "destination": "company-page"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Brand guidance changed; rerun the quality review"


def test_author_cannot_approve_and_revoked_approver_cannot_publish(client):
    owner, workspace_id, content, approval = setup_publish(client)
    reviewer = client.post("/api/v1/auth/register", json={
        "name": "Reviewer", "email": "revoked@example.com", "password": "reviewer-password-123",
        "organization_name": "Separate",
    }).json()
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "revoked@example.com", "role": "approver",
    })
    client.headers.update({"Authorization": f"Bearer {reviewer['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved",
    })
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    removed = client.delete(f"/api/v1/workspaces/{workspace_id}/members/{reviewer['user']['id']}")
    assert removed.status_code == 204
    queued = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "revoked-key-001",
    })
    assert queued.status_code == 201
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{queued.json()['id']}/dispatch"
    )
    assert response.status_code == 409
    assert client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()[0]["status"] == "blocked"


def test_edit_after_approval_blocks_queued_publish(client):
    owner, workspace_id, content, approval = setup_publish(client)
    reviewer = client.post("/api/v1/auth/register", json={
        "name": "Reviewer", "email": "late-edit@example.com", "password": "reviewer-password-123",
        "organization_name": "Separate",
    }).json()
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "late-edit@example.com", "role": "approver",
    })
    client.headers.update({"Authorization": f"Bearer {reviewer['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved",
    })
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    queued = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "approved-before-edit",
    }).json()
    edited = client.post(
        f"/api/v1/workspaces/{workspace_id}/content/{content['id']}/versions",
        json={"title": "Revised", "platform": "linkedin", "body": "Changed after approval."},
    )
    assert edited.status_code == 201
    result = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{queued['id']}/dispatch"
    )
    assert result.status_code == 409
    job = client.get(f"/api/v1/workspaces/{workspace_id}/publish-jobs").json()[0]
    assert job["status"] == "blocked"


def test_brand_change_after_approval_invalidates_queued_publish(client):
    owner, workspace_id, content, approval = setup_publish(client)
    reviewer = client.post("/api/v1/auth/register", json={
        "name": "Reviewer", "email": "brand-edit@example.com", "password": "reviewer-password-123",
        "organization_name": "Separate",
    }).json()
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/members", json={
        "email": "brand-edit@example.com", "role": "approver",
    })
    client.headers.update({"Authorization": f"Bearer {reviewer['access_token']}"})
    client.post(f"/api/v1/workspaces/{workspace_id}/approvals/{approval['id']}/decision", json={
        "decision": "approved",
    })
    client.headers.update({"Authorization": f"Bearer {owner['access_token']}"})
    queued = client.post(f"/api/v1/workspaces/{workspace_id}/publish-jobs", json={
        "approval_id": approval["id"], "idempotency_key": "brand-change-before-dispatch",
    }).json()
    updated = client.put(f"/api/v1/workspaces/{workspace_id}/brand", json={
        "name": "Northwind", "tone": "More formal",
    })
    assert updated.status_code == 200
    approval_response = client.get(f"/api/v1/workspaces/{workspace_id}/approvals")
    assert approval_response.status_code == 200, approval_response.text
    approval_rows = approval_response.json()
    assert next(row for row in approval_rows if row["id"] == approval["id"])["status"] == "invalidated"
    dispatch = client.post(
        f"/api/v1/workspaces/{workspace_id}/publish-jobs/{queued['id']}/dispatch"
    )
    assert dispatch.status_code == 409
