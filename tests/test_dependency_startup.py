import asyncio
import logging

from app.core.config import Settings
from app.core.dependency_startup import probe_dependencies


class _StubManager:
    def __init__(self, ok: bool):
        self._ok = ok
        self.quiet_calls: list[bool] = []

    async def ping(self, *, quiet: bool = False) -> bool:
        self.quiet_calls.append(quiet)
        return self._ok


def test_probe_dependencies_all_ok(monkeypatch, caplog):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    settings = Settings()
    mongo = _StubManager(True)
    redis = _StubManager(True)
    qdrant = _StubManager(True)

    with caplog.at_level(logging.INFO, logger="finsight"):
        results = asyncio.run(probe_dependencies(mongo, redis, qdrant, settings))

    assert results == {"mongodb": True, "redis": True, "qdrant": True}
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "runtime_profile_detected" in messages
    assert "runtime_profile_ready" in messages
    assert "mongodb://" not in messages
    assert mongo.quiet_calls == [True]


def test_probe_dependencies_logs_degraded_when_dependency_fails(monkeypatch, caplog):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    monkeypatch.setenv("RUNTIME__PROFILE", "compose")
    monkeypatch.setenv("MONGODB__URI", "mongodb://mongodb:27017")
    monkeypatch.setenv("REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://qdrant:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://redis:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://redis:6379/2")
    settings = Settings()
    mongo = _StubManager(True)
    redis = _StubManager(False)
    qdrant = _StubManager(False)

    with caplog.at_level(logging.INFO, logger="finsight"):
        results = asyncio.run(probe_dependencies(mongo, redis, qdrant, settings))

    assert results["redis"] is False
    assert results["qdrant"] is False
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "runtime_profile_detected" in messages
    assert "runtime_profile_degraded" in messages
    assert "dependency_unreachable" in messages
    assert "127.0.0.1" in messages
    assert "API key" in messages
    assert "mongodb://" not in messages


def test_probe_dependencies_host_profile_uses_generic_hint(monkeypatch, caplog):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    monkeypatch.setenv("RUNTIME__PROFILE", "host")
    monkeypatch.setenv("MONGODB__URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("REDIS__URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("QDRANT__URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://127.0.0.1:6379/2")
    settings = Settings()
    mongo = _StubManager(False)
    redis = _StubManager(True)
    qdrant = _StubManager(True)

    with caplog.at_level(logging.INFO, logger="finsight"):
        asyncio.run(probe_dependencies(mongo, redis, qdrant, settings))

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "runtime_profile_detected" in messages
    assert "dependency_unreachable" in messages
    assert "instead of Docker Compose service names" not in messages
    assert "Ensure the service is running" in messages
