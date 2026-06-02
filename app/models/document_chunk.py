from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import ProcessingStageStatus


class ChunkType(StrEnum):
    TEXT = "text"


class DocumentChunk(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    chunk_id: str = Field(alias="_id")
    document_id: str
    company_id: str
    document_type: str
    fiscal_year: int | None = None
    quarter: str | None = None
    period: str | None = None
    page_start: int
    page_end: int
    page_numbers: list[int] = Field(default_factory=list)
    section_title: str | None = None
    chunk_index: int
    chunk_type: ChunkType = ChunkType.TEXT
    text: str
    text_preview: str
    char_count: int
    word_count: int
    source_document_title: str
    source_url: str | None = None
    file_hash: str | None = None
    qdrant_collection: str | None = None
    qdrant_point_id: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    index_status: ProcessingStageStatus | None = None
    indexed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentChunkCreate(BaseModel):
    chunk_id: str
    document_id: str
    company_id: str
    document_type: str
    fiscal_year: int | None = None
    quarter: str | None = None
    period: str | None = None
    page_start: int
    page_end: int
    page_numbers: list[int] = Field(default_factory=list)
    section_title: str | None = None
    chunk_index: int
    chunk_type: ChunkType = ChunkType.TEXT
    text: str
    text_preview: str
    char_count: int
    word_count: int
    source_document_title: str
    source_url: str | None = None
    file_hash: str | None = None
