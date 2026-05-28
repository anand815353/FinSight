from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(StrEnum):
    FREE_USER = "free_user"
    BETA_USER = "beta_user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    LOCKED = "locked"
    DELETED = "deleted"


class User(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    user_id: str = Field(alias="_id")
    email: EmailStr
    password_hash: str
    full_name: str | None = None
    role: UserRole = UserRole.FREE_USER
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_login_at: datetime | None = None
    failed_login_count: int = 0
    preferences: dict[str, Any] = Field(default_factory=dict)
