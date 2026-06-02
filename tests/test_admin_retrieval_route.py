from __future__ import annotations

import asyncio

from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import DocumentCreate, ProcessingStageStatus
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.services.qdrant_indexing_service import QdrantIndexingService
from tests.test_admin_helpers import (
    admin_test_client,
    get_csrf,
    login,
    seed_admin_user,
    seed_free_user,
)
from tests.test_company_repo import _FakeMongoManager as _CompanyFakeMongo
from tests.test_document_chunk_repo_fakes import _FakeMongoManager as _ChunkFakeMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentFakeMongo


async def _seed_company_and_searchable_doc(app):
    settings = app.state.settings
    company_repo = app.state.company_repo
    document_repo = app.state.document_repo
    chunk_repo = app.state.document_chunk_repo
    fake_qdrant = app.state.retrieval_service._qdrant_manager.client

    await company_repo.create(
        CompanyCreate(
            company_id="reliance_industries",
            name="Reliance Industries Ltd",
            display_name="Reliance Industries",
            nse_symbol="RELIANCE",
            status=CompanyStatus.ACTIVE,
        )
    )

    document_id = "doc-admin-retrieval"
    await document_repo.create(
        DocumentCreate(
            company_id="reliance_industries",
            document_type="annual_report",
            title="Annual Report FY2025",
            source_type="admin_uploaded_official_source_document",
            file_hash="hash-admin-retrieval",
            searchable=False,
        ),
        document_id=document_id,
    )
    await document_repo._collection.update_one(
        {"_id": document_id},
        {"$set": {"searchable": True}},
    )

    chunk_id = f"{document_id}_chunk_0000"
    text = "Operating revenue details."
    await chunk_repo.create_many(
        [
            DocumentChunkCreate(
                chunk_id=chunk_id,
                document_id=document_id,
                company_id="reliance_industries",
                document_type="annual_report",
                page_start=1,
                page_end=1,
                page_numbers=[1],
                chunk_index=0,
                chunk_type=ChunkType.TEXT,
                text=text,
                text_preview=text,
                char_count=len(text),
                word_count=len(text.split()),
                source_document_title="Annual Report FY2025",
                file_hash="hash-admin-retrieval",
            )
        ]
    )
    await chunk_repo.update_indexing_metadata(
        [
            {
                "chunk_id": chunk_id,
                "qdrant_collection": settings.embedding.collection_name,
                "qdrant_point_id": QdrantIndexingService.point_id_for_chunk(chunk_id),
                "embedding_provider": "fake",
                "embedding_model": "test-model",
                "index_status": ProcessingStageStatus.COMPLETED.value,
            }
        ]
    )
    point_id = QdrantIndexingService.point_id_for_chunk(chunk_id)
    fake_qdrant.points[point_id] = {
        "collection": settings.embedding.collection_name,
        "vector": [0.1] * settings.embedding.vector_size,
        "payload": {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "company_id": "reliance_industries",
            "document_type": "annual_report",
            "text_preview": text,
            "source_document_title": "Annual Report FY2025",
        },
    }
    return document_id


def test_admin_retrieval_test_requires_auth(monkeypatch):
    with admin_test_client(monkeypatch) as (client, app, *_):
        response = client.post(
            "/admin/retrieval/test",
            data={"query": "revenue", "company_id": "reliance_industries"},
        )
        assert response.status_code in {401, 403, 302}


def test_admin_retrieval_test_csrf_and_success(monkeypatch):
    with admin_test_client(monkeypatch) as (client, app, fake_user_repo, _audit):
        asyncio.run(seed_admin_user(fake_user_repo))
        login(client, app, "admin@example.com", "StrongPass123")
        document_id = asyncio.run(_seed_company_and_searchable_doc(app))

        response = client.post(
            "/admin/retrieval/test",
            data={
                "query": "revenue",
                "company_id": "reliance_industries",
                "document_ids": document_id,
                "csrf_token": get_csrf(client, app),
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "evidence_found"
        assert payload["evidence_count"] >= 1
        assert payload["first_item"]["chunk_id"]

        bad_csrf = client.post(
            "/admin/retrieval/test",
            data={
                "query": "revenue",
                "company_id": "reliance_industries",
                "csrf_token": "invalid-token",
            },
        )
        assert bad_csrf.status_code == 403


def test_admin_retrieval_test_rejects_non_admin(monkeypatch):
    with admin_test_client(monkeypatch) as (client, app, fake_user_repo, _audit):
        asyncio.run(seed_free_user(fake_user_repo))
        login(client, app, "user@example.com", "StrongPass123")

        response = client.post(
            "/admin/retrieval/test",
            data={
                "query": "revenue",
                "company_id": "reliance_industries",
                "csrf_token": get_csrf(client, app),
            },
        )
        assert response.status_code == 403


def test_admin_retrieval_rejects_invalid_document_type(monkeypatch):
    with admin_test_client(monkeypatch) as (client, app, fake_user_repo, _audit):
        asyncio.run(seed_admin_user(fake_user_repo))
        login(client, app, "admin@example.com", "StrongPass123")

        response = client.post(
            "/admin/retrieval/test",
            data={
                "query": "revenue",
                "company_id": "reliance_industries",
                "document_types": "concall_transcript",
                "csrf_token": get_csrf(client, app),
            },
        )
        assert response.status_code == 400
        payload = response.json()
        assert "error" in payload
        assert "concall" in payload["error"].lower() or "excluded" in payload["error"].lower()
