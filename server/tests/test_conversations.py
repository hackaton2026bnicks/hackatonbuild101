from datetime import datetime


def test_create_and_read_conversation_without_api_key(client):
    message = "prompt " * 3_000
    created = client.post(
        "/conversations",
        json={"usuario": "ana", "mensaje": message},
    )

    assert created.status_code == 201
    conversation = created.json()
    assert conversation["id"] == 1
    assert conversation["usuario"] == "ana"
    assert conversation["mensaje"] == message
    assert datetime.fromisoformat(conversation["timestamp"])

    fetched = client.get(f"/conversations/{conversation['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == conversation


def test_list_conversations_is_paginated(client):
    for usuario in ("ana", "bob"):
        response = client.post(
            "/conversations",
            json={"usuario": usuario, "mensaje": f"Mensaje de {usuario}"},
        )
        assert response.status_code == 201

    response = client.get("/conversations", params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_conversation_validation_and_missing_id(client):
    too_long = client.post(
        "/conversations",
        json={"usuario": "ana", "mensaje": "x" * 100_001},
    )
    missing = client.get("/conversations/999")

    assert too_long.status_code == 422
    assert missing.status_code == 404
