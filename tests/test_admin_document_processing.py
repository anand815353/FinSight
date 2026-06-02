import asyncio
import json
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType
from app.models.document import ApprovalStatus, DocumentLifecycleStatus, ProcessingStageStatus
from app.models.processing_job import ProcessingJobStage, ProcessingJobStatus
from tests.fixtures.pdf_factory import two_page_pdf_bytes
from tests.test_admin_documents import MINIMAL_PDF
from app.services.processing_job_service import ProcessingJobError, ProcessingJobService
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
from tests.test_processing_job_repo_fakes import _FakeMongoManager as _ProcessingJobFakeMongo


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    upload_root = str(tmp_path / "uploads")
    parsed_root = str(tmp_path / "parsed")
    page_map_root = str(tmp_path / "page_maps")
    chunk_root = str(tmp_path / "chunks")
    with admin_test_client(
        monkeypatch,
        upload_root=upload_root,
        parsed_root=parsed_root,
        page_map_root=page_map_root,
        chunk_root=chunk_root,
    ) as (client, app, user_repo, audit_repo):
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
        yield client, app, audit_repo, upload_root, parsed_root, page_map_root, chunk_root


def _register_pending_document(client, app, *, pdf_bytes: bytes | None = None) -> str:
    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    content = pdf_bytes if pdf_bytes is not None else MINIMAL_PDF
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
            "period": "FY2025",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", content, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    docs = asyncio.run(app.state.document_service.list_all_documents())
    assert len(docs) == 1
    return docs[0].document_id


def _approve_document(client, app, document_id: str) -> None:
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/approve",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _register_and_approve(client, app) -> str:
    document_id = _register_pending_document(client, app)
    _approve_document(client, app, document_id)
    return document_id


def test_anonymous_cannot_access_processing_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (client, _app, *_rest):
        for path in (
            "/admin/processing/jobs",
            "/admin/documents/approved-for-processing",
            "/admin/processing/jobs/00000000-0000-0000-0000-000000000001",
        ):
            response = client.get(path)
            assert response.status_code in {401, 403, 422}


def test_non_admin_cannot_access_processing_routes(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (
        client,
        app,
        user_repo,
        _audit,
    ):
        asyncio.run(seed_free_user(user_repo))
        login(client, app, "user@example.com", "StrongPass123")
        response = client.get("/admin/processing/jobs")
        assert response.status_code == 403


def test_admin_can_list_processing_jobs(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/processing/jobs")
    assert response.status_code == 200
    assert "Processing Jobs" in response.text


def test_admin_can_view_eligible_documents(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    response = client.get("/admin/documents/approved-for-processing")
    assert response.status_code == 200
    assert "Documents Ready for Processing" in response.text
    assert document_id in response.text or "Annual Report FY2025" in response.text


def test_admin_can_request_processing(admin_client):
    client, app, audit_repo, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/processing/jobs/")

    job_id = response.headers["location"].rsplit("/", 1)[-1]
    job = asyncio.run(app.state.processing_job_service.get_processing_job(job_id))
    assert job.status == ProcessingJobStatus.QUEUED.value
    assert job.document_id == document_id

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.parse_status == ProcessingStageStatus.PENDING.value
    assert doc.lifecycle_status == DocumentLifecycleStatus.PARSE_PENDING.value

    requested = [
        e
        for e in audit_repo.events
        if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_PROCESSING_REQUESTED
    ]
    assert requested


def test_processing_does_not_set_searchable(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.searchable is False


def test_pending_document_cannot_be_processed(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
    )
    assert response.status_code in {400, 409}
    assert "approved" in response.text.lower()


def test_rejected_document_cannot_be_processed(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    client.post(
        f"/admin/documents/{document_id}/reject",
        data={"csrf_token": csrf, "rejection_reason": "Wrong period"},
        follow_redirects=False,
    )
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
    )
    assert response.status_code in {400, 409}


def test_document_without_file_hash_cannot_be_processed(monkeypatch, tmp_path):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.core.config import get_settings
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.processing_job_repo import ProcessingJobRepository

    get_settings.cache_clear()
    settings = get_settings()
    document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
    processing_job_repo = ProcessingJobRepository(_ProcessingJobFakeMongo(), settings)
    service = ProcessingJobService(processing_job_repo, document_repo)

    from datetime import UTC, datetime

    from app.models.document import DocumentCreate

    created = asyncio.run(
        document_repo.create(
            DocumentCreate(
                company_id="reliance_industries",
                document_type="annual_report",
                title="No hash doc",
                raw_storage_path="/tmp",
                searchable=False,
            )
        )
    )
    asyncio.run(
        document_repo.update_approval_review(
            created.document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.APPROVED,
            approved_by="admin-1",
            approved_at=datetime.now(UTC),
        )
    )
    with pytest.raises(ProcessingJobError) as exc_info:
        asyncio.run(
            service.request_document_processing(
                created.document_id,
                admin_user_id="admin-1",
            )
        )
    assert exc_info.value.status_code == 400
    assert "file hash" in exc_info.value.message.lower()


def test_duplicate_active_job_rejected(admin_client):
    client, app, audit_repo, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    first = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert first.status_code == 303

    csrf = get_csrf(client, app)
    second = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
    )
    assert second.status_code == 409
    assert "already active" in second.text.lower()

    jobs = asyncio.run(app.state.processing_job_service.list_processing_jobs(document_id=document_id))
    active = [j for j in jobs if j.status in {"queued", "running"}]
    assert len(active) == 1

    blocked = [
        e
        for e in audit_repo.events
        if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_PROCESSING_DUPLICATE_BLOCKED
    ]
    assert blocked


def test_process_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    response = client.post(f"/admin/documents/{document_id}/process", data={})
    assert response.status_code == 403


def test_documents_new_route_still_works(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/documents/new")
    assert response.status_code == 200
    assert "Register Official Document" in response.text


def test_processing_hub_redirects(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/processing", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/processing/jobs"


def test_admin_e2e_queue_then_parse(admin_client):
    client, app, audit_repo, _upload_root, parsed_root, page_map_root, _chunk_root = admin_client
    document_id = _register_pending_document(client, app, pdf_bytes=two_page_pdf_bytes())
    _approve_document(client, app, document_id)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert queue.status_code == 303
    job_id = queue.headers["location"].rsplit("/", 1)[-1]

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    assert detail.status_code == 200
    assert "Run parse" in detail.text
    csrf = get_csrf_from_html(detail.text)
    parsed = client.post(
        f"/admin/processing/jobs/{job_id}/parse",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert parsed.status_code == 303

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.parse_status == ProcessingStageStatus.COMPLETED.value
    assert doc.page_count == 2
    assert doc.searchable is False

    job = asyncio.run(app.state.processing_job_service.get_processing_job(job_id))
    assert job.stage == ProcessingJobStage.PARSE_COMPLETED.value

    pages_path = Path(parsed_root) / document_id / "pages.json"
    assert pages_path.is_file()
    assert json.loads(pages_path.read_text(encoding="utf-8"))["page_count"] == 2
    assert (Path(page_map_root) / document_id / "page_map.json").is_file()

    completed = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_PARSE_COMPLETED
    ]
    assert completed


def test_parse_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]
    response = client.post(f"/admin/processing/jobs/{job_id}/parse", data={})
    assert response.status_code == 403


def test_anonymous_cannot_parse_job(monkeypatch, tmp_path):
    upload_root = str(tmp_path / "uploads")
    with admin_test_client(
        monkeypatch,
        upload_root=upload_root,
        parsed_root=str(tmp_path / "parsed"),
        page_map_root=str(tmp_path / "page_maps"),
        chunk_root=str(tmp_path / "chunks"),
    ) as (client, _app, *_rest):
        response = client.post("/admin/processing/jobs/job-1/parse", data={})
        assert response.status_code in {401, 403, 422}


def test_admin_e2e_queue_parse_then_chunk(admin_client):
    client, app, audit_repo, _upload_root, parsed_root, page_map_root, chunk_root = admin_client
    document_id = _register_pending_document(client, app, pdf_bytes=two_page_pdf_bytes())
    _approve_document(client, app, document_id)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    csrf = get_csrf_from_html(detail.text)
    client.post(
        f"/admin/processing/jobs/{job_id}/parse",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    assert "Run chunking" in detail.text
    csrf = get_csrf_from_html(detail.text)
    chunked = client.post(
        f"/admin/processing/jobs/{job_id}/chunk",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert chunked.status_code == 303

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.chunk_status == ProcessingStageStatus.COMPLETED.value
    assert doc.chunk_count == 2
    assert doc.searchable is False

    job = asyncio.run(app.state.processing_job_service.get_processing_job(job_id))
    assert job.stage == ProcessingJobStage.INDEX_PENDING.value

    chunk_repo = app.state.document_chunk_repo
    stored = asyncio.run(chunk_repo.list_by_document_id(document_id))
    assert len(stored) == 2
    assert (Path(chunk_root) / document_id / "chunks.json").is_file()

    chunk_events = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_CHUNKING_COMPLETED
    ]
    assert chunk_events


def test_admin_e2e_queue_parse_chunk_then_index(admin_client):
    client, app, audit_repo, _upload_root, parsed_root, page_map_root, chunk_root = admin_client
    document_id = _register_pending_document(client, app, pdf_bytes=two_page_pdf_bytes())
    _approve_document(client, app, document_id)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    csrf = get_csrf_from_html(detail.text)
    client.post(
        f"/admin/processing/jobs/{job_id}/parse",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    csrf = get_csrf_from_html(detail.text)
    client.post(
        f"/admin/processing/jobs/{job_id}/chunk",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    detail = client.get(f"/admin/processing/jobs/{job_id}")
    assert "Run vector indexing" in detail.text
    csrf = get_csrf_from_html(detail.text)
    indexed = client.post(
        f"/admin/processing/jobs/{job_id}/index",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert indexed.status_code == 303

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.index_status == ProcessingStageStatus.COMPLETED.value
    assert doc.indexed_chunk_count == 2
    assert doc.searchable is False

    job = asyncio.run(app.state.processing_job_service.get_processing_job(job_id))
    assert job.stage == ProcessingJobStage.INDEX_COMPLETED.value

    index_events = [
        e for e in audit_repo.events if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_INDEXING_COMPLETED
    ]
    assert index_events


def test_chunk_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_and_approve(client, app)
    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]
    detail = client.get(f"/admin/processing/jobs/{job_id}")
    csrf = get_csrf_from_html(detail.text)
    client.post(f"/admin/processing/jobs/{job_id}/parse", data={"csrf_token": csrf}, follow_redirects=False)
    response = client.post(f"/admin/processing/jobs/{job_id}/chunk", data={})
    assert response.status_code == 403


def test_anonymous_cannot_chunk_job(monkeypatch, tmp_path):
    with admin_test_client(
        monkeypatch,
        upload_root=str(tmp_path / "uploads"),
        parsed_root=str(tmp_path / "parsed"),
        page_map_root=str(tmp_path / "page_maps"),
        chunk_root=str(tmp_path / "chunks"),
    ) as (client, _app, *_rest):
        response = client.post("/admin/processing/jobs/job-1/chunk", data={})
        assert response.status_code in {401, 403, 422}
