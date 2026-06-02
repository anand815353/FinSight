from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ASCENDING

from app.core.config import Settings
from app.models.document_chunk import DocumentChunk, DocumentChunkCreate


class DocumentChunkRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["document_chunks"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("_id", ASCENDING)], unique=True)
            await self._collection.create_index(
                [("document_id", ASCENDING), ("chunk_index", ASCENDING)]
            )
            await self._collection.create_index([("document_id", ASCENDING)])
            await self._collection.create_index(
                [("company_id", ASCENDING), ("document_type", ASCENDING)]
            )
        except Exception:
            return

    def _build_doc(self, payload: DocumentChunkCreate) -> dict:
        now = datetime.now(UTC)
        return {
            "_id": payload.chunk_id,
            "document_id": payload.document_id,
            "company_id": payload.company_id,
            "document_type": payload.document_type,
            "fiscal_year": payload.fiscal_year,
            "quarter": payload.quarter,
            "period": payload.period,
            "page_start": payload.page_start,
            "page_end": payload.page_end,
            "page_numbers": list(payload.page_numbers),
            "section_title": payload.section_title,
            "chunk_index": payload.chunk_index,
            "chunk_type": payload.chunk_type.value,
            "text": payload.text,
            "text_preview": payload.text_preview,
            "char_count": payload.char_count,
            "word_count": payload.word_count,
            "source_document_title": payload.source_document_title,
            "source_url": payload.source_url,
            "file_hash": payload.file_hash,
            "created_at": now,
            "updated_at": now,
        }

    def _serialize_for_model(self, doc: dict) -> dict:
        if doc is None:
            return doc
        serialized = dict(doc)
        for field in ("created_at", "updated_at", "indexed_at"):
            value = serialized.get(field)
            if isinstance(value, str) and value:
                serialized[field] = datetime.fromisoformat(value)
        return serialized

    async def create_many(self, payloads: list[DocumentChunkCreate]) -> list[DocumentChunk]:
        if not payloads:
            return []
        docs = [self._build_doc(payload) for payload in payloads]
        await self._collection.insert_many(docs)
        return [DocumentChunk.model_validate(self._serialize_for_model(doc)) for doc in docs]

    async def delete_by_document_id(self, document_id: str) -> int:
        result = await self._collection.delete_many({"document_id": document_id})
        return result.deleted_count

    async def list_by_document_id(self, document_id: str) -> list[DocumentChunk]:
        cursor = self._collection.find({"document_id": document_id}).sort("chunk_index", ASCENDING)
        return [
            DocumentChunk.model_validate(self._serialize_for_model(doc))
            async for doc in cursor
        ]

    async def get_by_chunk_ids(self, chunk_ids: list[str]) -> dict[str, DocumentChunk]:
        if not chunk_ids:
            return {}
        cursor = self._collection.find({"_id": {"$in": list(chunk_ids)}})
        result: dict[str, DocumentChunk] = {}
        async for doc in cursor:
            model = DocumentChunk.model_validate(self._serialize_for_model(doc))
            result[model.chunk_id] = model
        return result

    async def count_by_document_id(self, document_id: str) -> int:
        return await self._collection.count_documents({"document_id": document_id})

    async def update_indexing_metadata(
        self,
        updates: list[dict],
    ) -> None:
        if not updates:
            return
        now = datetime.now(UTC)
        for item in updates:
            chunk_id = item["chunk_id"]
            set_fields = {
                "qdrant_collection": item["qdrant_collection"],
                "qdrant_point_id": item["qdrant_point_id"],
                "embedding_provider": item["embedding_provider"],
                "embedding_model": item["embedding_model"],
                "index_status": item["index_status"],
                "indexed_at": item.get("indexed_at", now),
                "updated_at": now,
            }
            await self._collection.update_one({"_id": chunk_id}, {"$set": set_fields})
