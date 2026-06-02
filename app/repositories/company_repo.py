from __future__ import annotations

import re
from datetime import UTC, datetime

from pymongo import ASCENDING, TEXT
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.models.company import Company, CompanyCreate, CompanyStatus


class CompanyAlreadyExistsError(Exception):
    pass


class CompanyRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["companies"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("_id", ASCENDING)], unique=True)
            await self._collection.create_index([("nse_symbol", ASCENDING)])
            await self._collection.create_index([("bse_scrip_code", ASCENDING)], sparse=True)
            await self._collection.create_index([("isin", ASCENDING)], sparse=True)
            await self._collection.create_index([("status", ASCENDING)])
            await self._collection.create_index([("is_seed_company", ASCENDING)])
            await self._collection.create_index(
                [("name", TEXT), ("display_name", TEXT), ("nse_symbol", TEXT)]
            )
        except Exception:
            return

    def _to_doc(self, payload: CompanyCreate, *, now: datetime | None = None) -> dict:
        timestamp = now or datetime.now(UTC)
        return {
            "_id": payload.company_id,
            "name": payload.name,
            "display_name": payload.display_name,
            "nse_symbol": payload.nse_symbol.upper(),
            "bse_scrip_code": payload.bse_scrip_code,
            "isin": payload.isin,
            "sector": payload.sector,
            "industry": payload.industry,
            "market_index": payload.market_index,
            "priority_rank": payload.priority_rank,
            "is_seed_company": payload.is_seed_company,
            "status": payload.status.value,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    async def create(self, payload: CompanyCreate) -> Company:
        doc = self._to_doc(payload)
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise CompanyAlreadyExistsError(f"Company '{payload.company_id}' already exists.") from exc
        return Company.model_validate(doc)

    async def upsert(self, payload: CompanyCreate) -> Company:
        now = datetime.now(UTC)
        doc = self._to_doc(payload, now=now)
        existing = await self._collection.find_one({"_id": payload.company_id})
        if existing:
            doc["created_at"] = existing.get("created_at", now)
        await self._collection.replace_one({"_id": payload.company_id}, doc, upsert=True)
        return Company.model_validate(doc)

    async def get_by_id(self, company_id: str) -> Company | None:
        doc = await self._collection.find_one({"_id": company_id})
        return Company.model_validate(doc) if doc else None

    async def get_by_symbol(self, nse_symbol: str) -> Company | None:
        doc = await self._collection.find_one({"nse_symbol": nse_symbol.upper()})
        return Company.model_validate(doc) if doc else None

    async def list_active(self, *, limit: int = 100) -> list[Company]:
        cursor = self._collection.find({"status": CompanyStatus.ACTIVE.value}).sort("priority_rank", 1)
        return [Company.model_validate(doc) async for doc in cursor.limit(limit)]

    async def search(self, query: str | None = None, *, limit: int = 50) -> list[Company]:
        if not query or not query.strip():
            return await self.list_active(limit=limit)
        pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
        cursor = self._collection.find(
            {
                "status": CompanyStatus.ACTIVE.value,
                "$or": [
                    {"name": pattern},
                    {"display_name": pattern},
                    {"nse_symbol": pattern},
                ],
            }
        ).sort("priority_rank", 1)
        return [Company.model_validate(doc) async for doc in cursor.limit(limit)]
