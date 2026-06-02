from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.services.pdf_parser_service import PdfParseError, PdfParserService

_USER_SAFE_MESSAGES = {
    "file_not_found": "Uploaded PDF file is not available.",
    "invalid_pdf": "Unable to parse PDF file.",
    "empty_document": "PDF contains no extractable text.",
    "parse_failed": "Document parsing failed.",
    "job_not_found": "Processing job not found.",
    "document_not_found": "Document not found.",
    "job_not_runnable": "Processing job is not ready for parsing.",
    "document_not_approved": "Only approved documents can be parsed.",
}


class DocumentParseError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "parse_failed"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class DocumentParseService:
    def __init__(
        self,
        document_repo: DocumentRepository,
        processing_job_repo: ProcessingJobRepository,
        *,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
        pdf_parser: PdfParserService | None = None,
    ):
        self._document_repo = document_repo
        self._processing_job_repo = processing_job_repo
        self._audit_repo = audit_repo
        self._settings = settings
        self._pdf_parser = pdf_parser or PdfParserService()

    def _safe_message(self, code: str) -> str:
        return _USER_SAFE_MESSAGES.get(code, _USER_SAFE_MESSAGES["parse_failed"])

    def _resolve_pdf_path(self, document: Document) -> Path:
        if not document.raw_storage_path:
            raise DocumentParseError(
                self._safe_message("file_not_found"),
                code="file_not_found",
            )
        file_name = document.file_name or "original.pdf"
        return Path(document.raw_storage_path) / file_name

    def _artifact_paths(self, document_id: str) -> tuple[Path, Path]:
        if self._settings is None:
            raise DocumentParseError("Storage settings are not configured.", status_code=500)
        pages_path = (
            Path(self._settings.storage.parsed_storage_path) / document_id / "pages.json"
        )
        page_map_path = (
            Path(self._settings.storage.page_map_storage_path) / document_id / "page_map.json"
        )
        return pages_path, page_map_path

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    async def _audit(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None,
        document_id: str,
        job_id: str,
        company_id: str,
        page_count: int | None = None,
        error_code: str | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        details: dict = {
            "document_id": document_id,
            "job_id": job_id,
            "company_id": company_id,
        }
        if page_count is not None:
            details["page_count"] = page_count
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
            stage=ProcessingJobStage.PARSE_FAILED,
            error_code=error_code,
            error_message=error_message,
            completed_at=datetime.now(UTC),
        )
        await self._document_repo.update_parse_result(
            document_id,
            parse_status=ProcessingStageStatus.FAILED,
            lifecycle_status=DocumentLifecycleStatus.PARSE_FAILED,
            searchable=False,
        )
        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_PARSE_FAILED,
            user_id=admin_user_id,
            document_id=document_id,
            job_id=job.job_id,
            company_id=job.company_id,
            error_code=error_code,
        )

    async def parse_document_for_processing_job(
        self,
        job_id: str,
        *,
        admin_user_id: str,
    ) -> ProcessingJob:
        job = await self._processing_job_repo.get_by_id(job_id)
        if job is None:
            raise DocumentParseError(
                self._safe_message("job_not_found"),
                status_code=404,
                code="job_not_found",
            )

        runnable_statuses = {ProcessingJobStatus.QUEUED.value, ProcessingJobStatus.RUNNING.value}
        runnable_stages = {
            ProcessingJobStage.PARSE_PENDING.value,
            ProcessingJobStage.PARSE_RUNNING.value,
        }
        if job.status not in runnable_statuses or job.stage not in runnable_stages:
            raise DocumentParseError(
                self._safe_message("job_not_runnable"),
                status_code=409,
                code="job_not_runnable",
            )

        document = await self._document_repo.get_by_id(job.document_id)
        if document is None:
            raise DocumentParseError(
                self._safe_message("document_not_found"),
                status_code=404,
                code="document_not_found",
            )

        if ApprovalStatus(document.approval_status) != ApprovalStatus.APPROVED:
            raise DocumentParseError(
                self._safe_message("document_not_approved"),
                status_code=409,
                code="document_not_approved",
            )

        if document.searchable:
            raise DocumentParseError(
                "Document is already searchable and cannot be parsed again.",
                status_code=409,
                code="parse_failed",
            )

        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_PARSE_STARTED,
            user_id=admin_user_id,
            document_id=document.document_id,
            job_id=job.job_id,
            company_id=job.company_id,
        )

        started_at = datetime.now(UTC)
        await self._processing_job_repo.update_job(
            job.job_id,
            status=ProcessingJobStatus.RUNNING,
            stage=ProcessingJobStage.PARSE_RUNNING,
            started_at=started_at,
            clear_errors=True,
            attempts=job.attempts + 1,
        )

        try:
            pdf_path = self._resolve_pdf_path(document)
            parse_result = self._pdf_parser.parse_pdf_file(pdf_path)
            pages_path, page_map_path = self._artifact_paths(document.document_id)

            pages_payload = self._pdf_parser.build_pages_payload(
                document.document_id,
                parse_result,
            )
            page_map_payload = self._pdf_parser.build_page_map_payload(
                document_id=document.document_id,
                file_name=document.file_name,
                file_hash=document.file_hash,
                result=parse_result,
            )
            self._write_json_atomic(pages_path, pages_payload)
            self._write_json_atomic(page_map_path, page_map_payload)

            parsed_at = datetime.now(UTC)
            await self._document_repo.update_parse_result(
                document.document_id,
                parse_status=ProcessingStageStatus.COMPLETED,
                lifecycle_status=DocumentLifecycleStatus.PARSED,
                page_count=parse_result["page_count"],
                parsed_pages_path=pages_path.as_posix(),
                page_map_path=page_map_path.as_posix(),
                parsed_at=parsed_at,
                searchable=False,
            )
            await self._document_repo.update_processing_stages(
                document.document_id,
                chunk_status=ProcessingStageStatus.PENDING,
                searchable=False,
            )
            updated_job = await self._processing_job_repo.update_job(
                job.job_id,
                status=ProcessingJobStatus.COMPLETED,
                stage=ProcessingJobStage.PARSE_COMPLETED,
                completed_at=parsed_at,
                clear_errors=True,
            )
            if updated_job is None:
                raise DocumentParseError(
                    self._safe_message("job_not_found"),
                    status_code=404,
                    code="job_not_found",
                )

            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_PARSE_COMPLETED,
                user_id=admin_user_id,
                document_id=document.document_id,
                job_id=job.job_id,
                company_id=job.company_id,
                page_count=parse_result["page_count"],
            )
            return updated_job
        except (PdfParseError, DocumentParseError) as exc:
            if isinstance(exc, DocumentParseError) and exc.code not in {
                "file_not_found",
                "invalid_pdf",
                "empty_document",
                "parse_failed",
            }:
                raise
            code = exc.code if isinstance(exc, PdfParseError) else exc.code
            message = (
                exc.message
                if isinstance(exc, PdfParseError)
                else self._safe_message(code)
            )
            await self._mark_failure(
                job=job,
                document_id=document.document_id,
                admin_user_id=admin_user_id,
                error_code=code,
                error_message=message,
            )
            raise DocumentParseError(message, status_code=400, code=code) from exc
        except Exception:
            await self._mark_failure(
                job=job,
                document_id=document.document_id,
                admin_user_id=admin_user_id,
                error_code="parse_failed",
                error_message=self._safe_message("parse_failed"),
            )
            raise DocumentParseError(
                self._safe_message("parse_failed"),
                status_code=500,
                code="parse_failed",
            ) from None
