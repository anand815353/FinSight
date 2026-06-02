from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ACTIVE_PROCESSING_JOB_STATUSES = frozenset({"queued", "running"})


class ProcessingJobType(StrEnum):
    DOCUMENT_PROCESSING = "document_processing"


class ProcessingJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingJobStage(StrEnum):
    PROCESSING_REQUESTED = "processing_requested"
    PARSE_PENDING = "parse_pending"
    PARSE_RUNNING = "parse_running"
    PARSE_FAILED = "parse_failed"
    PARSE_COMPLETED = "parse_completed"
    CHUNK_PENDING = "chunk_pending"
    CHUNK_RUNNING = "chunk_running"
    CHUNK_COMPLETED = "chunk_completed"
    CHUNK_FAILED = "chunk_failed"
    INDEX_PENDING = "index_pending"
    INDEX_RUNNING = "index_running"
    INDEX_COMPLETED = "index_completed"
    INDEX_FAILED = "index_failed"
    READY_VALIDATION_PENDING = "ready_validation_pending"


class ProcessingJob(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    job_id: str = Field(alias="_id")
    document_id: str
    company_id: str
    requested_by: str
    job_type: ProcessingJobType = ProcessingJobType.DOCUMENT_PROCESSING
    status: ProcessingJobStatus = ProcessingJobStatus.QUEUED
    stage: ProcessingJobStage = ProcessingJobStage.PARSE_PENDING
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ProcessingJobCreate(BaseModel):
    document_id: str
    company_id: str
    requested_by: str
    job_type: ProcessingJobType = ProcessingJobType.DOCUMENT_PROCESSING
    status: ProcessingJobStatus = ProcessingJobStatus.QUEUED
    stage: ProcessingJobStage = ProcessingJobStage.PARSE_PENDING
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
