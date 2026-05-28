from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_request_id_created_when_missing(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_request_id_preserved_when_present(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
