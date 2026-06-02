import asyncio
from datetime import UTC, datetime

import pytest

from app.core.config import EmbeddingSettings, Settings
from app.db.qdrant import QdrantClientManager
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    DocumentCreate,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
)
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.models.processing_job import (
    ProcessingJobCreate,
    ProcessingJobStage,
    ProcessingJobStatus,
)
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.fake_embedding_provider import FakeEmbeddingProvider
from app.services.qdrant_indexing_service import QdrantIndexingError, QdrantIndexingService
from tests.test_auth_routes import _FakeAuditRepo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_processing_job_repo_fakes import _FakeMongoManager as _JobFakeMongo
from tests.test_qdrant_indexing_fakes import FakeQdrantClient, FakeQdrantManager


def _settings() -> Settings:
    return Settings(
        embedding=EmbeddingSettings(
            provider="fake",
            collection_name="finsight_chunks_hf_minilm_l6_v2",
            vector_size=384,
        )
    )


def _sample_chunk_create(document_id: str, index: int) -> DocumentChunkCreate:
    chunk_id = f"{document_id}_chunk_{index:04d}"
    text = f"Chunk text {index} for indexing."
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
        text_preview=text[:120],
        char_count=len(text),
        word_count=len(text.split()),
        source_document_title="Annual Report",
        file_hash="abc123",
    )


def test_build_payload_includes_citation_fields():
    settings = _settings()
    service = QdrantIndexingService(
        None,
        None,
        None,
        FakeQdrantManager(),
        FakeEmbeddingProvider(dim=384),
        settings=settings,
    )
    from app.models.document_chunk import DocumentChunk

    chunk = DocumentChunk.model_validate(
        {
            "_id": "doc1_chunk_0000",
            "document_id": "doc1",
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "page_start": 1,
            "page_end": 2,
            "page_numbers": [1, 2],
            "chunk_index": 0,
            "text": "full text not in payload",
            "text_preview": "preview only",
            "char_count": 10,
            "word_count": 2,
            "source_document_title": "Report",
            "file_hash": "hash1",
        }
    )
    payload = service.build_payload(
        chunk,
        embedding_provider="fake",
        embedding_model="test-model",
    )
    assert payload["chunk_id"] == "doc1_chunk_0000"
    assert payload["text_preview"] == "preview only"
    assert "full text not in payload" not in payload.values()
    assert payload["searchable"] is False
    assert payload["page_start"] == 1


def test_point_id_deterministic():
    a = QdrantIndexingService.point_id_for_chunk("doc1_chunk_0000")
    b = QdrantIndexingService.point_id_for_chunk("doc1_chunk_0000")
    c = QdrantIndexingService.point_id_for_chunk("doc1_chunk_0001")
    assert a == b
    assert a != c


def test_ensure_collection_uses_configured_embedding_collection_name():
    settings = _settings()
    fake_qdrant = FakeQdrantManager(FakeQdrantClient())
    service = QdrantIndexingService(
        None,
        None,
        None,
        fake_qdrant,
        FakeEmbeddingProvider(dim=384),
        settings=settings,
    )
    collection_name = asyncio.run(service.ensure_collection())
    assert collection_name == settings.embedding.collection_name
    assert collection_name == "finsight_chunks_hf_minilm_l6_v2"
    assert collection_name in fake_qdrant.client.collections
    assert fake_qdrant.client.create_collection_calls
    assert (
        fake_qdrant.client.create_collection_calls[0]["collection_name"]
        == settings.embedding.collection_name
    )


def test_indexing_service_upserts_and_updates_metadata(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    settings = _settings()
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    job_repo = ProcessingJobRepository(_JobFakeMongo(), settings)
    audit_repo = _FakeAuditRepo()
    fake_qdrant = FakeQdrantManager(FakeQdrantClient())
    provider = FakeEmbeddingProvider(dim=384)
    service = QdrantIndexingService(
        document_repo,
        chunk_repo,
        job_repo,
        fake_qdrant,
        provider,
        audit_repo=audit_repo,
        settings=settings,
    )

    document_id = "doc-index-1"
    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="Index Test",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.INDEX_PENDING,
            approved_by="admin-1",
        )
    )
    asyncio.run(
        document_repo.update_chunk_result(
            document_id,
            chunk_status=ProcessingStageStatus.COMPLETED,
            lifecycle_status=DocumentLifecycleStatus.INDEX_PENDING,
            chunk_count=2,
            searchable=False,
        )
    )
    asyncio.run(
        document_repo._collection.update_one(
            {"_id": document_id},
            {"$set": {"file_hash": "sha256-deadbeef"}},
        )
    )
    asyncio.run(chunk_repo.create_many([_sample_chunk_create(document_id, 0), _sample_chunk_create(document_id, 1)]))

    job = asyncio.run(
        job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.QUEUED,
                stage=ProcessingJobStage.INDEX_PENDING,
            ),
            job_id="job-index-1",
        )
    )

    updated = asyncio.run(
        service.index_document_for_processing_job(job.job_id, admin_user_id="admin-1")
    )
    assert updated.stage == ProcessingJobStage.INDEX_COMPLETED.value
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.index_status == ProcessingStageStatus.COMPLETED.value
    assert doc.indexed_chunk_count == 2
    assert doc.searchable is False
    assert doc.lifecycle_status == DocumentLifecycleStatus.INDEXED.value

    chunks = asyncio.run(chunk_repo.list_by_document_id(document_id))
    assert all(c.qdrant_point_id for c in chunks)
    assert all(c.index_status == ProcessingStageStatus.COMPLETED.value for c in chunks)

    assert len(fake_qdrant.client.upsert_calls) == 1
    assert len(fake_qdrant.client.upsert_calls[0]["points"]) == 2

    asyncio.run(
        service.index_document_for_processing_job(job.job_id, admin_user_id="admin-1")
    )
    assert len(fake_qdrant.client.upsert_calls) == 2
    assert len(fake_qdrant.client.points) == 2


def test_indexing_fails_without_chunks(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    settings = _settings()
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    job_repo = ProcessingJobRepository(_JobFakeMongo(), settings)
    service = QdrantIndexingService(
        document_repo,
        chunk_repo,
        job_repo,
        FakeQdrantManager(FakeQdrantClient()),
        FakeEmbeddingProvider(dim=384),
        settings=settings,
    )

    document_id = "doc-no-chunks"
    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="No Chunks",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.INDEX_PENDING,
            approved_by="admin-1",
        )
    )
    asyncio.run(
        document_repo.update_chunk_result(
            document_id,
            chunk_status=ProcessingStageStatus.COMPLETED,
            lifecycle_status=DocumentLifecycleStatus.INDEX_PENDING,
            searchable=False,
        )
    )
    asyncio.run(
        document_repo._collection.update_one(
            {"_id": document_id},
            {"$set": {"file_hash": "sha256-deadbeef"}},
        )
    )
    job = asyncio.run(
        job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.QUEUED,
                stage=ProcessingJobStage.INDEX_PENDING,
            ),
            job_id="job-no-chunks",
        )
    )

    with pytest.raises(QdrantIndexingError) as exc_info:
        asyncio.run(
            service.index_document_for_processing_job(job.job_id, admin_user_id="admin-1")
        )
    assert exc_info.value.code == "no_chunks"
