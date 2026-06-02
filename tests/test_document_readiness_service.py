import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import EmbeddingSettings, Settings
from app.models.audit_log import AuditEventType
from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentCreate,
    DocumentLifecycleStatus,
    DocumentReadinessStatus,
    ProcessingStageStatus,
)
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.models.document_validation import ValidationStatus
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.document_validation_repo import DocumentValidationRepository
from app.services.document_readiness_service import DocumentReadinessService
from app.services.qdrant_indexing_service import QdrantIndexingService
from tests.test_auth_routes import _FakeAuditRepo
from tests.test_company_repo import _FakeMongoManager as _CompanyFakeMongo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_document_validation_repo_fakes import _FakeMongoManager as _ValidationFakeMongo


def _settings() -> Settings:
    return Settings(
        embedding=EmbeddingSettings(
            provider="fake",
            collection_name="finsight_chunks_hf_minilm_l6_v2",
        )
    )


def _chunk_create(document_id: str, index: int) -> DocumentChunkCreate:
    chunk_id = f"{document_id}_chunk_{index:04d}"
    text = f"Chunk {index} citation text."
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
        source_url="https://example.com/ref",
        file_hash="abc123hash",
    )


async def _seed_ready_document(tmp_path: Path, *, broken_point_id: bool = False):
    settings = _settings()
    company_repo = CompanyRepository(_CompanyFakeMongo(), settings)
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    validation_repo = DocumentValidationRepository(_ValidationFakeMongo(), settings)
    audit_repo = _FakeAuditRepo()

    await company_repo.create(
        CompanyCreate(
            company_id="reliance_industries",
            name="Reliance Industries Ltd",
            display_name="Reliance Industries",
            nse_symbol="RELIANCE",
            status=CompanyStatus.ACTIVE,
        )
    )

    document_id = "doc-ready-1"
    raw_path = tmp_path / "raw" / document_id / "file.pdf"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"%PDF-1.4\n%%EOF")

    pages_path = tmp_path / "parsed" / document_id / "pages.json"
    pages_path.parent.mkdir(parents=True)
    pages_path.write_text(
        json.dumps({"document_id": document_id, "page_count": 1, "pages": []}),
        encoding="utf-8",
    )

    page_map_path = tmp_path / "page_maps" / document_id / "page_map.json"
    page_map_path.parent.mkdir(parents=True)
    page_map_path.write_text(json.dumps({"document_id": document_id, "pages": []}), encoding="utf-8")

    chunk_artifact = tmp_path / "chunks" / document_id / "chunks.json"
    chunk_artifact.parent.mkdir(parents=True)
    chunk_artifact.write_text("{}", encoding="utf-8")

    await document_repo.create(
        DocumentCreate(
            company_id="reliance_industries",
            document_type="annual_report",
            title="Ready Doc",
            source_type="admin_uploaded_official_source_document",
            file_hash="abc123hash",
            searchable=False,
        ),
        document_id=document_id,
    )
    await document_repo._collection.update_one(
        {"_id": document_id},
        {
            "$set": {
                "raw_storage_path": raw_path.as_posix(),
                "parsed_pages_path": pages_path.as_posix(),
                "page_map_path": page_map_path.as_posix(),
                "chunk_artifact_path": chunk_artifact.as_posix(),
                "approval_status": ApprovalStatus.APPROVED.value,
                "parse_status": ProcessingStageStatus.COMPLETED.value,
                "chunk_status": ProcessingStageStatus.COMPLETED.value,
                "index_status": ProcessingStageStatus.COMPLETED.value,
                "lifecycle_status": DocumentLifecycleStatus.INDEXED.value,
                "page_count": 1,
                "chunk_count": 1,
                "indexed_chunk_count": 1,
            }
        },
    )

    chunk_id = f"{document_id}_chunk_0000"
    await chunk_repo.create_many([_chunk_create(document_id, 0)])
    point_id = "bad-point-id" if broken_point_id else QdrantIndexingService.point_id_for_chunk(chunk_id)
    await chunk_repo.update_indexing_metadata(
        [
            {
                "chunk_id": chunk_id,
                "qdrant_collection": "finsight_chunks_hf_minilm_l6_v2",
                "qdrant_point_id": point_id,
                "embedding_provider": "fake",
                "embedding_model": "test-model",
                "index_status": ProcessingStageStatus.COMPLETED.value,
            }
        ]
    )

    service = DocumentReadinessService(
        document_repo,
        chunk_repo,
        company_repo,
        validation_repo,
        audit_repo=audit_repo,
        settings=settings,
    )
    return service, document_repo, document_id, audit_repo


def test_readiness_pass_enables_searchable(tmp_path):
    service, document_repo, document_id, audit_repo = asyncio.run(_seed_ready_document(tmp_path))
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.PASSED.value
    assert result.searchability_enabled is True

    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.searchable is True
    assert doc.lifecycle_status == DocumentLifecycleStatus.READY.value
    assert doc.readiness_status == DocumentReadinessStatus.PASSED.value
    assert doc.searchable_by == "admin-1"

    passed_events = [
        e for e in audit_repo.events
        if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_SEARCHABILITY_ENABLED
    ]
    assert passed_events


def test_readiness_fails_pending_approval(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    asyncio.run(
        document_repo._collection.update_one(
            {"_id": document_id},
            {"$set": {"approval_status": ApprovalStatus.PENDING.value}},
        )
    )
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.FAILED.value
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.searchable is False


def test_readiness_fails_missing_page_map(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    asyncio.run(
        document_repo._collection.update_one(
            {"_id": document_id},
            {"$set": {"page_map_path": None}},
        )
    )
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.FAILED.value
    assert any("page_map" in err for err in result.fatal_errors)


def test_readiness_fails_missing_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    from app.repositories.document_chunk_repo import DocumentChunkRepository
    from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo

    empty_chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), _settings())
    service._document_chunk_repo = empty_chunk_repo
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.FAILED.value
    assert any("chunk" in err.lower() for err in result.fatal_errors)


def test_readiness_fails_missing_chunk_index_status(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    chunk_id = f"{document_id}_chunk_0000"
    asyncio.run(
        service._document_chunk_repo._collection.update_one(
            {"_id": chunk_id},
            {"$set": {"index_status": None}},
        )
    )
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.FAILED.value
    assert any("index_status_missing" in err for err in result.fatal_errors)
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.searchable is False


def test_readiness_fails_invalid_chunk_index_status(tmp_path):
    from app.models.document_chunk import DocumentChunk

    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    document = asyncio.run(document_repo.get_by_id(document_id))
    chunks = asyncio.run(service._document_chunk_repo.list_by_document_id(document_id))
    bad_chunk = DocumentChunk.model_construct(
        **{
            **chunks[0].model_dump(by_alias=True),
            "index_status": "bogus",
        }
    )
    outcome = service._run_checks(
        document,
        chunks=[bad_chunk],
        expected_collection="finsight_chunks_hf_minilm_l6_v2",
    )
    assert outcome.passed is False
    assert any("index_status_invalid" in err for err in outcome.fatal_errors)


def test_readiness_fails_collection_mismatch(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    document = asyncio.run(document_repo.get_by_id(document_id))
    chunks = asyncio.run(service._document_chunk_repo.list_by_document_id(document_id))
    from app.models.document_chunk import DocumentChunk

    mismatched = DocumentChunk.model_construct(
        **{
            **chunks[0].model_dump(by_alias=True),
            "qdrant_collection": "finsight_chunks_gemini_v1",
        }
    )
    outcome = service._run_checks(
        document,
        chunks=[mismatched],
        expected_collection="finsight_chunks_hf_minilm_l6_v2",
    )
    assert outcome.passed is False
    assert any("collection_mismatch" in err for err in outcome.fatal_errors)


def test_readiness_fails_invalid_qdrant_point_id(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(
        _seed_ready_document(tmp_path, broken_point_id=True)
    )
    result = asyncio.run(
        service.validate_document_readiness(document_id, admin_user_id="admin-1")
    )
    assert result.status == ValidationStatus.FAILED.value
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.searchable is False


def test_readiness_fails_unsupported_document_type(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    document = asyncio.run(document_repo.get_by_id(document_id))
    chunks = asyncio.run(service._document_chunk_repo.list_by_document_id(document_id))
    invalid_doc = Document.model_construct(
        **{
            **document.model_dump(by_alias=True),
            "document_type": "concall_transcript",
        }
    )
    outcome = service._run_checks(
        invalid_doc,
        chunks=chunks,
        expected_collection="finsight_chunks_hf_minilm_l6_v2",
    )
    assert outcome.passed is False
    assert any("unsupported_document_type" in err for err in outcome.fatal_errors)


def test_readiness_fails_invalid_source_type(tmp_path):
    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    document = asyncio.run(document_repo.get_by_id(document_id))
    chunks = asyncio.run(service._document_chunk_repo.list_by_document_id(document_id))
    invalid_doc = Document.model_construct(
        **{
            **document.model_dump(by_alias=True),
            "source_type": "random_blog",
        }
    )
    outcome = service._run_checks(
        invalid_doc,
        chunks=chunks,
        expected_collection="finsight_chunks_hf_minilm_l6_v2",
    )
    assert outcome.passed is False
    assert any("invalid_source_type" in err for err in outcome.fatal_errors)
