import os

os.environ["AUTO_CREATE_SCHEMA"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
import app.publishing as publishing


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(publishing, "SessionLocal", TestingSession)
    Base.metadata.create_all(engine)

    def override_db():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client, email="owner@example.com", organization="Northwind"):
    response = client.post("/api/v1/auth/register", json={
        "name": "Owner", "email": email, "password": "valid-password-123",
        "organization_name": organization,
    })
    assert response.status_code == 201, response.text
    data = response.json()
    client.headers.update({"Authorization": f"Bearer {data['access_token']}"})
    return data
