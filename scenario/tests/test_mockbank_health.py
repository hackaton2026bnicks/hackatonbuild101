from fastapi.testclient import TestClient

from mockbank.main import app


def test_health_reports_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "mockbank"
