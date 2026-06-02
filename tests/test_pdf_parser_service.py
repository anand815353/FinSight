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
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.document_parse_service import DocumentParseError, DocumentParseService
from app.services.pdf_parser_service import PdfParseError, PdfParserService
from tests.fixtures.pdf_factory import two_page_pdf_bytes
from tests.test_auth_routes import _FakeAuditRepo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_processing_job_repo_fakes import _FakeMongoManager as _ProcessingJobFakeMongo


@pytest.fixture
def parser():
    return PdfParserService()


def test_parser_extracts_page_count_and_text(parser, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(two_page_pdf_bytes())
    result = parser.parse_pdf_file(pdf_path)
    assert result["page_count"] == 2
    assert len(result["pages"]) == 2
    assert "Page one text" in result["pages"][0]["text"]
    assert result["pages"][0]["extraction_status"] == "ok"
    assert result["page_map_pages"][1]["has_text"] is True


def test_parser_rejects_missing_file(parser, tmp_path):
    with pytest.raises(PdfParseError) as exc_info:
        parser.parse_pdf_file(tmp_path / "missing.pdf")
    assert exc_info.value.code == "file_not_found"


def test_parser_rejects_corrupt_pdf(parser, tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(PdfParseError) as exc_info:
        parser.parse_pdf_file(corrupt)
    assert exc_info.value.code == "invalid_pdf"


def test_parser_rejects_empty_text_pdf(parser, tmp_path):
    import fitz

    document = fitz.open()
    try:
        document.new_page()
        empty_path = tmp_path / "empty.pdf"
        empty_path.write_bytes(document.tobytes())
    finally:
        document.close()
    with pytest.raises(PdfParseError) as exc_info:
        parser.parse_pdf_file(empty_path)
    assert exc_info.value.code == "empty_document"


def test_parse_service_writes_artifacts_and_updates_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    parsed_root = tmp_path / "parsed"
    page_map_root = tmp_path / "page_maps"
    upload_root = tmp_path / "uploads"
    monkeypatch.setenv("STORAGE__PARSED_STORAGE_PATH", str(parsed_root))
    monkeypatch.setenv("STORAGE__PAGE_MAP_STORAGE_PATH", str(page_map_root))
    monkeypatch.setenv("STORAGE__ADMIN_PENDING_UPLOAD_PATH", str(upload_root))

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    processing_job_repo = ProcessingJobRepository(_ProcessingJobFakeMongo(), settings)
    audit_repo = _FakeAuditRepo()

    company_id = "reliance_industries"
    document_id = "doc-parse-1"
    storage_dir = upload_root / company_id / document_id
    storage_dir.mkdir(parents=True)
    (storage_dir / "original.pdf").write_bytes(two_page_pdf_bytes())

    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id=company_id,
                document_type="annual_report",
                title="Parse Test",
                file_name="original.pdf",
                file_hash="abc123",
                raw_storage_path=f"{storage_dir.as_posix()}/",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.PARSE_PENDING,
            approved_by="admin-1",
        )
    )
    job = asyncio.run(
        processing_job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id=company_id,
                requested_by="admin-1",
                status=ProcessingJobStatus.QUEUED,
                stage=ProcessingJobStage.PARSE_PENDING,
            ),
            job_id="job-parse-1",
        )
    )
    asyncio.run(
        document_repo.update_processing_stages(
            document_id,
            parse_status=ProcessingStageStatus.PENDING,
            lifecycle_status=DocumentLifecycleStatus.PARSE_PENDING,
            searchable=False,
        )
    )

    service = DocumentParseService(
        document_repo,
        processing_job_repo,
        audit_repo=audit_repo,
        settings=settings,
    )
    updated_job = asyncio.run(
        service.parse_document_for_processing_job(job.job_id, admin_user_id="admin-1")
    )
    assert updated_job.status == ProcessingJobStatus.COMPLETED.value
    assert updated_job.stage == ProcessingJobStage.PARSE_COMPLETED.value

    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc is not None
    assert doc.parse_status == ProcessingStageStatus.COMPLETED.value
    assert doc.lifecycle_status == DocumentLifecycleStatus.PARSED.value
    assert doc.page_count == 2
    assert doc.searchable is False
    assert doc.parsed_at is not None

    pages_path = parsed_root / document_id / "pages.json"
    page_map_path = page_map_root / document_id / "page_map.json"
    assert pages_path.is_file()
    assert page_map_path.is_file()
    pages_payload = json.loads(pages_path.read_text(encoding="utf-8"))
    page_map_payload = json.loads(page_map_path.read_text(encoding="utf-8"))
    assert pages_payload["page_count"] == 2
    assert page_map_payload["document_id"] == document_id
    assert page_map_payload["pages"][0]["text_preview"]

    started = [e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_PARSE_STARTED]
    completed = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_PARSE_COMPLETED
    ]
    assert started and completed


def test_parse_service_failure_updates_job_and_document(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    parsed_root = tmp_path / "parsed"
    page_map_root = tmp_path / "page_maps"
    monkeypatch.setenv("STORAGE__PARSED_STORAGE_PATH", str(parsed_root))
    monkeypatch.setenv("STORAGE__PAGE_MAP_STORAGE_PATH", str(page_map_root))

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    processing_job_repo = ProcessingJobRepository(_ProcessingJobFakeMongo(), settings)
    document_id = "doc-fail-1"
    asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="Fail Test",
                file_name="original.pdf",
                raw_storage_path="/missing/path/",
                searchable=False,
            ),
            document_id=document_id,
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.PARSE_PENDING,
            approved_by="admin-1",
        )
    )
    job = asyncio.run(
        processing_job_repo.create(
            ProcessingJobCreate(
                document_id=document_id,
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.QUEUED,
                stage=ProcessingJobStage.PARSE_PENDING,
            ),
            job_id="job-fail-1",
        )
    )
    service = DocumentParseService(document_repo, processing_job_repo, settings=settings)
    with pytest.raises(DocumentParseError):
        asyncio.run(
            service.parse_document_for_processing_job(job.job_id, admin_user_id="admin-1")
        )
    failed_job = asyncio.run(processing_job_repo.get_by_id("job-fail-1"))
    assert failed_job is not None
    assert failed_job.status == ProcessingJobStatus.FAILED.value
    assert failed_job.stage == ProcessingJobStage.PARSE_FAILED.value
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.parse_status == ProcessingStageStatus.FAILED.value


def test_parse_modules_do_not_import_qdrant_or_llm():
    import app.services.document_parse_service as parse_service
    import app.services.pdf_parser_service as pdf_service

    for module in (parse_service, pdf_service):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "qdrant" not in source.lower()
        assert "gemini" not in source.lower()
        assert "openai" not in source.lower()
        assert "qdrant_client" not in source
        assert "EmbeddingSettings" not in source
