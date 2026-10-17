import asyncio
import os
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient

# psycopg's async driver cannot run on the default Windows event loop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def client(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set; these tests need a Postgres database")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)

    from api.main import app

    with TestClient(app) as test_client:
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(
                "TRUNCATE api_keys, sessions, events, conversacion, session_resources, messages, deliveries "
                "RESTART IDENTITY"
            )
        yield test_client


@pytest.fixture
def make_key(client):
    def create(user: str, team: str = "default") -> dict:
        response = client.post(
            "/admin/keys",
            json={"user": user, "team": team},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['key']}"}

    return create
