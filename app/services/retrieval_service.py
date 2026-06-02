from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import Settings
from app.db.qdrant import QdrantClientManager
from app.db.qdrant_search import search_points
from app.models.retrieval import EvidencePack, EvidencePackStatus, RetrievalRequest
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.services.embedding_provider import EmbeddingProvider
from app.services.evidence_pack_builder import (
    VectorSearchHit,
    build_evidence_pack,
    build_insufficient_pack,
)

logger = logging.getLogger("finsight")


class RetrievalService:
    def __init__(
        self,
        qdrant_manager: QdrantClientManager,
        embedding_provider: EmbeddingProvider,
        document_repo: DocumentRepository,
        document_chunk_repo: DocumentChunkRepository,
        *,
        settings: Settings,
    ):
        self._qdrant_manager = qdrant_manager
        self._embedding_provider = embedding_provider
        self._document_repo = document_repo
        self._document_chunk_repo = document_chunk_repo
        self._settings = settings

    async def retrieve_evidence(
        self,
        request: RetrievalRequest,
        *,
        actor_user_id: str | None = None,
    ) -> EvidencePack:
        started = time.perf_counter()
        query = request.query.strip()
        if not query:
            return build_insufficient_pack(
                request,
                reason="invalid_query",
                retrieval_metadata={"missing_reason": "invalid_query"},
            )

        company_id = request.filters.company_id.strip()
        if not company_id:
            return build_insufficient_pack(
                request,
                reason="invalid_query",
                retrieval_metadata={"missing_reason": "invalid_query"},
            )

        client = self._qdrant_manager.client
        collection_name = self._settings.embedding.collection_name
        if client is None:
            return build_insufficient_pack(
                request,
                reason="qdrant_unavailable",
                retrieval_metadata={
                    "missing_reason": "qdrant_unavailable",
                    "collection_name": collection_name,
                },
            )

        vectors = self._embedding_provider.embed_texts([query])
        query_vector = vectors[0]

        top_k = min(
            request.top_k,
            self._settings.retrieval.max_top_k,
        )
        score_threshold = request.score_threshold
        if score_threshold is None:
            score_threshold = self._settings.retrieval.default_score_threshold

        try:
            raw_hits = await search_points(
                client,
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                filters=request.filters,
                score_threshold=score_threshold,
            )
        except Exception as exc:
            logger.warning(
                "retrieval_search_failed company_id=%s error_type=%s actor_user_id=%s",
                company_id,
                type(exc).__name__,
                actor_user_id or "",
            )
            return build_insufficient_pack(
                request,
                reason="qdrant_unavailable",
                retrieval_metadata={
                    "missing_reason": "qdrant_unavailable",
                    "collection_name": collection_name,
                },
            )

        if request.score_threshold is not None:
            raw_hits = [
                hit for hit in raw_hits if hit["score"] >= request.score_threshold
            ]

        chunk_ids = [hit["chunk_id"] for hit in raw_hits]
        document_ids = [
            doc_id
            for hit in raw_hits
            if (doc_id := hit["payload"].get("document_id"))
        ]
        documents = await self._document_repo.get_by_ids(list(set(document_ids)))
        chunks = await self._document_chunk_repo.get_by_chunk_ids(chunk_ids)

        vector_hits = [
            VectorSearchHit(
                chunk_id=hit["chunk_id"],
                score=hit["score"],
                payload=hit["payload"],
                qdrant_point_id=hit.get("qdrant_point_id"),
            )
            for hit in raw_hits
        ]

        duration_ms = int((time.perf_counter() - started) * 1000)
        metadata: dict[str, Any] = {
            "requested_top_k": top_k,
            "raw_hit_count": len(raw_hits),
            "validated_count": 0,
            "duration_ms": duration_ms,
            "collection_name": collection_name,
        }
        if actor_user_id:
            metadata["actor_user_id"] = actor_user_id

        pack = build_evidence_pack(
            request=request,
            hits=vector_hits,
            documents=documents,
            chunks=chunks,
            collection_name=collection_name,
            retrieval_metadata=metadata,
        )
        metadata["validated_count"] = len(pack.evidence_items)
        if pack.status == EvidencePackStatus.EVIDENCE_FOUND:
            metadata.pop("missing_reason", None)
        elif pack.missing_reason:
            metadata["missing_reason"] = pack.missing_reason
        pack.retrieval_metadata = metadata

        logger.info(
            "retrieval_completed company_id=%s raw_hits=%s validated=%s status=%s duration_ms=%s",
            company_id,
            len(raw_hits),
            len(pack.evidence_items),
            pack.status.value,
            duration_ms,
        )
        return pack
