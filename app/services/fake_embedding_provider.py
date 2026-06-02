from __future__ import annotations

import hashlib
import math
import struct


class FakeEmbeddingProvider:
    """Deterministic embeddings for tests; no external model downloads."""

    def __init__(self, *, dim: int = 384) -> None:
        self._dim = dim

    def embedding_dimension(self) -> int:
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for_text(text) for text in texts]

    def _vector_for_text(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < self._dim:
            for i in range(0, len(digest) - 3, 4):
                if len(values) >= self._dim:
                    break
                chunk = digest[i : i + 4]
                raw = struct.unpack(">I", chunk)[0]
                values.append((raw / 2**32) * 2.0 - 1.0)
            digest = hashlib.sha256(digest).digest()
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
