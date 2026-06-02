from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class DocumentReadinessValidation(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    validation_id: str = Field(alias="_id")
    document_id: str
    company_id: str
    status: ValidationStatus
    fatal_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    validated_by: str | None = None
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    searchability_enabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentReadinessValidationCreate(BaseModel):
    document_id: str
    company_id: str
    status: ValidationStatus
    fatal_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    validated_by: str | None = None
    validated_at: datetime | None = None
    searchability_enabled: bool = False


class ReadinessCheckOutcome(BaseModel):
    checks: dict[str, bool] = Field(default_factory=dict)
    fatal_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.fatal_errors) == 0

    def add_fatal(self, code: str, message: str | None = None) -> None:
        self.checks[code] = False
        self.fatal_errors.append(f"{code}: {message}" if message else code)

    def add_pass(self, code: str) -> None:
        self.checks[code] = True

    def add_warning(self, code: str, message: str | None = None) -> None:
        self.warnings.append(f"{code}: {message}" if message else code)

    def to_audit_summary(self) -> dict[str, Any]:
        return {
            "fatal_error_count": len(self.fatal_errors),
            "warning_count": len(self.warnings),
            "check_count": len(self.checks),
        }
