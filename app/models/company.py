from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class Company(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    company_id: str = Field(alias="_id")
    name: str
    display_name: str
    nse_symbol: str
    bse_scrip_code: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_index: str | None = None
    priority_rank: int | None = None
    is_seed_company: bool = False
    status: CompanyStatus = CompanyStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CompanyCreate(BaseModel):
    company_id: str
    name: str
    display_name: str
    nse_symbol: str
    bse_scrip_code: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_index: str | None = None
    priority_rank: int | None = None
    is_seed_company: bool = False
    status: CompanyStatus = CompanyStatus.ACTIVE


class CompanyPublic(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    company_id: str
    name: str
    display_name: str
    nse_symbol: str
    bse_scrip_code: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_index: str | None = None
    is_seed_company: bool = False
    status: CompanyStatus
