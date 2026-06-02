from __future__ import annotations

import asyncio
import importlib
import inspect
from pathlib import Path

import pytest

from app.core.config import EmbeddingSettings, Settings
from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import DocumentCreate, ProcessingStageStatus
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.models.retrieval import EvidencePackStatus, RetrievalFilters, RetrievalRequest
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.services.qdrant_indexing_service import QdrantIndexingService
from app.services.retrieval_service import RetrievalService
from tests.test_company_repo import _FakeMongoManager as _CompanyFakeMongo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_qdrant_indexing_fakes import FakeQdrantClient, FakeQdrantManager


def _settings() -> Settings:
    return Settings(
        embedding=EmbeddingSettings(
            provider="fake",
            collection_name="finsight_chunks_hf_minilm_l6_v2",
        )
    )


def _chunk_create(document_id: str, index: int = 0) -> DocumentChunkCreate:
    chunk_id = f"{document_id}_chunk_{index:04d}"
    text = "Revenue increased year over year."
    return DocumentChunkCreate(
        chunk_id=chunk_id,
        document_id=document_id,
        company_id="reliance_industries",
        document_type="annual_report",
        page_start=1,
        page_end=1,
        page_numbers=[1],
        chunk_index=index,
        chunk_type=ChunkType.TEXT,
        text=text,
        text_preview=text[:80],
        char_count=len(text),
        word_count=len(text.split()),
        source_document_title="Annual Report FY2025",
        file_hash="abc123hash",
    )


async def _seed_searchable_document(
    *,
    searchable: bool = True,
    qdrant_payload_searchable: bool = False,
):
    settings = _settings()
    company_repo = CompanyRepository(_CompanyFakeMongo(), settings)
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    fake_qdrant_client = FakeQdrantClient()
    fake_qdrant = FakeQdrantManager(fake_qdrant_client)

    await company_repo.create(
        CompanyCreate(
            company_id="reliance_industries",
            name="Reliance Industries Ltd",
            display_name="Reliance Industries",
            nse_symbol="RELIANCE",
            status=CompanyStatus.ACTIVE,
        )
    )

    document_id = "doc-retrieval-1"
    await document_repo.create(
        DocumentCreate(
            company_id="reliance_industries",
            document_type="annual_report",
            title="Annual Report FY2025",
            source_type="admin_uploaded_official_source_document",
            file_hash="abc123hash",
            searchable=False,
        ),
        document_id=document_id,
    )
    await document_repo._collection.update_one(
        {"_id": document_id},
        {"$set": {"searchable": searchable}},
    )

    await chunk_repo.create_many([_chunk_create(document_id)])
    chunk_id = f"{document_id}_chunk_0000"
    point_id = QdrantIndexingService.point_id_for_chunk(chunk_id)
    await chunk_repo.update_indexing_metadata(
        [
            {
                "chunk_id": chunk_id,
                "qdrant_collection": settings.embedding.collection_name,
                "qdrant_point_id": point_id,
                "embedding_provider": "fake",
                "embedding_model": "test-model",
                "index_status": ProcessingStageStatus.COMPLETED.value,
            }
        ]
    )

    chunk = await chunk_repo.list_by_document_id(document_id)
    assert chunk
    payload = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "company_id": "reliance_industries",
        "document_type": "annual_report",
        "page_start": 1,
        "page_end": 1,
        "page_numbers": [1],
        "text_preview": chunk[0].text_preview,
        "source_document_title": chunk[0].source_document_title,
        "searchable": qdrant_payload_searchable,
    }
    fake_qdrant_client.points[point_id] = {
        "collection": settings.embedding.collection_name,
        "vector": [0.1] * settings.embedding.vector_size,
        "payload": payload,
    }

    from app.services.embedding_provider import get_embedding_provider

    retrieval_service = RetrievalService(
        fake_qdrant,
        get_embedding_provider(settings),
        document_repo,
        chunk_repo,
        settings=settings,
    )
    return retrieval_service, fake_qdrant_client, document_id, chunk_id


def test_retrieval_module_has_no_gemini_or_sentence_transformers_imports():
    module = importlib.import_module("app.services.retrieval_service")
    source = inspect.getsource(module)
    assert "gemini" not in source.lower()
    assert "sentence_transformers" not in source.lower()


def test_retrieve_evidence_found_when_searchable():
    service, fake_qdrant, document_id, _chunk_id = asyncio.run(
        _seed_searchable_document(searchable=True)
    )
    request = RetrievalRequest(
        query="revenue growth",
        filters=RetrievalFilters(company_id="reliance_industries", document_ids=[document_id]),
        top_k=5,
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.EVIDENCE_FOUND
    assert len(pack.evidence_items) == 1
    assert pack.missing_reason is None
    assert "missing_reason" not in pack.retrieval_metadata
    assert fake_qdrant.search_calls
    query_filter = fake_qdrant.search_calls[0]["query_filter"]
    must = getattr(query_filter, "must", [])
    keys = {getattr(condition, "key", None) for condition in must}
    assert "company_id" in keys


def test_retrieve_excludes_non_searchable_document_even_if_qdrant_hit():
    service, fake_qdrant, document_id, _chunk_id = asyncio.run(
        _seed_searchable_document(searchable=False, qdrant_payload_searchable=True)
    )
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries", document_ids=[document_id]),
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.missing_reason == "no_searchable_matches"
    assert pack.retrieval_metadata.get("missing_reason") == "no_searchable_matches"
    assert fake_qdrant.search_calls


def test_retrieve_excludes_chunk_missing_page_metadata():
    service, fake_qdrant, document_id, chunk_id = asyncio.run(
        _seed_searchable_document(searchable=True)
    )
    asyncio.run(
        service._document_chunk_repo._collection.update_one(
            {"_id": chunk_id},
            {"$set": {"page_numbers": [], "text_preview": ""}},
        )
    )
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries"),
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.missing_reason == "no_searchable_matches"
    assert pack.retrieval_metadata.get("missing_reason") == "no_searchable_matches"


def test_retrieve_insufficient_on_empty_hits():
    service, fake_qdrant, document_id, _chunk_id = asyncio.run(
        _seed_searchable_document(searchable=True)
    )
    fake_qdrant.points.clear()
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries", document_ids=[document_id]),
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.missing_reason == "no_searchable_matches"
    assert pack.retrieval_metadata.get("missing_reason") == "no_searchable_matches"


def test_retrieve_respects_top_k():
    service, fake_qdrant, document_id, _chunk_id = asyncio.run(
        _seed_searchable_document(searchable=True)
    )
    collection = service._settings.embedding.collection_name
    for index in range(1, 4):
        chunk_id = f"{document_id}_chunk_{index:04d}"
        point_id = QdrantIndexingService.point_id_for_chunk(chunk_id)
        fake_qdrant.points[point_id] = {
            "collection": collection,
            "vector": [0.1] * service._settings.embedding.vector_size,
            "payload": {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "company_id": "reliance_industries",
                "document_type": "annual_report",
                "text_preview": f"preview {index}",
            },
        }
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries"),
        top_k=2,
    )
    asyncio.run(service.retrieve_evidence(request))
    assert fake_qdrant.search_calls[-1]["limit"] == 2


def test_retrieve_score_threshold_filters_low_scores(monkeypatch):
    service, fake_qdrant, document_id, _chunk_id = asyncio.run(
        _seed_searchable_document(searchable=True)
    )

    async def low_score_search(*args, **kwargs):
        return []

    monkeypatch.setattr(fake_qdrant, "search", low_score_search)
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries"),
        score_threshold=0.99,
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE


def test_retrieve_qdrant_unavailable_without_client():
    settings = _settings()
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    from app.services.embedding_provider import get_embedding_provider

    manager = FakeQdrantManager()
    manager._client = None
    service = RetrievalService(
        manager,
        get_embedding_provider(settings),
        document_repo,
        chunk_repo,
        settings=settings,
    )
    request = RetrievalRequest(
        query="revenue",
        filters=RetrievalFilters(company_id="reliance_industries"),
    )
    pack = asyncio.run(service.retrieve_evidence(request))
    assert pack.status == EvidencePackStatus.INSUFFICIENT_EVIDENCE
    assert pack.missing_reason == "qdrant_unavailable"
