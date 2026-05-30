from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentCreate,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
    compute_searchable,
)


class DocumentAlreadyExistsError(Exception):
    pass


class DocumentRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["documents"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("_id", ASCENDING)], unique=True)
            await self._collection.create_index([("file_hash", ASCENDING)], unique=True, sparse=True)
            await self._collection.create_index(
                [("company_id", ASCENDING), ("document_type", ASCENDING), ("period", ASCENDING)]
            )
            await self._collection.create_index([("company_id", ASCENDING)])
            await self._collection.create_index([("approval_status", ASCENDING)])
            await self._collection.create_index([("lifecycle_status", ASCENDING)])
            await self._collection.create_index([("searchable", ASCENDING)])
            await self._collection.create_index([("created_at", DESCENDING)])
        except Exception:
            return

    def _normalize_title(self, title: str) -> str:
        return " ".join(title.strip().lower().split())

    def _build_doc(self, payload: DocumentCreate, *, document_id: str | None = None) -> dict:
        now = datetime.now(UTC)
        doc_id = document_id or str(uuid4())
        storage_path = payload.raw_storage_path
        if storage_path is None:
            storage_path = f"data/filings/{payload.company_id}/{doc_id}/"
        return {
            "_id": doc_id,
            "company_id": payload.company_id,
            "document_type": payload.document_type,
            "title": payload.title,
            "normalized_title": self._normalize_title(payload.title),
            "period": payload.period,
            "fiscal_year": payload.fiscal_year,
            "quarter": payload.quarter,
            "filing_date": payload.filing_date.isoformat() if payload.filing_date else None,
            "source_id": payload.source_id,
            "source_type": payload.source_type,
            "source_url": payload.source_url,
            "file_name": payload.file_name,
            "file_hash": payload.file_hash,
            "file_size_bytes": payload.file_size_bytes,
            "mime_type": payload.mime_type,
            "page_count": payload.page_count,
            "version": payload.version,
            "is_latest": payload.is_latest,
            "document_group_id": payload.document_group_id,
            "raw_storage_path": storage_path,
            "parse_status": ProcessingStageStatus.NOT_STARTED.value,
            "chunk_status": ProcessingStageStatus.NOT_STARTED.value,
            "index_status": ProcessingStageStatus.NOT_STARTED.value,
            "approval_status": ApprovalStatus.PENDING.value,
            "approved_by": None,
            "approved_at": None,
            "lifecycle_status": payload.lifecycle_status.value,
            "searchable": False,
            "notes": payload.notes,
            "created_at": now,
            "updated_at": now,
        }

    async def create(self, payload: DocumentCreate, *, document_id: str | None = None) -> Document:
        doc = self._build_doc(payload, document_id=document_id)
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise DocumentAlreadyExistsError("Document with duplicate id or file_hash.") from exc
        return Document.model_validate(self._serialize_for_model(doc))

    async def get_by_id(self, document_id: str) -> Document | None:
        doc = await self._collection.find_one({"_id": document_id})
        return Document.model_validate(self._serialize_for_model(doc)) if doc else None

    async def list_by_company(
        self,
        company_id: str,
        *,
        limit: int = 100,
        admin_view: bool = False,
    ) -> list[Document]:
        query: dict = {"company_id": company_id}
        cursor = self._collection.find(query).sort("created_at", DESCENDING)
        return [
            Document.model_validate(self._serialize_for_model(doc))
            async for doc in cursor.limit(limit)
        ]

    async def list_all(self, *, limit: int = 100) -> list[Document]:
        cursor = self._collection.find({}).sort("created_at", DESCENDING)
        return [
            Document.model_validate(self._serialize_for_model(doc))
            async for doc in cursor.limit(limit)
        ]

    async def update_searchable(
        self,
        document_id: str,
        *,
        searchable: bool,
        approval_status: ApprovalStatus | None = None,
        lifecycle_status: DocumentLifecycleStatus | None = None,
        index_status: ProcessingStageStatus | None = None,
    ) -> Document | None:
        if searchable:
            if approval_status is None or lifecycle_status is None or index_status is None:
                raise ValueError(
                    "searchable=True requires approval_status, lifecycle_status, and index_status."
                )
            if not compute_searchable(
                searchable_flag=True,
                approval_status=approval_status,
                lifecycle_status=lifecycle_status,
                index_status=index_status,
            ):
                raise ValueError("Document does not meet searchability requirements.")
        update_fields: dict = {
            "searchable": searchable,
            "updated_at": datetime.now(UTC),
        }
        if approval_status is not None:
            update_fields["approval_status"] = approval_status.value
        if lifecycle_status is not None:
            update_fields["lifecycle_status"] = lifecycle_status.value
        if index_status is not None:
            update_fields["index_status"] = index_status.value
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    def _serialize_for_model(self, doc: dict) -> dict:
        if doc is None:
            return doc
        serialized = dict(doc)
        filing_date = serialized.get("filing_date")
        if isinstance(filing_date, str) and filing_date:
            from datetime import date

            serialized["filing_date"] = date.fromisoformat(filing_date)
        approved_at = serialized.get("approved_at")
        if isinstance(approved_at, str) and approved_at:
            serialized["approved_at"] = datetime.fromisoformat(approved_at)
        return serialized
