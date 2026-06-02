from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.models.processing_job import (
    ACTIVE_PROCESSING_JOB_STATUSES,
    ProcessingJob,
    ProcessingJobCreate,
    ProcessingJobStage,
    ProcessingJobStatus,
)


class ProcessingJobAlreadyExistsError(Exception):
    pass


class ProcessingJobRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["document_processing_jobs"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("_id", ASCENDING)], unique=True)
            await self._collection.create_index(
                [("document_id", ASCENDING), ("status", ASCENDING)]
            )
            await self._collection.create_index(
                [("company_id", ASCENDING), ("created_at", DESCENDING)]
            )
            await self._collection.create_index(
                [("status", ASCENDING), ("created_at", DESCENDING)]
            )
        except Exception:
            return

    def _build_doc(self, payload: ProcessingJobCreate, *, job_id: str | None = None) -> dict:
        now = datetime.now(UTC)
        resolved_id = job_id or str(uuid4())
        return {
            "_id": resolved_id,
            "document_id": payload.document_id,
            "company_id": payload.company_id,
            "requested_by": payload.requested_by,
            "job_type": payload.job_type.value,
            "status": payload.status.value,
            "stage": payload.stage.value,
            "priority": payload.priority,
            "attempts": payload.attempts,
            "max_attempts": payload.max_attempts,
            "error_code": payload.error_code,
            "error_message": payload.error_message,
            "metadata": dict(payload.metadata),
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }

    def _serialize_for_model(self, doc: dict) -> dict:
        if doc is None:
            return doc
        serialized = dict(doc)
        for field in ("created_at", "updated_at", "started_at", "completed_at"):
            value = serialized.get(field)
            if isinstance(value, str) and value:
                serialized[field] = datetime.fromisoformat(value)
        return serialized

    async def create(self, payload: ProcessingJobCreate, *, job_id: str | None = None) -> ProcessingJob:
        doc = self._build_doc(payload, job_id=job_id)
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ProcessingJobAlreadyExistsError("Processing job with duplicate id.") from exc
        return ProcessingJob.model_validate(self._serialize_for_model(doc))

    async def get_by_id(self, job_id: str) -> ProcessingJob | None:
        doc = await self._collection.find_one({"_id": job_id})
        return ProcessingJob.model_validate(self._serialize_for_model(doc)) if doc else None

    async def list_jobs(
        self,
        *,
        status: ProcessingJobStatus | None = None,
        document_id: str | None = None,
        limit: int = 200,
    ) -> list[ProcessingJob]:
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        if document_id is not None:
            query["document_id"] = document_id
        cursor = self._collection.find(query).sort("created_at", DESCENDING)
        return [
            ProcessingJob.model_validate(self._serialize_for_model(doc))
            async for doc in cursor.limit(limit)
        ]

    async def find_active_job_by_document_id(self, document_id: str) -> ProcessingJob | None:
        cursor = self._collection.find(
            {
                "document_id": document_id,
                "status": {"$in": sorted(ACTIVE_PROCESSING_JOB_STATUSES)},
            }
        ).sort("created_at", DESCENDING)
        async for doc in cursor.limit(1):
            return ProcessingJob.model_validate(self._serialize_for_model(doc))
        return None

    async def update_job(
        self,
        job_id: str,
        *,
        status: ProcessingJobStatus | None = None,
        stage: ProcessingJobStage | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        attempts: int | None = None,
        clear_errors: bool = False,
    ) -> ProcessingJob | None:
        update_fields: dict = {"updated_at": datetime.now(UTC)}
        if status is not None:
            update_fields["status"] = status.value
        if stage is not None:
            update_fields["stage"] = stage.value
        if clear_errors:
            update_fields["error_code"] = None
            update_fields["error_message"] = None
        else:
            if error_code is not None:
                update_fields["error_code"] = error_code
            if error_message is not None:
                update_fields["error_message"] = error_message
        if started_at is not None:
            update_fields["started_at"] = started_at
        if completed_at is not None:
            update_fields["completed_at"] = completed_at
        if attempts is not None:
            update_fields["attempts"] = attempts
        result = await self._collection.update_one({"_id": job_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(job_id)
