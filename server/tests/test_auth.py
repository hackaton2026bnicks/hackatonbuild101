from conftest import ADMIN_TOKEN


def test_events_require_a_key(client):
    response = client.post("/events", json={"events": []})
    assert response.status_code == 401


def test_unknown_key_is_rejected(client):
    response = client.get("/me", headers={"Authorization": "Bearer b101_not-a-real-key"})
    assert response.status_code == 401


def test_admin_endpoints_require_the_admin_token(client):
    response = client.post("/admin/keys", json={"user": "ana"}, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_key_identifies_user_and_team(client, make_key):
    headers = make_key("ana", team="build101")
    response = client.get("/me", headers=headers)
    assert response.json() == {"user": "ana", "team": "build101"}


def test_revoked_key_stops_working(client, make_key):
    headers = make_key("ana")
    admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    key_id = client.get("/admin/keys", headers=admin).json()["keys"][0]["id"]

    assert client.delete(f"/admin/keys/{key_id}", headers=admin).status_code == 200
    assert client.get("/me", headers=headers).status_code == 401


def test_user_names_are_validated(client):
    response = client.post(
        "/admin/keys",
        json={"user": 'ana", "team": "x'},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 422
