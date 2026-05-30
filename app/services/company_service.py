from __future__ import annotations

import re

from fastapi import HTTPException

from app.models.company import Company, CompanyCreate, CompanyPublic
from app.repositories.company_repo import CompanyAlreadyExistsError, CompanyRepository
from app.services.company_seed_data import MVP_SEED_COMPANIES, seed_source_registry_entries


def slugify_company_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return slug.strip("_") or "company"


class CompanyService:
    def __init__(self, company_repo: CompanyRepository, source_registry_repo=None):
        self._company_repo = company_repo
        self._source_registry_repo = source_registry_repo

    def to_public(self, company: Company) -> CompanyPublic:
        return CompanyPublic(
            company_id=company.company_id,
            name=company.name,
            display_name=company.display_name,
            nse_symbol=company.nse_symbol,
            bse_scrip_code=company.bse_scrip_code,
            isin=company.isin,
            sector=company.sector,
            industry=company.industry,
            market_index=company.market_index,
            is_seed_company=company.is_seed_company,
            status=company.status,
        )

    async def get_company(self, company_id: str) -> Company | None:
        return await self._company_repo.get_by_id(company_id)

    async def list_companies(self, query: str | None = None) -> list[CompanyPublic]:
        companies = await self._company_repo.search(query)
        return [self.to_public(company) for company in companies]

    async def list_companies_raw(self, query: str | None = None) -> list[Company]:
        return await self._company_repo.search(query)

    async def create_company(self, payload: CompanyCreate) -> Company:
        try:
            return await self._company_repo.create(payload)
        except CompanyAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def seed_mvp_companies(self, *, include_sources: bool = False) -> list[Company]:
        seeded: list[Company] = []
        for payload in MVP_SEED_COMPANIES:
            company = await self._company_repo.upsert(payload)
            seeded.append(company)
            if include_sources and self._source_registry_repo is not None:
                for source_payload in seed_source_registry_entries(payload.company_id):
                    await self._source_registry_repo.upsert_by_key(source_payload)
        return seeded
