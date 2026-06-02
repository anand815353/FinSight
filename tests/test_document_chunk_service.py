import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    DocumentCreate,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
)
from app.models.processing_job import (
    ProcessingJobCreate,
    ProcessingJobStage,
    ProcessingJobStatus,
)
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.chunk_builder import CHUNK_MAX_CHARS, build_chunks_from_pages
from app.services.document_chunk_service import DocumentChunkError, DocumentChunkService
from tests.test_auth_routes import _FakeAuditRepo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_processing_job_repo_fakes import _FakeMongoManager as _JobFakeMongo


def _sample_document():
    from app.models.document import Document

    return Document.model_validate(
        {
            "_id": "doc-chunk-1",
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
            "normalized_title": "annual report fy2025",
            "parse_status": "completed",
            "approval_status": "approved",
            "searchable": False,
        }
    )


def test_build_chunks_skips_empty_pages():
    document = _sample_document()
    pages_payload = {
        "document_id": document.document_id,
        "page_count": 2,
        "pages": [
            {"page_number": 1, "text": "   ", "text_length": 0, "extraction_status": "empty"},
            {"page_number": 2, "text": "Valid page text.", "text_length": 16, "extraction_status": "ok"},
        ],
    }
    chunks = build_chunks_from_pages(document, pages_payload)
    assert len(chunks) == 1
    assert chunks[0].page_start == 2
    assert chunks[0].chunk_index == 0


def test_build_chunks_splits_long_page():
    document = _sample_document()
    long_text = "A" * (CHUNK_MAX_CHARS + 500)
    pages_payload = {
        "document_id": document.document_id,
        "page_count": 1,
        "pages": [
            {"page_number": 1, "text": long_text, "text_length": len(long_text), "extraction_status": "ok"},
        ],
    }
    chunks = build_chunks_from_pages(document, pages_payload)
    assert len(chunks) >= 2
    assert all(chunk.page_start == 1 and chunk.page_end == 1 for chunk in chunks)
    assert chunks[0].chunk_id == f"{document.document_id}_chunk_0000"


def test_chunk_service_success_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    parsed_root = tmp_path / "parsed"
    chunk_root = tmp_path / "chunks"
    monkeypatch.setenv("STORAGE__PARSED_STORAGE_PATH", str(parsed_root))
    monkeypatch.setenv("STORAGE__CHUNK_STORAGE_PATH", str(chunk_root))

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    job_repo = ProcessingJobRepository(_JobFakeMongo(), settings)
    audit_repo = _FakeAuditRepo()

    document_id = "doc-chunk-svc"
    pages_path = parsed_root / document_id / "pages.json"
    pages_path.parent.mkdir(parents=True)
    pages_path.write_text(
        json.dumps(
            {
                "document_id": document_id,
                "page_count": 2,
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Page one chunkable text.",
                        "text_length": 24,
                        "extraction_status": "ok",
                    },
                    {
                        "page_number": 2,
                        "text": "Page two chunkable text.",
                        "text_length": 24,
                        "extraction_status": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="Chunk Service Test",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.PARSED,
            approved_by="admin-1",
        )
    )
    asyncio.run(
        document_repo.update_parse_result(
            document_id,
            parse_status=ProcessingStageStatus.COMPLETED,
            lifecycle_status=DocumentLifecycleStatus.PARSED,
            parsed_pages_path=pages_path.as_posix(),
            page_count=2,
            searchable=False,
        )
    )
    job = asyncio.run(
        job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.COMPLETED,
                stage=ProcessingJobStage.PARSE_COMPLETED,
            ),
            job_id="job-chunk-1",
        )
    )

    service = DocumentChunkService(
        document_repo,
        chunk_repo,
        job_repo,
        audit_repo=audit_repo,
        settings=settings,
    )
    updated = asyncio.run(
        service.chunk_document_for_processing_job(job.job_id, admin_user_id="admin-1")
    )
    assert updated.stage == ProcessingJobStage.INDEX_PENDING.value
    assert updated.status == ProcessingJobStatus.QUEUED.value

    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.chunk_status == ProcessingStageStatus.COMPLETED.value
    assert doc.chunk_count == 2
    assert doc.searchable is False
    assert (chunk_root / document_id / "chunks.json").is_file()

    first_ids = {c.chunk_id for c in asyncio.run(chunk_repo.list_by_document_id(document_id))}
    asyncio.run(service.chunk_document_for_processing_job(job.job_id, admin_user_id="admin-1"))
    second_ids = {c.chunk_id for c in asyncio.run(chunk_repo.list_by_document_id(document_id))}
    assert first_ids == second_ids
    assert len(second_ids) == 2

    completed = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_CHUNKING_COMPLETED
    ]
    assert len(completed) == 2


def test_chunk_service_missing_parsed_file_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    monkeypatch.setenv("STORAGE__CHUNK_STORAGE_PATH", str(tmp_path / "chunks"))

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
    job_repo = ProcessingJobRepository(_JobFakeMongo(), settings)
    document_id = "doc-missing-pages"
    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="Missing Pages",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.PARSED,
            approved_by="admin-1",
        )
    )
    asyncio.run(
        document_repo.update_parse_result(
            document_id,
            parse_status=ProcessingStageStatus.COMPLETED,
            lifecycle_status=DocumentLifecycleStatus.PARSED,
            parsed_pages_path="/missing/pages.json",
            searchable=False,
        )
    )
    job = asyncio.run(
        job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.COMPLETED,
                stage=ProcessingJobStage.PARSE_COMPLETED,
            ),
            job_id="job-missing",
        )
    )
    service = DocumentChunkService(document_repo, chunk_repo, job_repo, settings=settings)
    with pytest.raises(DocumentChunkError) as exc_info:
        asyncio.run(service.chunk_document_for_processing_job(job.job_id, admin_user_id="admin-1"))
    assert exc_info.value.code == "parsed_pages_missing"
    assert asyncio.run(chunk_repo.count_by_document_id(document_id)) == 0


def test_chunk_modules_do_not_import_qdrant_or_llm():
    from pathlib import Path as PathLib

    import app.services.chunk_builder as builder
    import app.services.document_chunk_service as service

    for module in (builder, service):
        source = PathLib(module.__file__).read_text(encoding="utf-8")
        assert "qdrant" not in source.lower()
        assert "gemini" not in source.lower()
        assert "sentence_transformers" not in source.lower()
