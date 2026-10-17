def test_health_requires_a_key(client):
    response = client.get("/health")
    assert response.status_code == 401


def test_health_reports_ok_and_db(client, make_key):
    response = client.get("/health", headers=make_key("ana"))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["db"] == "ok"
