from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.db.qdrant import QdrantClientManager
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
)
from app.models.document_chunk import DocumentChunk
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStage,
    ProcessingJobStatus,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.embedding_provider import EmbeddingProvider

_USER_SAFE_MESSAGES = {
    "job_not_found": "Processing job not found.",
    "document_not_found": "Document not found.",
    "job_not_runnable": "Processing job is not ready for indexing.",
    "document_not_approved": "Only approved documents can be indexed.",
    "document_not_chunked": "Document must be chunked before indexing.",
    "file_hash_missing": "Document file hash is required for indexing.",
    "no_chunks": "No chunks found for this document.",
    "qdrant_unavailable": "Vector index is not available.",
    "indexing_failed": "Document indexing failed.",
}


class QdrantIndexingError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "indexing_failed"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class QdrantIndexingService:
    def __init__(
        self,
        document_repo: DocumentRepository,
        document_chunk_repo: DocumentChunkRepository,
        processing_job_repo: ProcessingJobRepository,
        qdrant_manager: QdrantClientManager,
        embedding_provider: EmbeddingProvider,
        *,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
    ):
        self._document_repo = document_repo
        self._document_chunk_repo = document_chunk_repo
        self._processing_job_repo = processing_job_repo
        self._qdrant_manager = qdrant_manager
        self._embedding_provider = embedding_provider
        self._audit_repo = audit_repo
        self._settings = settings

    def _safe_message(self, code: str) -> str:
        return _USER_SAFE_MESSAGES.get(code, _USER_SAFE_MESSAGES["indexing_failed"])

    @staticmethod
    def point_id_for_chunk(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def build_payload(self, chunk: DocumentChunk, *, embedding_provider: str, embedding_model: str) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "company_id": chunk.company_id,
            "document_type": chunk.document_type,
            "fiscal_year": chunk.fiscal_year,
            "quarter": chunk.quarter,
            "period": chunk.period,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "page_numbers": list(chunk.page_numbers),
            "section_title": chunk.section_title,
            "chunk_index": chunk.chunk_index,
            "chunk_type": str(chunk.chunk_type),
            "text_preview": chunk.text_preview,
            "source_document_title": chunk.source_document_title,
            "source_url": chunk.source_url,
            "file_hash": chunk.file_hash,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            # Mongo document.searchable is authoritative; Qdrant payload searchability
            # is not used as the final retrieval gate.
            "searchable": False,
        }

    async def _audit(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None,
        document_id: str,
        job_id: str,
        company_id: str,
        indexed_chunk_count: int | None = None,
        error_code: str | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        details: dict = {
            "document_id": document_id,
            "job_id": job_id,
            "company_id": company_id,
        }
        if indexed_chunk_count is not None:
            details["indexed_chunk_count"] = indexed_chunk_count
        if error_code is not None:
            details["error_code"] = error_code
        await self._audit_repo.log_event(
            event_type=event_type,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
            details=details,
        )

    async def _mark_failure(
        self,
        *,
        job: ProcessingJob,
        document_id: str,
        admin_user_id: str | None,
        error_code: str,
        error_message: str,
    ) -> None:
        await self._processing_job_repo.update_job(
            job.job_id,
            status=ProcessingJobStatus.FAILED,
            stage=ProcessingJobStage.INDEX_FAILED,
            error_code=error_code,
            error_message=error_message,
            completed_at=datetime.now(UTC),
        )
        await self._document_repo.update_index_result(
            document_id,
            index_status=ProcessingStageStatus.FAILED,
            lifecycle_status=DocumentLifecycleStatus.INDEX_FAILED,
            searchable=False,
        )
        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_INDEXING_FAILED,
            user_id=admin_user_id,
            document_id=document_id,
            job_id=job.job_id,
            company_id=job.company_id,
            error_code=error_code,
        )

    async def ensure_collection(self) -> str:
        if self._settings is None:
            raise QdrantIndexingError("Settings are not configured.", status_code=500)
        client = self._qdrant_manager.client
        if client is None:
            raise QdrantIndexingError(
                self._safe_message("qdrant_unavailable"),
                status_code=503,
                code="qdrant_unavailable",
            )
        collection_name = self._settings.embedding.collection_name
        from qdrant_client.models import Distance, VectorParams

        exists = False
        collections = await client.get_collections()
        for collection in collections.collections:
            if collection.name == collection_name:
                exists = True
                break
        if not exists:
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self._embedding_provider.embedding_dimension(),
                    distance=Distance.COSINE,
                ),
            )
        return collection_name

    async def index_document_for_processing_job(
        self,
        job_id: str,
        *,
        admin_user_id: str,
    ) -> ProcessingJob:
        job = await self._processing_job_repo.get_by_id(job_id)
        if job is None:
            raise QdrantIndexingError(
                self._safe_message("job_not_found"),
                status_code=404,
                code="job_not_found",
            )

        runnable_stages = {
            ProcessingJobStage.INDEX_PENDING.value,
            ProcessingJobStage.INDEX_FAILED.value,
            ProcessingJobStage.INDEX_COMPLETED.value,
        }
        runnable_statuses = {
            ProcessingJobStatus.QUEUED.value,
            ProcessingJobStatus.COMPLETED.value,
            ProcessingJobStatus.FAILED.value,
        }
        if job.stage not in runnable_stages or job.status not in runnable_statuses:
            raise QdrantIndexingError(
                self._safe_message("job_not_runnable"),
                status_code=409,
                code="job_not_runnable",
            )

        document = await self._document_repo.get_by_id(job.document_id)
        if document is None:
            raise QdrantIndexingError(
                self._safe_message("document_not_found"),
                status_code=404,
                code="document_not_found",
            )

        if ApprovalStatus(document.approval_status) != ApprovalStatus.APPROVED:
            raise QdrantIndexingError(
                self._safe_message("document_not_approved"),
                status_code=409,
                code="document_not_approved",
            )

        if ProcessingStageStatus(document.chunk_status) != ProcessingStageStatus.COMPLETED:
            raise QdrantIndexingError(
                self._safe_message("document_not_chunked"),
                status_code=409,
                code="document_not_chunked",
            )

        if not document.file_hash:
            raise QdrantIndexingError(
                self._safe_message("file_hash_missing"),
                status_code=409,
                code="file_hash_missing",
            )

        if document.searchable:
            raise QdrantIndexingError(
                "Document is already searchable and cannot be re-indexed.",
                status_code=409,
                code="indexing_failed",
            )

        chunks = await self._document_chunk_repo.list_by_document_id(document.document_id)
        if not chunks:
            raise QdrantIndexingError(
                self._safe_message("no_chunks"),
                status_code=400,
                code="no_chunks",
            )

        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_INDEXING_STARTED,
            user_id=admin_user_id,
            document_id=document.document_id,
            job_id=job.job_id,
            company_id=job.company_id,
        )

        await self._processing_job_repo.update_job(
            job.job_id,
            status=ProcessingJobStatus.RUNNING,
            stage=ProcessingJobStage.INDEX_RUNNING,
            started_at=datetime.now(UTC),
            clear_errors=True,
            attempts=job.attempts + 1,
        )
        await self._document_repo.update_index_status_only(
            document.document_id,
            index_status=ProcessingStageStatus.PENDING,
        )

        try:
            collection_name = await self.ensure_collection()
            client = self._qdrant_manager.client
            if client is None:
                raise QdrantIndexingError(
                    self._safe_message("qdrant_unavailable"),
                    status_code=503,
                    code="qdrant_unavailable",
                )

            embedding_provider_name = (
                self._settings.embedding.provider if self._settings else "huggingface"
            )
            embedding_model_name = (
                self._settings.embedding.hf_model_name if self._settings else "unknown"
            )

            texts = [chunk.text for chunk in chunks]
            vectors = self._embedding_provider.embed_texts(texts)

            from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

            await client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document.document_id),
                        )
                    ]
                ),
            )

            points = []
            chunk_updates: list[dict] = []
            indexed_at = datetime.now(UTC)
            for chunk, vector in zip(chunks, vectors, strict=True):
                point_id = self.point_id_for_chunk(chunk.chunk_id)
                payload = self.build_payload(
                    chunk,
                    embedding_provider=embedding_provider_name,
                    embedding_model=embedding_model_name,
                )
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                )
                chunk_updates.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "qdrant_collection": collection_name,
                        "qdrant_point_id": point_id,
                        "embedding_provider": embedding_provider_name,
                        "embedding_model": embedding_model_name,
                        "index_status": ProcessingStageStatus.COMPLETED.value,
                        "indexed_at": indexed_at,
                    }
                )

            await client.upsert(collection_name=collection_name, points=points)
            await self._document_chunk_repo.update_indexing_metadata(chunk_updates)

            await self._document_repo.update_index_result(
                document.document_id,
                index_status=ProcessingStageStatus.COMPLETED,
                lifecycle_status=DocumentLifecycleStatus.INDEXED,
                indexed_chunk_count=len(chunks),
                indexed_at=indexed_at,
                searchable=False,
            )
            updated_job = await self._processing_job_repo.update_job(
                job.job_id,
                status=ProcessingJobStatus.COMPLETED,
                stage=ProcessingJobStage.INDEX_COMPLETED,
                completed_at=indexed_at,
                clear_errors=True,
            )
            if updated_job is None:
                raise QdrantIndexingError(
                    self._safe_message("job_not_found"),
                    status_code=404,
                    code="job_not_found",
                )

            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_INDEXING_COMPLETED,
                user_id=admin_user_id,
                document_id=document.document_id,
                job_id=job.job_id,
                company_id=job.company_id,
                indexed_chunk_count=len(chunks),
            )
            return updated_job
        except QdrantIndexingError:
            raise
        except Exception:
            await self._mark_failure(
                job=job,
                document_id=document.document_id,
                admin_user_id=admin_user_id,
                error_code="indexing_failed",
                error_message=self._safe_message("indexing_failed"),
            )
            raise QdrantIndexingError(
                self._safe_message("indexing_failed"),
                status_code=500,
                code="indexing_failed",
            ) from None
