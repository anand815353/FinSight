from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventType(StrEnum):
    REGISTER_SUCCESS = "register_success"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PROTECTED_ROUTE_DENIED = "protected_route_denied"
    ADMIN_ACCESS_DENIED = "admin_access_denied"
    ADMIN_COMPANY_CREATED = "admin_company_created"
    ADMIN_DOCUMENT_REGISTERED = "admin_document_registered"
    ADMIN_DOCUMENT_UPLOADED = "admin_document_uploaded"
    ADMIN_DOCUMENT_REGISTRATION_FAILED = "admin_document_registration_failed"
    ADMIN_DOCUMENT_REVIEW_OPENED = "admin_document_review_opened"
    ADMIN_DOCUMENT_APPROVED = "admin_document_approved"
    ADMIN_DOCUMENT_REJECTED = "admin_document_rejected"
    ADMIN_DOCUMENT_MARKED_PENDING = "admin_document_marked_pending"
    ADMIN_DOCUMENT_APPROVAL_FAILED = "admin_document_approval_failed"
    ADMIN_DOCUMENT_PROCESSING_REQUESTED = "admin_document_processing_requested"
    ADMIN_DOCUMENT_PROCESSING_DUPLICATE_BLOCKED = "admin_document_processing_duplicate_blocked"
    ADMIN_DOCUMENT_PROCESSING_VIEWED = "admin_document_processing_viewed"
    ADMIN_DOCUMENT_PARSE_STARTED = "admin_document_parse_started"
    ADMIN_DOCUMENT_PARSE_COMPLETED = "admin_document_parse_completed"
    ADMIN_DOCUMENT_PARSE_FAILED = "admin_document_parse_failed"
    ADMIN_DOCUMENT_CHUNKING_STARTED = "admin_document_chunking_started"
    ADMIN_DOCUMENT_CHUNKING_COMPLETED = "admin_document_chunking_completed"
    ADMIN_DOCUMENT_CHUNKING_FAILED = "admin_document_chunking_failed"
    ADMIN_DOCUMENT_INDEXING_STARTED = "admin_document_indexing_started"
    ADMIN_DOCUMENT_INDEXING_COMPLETED = "admin_document_indexing_completed"
    ADMIN_DOCUMENT_INDEXING_FAILED = "admin_document_indexing_failed"
    ADMIN_DOCUMENT_READINESS_VALIDATION_STARTED = "admin_document_readiness_validation_started"
    ADMIN_DOCUMENT_READINESS_VALIDATION_PASSED = "admin_document_readiness_validation_passed"
    ADMIN_DOCUMENT_READINESS_VALIDATION_FAILED = "admin_document_readiness_validation_failed"
    ADMIN_DOCUMENT_SEARCHABILITY_ENABLED = "admin_document_searchability_enabled"


class AuditLog(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    event_id: str = Field(alias="_id")
    event_type: AuditEventType
    user_id: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
