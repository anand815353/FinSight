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


class AuditLog(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    event_id: str = Field(alias="_id")
    event_type: AuditEventType
    user_id: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
