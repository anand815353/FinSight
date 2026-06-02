import asyncio
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.repositories.document_chunk_repo import DocumentChunkRepository
from tests.test_document_chunk_repo_fakes import _FakeMongoManager


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    return DocumentChunkRepository(_FakeMongoManager(), Settings())


def _sample_chunk(chunk_id: str, *, document_id: str = "doc-1", chunk_index: int = 0):
    return DocumentChunkCreate(
        chunk_id=chunk_id,
        document_id=document_id,
        company_id="reliance_industries",
        document_type="annual_report",
        page_start=1,
        page_end=1,
        page_numbers=[1],
        chunk_index=chunk_index,
        text="Sample chunk text for tests.",
        text_preview="Sample chunk text",
        char_count=28,
        word_count=5,
        source_document_title="Annual Report",
        chunk_type=ChunkType.TEXT,
    )


def test_create_many_and_list(repo):
    asyncio.run(repo.create_many([_sample_chunk("chunk-0"), _sample_chunk("chunk-1", chunk_index=1)]))
    listed = asyncio.run(repo.list_by_document_id("doc-1"))
    assert len(listed) == 2
    assert listed[0].chunk_index == 0
    assert listed[1].chunk_index == 1


def test_update_indexing_metadata(repo):
    asyncio.run(repo.create_many([_sample_chunk("chunk-0")]))
    indexed_at = datetime.now(UTC)
    asyncio.run(
        repo.update_indexing_metadata(
            [
                {
                    "chunk_id": "chunk-0",
                    "qdrant_collection": "finsight_chunks_hf_minilm_l6_v2",
                    "qdrant_point_id": "point-uuid-1",
                    "embedding_provider": "fake",
                    "embedding_model": "test-model",
                    "index_status": "completed",
                    "indexed_at": indexed_at,
                }
            ]
        )
    )
    listed = asyncio.run(repo.list_by_document_id("doc-1"))
    assert listed[0].qdrant_point_id == "point-uuid-1"
    assert listed[0].embedding_provider == "fake"


def test_delete_by_document_id(repo):
    asyncio.run(repo.create_many([_sample_chunk("chunk-0"), _sample_chunk("chunk-1", chunk_index=1)]))
    deleted = asyncio.run(repo.delete_by_document_id("doc-1"))
    assert deleted == 2
    assert asyncio.run(repo.count_by_document_id("doc-1")) == 0
