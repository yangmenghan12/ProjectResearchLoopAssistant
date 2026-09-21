from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_api_status():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "Loop Engineering API",
    }
