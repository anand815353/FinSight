from __future__ import annotations

from typing import Protocol

from app.core.config import EmbeddingSettings, Settings
from app.services.fake_embedding_provider import FakeEmbeddingProvider
from app.services.huggingface_embedding_provider import HuggingFaceEmbeddingProvider


class EmbeddingProvider(Protocol):
    def embedding_dimension(self) -> int: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding.provider
    if provider == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding.vector_size)
    if provider == "huggingface":
        return HuggingFaceEmbeddingProvider(settings.embedding)
    raise ValueError(f"Unsupported embedding provider: {provider}")
