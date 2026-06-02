import asyncio
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType
from app.models.document import ApprovalStatus, ProcessingStageStatus
from app.services.document_service import DocumentApprovalError, DocumentService
from tests.test_admin_documents import MINIMAL_PDF
from tests.test_admin_helpers import (
    admin_test_client,
    get_csrf,
    get_csrf_from_html,
    login,
    seed_admin_user,
    seed_free_user,
)
from tests.test_company_repo import _FakeMongoManager as _CompanyFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    upload_root = str(tmp_path / "uploads")
    with admin_test_client(monkeypatch, upload_root=upload_root) as (client, app, user_repo, audit_repo):
        asyncio.run(seed_admin_user(user_repo))
        login(client, app, "admin@example.com", "StrongPass123")
        from app.models.company import CompanyCreate, CompanyStatus

        company_repo = app.state.company_repo
        asyncio.run(
            company_repo.create(
                CompanyCreate(
                    company_id="reliance_industries",
                    name="Reliance Industries Ltd",
                    display_name="Reliance Industries",
                    nse_symbol="RELIANCE",
                    status=CompanyStatus.ACTIVE,
                )
            )
        )
        yield client, app, audit_repo, upload_root


def _register_pending_document(client, app) -> str:
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
            "period": "FY2025",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1
    return docs[0].document_id


def test_anonymous_cannot_access_approval_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (client, _app, *_rest):
        for path in (
            "/admin/documents/pending",
            "/admin/documents/00000000-0000-0000-0000-000000000001/review",
        ):
            response = client.get(path)
            assert response.status_code in {401, 403, 422}


def test_non_admin_cannot_access_approval_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (
        client,
        app,
        user_repo,
        _audit,
    ):
        asyncio.run(seed_free_user(user_repo))
        login(client, app, "user@example.com", "StrongPass123")
        response = client.get("/admin/documents/pending")
        assert response.status_code == 403


def test_admin_can_list_pending_documents(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    response = client.get("/admin/documents/pending")
    assert response.status_code == 200
    assert "Documents Pending Approval" in response.text
    assert document_id in response.text or "Annual Report FY2025" in response.text


def test_admin_can_open_review_page(admin_client):
    client, app, audit_repo, _upload_root = admin_client
    document_id = _register_pending_document(client, app)
    response = client.get(f"/admin/documents/{document_id}/review")
    assert response.status_code == 200
    assert "Document Review" in response.text
    assert "Approve for later processing" in response.text
    assert "does not parse" in response.text.lower() or "does not parse, chunk" in response.text

    opened = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_REVIEW_OPENED
    ]
    assert opened


def test_admin_can_approve_pending_document(admin_client):
    client, app, audit_repo, upload_root = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/approve",
        data={"csrf_token": csrf, "review_notes": "Looks correct"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/documents/pending"

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.approval_status == ApprovalStatus.APPROVED.value
    assert doc.approved_by is not None
    assert doc.approved_at is not None
    assert doc.searchable is False
    assert doc.review_notes == "Looks correct"
    assert doc.parse_status == ProcessingStageStatus.NOT_STARTED.value
    assert doc.index_status == ProcessingStageStatus.NOT_STARTED.value

    approved_events = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_APPROVED
    ]
    assert approved_events

    stored = Path(upload_root) / "reliance_industries" / document_id / "original.pdf"
    assert stored.is_file()


def test_approve_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    response = client.post(
        f"/admin/documents/{document_id}/approve",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Session expired" in response.text


def test_admin_can_reject_pending_document_with_reason(admin_client):
    client, app, audit_repo, _upload_root = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/reject",
        data={"csrf_token": csrf, "rejection_reason": "Wrong filing period"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.approval_status == ApprovalStatus.REJECTED.value
    assert doc.rejected_by is not None
    assert doc.rejection_reason == "Wrong filing period"
    assert doc.searchable is False

    rejected_events = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_REJECTED
    ]
    assert rejected_events


def test_reject_without_reason_fails(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/reject",
        data={"csrf_token": csrf, "rejection_reason": "   "},
    )
    assert response.status_code == 400
    assert "Rejection reason is required" in response.text

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.approval_status == ApprovalStatus.PENDING.value


def test_reject_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    response = client.post(
        f"/admin/documents/{document_id}/reject",
        data={"rejection_reason": "Bad metadata"},
    )
    assert response.status_code == 403


def test_cannot_approve_rejected_without_mark_pending(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    client.post(
        f"/admin/documents/{document_id}/reject",
        data={"csrf_token": csrf, "rejection_reason": "Needs correction"},
        follow_redirects=False,
    )
    review2 = client.get(f"/admin/documents/{document_id}/review")
    csrf2 = get_csrf_from_html(review2.text)
    response = client.post(
        f"/admin/documents/{document_id}/approve",
        data={"csrf_token": csrf2},
    )
    assert response.status_code == 409
    assert "Mark it pending first" in response.text


def test_mark_pending_then_approve(admin_client):
    client, app, audit_repo, _upload_root = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    client.post(
        f"/admin/documents/{document_id}/reject",
        data={"csrf_token": csrf, "rejection_reason": "Fix period label"},
        follow_redirects=False,
    )
    review2 = client.get(f"/admin/documents/{document_id}/review")
    csrf2 = get_csrf_from_html(review2.text)
    reopen = client.post(
        f"/admin/documents/{document_id}/mark-pending",
        data={"csrf_token": csrf2},
        follow_redirects=False,
    )
    assert reopen.status_code == 303

    review3 = client.get(f"/admin/documents/{document_id}/review")
    csrf3 = get_csrf_from_html(review3.text)
    approve = client.post(
        f"/admin/documents/{document_id}/approve",
        data={"csrf_token": csrf3},
        follow_redirects=False,
    )
    assert approve.status_code == 303
    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.approval_status == ApprovalStatus.APPROVED.value
    assert doc.searchable is False

    marked = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_MARKED_PENDING
    ]
    assert marked


def test_unsupported_document_type_cannot_be_approved(monkeypatch, tmp_path):
    from app.core.config import Settings
    from app.models.document import DocumentCreate
    from app.repositories.document_repo import DocumentRepository
    from app.services import document_service as document_service_module

    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / "original.pdf").write_bytes(MINIMAL_PDF)
    monkeypatch.setenv("STORAGE__ADMIN_PENDING_UPLOAD_PATH", str(upload_root))
    settings = Settings()
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    service = DocumentService(document_repo, settings=settings)
    doc = asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="FY2025 Annual Report",
                file_name="original.pdf",
                mime_type="application/pdf",
                file_size_bytes=len(MINIMAL_PDF),
                raw_storage_path=f"{upload_root.as_posix()}/",
            )
        )
    )

    def _reject_type(_value):
        raise ValueError("Document type 'concall_transcript' is excluded from MVP scope.")

    monkeypatch.setattr(document_service_module, "validate_mvp_document_type", _reject_type)
    with pytest.raises(DocumentApprovalError, match="excluded"):
        asyncio.run(service.approve_document(doc.document_id, admin_user_id="admin-user"))


def test_documents_new_route_still_works(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/documents/new")
    assert response.status_code == 200
    assert "Register Official Document" in response.text
