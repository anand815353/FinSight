from datetime import date
import hashlib

import asyncio
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType
from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import ApprovalStatus, DocumentCreate
from app.services.document_service import (
    DocumentRegistrationError,
    DocumentService,
    calculate_sha256_bytes,
    safe_storage_company_id,
)
from tests.test_admin_helpers import (
    MINIMAL_PDF,
    admin_test_client,
    get_csrf,
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


def test_anonymous_cannot_access_document_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (client, _app, *_rest):
        response = client.get("/admin/documents/new")
        assert response.status_code in {401, 403, 422}


def test_non_admin_cannot_access_document_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (
        client,
        app,
        user_repo,
        _audit,
    ):
        asyncio.run(seed_free_user(user_repo))
        login(client, app, "user@example.com", "StrongPass123")
        response = client.get("/admin/documents/new")
        assert response.status_code == 403


def test_admin_can_open_document_registration_form(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/documents/new")
    assert response.status_code == 200
    assert "Register Official Document" in response.text
    assert "does not download or crawl" in response.text
    assert 'name="csrf_token"' in response.text


def test_admin_can_register_valid_mvp_document(admin_client):
    client, app, audit_repo, upload_root = admin_client
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
            "fiscal_year": "2025",
            "period": "FY2025",
            "source_type": "admin_uploaded_official_source_document",
            "source_url": "https://example.com/ir/annual-report-2025.pdf",
            "notes": "Admin uploaded official copy",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/documents"

    list_response = client.get("/admin/documents")
    assert list_response.status_code == 200
    assert "Annual Report FY2025" in list_response.text
    assert "False" in list_response.text or "false" in list_response.text

    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1
    doc = docs[0]
    assert doc.searchable is False
    assert doc.approval_status == ApprovalStatus.PENDING
    assert doc.source_url == "https://example.com/ir/annual-report-2025.pdf"
    assert doc.notes == "Admin uploaded official copy"

    stored = Path(upload_root) / "reliance_industries" / doc.document_id / "original.pdf"
    assert stored.is_file()

    event_types = {event["event_type"] for event in audit_repo.events}
    assert AuditEventType.ADMIN_DOCUMENT_REGISTERED in event_types
    assert AuditEventType.ADMIN_DOCUMENT_UPLOADED in event_types
    assert doc.file_hash == calculate_sha256_bytes(MINIMAL_PDF)
    assert len(doc.file_hash) == 64


def _register_document_post(client, app, *, title: str = "Annual Report FY2025"):
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    return client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": title,
            "fiscal_year": "2025",
            "period": "FY2025",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )


def test_register_document_stores_sha256_hash(admin_client):
    client, app, *_rest = admin_client
    response = _register_document_post(client, app)
    assert response.status_code == 303
    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1
    expected = hashlib.sha256(MINIMAL_PDF).hexdigest()
    assert docs[0].file_hash == expected


def test_duplicate_upload_rejected(admin_client):
    client, app, audit_repo, _upload_root = admin_client
    first = _register_document_post(client, app, title="First upload")
    assert first.status_code == 303
    second = _register_document_post(client, app, title="Duplicate upload attempt")
    assert second.status_code == 409
    assert "already been registered" in second.text

    failed = [
        e
        for e in audit_repo.events
        if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_REGISTRATION_FAILED
    ]
    assert failed
    assert any(e["details"].get("reason") == "duplicate_file_hash" for e in failed)


def test_duplicate_does_not_create_second_document(admin_client):
    client, app, *_rest = admin_client
    assert _register_document_post(client, app).status_code == 303
    assert _register_document_post(client, app, title="Second").status_code == 409
    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1


def test_duplicate_upload_does_not_leave_extra_pending_dirs(admin_client):
    client, app, _audit_repo, upload_root = admin_client
    assert _register_document_post(client, app).status_code == 303
    assert _register_document_post(client, app, title="Duplicate").status_code == 409
    company_dirs = list((Path(upload_root) / "reliance_industries").iterdir())
    assert len(company_dirs) == 1


def test_different_pdf_bytes_produce_different_hashes():
    other_pdf = MINIMAL_PDF + b"\n% extra"
    assert calculate_sha256_bytes(MINIMAL_PDF) != calculate_sha256_bytes(other_pdf)


def test_register_document_with_valid_filing_date(admin_client):
    client, app, _audit_repo, _upload_root = admin_client
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2026",
            "filing_date": "2026-05-29",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/documents"

    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1
    doc = docs[0]
    assert doc.filing_date == date(2026, 5, 29)
    assert doc.searchable is False
    assert doc.approval_status == ApprovalStatus.PENDING


def test_register_document_rejects_invalid_filing_date(admin_client):
    client, app, _audit_repo, _upload_root = admin_client
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2026",
            "filing_date": "not-a-date",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 400
    assert "Invalid filing date" in response.text

    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 0


def test_unsupported_document_type_is_rejected(admin_client):
    client, app, audit_repo, _upload_root = admin_client
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "concall_transcript",
            "title": "Concall",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 422
    assert "excluded" in response.text.lower()
    failed = [
        event
        for event in audit_repo.events
        if event["event_type"] == AuditEventType.ADMIN_DOCUMENT_REGISTRATION_FAILED
    ]
    assert failed


def test_document_registration_requires_csrf(admin_client):
    client, _app, *_rest = admin_client
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
        },
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Session expired" in response.text
    assert "text/html" in response.headers.get("content-type", "")


def test_source_url_is_metadata_only_no_download(monkeypatch, tmp_path):
    """register_document_with_upload must not perform outbound HTTP for source_url."""
    from app.core.config import Settings

    settings = Settings()
    settings.storage.admin_pending_upload_path = str(tmp_path / "uploads")
    company_repo = __import__(
        "app.repositories.company_repo", fromlist=["CompanyRepository"]
    ).CompanyRepository(_CompanyFakeMongo(), settings)
    document_repo = __import__(
        "app.repositories.document_repo", fromlist=["DocumentRepository"]
    ).DocumentRepository(_DocumentFakeMongo(), settings)
    audit_repo = __import__(
        "tests.test_auth_routes", fromlist=["_FakeAuditRepo"]
    )._FakeAuditRepo()

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

    service = DocumentService(
        document_repo,
        company_repo=company_repo,
        audit_repo=audit_repo,
        settings=settings,
    )

    class _Upload:
        filename = "report.pdf"
        content_type = "application/pdf"

        async def read(self):
            return MINIMAL_PDF

    called = {"http": False}

    def _fail_http(*_args, **_kwargs):
        called["http"] = True
        raise AssertionError("HTTP client must not be called for source_url")

    monkeypatch.setattr("httpx.get", _fail_http, raising=False)
    monkeypatch.setattr("httpx.AsyncClient", _fail_http, raising=False)

    payload = DocumentCreate(
        company_id="reliance_industries",
        document_type="annual_report",
        title="Annual Report FY2025",
        source_url="https://example.com/ir/annual-report.pdf",
        searchable=False,
    )
    doc = asyncio.run(
        service.register_document_with_upload(
            payload=payload,
            upload=_Upload(),
            admin_user_id="admin-1",
        )
    )
    assert doc.source_url == "https://example.com/ir/annual-report.pdf"
    assert called["http"] is False


def test_safe_storage_company_id_rejects_path_traversal():
    with pytest.raises(DocumentRegistrationError, match="Invalid company_id"):
        safe_storage_company_id("../evil")
    with pytest.raises(DocumentRegistrationError, match="Invalid company_id"):
        safe_storage_company_id("reliance/industries")


def test_register_document_rejects_non_pdf(monkeypatch, tmp_path):
    from app.core.config import Settings

    settings = Settings()
    settings.storage.admin_pending_upload_path = str(tmp_path / "uploads")
    company_repo = __import__(
        "app.repositories.company_repo", fromlist=["CompanyRepository"]
    ).CompanyRepository(_CompanyFakeMongo(), settings)
    document_repo = __import__(
        "app.repositories.document_repo", fromlist=["DocumentRepository"]
    ).DocumentRepository(_DocumentFakeMongo(), settings)

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

    service = DocumentService(document_repo, company_repo=company_repo, settings=settings)

    class _Upload:
        filename = "notes.txt"
        content_type = "text/plain"

        async def read(self):
            return b"not a pdf"

    payload = DocumentCreate(
        company_id="reliance_industries",
        document_type="annual_report",
        title="Bad file",
        searchable=False,
    )
    with pytest.raises(DocumentRegistrationError, match="PDF"):
        asyncio.run(
            service.register_document_with_upload(payload=payload, upload=_Upload())
        )
