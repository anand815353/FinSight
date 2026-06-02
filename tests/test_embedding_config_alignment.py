from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    GEMINI_BACKUP_COLLECTION_NAME,
    EmbeddingSettings,
    Settings,
    get_settings,
)

HF_COLLECTION = "finsight_chunks_hf_minilm_l6_v2"
HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / ".env.example"

EMBEDDING_SETTINGS_ENV_FIELDS = {
    "EMBEDDING__PROVIDER": "provider",
    "EMBEDDING__HF_MODEL_NAME": "hf_model_name",
    "EMBEDDING__COLLECTION_NAME": "collection_name",
    "EMBEDDING__VECTOR_SIZE": "vector_size",
}


def test_embedding_settings_defaults():
    embedding = EmbeddingSettings()
    assert embedding.provider == "huggingface"
    assert embedding.hf_model_name == HF_MODEL
    assert embedding.collection_name == HF_COLLECTION
    assert embedding.vector_size == 384


def test_gemini_backup_collection_distinct_from_active_hf():
    embedding = EmbeddingSettings()
    assert GEMINI_BACKUP_COLLECTION_NAME != embedding.collection_name
    assert GEMINI_BACKUP_COLLECTION_NAME == "finsight_chunks_gemini_embedding_001"
    assert embedding.collection_name != "finsight_chunks_gemini_v1"


def test_embedding_settings_rejects_stale_gemini_v1_collection():
    with pytest.raises(ValidationError, match="deprecated"):
        EmbeddingSettings(collection_name="finsight_chunks_gemini_v1")


def test_embedding_settings_rejects_books_prefix_collection():
    with pytest.raises(ValidationError, match="books_"):
        EmbeddingSettings(collection_name="books_some_legacy_collection")


def test_embedding_settings_rejects_gemini_backup_as_active_collection():
    with pytest.raises(ValidationError, match="optional backup"):
        EmbeddingSettings(collection_name=GEMINI_BACKUP_COLLECTION_NAME)


def test_settings_embedding_env_override(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    monkeypatch.setenv("EMBEDDING__PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING__COLLECTION_NAME", HF_COLLECTION)
    monkeypatch.setenv("EMBEDDING__HF_MODEL_NAME", HF_MODEL)
    settings = Settings()
    assert settings.embedding.provider == "fake"
    assert settings.embedding.collection_name == HF_COLLECTION
    assert settings.embedding.hf_model_name == HF_MODEL


def test_env_example_embedding_keys_match_schema():
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    active_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("EMBEDDING__") and not line.strip().startswith("#")
    ]
    assert active_lines, ".env.example must document active EMBEDDING__ settings"
    for line in active_lines:
        key = line.split("=", 1)[0]
        assert key in EMBEDDING_SETTINGS_ENV_FIELDS, f"Unknown embedding env key: {key}"
        assert key != "EMBEDDING__MODEL_VERSION"
        assert "finsight_chunks_gemini_v1" not in line
    assert HF_COLLECTION in text


def test_env_example_documents_gemini_backup_separately():
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert GEMINI_BACKUP_COLLECTION_NAME in text
    assert any(
        line.startswith(f"EMBEDDING__COLLECTION_NAME={HF_COLLECTION}")
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
