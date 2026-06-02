import asyncio

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.document import (
    ApprovalStatus,
    DocumentCreate,
    DocumentLifecycleStatus,
    DocumentType,
    ProcessingStageStatus,
    compute_searchable,
    validate_mvp_document_type,
    validate_source_type,
)
from app.repositories.document_repo import DocumentRepository
from app.models.audit_log import AuditEventType
from app.services.document_service import (
    DocumentApprovalError,
    DocumentService,
    calculate_sha256_bytes,
)
from tests.test_auth_routes import _FakeAuditRepo
from tests.test_document_repo_fakes import _FakeMongoManager


def test_calculate_sha256_bytes_is_deterministic():
    content = b"%PDF-1.4 sample"
    assert calculate_sha256_bytes(content) == calculate_sha256_bytes(content)
    assert len(calculate_sha256_bytes(content)) == 64


def test_get_by_file_hash_returns_document(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = DocumentRepository(_FakeMongoManager(), Settings())
    expected_hash = calculate_sha256_bytes(b"%PDF-1.4\n%%EOF")
    asyncio.run(
        repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="FY2025 Annual Report",
                file_hash=expected_hash,
            )
        )
    )
    found = asyncio.run(repo.get_by_file_hash(expected_hash))
    assert found is not None
    assert found.file_hash == expected_hash


def test_validate_mvp_document_type_allowed():
    assert validate_mvp_document_type("annual_report") == DocumentType.ANNUAL_REPORT


def test_validate_mvp_document_type_excluded():
    with pytest.raises(ValueError, match="excluded"):
        validate_mvp_document_type("concall_transcript")
    with pytest.raises(ValueError, match="excluded"):
        validate_mvp_document_type("drhp")


def test_validate_mvp_document_type_unsupported():
    with pytest.raises(ValueError, match="not supported"):
        validate_mvp_document_type("corporate_announcement")


def test_validate_source_type_allowed():
    assert validate_source_type("admin_uploaded_official_source_document") == (
        "admin_uploaded_official_source_document"
    )
    assert validate_source_type(None) is None


def test_validate_source_type_rejects_arbitrary_string():
    with pytest.raises(ValueError, match="not allowed"):
        validate_source_type("random_source")


def test_document_create_rejects_invalid_source_type():
    with pytest.raises(ValidationError, match="not allowed"):
        DocumentCreate(
            company_id="reliance_industries",
            document_type="annual_report",
            title="Annual Report FY2025",
            source_type="unsupported_source",
        )


def test_readiness_pass_sets_lifecycle_ready(monkeypatch, tmp_path):
    from tests.test_document_readiness_service import _seed_ready_document

    service, document_repo, document_id, _audit = asyncio.run(_seed_ready_document(tmp_path))
    asyncio.run(service.validate_document_readiness(document_id, admin_user_id="admin-1"))
    doc = asyncio.run(document_repo.get_by_id(document_id))
    assert doc.lifecycle_status == DocumentLifecycleStatus.READY.value
    assert doc.searchable is True


def test_compute_searchable_rules():
    assert (
        compute_searchable(
            searchable_flag=False,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
        is False
    )
    assert (
        compute_searchable(
            searchable_flag=True,
            approval_status=ApprovalStatus.PENDING,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
        is False
    )
    assert (
        compute_searchable(
            searchable_flag=True,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
        is True
    )


def test_document_create_defaults_searchable_false(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = DocumentRepository(_FakeMongoManager(), Settings())
    service = DocumentService(repo)
    doc = asyncio.run(
        service.create_document(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="Annual Report FY2025",
                period="FY2025",
                searchable=True,
            )
        )
    )
    assert doc.searchable is False
    assert doc.approval_status == ApprovalStatus.PENDING.value


def test_document_create_rejects_excluded_type():
    with pytest.raises(ValidationError, match="excluded"):
        DocumentCreate(
            company_id="itc",
            document_type="earnings_call_transcript",
            title="Q1 Call",
        )


def test_apply_searchable_update_enforces_rules(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = DocumentRepository(_FakeMongoManager(), Settings())
    service = DocumentService(repo)
    doc = asyncio.run(
        service.create_document(
            DocumentCreate(
                company_id="infosys",
                document_type="investor_presentation",
                title="Investor Presentation Q4",
            )
        )
    )
    updated = asyncio.run(
        service.apply_searchable_update(
            doc,
            requested_searchable=True,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
    )
    assert updated.searchable is True

    still_pending = asyncio.run(
        service.apply_searchable_update(
            updated,
            requested_searchable=True,
            approval_status=ApprovalStatus.PENDING,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
    )
    assert still_pending.searchable is False


def test_repo_rejects_invalid_searchable_true(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = DocumentRepository(_FakeMongoManager(), Settings())
    doc = asyncio.run(
        repo.create(
            DocumentCreate(
                company_id="itc",
                document_type="financial_result",
                title="Q1 Results",
            )
        )
    )
    with pytest.raises(ValueError, match="searchability requirements"):
        asyncio.run(
            repo.update_searchable(
                doc.document_id,
                searchable=True,
                approval_status=ApprovalStatus.PENDING,
                lifecycle_status=DocumentLifecycleStatus.READY,
                index_status=ProcessingStageStatus.COMPLETED,
            )
        )


def test_approve_document_keeps_searchable_false(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True)
    (upload_dir / "original.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setenv("STORAGE__ADMIN_PENDING_UPLOAD_PATH", str(upload_dir))
    settings = Settings()
    repo = DocumentRepository(_FakeMongoManager(), settings)
    audit_repo = _FakeAuditRepo()
    service = DocumentService(repo, audit_repo=audit_repo, settings=settings)
    doc = asyncio.run(
        repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="FY2025 Annual Report",
                file_name="original.pdf",
                mime_type="application/pdf",
                file_size_bytes=100,
                raw_storage_path=f"{upload_dir.as_posix()}/",
            )
        )
    )
    approved = asyncio.run(
        service.approve_document(doc.document_id, admin_user_id="admin-1")
    )
    assert approved.approval_status == ApprovalStatus.APPROVED.value
    assert approved.searchable is False
    assert approved.lifecycle_status == DocumentLifecycleStatus.APPROVED.value
    event_types = {e["event_type"] for e in audit_repo.events}
    assert AuditEventType.ADMIN_DOCUMENT_APPROVED in event_types


def test_reject_document_requires_reason(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = DocumentRepository(_FakeMongoManager(), Settings())
    service = DocumentService(repo)
    doc = asyncio.run(
        repo.create(
            DocumentCreate(
                company_id="hdfc_bank",
                document_type="financial_result",
                title="Q1 Results",
            )
        )
    )
    with pytest.raises(DocumentApprovalError, match="Rejection reason is required"):
        asyncio.run(
            service.reject_document(doc.document_id, admin_user_id="admin-1", rejection_reason="")
        )


def test_list_by_company_includes_all_metadata(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _FakeMongoManager()
    repo = DocumentRepository(mongo, Settings())
    asyncio.run(
        repo.create(
            DocumentCreate(
                company_id="hdfc_bank",
                document_type="annual_report",
                title="FY2025 Annual Report",
            )
        )
    )
    doc_id = next(iter(mongo._client.documents.items.keys()))
    mongo._client.documents.items[doc_id]["searchable"] = True
    listed = asyncio.run(repo.list_by_company("hdfc_bank"))
    assert len(listed) == 1
