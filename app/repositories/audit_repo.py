from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING

from app.core.config import Settings
from app.models.audit_log import AuditEventType


class AuditRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["audit_logs"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("event_type", ASCENDING)])
            await self._collection.create_index([("user_id", ASCENDING)])
            await self._collection.create_index([("created_at", DESCENDING)])
            await self._collection.create_index([("request_id", ASCENDING)])
        except Exception:
            # Tests and local startup may run without a live MongoDB instance.
            return

    async def log_event(
        self,
        *,
        event_type: AuditEventType,
        user_id: str | None,
        request_id: str | None,
        details: dict | None = None,
    ) -> None:
        payload = {
            "_id": str(uuid4()),
            "event_type": event_type.value,
            "user_id": user_id,
            "request_id": request_id,
            "details": details or {},
            "created_at": datetime.now(UTC),
        }
        await self._collection.insert_one(payload)
