"""Shared helpers for admin route tests."""

from __future__ import annotations

import re
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.user import UserRole
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_validation_repo import DocumentValidationRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.auth_service import AuthService
from app.services.company_service import CompanyService
from app.services.document_chunk_service import DocumentChunkService
from app.services.document_service import DocumentService
from app.services.document_parse_service import DocumentParseService
from app.services.embedding_provider import get_embedding_provider
from app.services.processing_job_service import ProcessingJobService
from app.services.document_readiness_service import DocumentReadinessService
from app.services.qdrant_indexing_service import QdrantIndexingService
from app.services.retrieval_service import RetrievalService
from tests.test_qdrant_indexing_fakes import FakeQdrantClient, FakeQdrantManager
from tests.test_auth_routes import _FakeAuditRepo, _FakeUserRepo
from tests.test_company_repo import _FakeMongoManager as _CompanyFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_processing_job_repo_fakes import _FakeMongoManager as _ProcessingJobFakeMongo
from tests.test_document_validation_repo_fakes import _FakeMongoManager as _ValidationFakeMongo

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


@contextmanager
def admin_test_client(
    monkeypatch,
    *,
    upload_root: str | None = None,
    parsed_root: str | None = None,
    page_map_root: str | None = None,
    chunk_root: str | None = None,
):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    if upload_root:
        monkeypatch.setenv("STORAGE__ADMIN_PENDING_UPLOAD_PATH", upload_root)
    if parsed_root:
        monkeypatch.setenv("STORAGE__PARSED_STORAGE_PATH", parsed_root)
    if page_map_root:
        monkeypatch.setenv("STORAGE__PAGE_MAP_STORAGE_PATH", page_map_root)
    if chunk_root:
        monkeypatch.setenv("STORAGE__CHUNK_STORAGE_PATH", chunk_root)
    monkeypatch.setenv("EMBEDDING__PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING__COLLECTION_NAME", "finsight_chunks_hf_minilm_l6_v2")
    monkeypatch.setenv(
        "EMBEDDING__HF_MODEL_NAME",
        "sentence-transformers/all-MiniLM-L6-v2",
    )

    from app.main import app

    with TestClient(app) as client:
        settings = app.state.settings
        fake_user_repo = _FakeUserRepo()
        fake_audit_repo = _FakeAuditRepo()
        company_repo = CompanyRepository(_CompanyFakeMongo(), settings)
        document_repo = DocumentRepository(_DocumentFakeMongo(), settings)
        processing_job_repo = ProcessingJobRepository(_ProcessingJobFakeMongo(), settings)
        document_chunk_repo = DocumentChunkRepository(_ChunkFakeMongo(), settings)
        document_validation_repo = DocumentValidationRepository(_ValidationFakeMongo(), settings)
        auth_service = AuthService(fake_user_repo, fake_audit_repo, settings)
        company_service = CompanyService(company_repo)
        document_service = DocumentService(
            document_repo,
            company_repo=company_repo,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        processing_job_service = ProcessingJobService(
            processing_job_repo,
            document_repo,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        document_parse_service = DocumentParseService(
            document_repo,
            processing_job_repo,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        document_chunk_service = DocumentChunkService(
            document_repo,
            document_chunk_repo,
            processing_job_repo,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        fake_qdrant = FakeQdrantManager(FakeQdrantClient())
        embedding_provider = get_embedding_provider(settings)
        qdrant_indexing_service = QdrantIndexingService(
            document_repo,
            document_chunk_repo,
            processing_job_repo,
            fake_qdrant,
            embedding_provider,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        document_readiness_service = DocumentReadinessService(
            document_repo,
            document_chunk_repo,
            company_repo,
            document_validation_repo,
            audit_repo=fake_audit_repo,
            settings=settings,
        )
        retrieval_service = RetrievalService(
            fake_qdrant,
            embedding_provider,
            document_repo,
            document_chunk_repo,
            settings=settings,
        )

        app.state.user_repo = fake_user_repo
        app.state.audit_repo = fake_audit_repo
        app.state.auth_service = auth_service
        app.state.company_repo = company_repo
        app.state.document_repo = document_repo
        app.state.processing_job_repo = processing_job_repo
        app.state.document_chunk_repo = document_chunk_repo
        app.state.document_validation_repo = document_validation_repo
        app.state.company_service = company_service
        app.state.document_service = document_service
        app.state.processing_job_service = processing_job_service
        app.state.document_parse_service = document_parse_service
        app.state.document_chunk_service = document_chunk_service
        app.state.embedding_provider = embedding_provider
        app.state.qdrant_indexing_service = qdrant_indexing_service
        app.state.document_readiness_service = document_readiness_service
        app.state.retrieval_service = retrieval_service

        yield client, app, fake_user_repo, fake_audit_repo


async def seed_admin_user(fake_user_repo: _FakeUserRepo):
    return await fake_user_repo.create_user(
        email="admin@example.com",
        password_hash=hash_password("StrongPass123"),
        full_name="Admin User",
        role=UserRole.ADMIN,
    )


async def seed_free_user(fake_user_repo: _FakeUserRepo):
    return await fake_user_repo.create_user(
        email="user@example.com",
        password_hash=hash_password("StrongPass123"),
        full_name="Free User",
        role=UserRole.FREE_USER,
    )


def login(client: TestClient, app, email: str, password: str) -> None:
    client.get("/auth/login")
    csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
    response = client.post(
        "/auth/login",
        data={"email": email, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def get_csrf(client: TestClient, app) -> str | None:
    return client.cookies.get(app.state.settings.session.csrf_cookie_name)


def get_csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match is not None, "csrf_token hidden field not found in HTML"
    token = match.group(1)
    assert token, "csrf_token hidden field is empty in HTML"
    return token
