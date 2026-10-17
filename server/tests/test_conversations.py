from datetime import datetime


def test_create_and_read_conversation_with_api_key(client, make_key):
    headers = make_key("ana")
    message = "prompt " * 3_000
    created = client.post(
        "/conversations",
        json={"usuario": "ana", "mensaje": message},
        headers=headers,
    )

    assert created.status_code == 201
    conversation = created.json()
    assert conversation["id"] == 1
    assert conversation["usuario"] == "ana"
    assert conversation["mensaje"] == message
    assert datetime.fromisoformat(conversation["timestamp"])

    fetched = client.get(f"/conversations/{conversation['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == conversation


def test_list_conversations_is_paginated(client, make_key):
    headers = make_key("ana")
    for usuario in ("ana", "bob"):
        response = client.post(
            "/conversations",
            json={"usuario": usuario, "mensaje": f"Mensaje de {usuario}"},
            headers=headers,
        )
        assert response.status_code == 201

    response = client.get("/conversations", params={"limit": 1}, headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_conversation_validation_and_missing_id(client, make_key):
    headers = make_key("ana")
    too_long = client.post(
        "/conversations",
        json={"usuario": "ana", "mensaje": "x" * 100_001},
        headers=headers,
    )
    missing = client.get("/conversations/999", headers=headers)

    assert too_long.status_code == 422
    assert missing.status_code == 404


def test_conversation_endpoints_require_an_api_key(client):
    created = client.post(
        "/conversations",
        json={"usuario": "ana", "mensaje": "Sin credenciales"},
    )
    listed = client.get("/conversations")
    fetched = client.get("/conversations/1")

    assert created.status_code == 401
    assert listed.status_code == 401
    assert fetched.status_code == 401
