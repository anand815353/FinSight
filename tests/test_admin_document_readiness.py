import asyncio

import pytest

from app.models.audit_log import AuditEventType
from app.models.document import ProcessingStageStatus
from app.models.processing_job import ProcessingJobStage
from tests.test_admin_document_processing import (
    _approve_document,
    _register_pending_document,
)
from tests.fixtures.pdf_factory import two_page_pdf_bytes
from tests.test_admin_helpers import (
    admin_test_client,
    get_csrf_from_html,
    login,
    seed_admin_user,
)


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


def _register_with_source_type(client, app, *, pdf_bytes: bytes | None = None) -> str:
    from tests.test_admin_helpers import get_csrf

    client.get("/admin/documents/new")
    csrf = get_csrf(client, app)
    from tests.test_admin_helpers import MINIMAL_PDF

    content = pdf_bytes if pdf_bytes is not None else MINIMAL_PDF
    response = client.post(
        "/admin/documents",
        data={
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "title": "Annual Report FY2025",
            "period": "FY2025",
            "source_type": "admin_uploaded_official_source_document",
            "csrf_token": csrf,
        },
        files={"file": ("report.pdf", content, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    docs = asyncio.run(app.state.document_service.list_all_documents())
    return docs[0].document_id


def test_admin_e2e_validate_readiness_enables_searchable(admin_client):
    client, app, audit_repo, _upload, _parsed, _page_map, _chunk = admin_client
    document_id = _register_with_source_type(client, app, pdf_bytes=two_page_pdf_bytes())
    _approve_document(client, app, document_id)

    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]

    for path_suffix in ("parse", "chunk", "index"):
        detail = client.get(f"/admin/processing/jobs/{job_id}")
        csrf = get_csrf_from_html(detail.text)
        response = client.post(
            f"/admin/processing/jobs/{job_id}/{path_suffix}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303

    review = client.get(f"/admin/documents/{document_id}/review")
    assert "Validate readiness" in review.text
    csrf = get_csrf_from_html(review.text)
    validated = client.post(
        f"/admin/documents/{document_id}/validate-readiness",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert validated.status_code == 200

    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.searchable is True
    assert doc.index_status == ProcessingStageStatus.COMPLETED.value

    job = asyncio.run(app.state.processing_job_service.get_processing_job(job_id))
    assert job.stage == ProcessingJobStage.INDEX_COMPLETED.value

    enabled_events = [
        e for e in audit_repo.events
        if e["event_type"] == AuditEventType.ADMIN_DOCUMENT_SEARCHABILITY_ENABLED
    ]
    assert enabled_events


def test_admin_e2e_readiness_then_retrieval_test(admin_client):
    client, app, _audit_repo, _upload, _parsed, _page_map, _chunk = admin_client
    document_id = _register_with_source_type(client, app, pdf_bytes=two_page_pdf_bytes())
    _approve_document(client, app, document_id)

    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    queue = client.post(
        f"/admin/documents/{document_id}/process",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    job_id = queue.headers["location"].rsplit("/", 1)[-1]

    for path_suffix in ("parse", "chunk", "index"):
        detail = client.get(f"/admin/processing/jobs/{job_id}")
        csrf = get_csrf_from_html(detail.text)
        response = client.post(
            f"/admin/processing/jobs/{job_id}/{path_suffix}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303

    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    validated = client.post(
        f"/admin/documents/{document_id}/validate-readiness",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert validated.status_code == 200

    from tests.test_admin_helpers import get_csrf

    retrieval = client.post(
        "/admin/retrieval/test",
        data={
            "query": "Page one text",
            "company_id": "reliance_industries",
            "document_ids": document_id,
            "csrf_token": get_csrf(client, app),
        },
    )
    assert retrieval.status_code == 200
    payload = retrieval.json()
    assert payload["status"] == "evidence_found"
    assert payload["evidence_count"] >= 1


def test_validate_readiness_requires_csrf(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    _approve_document(client, app, document_id)
    response = client.post(f"/admin/documents/{document_id}/validate-readiness", data={})
    assert response.status_code == 403


def test_anonymous_cannot_validate_readiness(monkeypatch, tmp_path):
    with admin_test_client(
        monkeypatch,
        upload_root=str(tmp_path / "uploads"),
        parsed_root=str(tmp_path / "parsed"),
        page_map_root=str(tmp_path / "page_maps"),
        chunk_root=str(tmp_path / "chunks"),
    ) as (client, _app, *_rest):
        response = client.post("/admin/documents/doc-1/validate-readiness", data={})
        assert response.status_code in {401, 403, 422}


def test_failed_readiness_keeps_searchable_false(admin_client):
    client, app, *_rest = admin_client
    document_id = _register_pending_document(client, app)
    _approve_document(client, app, document_id)

    review = client.get(f"/admin/documents/{document_id}/review")
    csrf = get_csrf_from_html(review.text)
    response = client.post(
        f"/admin/documents/{document_id}/validate-readiness",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 200
    doc = asyncio.run(app.state.document_service.get_document(document_id))
    assert doc.searchable is False
