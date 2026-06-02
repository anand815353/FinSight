from __future__ import annotations

from app.models.source_registry import SourceRegistryCreate, SourceRegistryEntry
from app.repositories.source_registry_repo import SourceRegistryRepository


class SourceRegistryService:
    def __init__(self, source_registry_repo: SourceRegistryRepository):
        self._source_registry_repo = source_registry_repo

    async def register_source(self, payload: SourceRegistryCreate) -> SourceRegistryEntry:
        return await self._source_registry_repo.create(payload)

    async def list_company_sources(self, company_id: str) -> list[SourceRegistryEntry]:
        return await self._source_registry_repo.list_by_company(company_id)

    async def upsert_source(self, payload: SourceRegistryCreate) -> SourceRegistryEntry:
        return await self._source_registry_repo.upsert_by_key(payload)
