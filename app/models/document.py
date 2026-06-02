from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.source_registry import SourceType

MVP_DOCUMENT_TYPES = frozenset(
    {
        "annual_report",
        "financial_result",
        "investor_presentation",
    }
)

EXCLUDED_DOCUMENT_TYPES = frozenset(
    {
        "concall_transcript",
        "drhp",
        "earnings_call_transcript",
        "brsr",
        "pillar_3",
        "sec_20f",
    }
)


class DocumentType(StrEnum):
    ANNUAL_REPORT = "annual_report"
    FINANCIAL_RESULT = "financial_result"
    INVESTOR_PRESENTATION = "investor_presentation"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProcessingStageStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentReadinessStatus(StrEnum):
    NOT_VALIDATED = "not_validated"
    PASSED = "passed"
    FAILED = "failed"


class DocumentLifecycleStatus(StrEnum):
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADED = "downloaded"
    DOWNLOAD_FAILED = "download_failed"
    PARSE_PENDING = "parse_pending"
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"
    CHUNK_PENDING = "chunk_pending"
    CHUNKED = "chunked"
    CHUNK_FAILED = "chunk_failed"
    INDEX_PENDING = "index_pending"
    INDEXED = "indexed"
    INDEX_FAILED = "index_failed"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    READY = "ready"
    ARCHIVED = "archived"


def compute_searchable(
    *,
    searchable_flag: bool,
    approval_status: ApprovalStatus,
    lifecycle_status: DocumentLifecycleStatus,
    index_status: ProcessingStageStatus,
) -> bool:
    if not searchable_flag:
        return False
    if approval_status != ApprovalStatus.APPROVED:
        return False
    if lifecycle_status != DocumentLifecycleStatus.READY:
        return False
    if index_status != ProcessingStageStatus.COMPLETED:
        return False
    return True


def validate_mvp_document_type(document_type: str) -> DocumentType:
    normalized = document_type.strip().lower()
    if normalized in EXCLUDED_DOCUMENT_TYPES:
        raise ValueError(f"Document type '{document_type}' is excluded from MVP scope.")
    if normalized not in MVP_DOCUMENT_TYPES:
        raise ValueError(
            f"Document type '{document_type}' is not supported. "
            f"Allowed types: {', '.join(sorted(MVP_DOCUMENT_TYPES))}."
        )
    return DocumentType(normalized)


def validate_source_type(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, SourceType):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        allowed = {member.value for member in SourceType}
        if normalized in allowed:
            return normalized
        raise ValueError(
            f"Source type '{value}' is not allowed. "
            f"Allowed types: {', '.join(sorted(allowed))}."
        )
    raise ValueError("source_type must be a string or SourceType.")


class Document(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    document_id: str = Field(alias="_id")
    company_id: str
    document_type: DocumentType
    title: str
    normalized_title: str
    period: str | None = None
    fiscal_year: int | None = None
    quarter: str | None = None
    filing_date: date | None = None
    source_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    file_name: str | None = None
    file_hash: str | None = None
    file_size_bytes: int | None = None
    mime_type: str | None = None
    page_count: int | None = None
    version: int = 1
    is_latest: bool = True
    document_group_id: str | None = None
    raw_storage_path: str | None = None
    parsed_pages_path: str | None = None
    page_map_path: str | None = None
    parsed_at: datetime | None = None
    parse_status: ProcessingStageStatus = ProcessingStageStatus.NOT_STARTED
    chunk_status: ProcessingStageStatus = ProcessingStageStatus.NOT_STARTED
    chunk_count: int | None = None
    chunk_artifact_path: str | None = None
    chunked_at: datetime | None = None
    index_status: ProcessingStageStatus = ProcessingStageStatus.NOT_STARTED
    indexed_chunk_count: int | None = None
    indexed_at: datetime | None = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    review_notes: str | None = None
    lifecycle_status: DocumentLifecycleStatus = DocumentLifecycleStatus.REGISTERED
    searchable: bool = False
    readiness_status: DocumentReadinessStatus = DocumentReadinessStatus.NOT_VALIDATED
    readiness_validated_at: datetime | None = None
    readiness_validation_id: str | None = None
    searchable_at: datetime | None = None
    searchable_by: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("document_type", mode="before")
    @classmethod
    def _validate_document_type(cls, value: object) -> DocumentType:
        if isinstance(value, DocumentType):
            return value
        if isinstance(value, str):
            return validate_mvp_document_type(value)
        raise ValueError("document_type must be a string or DocumentType.")

    @field_validator("source_type", mode="before")
    @classmethod
    def _validate_source_type(cls, value: object) -> str | None:
        return validate_source_type(value)


class DocumentCreate(BaseModel):
    company_id: str
    document_type: str
    title: str
    period: str | None = None
    fiscal_year: int | None = None
    quarter: str | None = None
    filing_date: date | None = None
    source_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    file_name: str | None = None
    file_hash: str | None = None
    file_size_bytes: int | None = None
    mime_type: str | None = None
    page_count: int | None = None
    version: int = 1
    is_latest: bool = True
    document_group_id: str | None = None
    raw_storage_path: str | None = None
    lifecycle_status: DocumentLifecycleStatus = DocumentLifecycleStatus.REGISTERED
    searchable: bool = False
    notes: str | None = None

    @field_validator("document_type", mode="before")
    @classmethod
    def _validate_document_type(cls, value: object) -> str:
        if isinstance(value, DocumentType):
            return value.value
        if isinstance(value, str):
            return validate_mvp_document_type(value).value
        raise ValueError("document_type must be a string or DocumentType.")

    @field_validator("source_type", mode="before")
    @classmethod
    def _validate_source_type(cls, value: object) -> str | None:
        return validate_source_type(value)
