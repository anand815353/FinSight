from __future__ import annotations

from app.models.document import Document, ProcessingStageStatus
from app.models.document_chunk import ChunkType, DocumentChunk
from app.models.retrieval import EvidencePackStatus, RetrievalFilters, RetrievalRequest
from app.services.evidence_pack_builder import (
    VectorSearchHit,
    build_evidence_item,
    build_evidence_pack,
    build_insufficient_pack,
    validate_citation_metadata,
)


def _document(*, searchable: bool = True) -> Document:
    return Document.model_construct(
        _id="doc-1",
        company_id="reliance_industries",
        document_type="annual_report",
        title="Annual Report FY2025",
        searchable=searchable,
    )


def _chunk(*, preview: str = "Snippet text.", page_numbers: list[int] | None = None) -> DocumentChunk:
    return DocumentChunk.model_construct(
        _id="doc-1_chunk_0000",
        document_id="doc-1",
        company_id="reliance_industries",
        document_type="annual_report",
        page_start=1,
        page_end=1,
        page_numbers=page_numbers if page_numbers is not None else [1],
        chunk_index=0,
        chunk_type=ChunkType.TEXT,
        text="Snippet text.",
        text_preview=preview,
        char_count=12,
        word_count=2,
        source_document_title="Annual Report FY2025",
        index_status=ProcessingStageStatus.COMPLETED,
    )


def test_validate_citation_metadata_requires_searchable():
    document = _document(searchable=False)
    chunk = _chunk()
    assert validate_citation_metadata(document=document, chunk=chunk, payload={}) is False


def test_validate_citation_metadata_requires_page_and_preview():
    document = _document()
    chunk = _chunk(preview="", page_numbers=[])
    assert validate_citation_metadata(document=document, chunk=chunk, payload={}) is False


def test_build_evidence_item_has_citation_fields():
    document = _document()
    chunk = _chunk()
    hit = VectorSearchHit(
        chunk_id=chunk.chunk_id,
        score=0.91,
        payload={"text_preview": chunk.text_preview},
        qdrant_point_id="point-1",
    )
    item = build_evidence_item(
        hit=hit,
        document=document,
        chunk=chunk,
        collection_name="finsight_chunks_hf_minilm_l6_v2",
    )
    assert item is not None
    assert item.chunk_id == chunk.chunk_id
    assert item.page_start == 1
    assert item.source_document_title
    assert item.text_snippet


def test_build_evidence_pack_insufficient_when_no_valid_items():
    request = RetrievalRequest(
        query="revenue growth",
        filters=RetrievalFilters(company_id="reliance_industries"),
    )
    pack = build_evidence_pack(
        request=request,
        hits=[
            VectorSearchHit(
                chunk_id="missing-chunk",
                score=0.9,
                payload={"document_id": "doc-1"},
            )
        ],
        documents={},
        chunks={},
        collection_name="finsight_chunks_hf_minilm_l6_v2",
        retrieval_metadata={"missing_reason": "no_searchable_matches"},
    )
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.missing_reason == "no_searchable_matches"


def test_validate_citation_metadata_rejects_incomplete_index_status():
    document = _document()
    chunk = _chunk()
    chunk = DocumentChunk.model_construct(
        **{
            **chunk.model_dump(by_alias=True),
            "index_status": ProcessingStageStatus.PENDING,
        }
    )
    assert validate_citation_metadata(document=document, chunk=chunk, payload={}) is False


def test_build_evidence_pack_insufficient_when_index_status_incomplete():
    document = _document()
    chunk = _chunk()
    chunk = DocumentChunk.model_construct(
        **{
            **chunk.model_dump(by_alias=True),
            "index_status": ProcessingStageStatus.PENDING,
        }
    )
    request = RetrievalRequest(
        query="revenue growth",
        filters=RetrievalFilters(company_id="reliance_industries"),
    )
    pack = build_evidence_pack(
        request=request,
        hits=[
            VectorSearchHit(
                chunk_id=chunk.chunk_id,
                score=0.9,
                payload={"text_preview": chunk.text_preview},
            )
        ],
        documents={document.document_id: document},
        chunks={chunk.chunk_id: chunk},
        collection_name="finsight_chunks_hf_minilm_l6_v2",
        retrieval_metadata={"missing_reason": "no_searchable_matches"},
    )
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.evidence_items == []


def test_build_insufficient_pack_shape():
    request = RetrievalRequest(
        query="test",
        filters=RetrievalFilters(company_id="reliance_industries"),
    )
    pack = build_insufficient_pack(request, reason="qdrant_unavailable")
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.evidence_items == []
    assert pack.missing_reason == "qdrant_unavailable"
