from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import get_settings


class _DummyManager:
    def __init__(self, ok: bool):
        self._ok = ok

    async def ping(self) -> bool:
        return self._ok

    async def disconnect(self) -> None:
        return None


def test_health_ready_returns_ready_when_all_dependencies_ok(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app) as client:
        app.state.mongo = _DummyManager(True)
        app.state.redis = _DummyManager(True)
        app.state.qdrant = _DummyManager(True)

        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["status"] == "ok"
    assert body["dependencies"] == {
        "mongodb": True,
        "redis": True,
        "qdrant": True,
    }
    assert "runtime_profile" in body
    assert "runtime_profile_declared" in body
    assert "runtime_profile_detected" in body


def test_health_ready_returns_degraded_when_any_dependency_fails(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app) as client:
        app.state.mongo = _DummyManager(True)
        app.state.redis = _DummyManager(False)
        app.state.qdrant = _DummyManager(True)

        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["status"] == "degraded"
    assert body["dependencies"] == {
        "mongodb": True,
        "redis": False,
        "qdrant": True,
    }


def test_health_ready_includes_hints_in_local_env_when_degraded(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    get_settings.cache_clear()
    from app.main import app

    with patch("app.main.probe_dependencies", new_callable=AsyncMock):
        with TestClient(app) as client:
            app.state.mongo = _DummyManager(True)
            app.state.redis = _DummyManager(False)
            app.state.qdrant = _DummyManager(True)

            response = client.get("/health/ready")

    body = response.json()
    assert "hints" in body
    assert any("127.0.0.1" in hint for hint in body["hints"])
