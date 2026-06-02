from __future__ import annotations

import logging

from app.core.config import EmbeddingSettings

logger = logging.getLogger("finsight")


class HuggingFaceEmbeddingProvider:
    """Lazy-loaded Sentence Transformers embeddings for production indexing."""

    def __init__(self, embedding_settings: EmbeddingSettings) -> None:
        self._settings = embedding_settings
        self._model = None

    def embedding_dimension(self) -> int:
        return self._settings.vector_size

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(
                "embedding_model_loading model=%s",
                self._settings.hf_model_name,
            )
            self._model = SentenceTransformer(self._settings.hf_model_name)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        batch_size = max(1, self._settings.batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = model.encode(
                batch,
                normalize_embeddings=self._settings.normalize_embeddings,
                show_progress_bar=False,
            )
            vectors.extend(vec.tolist() for vec in encoded)
        logger.info(
            "embedding_batch_completed count=%s dimension=%s",
            len(texts),
            self.embedding_dimension(),
        )
        return vectors
