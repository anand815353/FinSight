import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.dependency_targets import (
    build_runtime_profile_context,
    detect_profile_from_urls,
    resolve_runtime_profile,
)


def _host_urls(monkeypatch):
    monkeypatch.setenv("MONGODB__URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("REDIS__URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://127.0.0.1:6379/2")


def _compose_urls(monkeypatch):
    monkeypatch.setenv("MONGODB__URI", "mongodb://mongodb:27017")
    monkeypatch.setenv("REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://qdrant:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://redis:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://redis:6379/2")


def test_detect_profile_host_from_urls():
    urls = [
        "mongodb://127.0.0.1:27017",
        "redis://127.0.0.1:6379/0",
        "http://127.0.0.1:6333",
        "redis://127.0.0.1:6379/1",
        "redis://127.0.0.1:6379/2",
    ]
    assert detect_profile_from_urls(urls) == "host"


def test_detect_profile_compose_from_urls():
    urls = [
        "mongodb://mongodb:27017",
        "redis://redis:6379/0",
        "http://qdrant:6333",
        "redis://redis:6379/1",
        "redis://redis:6379/2",
    ]
    assert detect_profile_from_urls(urls) == "compose"


def test_detect_profile_mixed_from_urls():
    urls = [
        "mongodb://127.0.0.1:27017",
        "redis://redis:6379/0",
        "http://127.0.0.1:6333",
        "redis://127.0.0.1:6379/1",
        "redis://127.0.0.1:6379/2",
    ]
    assert detect_profile_from_urls(urls) == "mixed"


def test_resolve_runtime_profile_auto_uses_detected():
    assert resolve_runtime_profile("auto", "host") == "host"
    assert resolve_runtime_profile("auto", "compose") == "compose"


def test_build_runtime_profile_context_auto_host(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    monkeypatch.setenv("RUNTIME__PROFILE", "auto")
    _host_urls(monkeypatch)
    settings = Settings()
    ctx = build_runtime_profile_context(
        declared=settings.runtime.profile,
        mongodb_uri=settings.mongodb.uri,
        redis_url=settings.redis.url,
        qdrant_url=settings.qdrant.url,
        celery_broker_url=settings.celery.broker_url,
        celery_result_backend=settings.celery.result_backend,
    )
    assert ctx.detected == "host"
    assert ctx.effective == "host"


def test_settings_strict_rejects_mixed_profile_in_local(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    monkeypatch.setenv("RUNTIME__PROFILE", "host")
    monkeypatch.setenv("RUNTIME__STRICT_PROFILE_VALIDATION", "true")
    monkeypatch.setenv("MONGODB__URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://127.0.0.1:6379/2")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_production_rejects_mixed_profile(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "strong-production-secret-key-32chars")
    monkeypatch.setenv("APP__ENV", "production")
    monkeypatch.setenv("SESSION__COOKIE_SECURE", "true")
    monkeypatch.setenv("RUNTIME__PROFILE", "auto")
    monkeypatch.setenv("MONGODB__URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://127.0.0.1:6379/2")
    with pytest.raises(ValidationError):
        Settings()
