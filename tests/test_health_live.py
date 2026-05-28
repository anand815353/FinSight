from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_health_live_returns_ok(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health/live")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert "runtime_profile" in body
    assert "runtime_profile_declared" in body
    assert "runtime_profile_detected" in body
