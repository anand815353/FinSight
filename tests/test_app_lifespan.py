from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_lifespan_initializes_dependency_managers(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    from app.main import app

    with TestClient(app):
        assert hasattr(app.state, "mongo")
        assert hasattr(app.state, "redis")
        assert hasattr(app.state, "qdrant")
        assert hasattr(app.state, "company_repo")
        assert hasattr(app.state, "source_registry_repo")
        assert hasattr(app.state, "document_repo")
        assert hasattr(app.state, "company_service")
        assert hasattr(app.state, "document_service")


def test_lifespan_skips_startup_probe_in_test_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app

    with patch("app.main.probe_dependencies", new_callable=AsyncMock) as probe_mock:
        with TestClient(app):
            probe_mock.assert_not_called()
