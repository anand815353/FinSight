from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING

from app.core.config import Settings
from app.models.document_validation import (
    DocumentReadinessValidation,
    DocumentReadinessValidationCreate,
)


class DocumentValidationRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["document_readiness_validations"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("document_id", ASCENDING)])
            await self._collection.create_index(
                [("document_id", ASCENDING), ("validated_at", DESCENDING)]
            )
        except Exception:
            return

    def _serialize_for_model(self, doc: dict) -> dict:
        if doc is None:
            return doc
        serialized = dict(doc)
        for field in ("validated_at", "created_at", "updated_at"):
            value = serialized.get(field)
            if isinstance(value, str) and value:
                serialized[field] = datetime.fromisoformat(value)
        return serialized

    def _build_doc(
        self,
        payload: DocumentReadinessValidationCreate,
        *,
        validation_id: str | None = None,
    ) -> dict:
        now = datetime.now(UTC)
        validated_at = payload.validated_at or now
        return {
            "_id": validation_id or str(uuid4()),
            "document_id": payload.document_id,
            "company_id": payload.company_id,
            "status": payload.status.value,
            "fatal_errors": list(payload.fatal_errors),
            "warnings": list(payload.warnings),
            "checks": dict(payload.checks),
            "validated_by": payload.validated_by,
            "validated_at": validated_at,
            "searchability_enabled": payload.searchability_enabled,
            "created_at": now,
            "updated_at": now,
        }

    async def create(
        self,
        payload: DocumentReadinessValidationCreate,
        *,
        validation_id: str | None = None,
    ) -> DocumentReadinessValidation:
        doc = self._build_doc(payload, validation_id=validation_id)
        await self._collection.insert_one(doc)
        return DocumentReadinessValidation.model_validate(self._serialize_for_model(doc))

    async def get_latest_by_document_id(
        self, document_id: str
    ) -> DocumentReadinessValidation | None:
        cursor = (
            self._collection.find({"document_id": document_id})
            .sort("validated_at", DESCENDING)
            .limit(1)
        )
        async for doc in cursor:
            return DocumentReadinessValidation.model_validate(self._serialize_for_model(doc))
        return None
