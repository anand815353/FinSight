from app.core.config import Settings, get_settings


def test_feature_flags_default_false(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    settings = Settings()
    assert settings.features.enable_rag is False
    assert settings.features.enable_ingestion is False
    assert settings.features.enable_red_flag_scanner is False
    assert settings.features.enable_exports is False
