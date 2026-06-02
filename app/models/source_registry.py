from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    COMPANY_INVESTOR_RELATIONS = "company_investor_relations"
    NSE_CORPORATE_FILINGS = "nse_corporate_filings"
    BSE_CORPORATE_FILINGS = "bse_corporate_filings"
    ADMIN_UPLOADED_OFFICIAL_SOURCE_DOCUMENT = "admin_uploaded_official_source_document"


class SourceTrustLevel(StrEnum):
    OFFICIAL = "official"
    VERIFIED = "verified"
    ADMIN_UPLOADED = "admin_uploaded"


class SourceExchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"
    NA = "NA"


class SourceRegistryEntry(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    source_id: str = Field(alias="_id")
    company_id: str
    source_type: SourceType
    source_name: str
    source_url: str
    exchange: SourceExchange = SourceExchange.NA
    is_active: bool = True
    trust_level: SourceTrustLevel = SourceTrustLevel.OFFICIAL
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceRegistryCreate(BaseModel):
    company_id: str
    source_type: SourceType
    source_name: str
    source_url: str
    exchange: SourceExchange = SourceExchange.NA
    is_active: bool = True
    trust_level: SourceTrustLevel = SourceTrustLevel.OFFICIAL
    notes: str | None = None
