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
    DocumentReadinessStatus,
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
            "parsed_pages_path": None,
            "page_map_path": None,
            "parsed_at": None,
            "chunk_count": None,
            "chunk_artifact_path": None,
            "chunked_at": None,
            "parse_status": ProcessingStageStatus.NOT_STARTED.value,
            "chunk_status": ProcessingStageStatus.NOT_STARTED.value,
            "index_status": ProcessingStageStatus.NOT_STARTED.value,
            "approval_status": ApprovalStatus.PENDING.value,
            "approved_by": None,
            "approved_at": None,
            "rejected_by": None,
            "rejected_at": None,
            "rejection_reason": None,
            "review_notes": None,
            "lifecycle_status": payload.lifecycle_status.value,
            "searchable": False,
            "readiness_status": DocumentReadinessStatus.NOT_VALIDATED.value,
            "readiness_validated_at": None,
            "readiness_validation_id": None,
            "searchable_at": None,
            "searchable_by": None,
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

    async def get_by_ids(self, document_ids: list[str]) -> dict[str, Document]:
        if not document_ids:
            return {}
        cursor = self._collection.find({"_id": {"$in": list(document_ids)}})
        result: dict[str, Document] = {}
        async for doc in cursor:
            model = Document.model_validate(self._serialize_for_model(doc))
            result[model.document_id] = model
        return result

    async def get_by_file_hash(self, file_hash: str) -> Document | None:
        doc = await self._collection.find_one({"file_hash": file_hash})
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

    async def list_by_approval_status(
        self,
        approval_status: ApprovalStatus,
        *,
        limit: int = 200,
    ) -> list[Document]:
        cursor = self._collection.find({"approval_status": approval_status.value}).sort(
            "created_at", DESCENDING
        )
        return [
            Document.model_validate(self._serialize_for_model(doc))
            async for doc in cursor.limit(limit)
        ]

    async def update_approval_review(
        self,
        document_id: str,
        *,
        approval_status: ApprovalStatus,
        lifecycle_status: DocumentLifecycleStatus,
        searchable: bool = False,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        rejected_by: str | None = None,
        rejected_at: datetime | None = None,
        rejection_reason: str | None = None,
        review_notes: str | None = None,
        clear_approved: bool = False,
        clear_rejected: bool = False,
    ) -> Document | None:
        update_fields: dict = {
            "approval_status": approval_status.value,
            "lifecycle_status": lifecycle_status.value,
            "searchable": searchable,
            "updated_at": datetime.now(UTC),
        }
        if clear_approved:
            update_fields["approved_by"] = None
            update_fields["approved_at"] = None
        elif approved_by is not None:
            update_fields["approved_by"] = approved_by
            update_fields["approved_at"] = approved_at or datetime.now(UTC)
        if clear_rejected:
            update_fields["rejected_by"] = None
            update_fields["rejected_at"] = None
            update_fields["rejection_reason"] = None
        elif rejected_by is not None:
            update_fields["rejected_by"] = rejected_by
            update_fields["rejected_at"] = rejected_at or datetime.now(UTC)
            update_fields["rejection_reason"] = rejection_reason
        if review_notes is not None:
            update_fields["review_notes"] = review_notes
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    async def update_processing_stages(
        self,
        document_id: str,
        *,
        parse_status: ProcessingStageStatus | None = None,
        chunk_status: ProcessingStageStatus | None = None,
        index_status: ProcessingStageStatus | None = None,
        lifecycle_status: DocumentLifecycleStatus | None = None,
        searchable: bool | None = None,
    ) -> Document | None:
        update_fields: dict = {"updated_at": datetime.now(UTC)}
        if parse_status is not None:
            update_fields["parse_status"] = parse_status.value
        if chunk_status is not None:
            update_fields["chunk_status"] = chunk_status.value
        if index_status is not None:
            update_fields["index_status"] = index_status.value
        if lifecycle_status is not None:
            update_fields["lifecycle_status"] = lifecycle_status.value
        if searchable is not None:
            update_fields["searchable"] = searchable
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    async def update_parse_result(
        self,
        document_id: str,
        *,
        parse_status: ProcessingStageStatus,
        lifecycle_status: DocumentLifecycleStatus,
        page_count: int | None = None,
        parsed_pages_path: str | None = None,
        page_map_path: str | None = None,
        parsed_at: datetime | None = None,
        searchable: bool = False,
    ) -> Document | None:
        update_fields: dict = {
            "parse_status": parse_status.value,
            "lifecycle_status": lifecycle_status.value,
            "searchable": searchable,
            "updated_at": datetime.now(UTC),
        }
        if page_count is not None:
            update_fields["page_count"] = page_count
        if parsed_pages_path is not None:
            update_fields["parsed_pages_path"] = parsed_pages_path
        if page_map_path is not None:
            update_fields["page_map_path"] = page_map_path
        if parsed_at is not None:
            update_fields["parsed_at"] = parsed_at
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    async def update_chunk_result(
        self,
        document_id: str,
        *,
        chunk_status: ProcessingStageStatus,
        lifecycle_status: DocumentLifecycleStatus,
        chunk_count: int | None = None,
        chunk_artifact_path: str | None = None,
        chunked_at: datetime | None = None,
        searchable: bool = False,
    ) -> Document | None:
        update_fields: dict = {
            "chunk_status": chunk_status.value,
            "lifecycle_status": lifecycle_status.value,
            "searchable": searchable,
            "updated_at": datetime.now(UTC),
        }
        if chunk_count is not None:
            update_fields["chunk_count"] = chunk_count
        if chunk_artifact_path is not None:
            update_fields["chunk_artifact_path"] = chunk_artifact_path
        if chunked_at is not None:
            update_fields["chunked_at"] = chunked_at
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    async def update_index_result(
        self,
        document_id: str,
        *,
        index_status: ProcessingStageStatus,
        lifecycle_status: DocumentLifecycleStatus,
        indexed_chunk_count: int | None = None,
        indexed_at: datetime | None = None,
        searchable: bool = False,
    ) -> Document | None:
        update_fields: dict = {
            "index_status": index_status.value,
            "lifecycle_status": lifecycle_status.value,
            "searchable": searchable,
            "updated_at": datetime.now(UTC),
        }
        if indexed_chunk_count is not None:
            update_fields["indexed_chunk_count"] = indexed_chunk_count
        if indexed_at is not None:
            update_fields["indexed_at"] = indexed_at
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

    async def update_index_status_only(
        self,
        document_id: str,
        *,
        index_status: ProcessingStageStatus,
        lifecycle_status: DocumentLifecycleStatus | None = None,
    ) -> Document | None:
        update_fields: dict = {
            "index_status": index_status.value,
            "updated_at": datetime.now(UTC),
        }
        if lifecycle_status is not None:
            update_fields["lifecycle_status"] = lifecycle_status.value
        result = await self._collection.update_one({"_id": document_id}, {"$set": update_fields})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(document_id)

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

    async def update_readiness_result(
        self,
        document_id: str,
        *,
        readiness_status: DocumentReadinessStatus,
        readiness_validation_id: str | None = None,
        readiness_validated_at: datetime | None = None,
        searchable: bool | None = None,
        searchable_at: datetime | None = None,
        searchable_by: str | None = None,
        lifecycle_status: DocumentLifecycleStatus | None = None,
        approval_status: ApprovalStatus | None = None,
        index_status: ProcessingStageStatus | None = None,
    ) -> Document | None:
        update_fields: dict = {
            "readiness_status": readiness_status.value,
            "updated_at": datetime.now(UTC),
        }
        if readiness_validation_id is not None:
            update_fields["readiness_validation_id"] = readiness_validation_id
        if readiness_validated_at is not None:
            update_fields["readiness_validated_at"] = readiness_validated_at
        if searchable is not None:
            update_fields["searchable"] = searchable
        if searchable_at is not None:
            update_fields["searchable_at"] = searchable_at
        if searchable_by is not None:
            update_fields["searchable_by"] = searchable_by
        if lifecycle_status is not None:
            update_fields["lifecycle_status"] = lifecycle_status.value
        if approval_status is not None:
            update_fields["approval_status"] = approval_status.value
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
        rejected_at = serialized.get("rejected_at")
        if isinstance(rejected_at, str) and rejected_at:
            serialized["rejected_at"] = datetime.fromisoformat(rejected_at)
        parsed_at = serialized.get("parsed_at")
        if isinstance(parsed_at, str) and parsed_at:
            serialized["parsed_at"] = datetime.fromisoformat(parsed_at)
        chunked_at = serialized.get("chunked_at")
        if isinstance(chunked_at, str) and chunked_at:
            serialized["chunked_at"] = datetime.fromisoformat(chunked_at)
        indexed_at = serialized.get("indexed_at")
        if isinstance(indexed_at, str) and indexed_at:
            serialized["indexed_at"] = datetime.fromisoformat(indexed_at)
        readiness_validated_at = serialized.get("readiness_validated_at")
        if isinstance(readiness_validated_at, str) and readiness_validated_at:
            serialized["readiness_validated_at"] = datetime.fromisoformat(readiness_validated_at)
        searchable_at = serialized.get("searchable_at")
        if isinstance(searchable_at, str) and searchable_at:
            serialized["searchable_at"] = datetime.fromisoformat(searchable_at)
        return serialized
