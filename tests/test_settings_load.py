import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

PRODUCTION_SECRET = "strong-production-secret-key-32chars-ok"


def test_settings_loads_with_required_values(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    settings = Settings()
    assert settings.app.name == "FinSight"
    assert settings.logging.level == "INFO"


def test_settings_fails_without_required_secret(monkeypatch):
    get_settings.cache_clear()
    # Empty env var overrides values loaded from .env for this assertion.
    monkeypatch.setenv("APP__SECRET_KEY", "")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_fails_for_weak_secret_in_production(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("APP__SECRET_KEY", "change-me-local")
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "true")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_fails_for_short_secret_in_production(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("APP__SECRET_KEY", "short-secret")
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "true")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_fails_when_debug_true_in_production(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("APP__SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("APP__DEBUG", "true")
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "true")
    with pytest.raises(ValidationError, match="APP_DEBUG"):
        Settings()


def test_settings_fails_when_cookie_insecure_in_production(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("APP__SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "false")
    with pytest.raises(ValidationError, match="cookie_secure"):
        Settings()


def test_settings_production_rejects_declared_host_with_compose_urls(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("APP__SECRET_KEY", "strong-production-secret-key-32chars")
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "true")
    monkeypatch.setenv("RUNTIME__PROFILE", "host")
    monkeypatch.setenv("MONGODB__URI", "mongodb://mongodb:27017")
    monkeypatch.setenv("REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://qdrant:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://redis:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://redis:6379/2")
    with pytest.raises(ValidationError):
        Settings()


def test_empty_optional_api_keys_normalize_to_none(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("QDRANT__API_KEY", "")
    monkeypatch.setenv("LLM__API_KEY", "")
    settings = Settings()
    assert settings.qdrant.api_key is None
    assert settings.llm.api_key is None
