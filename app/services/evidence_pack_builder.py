from __future__ import annotations

from typing import Any

from app.models.document import Document, ProcessingStageStatus
from app.models.document_chunk import DocumentChunk
from app.models.retrieval import (
    EvidenceItem,
    EvidencePack,
    EvidencePackStatus,
    RetrievalRequest,
)


class VectorSearchHit:
    def __init__(
        self,
        *,
        chunk_id: str,
        score: float,
        payload: dict[str, Any],
        qdrant_point_id: str | None = None,
    ):
        self.chunk_id = chunk_id
        self.score = score
        self.payload = payload
        self.qdrant_point_id = qdrant_point_id


def validate_citation_metadata(
    *,
    document: Document,
    chunk: DocumentChunk,
    payload: dict[str, Any],
) -> bool:
    if not document.searchable:
        return False
    if chunk.page_start < 1 or chunk.page_end < chunk.page_start:
        return False
    if not chunk.page_numbers:
        return False
    preview = (chunk.text_preview or payload.get("text_preview") or "").strip()
    if not preview:
        return False
    title = (chunk.source_document_title or document.title or "").strip()
    if not title:
        return False
    if not chunk.chunk_id or not chunk.document_id:
        return False
    if chunk.index_status is not None:
        if ProcessingStageStatus(chunk.index_status) != ProcessingStageStatus.COMPLETED:
            return False
    return True


def build_evidence_item(
    *,
    hit: VectorSearchHit,
    document: Document,
    chunk: DocumentChunk,
    collection_name: str,
) -> EvidenceItem | None:
    if not validate_citation_metadata(document=document, chunk=chunk, payload=hit.payload):
        return None
    preview = (chunk.text_preview or hit.payload.get("text_preview") or "").strip()
    title = (chunk.source_document_title or document.title or document.document_id).strip()
    return EvidenceItem(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        company_id=chunk.company_id,
        document_type=str(chunk.document_type),
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        page_numbers=list(chunk.page_numbers),
        section_title=chunk.section_title or hit.payload.get("section_title"),
        text_snippet=preview,
        text_preview=preview,
        source_document_title=title,
        source_url=chunk.source_url or hit.payload.get("source_url"),
        file_hash=chunk.file_hash or hit.payload.get("file_hash"),
        score=hit.score,
        qdrant_point_id=hit.qdrant_point_id or chunk.qdrant_point_id,
        qdrant_collection=chunk.qdrant_collection or collection_name,
        embedding_model=chunk.embedding_model or hit.payload.get("embedding_model"),
        embedding_provider=chunk.embedding_provider or hit.payload.get("embedding_provider"),
    )


def build_evidence_pack(
    *,
    request: RetrievalRequest,
    hits: list[VectorSearchHit],
    documents: dict[str, Document],
    chunks: dict[str, DocumentChunk],
    collection_name: str,
    retrieval_metadata: dict[str, Any],
) -> EvidencePack:
    items: list[EvidenceItem] = []
    for hit in hits:
        chunk = chunks.get(hit.chunk_id)
        if chunk is None:
            continue
        document = documents.get(chunk.document_id)
        if document is None:
            continue
        item = build_evidence_item(
            hit=hit,
            document=document,
            chunk=chunk,
            collection_name=collection_name,
        )
        if item is not None:
            items.append(item)

    if items:
        return EvidencePack(
            query=request.query,
            filters=request.filters,
            status=EvidencePackStatus.EVIDENCE_FOUND,
            evidence_items=items,
            missing_reason=None,
            retrieval_metadata=retrieval_metadata,
        )
    return build_insufficient_pack(
        request,
        reason=str(retrieval_metadata.get("missing_reason", "no_searchable_matches")),
        retrieval_metadata=retrieval_metadata,
    )


def build_insufficient_pack(
    request: RetrievalRequest,
    *,
    reason: str,
    retrieval_metadata: dict[str, Any] | None = None,
) -> EvidencePack:
    return EvidencePack(
        query=request.query,
        filters=request.filters,
        status=EvidencePackStatus.INSUFFICIENT_EVIDENCE,
        evidence_items=[],
        missing_reason=reason,
        retrieval_metadata=retrieval_metadata or {},
    )
