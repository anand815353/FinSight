from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
    validate_mvp_document_type,
)
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobCreate,
    ProcessingJobStatus,
    ProcessingJobStage,
    ProcessingJobType,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.processing_job_repo import ProcessingJobRepository

_DUPLICATE_ACTIVE_JOB_MESSAGE = "A processing job is already active for this document."


class ProcessingJobError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProcessingJobService:
    def __init__(
        self,
        processing_job_repo: ProcessingJobRepository,
        document_repo: DocumentRepository,
        *,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
    ):
        self._processing_job_repo = processing_job_repo
        self._document_repo = document_repo
        self._audit_repo = audit_repo
        self._settings = settings

    async def _log_duplicate_blocked(
        self,
        *,
        user_id: str | None,
        document_id: str,
        company_id: str | None,
    ) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.log_event(
            event_type=AuditEventType.ADMIN_DOCUMENT_PROCESSING_DUPLICATE_BLOCKED,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
            details={"document_id": document_id, "company_id": company_id},
        )

    def _validate_document_file_on_disk(self, document: Document) -> None:
        if self._settings is None or not document.raw_storage_path or not document.file_name:
            return
        file_path = Path(document.raw_storage_path) / document.file_name
        if not file_path.is_file():
            raise ProcessingJobError(
                "Uploaded document file is not available on disk.",
                status_code=400,
            )

    async def _get_eligible_document(self, document_id: str) -> Document:
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise ProcessingJobError("Document not found.", status_code=404)

        if ApprovalStatus(document.approval_status) != ApprovalStatus.APPROVED:
            raise ProcessingJobError(
                "Only approved documents can be submitted for processing.",
                status_code=409,
            )

        if not document.file_hash:
            raise ProcessingJobError(
                "Document is missing file hash and cannot be processed.",
                status_code=400,
            )

        if not document.raw_storage_path:
            raise ProcessingJobError(
                "Document is missing uploaded file storage path.",
                status_code=400,
            )

        try:
            validate_mvp_document_type(str(document.document_type))
        except ValueError as exc:
            raise ProcessingJobError(str(exc), status_code=422) from exc

        if document.searchable:
            raise ProcessingJobError(
                "Document is already searchable and cannot be queued for processing.",
                status_code=409,
            )

        self._validate_document_file_on_disk(document)
        return document

    async def list_documents_eligible_for_processing(self, *, limit: int = 200) -> list[Document]:
        approved = await self._document_repo.list_by_approval_status(
            ApprovalStatus.APPROVED, limit=limit
        )
        eligible: list[Document] = []
        for document in approved:
            if document.searchable:
                continue
            if not document.file_hash or not document.raw_storage_path:
                continue
            try:
                validate_mvp_document_type(str(document.document_type))
            except ValueError:
                continue
            active = await self._processing_job_repo.find_active_job_by_document_id(
                document.document_id
            )
            if active is not None:
                continue
            eligible.append(document)
        return eligible

    async def can_request_processing(self, document_id: str) -> bool:
        try:
            await self._get_eligible_document(document_id)
        except ProcessingJobError:
            return False
        active = await self._processing_job_repo.find_active_job_by_document_id(document_id)
        return active is None

    async def request_document_processing(
        self,
        document_id: str,
        *,
        admin_user_id: str,
    ) -> ProcessingJob:
        document = await self._get_eligible_document(document_id)

        active = await self._processing_job_repo.find_active_job_by_document_id(document_id)
        if active is not None:
            await self._log_duplicate_blocked(
                user_id=admin_user_id,
                document_id=document_id,
                company_id=document.company_id,
            )
            raise ProcessingJobError(_DUPLICATE_ACTIVE_JOB_MESSAGE, status_code=409)

        payload = ProcessingJobCreate(
            document_id=document.document_id,
            company_id=document.company_id,
            requested_by=admin_user_id,
            job_type=ProcessingJobType.DOCUMENT_PROCESSING,
            status=ProcessingJobStatus.QUEUED,
            stage=ProcessingJobStage.PARSE_PENDING,
        )
        job = await self._processing_job_repo.create(payload)

        updated = await self._document_repo.update_processing_stages(
            document.document_id,
            parse_status=ProcessingStageStatus.PENDING,
            lifecycle_status=DocumentLifecycleStatus.PARSE_PENDING,
            searchable=False,
        )
        if updated is None:
            raise ProcessingJobError("Document not found.", status_code=404)

        if self._audit_repo is not None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_PROCESSING_REQUESTED,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": document.document_id,
                    "company_id": document.company_id,
                    "job_id": job.job_id,
                },
            )

        return job

    async def get_processing_job(self, job_id: str) -> ProcessingJob:
        job = await self._processing_job_repo.get_by_id(job_id)
        if job is None:
            raise ProcessingJobError("Processing job not found.", status_code=404)
        return job

    async def list_processing_jobs(
        self,
        *,
        status: ProcessingJobStatus | None = None,
        document_id: str | None = None,
        limit: int = 200,
    ) -> list[ProcessingJob]:
        return await self._processing_job_repo.list_jobs(
            status=status,
            document_id=document_id,
            limit=limit,
        )
