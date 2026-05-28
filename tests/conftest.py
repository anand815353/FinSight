import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def finsight_test_env(monkeypatch):
    """Avoid live dependency probes during TestClient lifespan in the suite."""
    monkeypatch.setenv("APP__ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
