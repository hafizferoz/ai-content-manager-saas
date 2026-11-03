import httpx
from cryptography.fernet import Fernet

from app.database import get_db
from app.main import app
from app.models import AIProviderConfiguration


def register_workspace(client):
    response = client.post("/api/v1/auth/register", json={
        "name": "Provider Admin",
        "email": "provider-admin@example.com",
        "password": "secure-password-123",
        "organization_name": "Provider Workspace",
    })
    assert response.status_code == 201, response.text
    result = response.json()
    client.headers.update({"Authorization": f"Bearer {result['access_token']}"})
    return result["workspace"]["id"]


def test_workspace_provider_key_is_encrypted_and_never_returned(client, monkeypatch):
    workspace_id = register_workspace(client)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    secret = "synthetic-provider-api-key"

    response = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/groq",
        json={"api_key": secret, "model": "account-model", "enabled": True, "priority": 0},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider": "groq",
        "model": "account-model",
        "enabled": True,
        "priority": 0,
        "api_key_configured": True,
    }
    assert secret not in response.text
    assert "api_key_ciphertext" not in response.text

    dependency = app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        stored = db.query(AIProviderConfiguration).filter_by(
            organization_id=workspace_id,
            provider="groq",
        ).one()
        assert stored.api_key_ciphertext != secret
        assert secret not in stored.api_key_ciphertext
    finally:
        dependency.close()


def test_workspace_provider_models_and_validation_are_server_side(client, monkeypatch):
    workspace_id = register_workspace(client)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    saved = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/openrouter",
        json={"api_key": "synthetic-key", "model": "provider/model", "enabled": True, "priority": 0},
    )
    assert saved.status_code == 200, saved.text

    seen = []

    def fake_get(url, headers, timeout):
        seen.append((url, headers))
        return httpx.Response(
            200,
            json={"data": [{"id": "provider/model"}, {"id": "provider/other"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.ai_provider.httpx.get", fake_get)
    models = client.get(f"/api/v1/workspaces/{workspace_id}/ai/providers/openrouter/models")
    assert models.status_code == 200
    assert models.json()["models"] == ["provider/model", "provider/other"]

    validation = client.post(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/openrouter/validate"
    )
    assert validation.status_code == 200
    assert validation.json()["configured_model_available"] is True
    assert len(seen) == 2
    assert all(url.endswith("/models") for url, _ in seen)
    assert all(headers["Authorization"] == "Bearer synthetic-key" for _, headers in seen)


def test_workspace_provider_configuration_requires_admin_and_encryption_key(client, monkeypatch):
    workspace_id = register_workspace(client)
    monkeypatch.delenv("AI_ENCRYPTION_KEY", raising=False)

    response = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/mistral",
        json={"api_key": "synthetic-key", "model": "mistral-model", "enabled": True},
    )

    assert response.status_code == 503
    assert "AI_ENCRYPTION_KEY" in response.json()["detail"]
    assert "synthetic-key" not in response.text


def test_non_admin_workspace_member_cannot_manage_provider_credentials(client, monkeypatch):
    workspace_id = register_workspace(client)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    member = client.post("/api/v1/auth/register", json={
        "name": "Workspace Creator",
        "email": "workspace-creator@example.com",
        "password": "secure-password-456",
        "organization_name": "Separate Workspace",
    })
    assert member.status_code == 201
    added = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": "workspace-creator@example.com", "role": "creator"},
    )
    assert added.status_code == 201

    client.headers.update({"Authorization": f"Bearer {member.json()['access_token']}"})
    response = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/gemini",
        json={"api_key": "synthetic-key", "model": "account-model"},
    )

    assert response.status_code == 403
    assert "synthetic-key" not in response.text


def test_provider_configuration_does_not_expose_keys_and_delete_is_audited(client, monkeypatch):
    workspace_id = register_workspace(client)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    response = client.put(
        f"/api/v1/workspaces/{workspace_id}/ai/providers/gemini",
        json={"api_key": "synthetic-key", "model": "gemini-account-model"},
    )
    assert response.status_code == 200

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/ai/providers")
    assert listed.status_code == 200
    assert listed.json()["configurations"][0]["api_key_configured"] is True
    assert "synthetic-key" not in listed.text

    deleted = client.delete(f"/api/v1/workspaces/{workspace_id}/ai/providers/gemini")
    assert deleted.status_code == 204
    audit = client.get(f"/api/v1/workspaces/{workspace_id}/audit-logs")
    assert audit.status_code == 200
    assert any(event["action"] == "ai.provider_removed" for event in audit.json())
