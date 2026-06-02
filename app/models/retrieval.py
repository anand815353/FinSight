from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.document import validate_mvp_document_type


class EvidencePackStatus(StrEnum):
    EVIDENCE_FOUND = "evidence_found"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RetrievalFilters(BaseModel):
    company_id: str
    document_ids: list[str] | None = None
    document_types: list[str] | None = None
    fiscal_year: int | None = None
    quarter: str | None = None
    period: str | None = None

    @field_validator("document_types", mode="before")
    @classmethod
    def _validate_document_types(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("document_types must be a list.")
        validated: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("document_types entries must be strings.")
            validated.append(validate_mvp_document_type(item).value)
        return validated


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    filters: RetrievalFilters
    top_k: int = Field(default=8, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class EvidenceItem(BaseModel):
    chunk_id: str
    document_id: str
    company_id: str
    document_type: str
    page_start: int
    page_end: int
    page_numbers: list[int] = Field(default_factory=list)
    section_title: str | None = None
    text_snippet: str
    text_preview: str
    source_document_title: str
    source_url: str | None = None
    file_hash: str | None = None
    score: float
    qdrant_point_id: str | None = None
    qdrant_collection: str | None = None
    embedding_model: str | None = None
    embedding_provider: str | None = None


class EvidencePack(BaseModel):
    query: str
    filters: RetrievalFilters
    status: EvidencePackStatus
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    missing_reason: str | None = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
