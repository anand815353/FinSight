from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
)
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStage,
    ProcessingJobStatus,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.chunk_builder import build_chunks_from_pages

_USER_SAFE_MESSAGES = {
    "job_not_found": "Processing job not found.",
    "document_not_found": "Document not found.",
    "job_not_runnable": "Processing job is not ready for chunking.",
    "document_not_approved": "Only approved documents can be chunked.",
    "document_not_parsed": "Document must be parsed before chunking.",
    "parsed_pages_missing": "Parsed pages file is not available.",
    "parsed_pages_invalid": "Parsed pages file is invalid.",
    "no_chunks": "No chunkable text found in parsed pages.",
    "chunking_failed": "Document chunking failed.",
}


class DocumentChunkError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "chunking_failed"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class DocumentChunkService:
    def __init__(
        self,
        document_repo: DocumentRepository,
        document_chunk_repo: DocumentChunkRepository,
        processing_job_repo: ProcessingJobRepository,
        *,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
    ):
        self._document_repo = document_repo
        self._document_chunk_repo = document_chunk_repo
        self._processing_job_repo = processing_job_repo
        self._audit_repo = audit_repo
        self._settings = settings

    def _safe_message(self, code: str) -> str:
        return _USER_SAFE_MESSAGES.get(code, _USER_SAFE_MESSAGES["chunking_failed"])

    def _chunk_artifact_path(self, document_id: str) -> Path:
        if self._settings is None:
            raise DocumentChunkError("Storage settings are not configured.", status_code=500)
        return Path(self._settings.storage.chunk_storage_path) / document_id / "chunks.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _build_artifact_payload(self, chunks: list) -> dict[str, Any]:
        return {
            "document_id": chunks[0].document_id if chunks else None,
            "chunk_count": len(chunks),
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "page_numbers": chunk.page_numbers,
                    "char_count": chunk.char_count,
                    "word_count": chunk.word_count,
                    "text_preview": chunk.text_preview,
                    "chunk_type": chunk.chunk_type,
                }
                for chunk in chunks
            ],
        }

    async def _audit(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None,
        document_id: str,
        job_id: str,
        company_id: str,
        chunk_count: int | None = None,
        error_code: str | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        details: dict = {
            "document_id": document_id,
            "job_id": job_id,
            "company_id": company_id,
        }
        if chunk_count is not None:
            details["chunk_count"] = chunk_count
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
            stage=ProcessingJobStage.CHUNK_FAILED,
            error_code=error_code,
            error_message=error_message,
            completed_at=datetime.now(UTC),
        )
        await self._document_repo.update_chunk_result(
            document_id,
            chunk_status=ProcessingStageStatus.FAILED,
            lifecycle_status=DocumentLifecycleStatus.CHUNK_FAILED,
            searchable=False,
        )
        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_CHUNKING_FAILED,
            user_id=admin_user_id,
            document_id=document_id,
            job_id=job.job_id,
            company_id=job.company_id,
            error_code=error_code,
        )

    def _load_pages_payload(self, document: Document) -> dict[str, Any]:
        if not document.parsed_pages_path:
            raise DocumentChunkError(
                self._safe_message("parsed_pages_missing"),
                code="parsed_pages_missing",
            )
        pages_path = Path(document.parsed_pages_path)
        if not pages_path.is_file():
            raise DocumentChunkError(
                self._safe_message("parsed_pages_missing"),
                code="parsed_pages_missing",
            )
        try:
            return json.loads(pages_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise DocumentChunkError(
                self._safe_message("parsed_pages_invalid"),
                code="parsed_pages_invalid",
            ) from exc

    async def chunk_document_for_processing_job(
        self,
        job_id: str,
        *,
        admin_user_id: str,
    ) -> ProcessingJob:
        job = await self._processing_job_repo.get_by_id(job_id)
        if job is None:
            raise DocumentChunkError(
                self._safe_message("job_not_found"),
                status_code=404,
                code="job_not_found",
            )

        runnable_stages = {
            ProcessingJobStage.PARSE_COMPLETED.value,
            ProcessingJobStage.CHUNK_FAILED.value,
            ProcessingJobStage.CHUNK_COMPLETED.value,
            ProcessingJobStage.INDEX_PENDING.value,
            ProcessingJobStage.INDEX_FAILED.value,
        }
        runnable_statuses = {
            ProcessingJobStatus.QUEUED.value,
            ProcessingJobStatus.COMPLETED.value,
            ProcessingJobStatus.FAILED.value,
        }
        if job.stage not in runnable_stages or job.status not in runnable_statuses:
            raise DocumentChunkError(
                self._safe_message("job_not_runnable"),
                status_code=409,
                code="job_not_runnable",
            )

        document = await self._document_repo.get_by_id(job.document_id)
        if document is None:
            raise DocumentChunkError(
                self._safe_message("document_not_found"),
                status_code=404,
                code="document_not_found",
            )

        if ApprovalStatus(document.approval_status) != ApprovalStatus.APPROVED:
            raise DocumentChunkError(
                self._safe_message("document_not_approved"),
                status_code=409,
                code="document_not_approved",
            )

        if ProcessingStageStatus(document.parse_status) != ProcessingStageStatus.COMPLETED:
            raise DocumentChunkError(
                self._safe_message("document_not_parsed"),
                status_code=409,
                code="document_not_parsed",
            )

        if document.searchable:
            raise DocumentChunkError(
                "Document is already searchable and cannot be chunked again.",
                status_code=409,
                code="chunking_failed",
            )

        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_CHUNKING_STARTED,
            user_id=admin_user_id,
            document_id=document.document_id,
            job_id=job.job_id,
            company_id=job.company_id,
        )

        await self._processing_job_repo.update_job(
            job.job_id,
            status=ProcessingJobStatus.RUNNING,
            stage=ProcessingJobStage.CHUNK_RUNNING,
            started_at=datetime.now(UTC),
            clear_errors=True,
            attempts=job.attempts + 1,
        )

        try:
            pages_payload = self._load_pages_payload(document)
            chunk_creates = build_chunks_from_pages(document, pages_payload)
            await self._document_chunk_repo.delete_by_document_id(document.document_id)
            stored_chunks = await self._document_chunk_repo.create_many(chunk_creates)

            artifact_path = self._chunk_artifact_path(document.document_id)
            self._write_json_atomic(artifact_path, self._build_artifact_payload(stored_chunks))

            chunked_at = datetime.now(UTC)
            await self._document_repo.update_chunk_result(
                document.document_id,
                chunk_status=ProcessingStageStatus.COMPLETED,
                lifecycle_status=DocumentLifecycleStatus.INDEX_PENDING,
                chunk_count=len(stored_chunks),
                chunk_artifact_path=artifact_path.as_posix(),
                chunked_at=chunked_at,
                searchable=False,
            )
            await self._document_repo.update_index_status_only(
                document.document_id,
                index_status=ProcessingStageStatus.PENDING,
            )
            updated_job = await self._processing_job_repo.update_job(
                job.job_id,
                status=ProcessingJobStatus.QUEUED,
                stage=ProcessingJobStage.INDEX_PENDING,
                completed_at=chunked_at,
                clear_errors=True,
            )
            if updated_job is None:
                raise DocumentChunkError(
                    self._safe_message("job_not_found"),
                    status_code=404,
                    code="job_not_found",
                )

            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_CHUNKING_COMPLETED,
                user_id=admin_user_id,
                document_id=document.document_id,
                job_id=job.job_id,
                company_id=job.company_id,
                chunk_count=len(stored_chunks),
            )
            return updated_job
        except (DocumentChunkError, ValueError) as exc:
            if isinstance(exc, DocumentChunkError) and exc.code not in {
                "parsed_pages_missing",
                "parsed_pages_invalid",
                "no_chunks",
                "chunking_failed",
            }:
                raise
            code = "no_chunks" if isinstance(exc, ValueError) else exc.code
            message = (
                self._safe_message("no_chunks")
                if isinstance(exc, ValueError)
                else exc.message
            )
            await self._mark_failure(
                job=job,
                document_id=document.document_id,
                admin_user_id=admin_user_id,
                error_code=code,
                error_message=message,
            )
            raise DocumentChunkError(message, status_code=400, code=code) from exc
        except Exception:
            await self._mark_failure(
                job=job,
                document_id=document.document_id,
                admin_user_id=admin_user_id,
                error_code="chunking_failed",
                error_message=self._safe_message("chunking_failed"),
            )
            raise DocumentChunkError(
                self._safe_message("chunking_failed"),
                status_code=500,
                code="chunking_failed",
            ) from None
