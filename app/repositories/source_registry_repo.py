from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.models.source_registry import SourceRegistryCreate, SourceRegistryEntry


class SourceRegistryDuplicateError(Exception):
    pass


class SourceRegistryRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["source_registry"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("_id", ASCENDING)], unique=True)
            await self._collection.create_index(
                [("company_id", ASCENDING), ("source_type", ASCENDING), ("source_url", ASCENDING)],
                unique=True,
            )
            await self._collection.create_index([("company_id", ASCENDING)])
            await self._collection.create_index([("is_active", ASCENDING)])
        except Exception:
            return

    async def create(self, payload: SourceRegistryCreate) -> SourceRegistryEntry:
        now = datetime.now(UTC)
        doc = {
            "_id": str(uuid4()),
            "company_id": payload.company_id,
            "source_type": payload.source_type.value,
            "source_name": payload.source_name,
            "source_url": payload.source_url,
            "exchange": payload.exchange.value,
            "is_active": payload.is_active,
            "trust_level": payload.trust_level.value,
            "notes": payload.notes,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise SourceRegistryDuplicateError(
                "Source registry entry already exists for company/type/url."
            ) from exc
        return SourceRegistryEntry.model_validate(doc)

    async def upsert_by_key(self, payload: SourceRegistryCreate, *, source_id: str | None = None) -> SourceRegistryEntry:
        now = datetime.now(UTC)
        existing = await self._collection.find_one(
            {
                "company_id": payload.company_id,
                "source_type": payload.source_type.value,
                "source_url": payload.source_url,
            }
        )
        doc = {
            "_id": source_id or (existing["_id"] if existing else str(uuid4())),
            "company_id": payload.company_id,
            "source_type": payload.source_type.value,
            "source_name": payload.source_name,
            "source_url": payload.source_url,
            "exchange": payload.exchange.value,
            "is_active": payload.is_active,
            "trust_level": payload.trust_level.value,
            "notes": payload.notes,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        await self._collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return SourceRegistryEntry.model_validate(doc)

    async def get_by_id(self, source_id: str) -> SourceRegistryEntry | None:
        doc = await self._collection.find_one({"_id": source_id})
        return SourceRegistryEntry.model_validate(doc) if doc else None

    async def list_by_company(self, company_id: str, *, active_only: bool = True) -> list[SourceRegistryEntry]:
        query: dict = {"company_id": company_id}
        if active_only:
            query["is_active"] = True
        cursor = self._collection.find(query).sort("source_type", 1)
        return [SourceRegistryEntry.model_validate(doc) async for doc in cursor]
