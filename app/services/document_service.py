from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from app.core.config import Settings
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.document import (
    ApprovalStatus,
    Document,
    DocumentCreate,
    DocumentLifecycleStatus,
    ProcessingStageStatus,
    compute_searchable,
    validate_mvp_document_type,
)
from app.models.source_registry import SourceType
from app.repositories.audit_repo import AuditRepository
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_repo import DocumentAlreadyExistsError, DocumentRepository

PDF_MAGIC = b"%PDF"
ALLOWED_EXTENSIONS = {".pdf"}
_SAFE_STORAGE_COMPANY_ID = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_SAFE_STORAGE_DOCUMENT_ID = re.compile(r"^[0-9a-f-]{36}$")
_DUPLICATE_FILE_MESSAGE = "This file appears to have already been registered."


def calculate_sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class DocumentRegistrationError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DocumentApprovalError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def safe_storage_company_id(company_id: str) -> str:
    normalized = company_id.strip()
    if not _SAFE_STORAGE_COMPANY_ID.fullmatch(normalized):
        raise DocumentRegistrationError("Invalid company_id for file storage.")
    return normalized


def safe_storage_document_id(document_id: str) -> str:
    normalized = document_id.strip()
    if not _SAFE_STORAGE_DOCUMENT_ID.fullmatch(normalized):
        raise DocumentRegistrationError("Invalid document_id for file storage.")
    return normalized


def resolve_pending_upload_dir(base_path: Path, company_id: str, document_id: str) -> Path:
    safe_company = safe_storage_company_id(company_id)
    safe_document = safe_storage_document_id(document_id)
    base_resolved = base_path.resolve()
    storage_dir = (base_resolved / safe_company / safe_document).resolve()
    try:
        storage_dir.relative_to(base_resolved)
    except ValueError as exc:
        raise DocumentRegistrationError("Invalid storage path.") from exc
    return storage_dir


class DocumentService:
    def __init__(
        self,
        document_repo: DocumentRepository,
        *,
        company_repo: CompanyRepository | None = None,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
    ):
        self._document_repo = document_repo
        self._company_repo = company_repo
        self._audit_repo = audit_repo
        self._settings = settings

    async def create_document(self, payload: DocumentCreate) -> Document:
        try:
            validate_mvp_document_type(payload.document_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        safe_payload = payload.model_copy(update={"searchable": False})
        try:
            return await self._document_repo.create(safe_payload)
        except DocumentAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def get_document(self, document_id: str) -> Document | None:
        return await self._document_repo.get_by_id(document_id)

    async def list_company_documents(self, company_id: str, *, admin_view: bool = False) -> list[Document]:
        return await self._document_repo.list_by_company(company_id, admin_view=admin_view)

    async def list_all_documents(self, *, limit: int = 100) -> list[Document]:
        return await self._document_repo.list_all(limit=limit)

    async def list_pending_approval_documents(self, *, limit: int = 200) -> list[Document]:
        return await self._document_repo.list_by_approval_status(
            ApprovalStatus.PENDING, limit=limit
        )

    async def _log_approval_failed(
        self,
        *,
        user_id: str | None,
        reason: str,
        document_id: str | None,
        company_id: str | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.log_event(
            event_type=AuditEventType.ADMIN_DOCUMENT_APPROVAL_FAILED,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
            details={
                "reason": reason,
                "document_id": document_id,
                "company_id": company_id,
            },
        )

    def _validate_document_for_approval(self, document: Document) -> None:
        try:
            validate_mvp_document_type(str(document.document_type))
        except ValueError as exc:
            raise DocumentApprovalError(str(exc), status_code=422) from exc

        if document.raw_storage_path:
            if not document.file_name or not document.mime_type or document.file_size_bytes is None:
                raise DocumentApprovalError(
                    "Uploaded document is missing required file metadata.",
                    status_code=400,
                )
            file_path = Path(document.raw_storage_path) / document.file_name
            if self._settings is not None and not file_path.is_file():
                raise DocumentApprovalError(
                    "Uploaded document file is not available on disk.",
                    status_code=400,
                )

    async def _get_document_for_approval_action(self, document_id: str) -> Document:
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise DocumentApprovalError("Document not found.", status_code=404)
        self._validate_document_for_approval(document)
        return document

    async def approve_document(
        self,
        document_id: str,
        *,
        admin_user_id: str,
        review_notes: str | None = None,
    ) -> Document:
        document = await self._get_document_for_approval_action(document_id)
        status = ApprovalStatus(document.approval_status)

        if status == ApprovalStatus.APPROVED:
            raise DocumentApprovalError("Document is already approved.", status_code=409)
        if status == ApprovalStatus.REJECTED:
            await self._log_approval_failed(
                user_id=admin_user_id,
                reason="Cannot approve a rejected document. Mark it pending first.",
                document_id=document_id,
                company_id=document.company_id,
            )
            raise DocumentApprovalError(
                "Cannot approve a rejected document. Mark it pending first.",
                status_code=409,
            )
        if status != ApprovalStatus.PENDING:
            await self._log_approval_failed(
                user_id=admin_user_id,
                reason=f"Document is not pending approval (status={status}).",
                document_id=document_id,
                company_id=document.company_id,
            )
            raise DocumentApprovalError(
                "Only pending documents can be approved.",
                status_code=409,
            )

        now = datetime.now(UTC)
        notes = review_notes.strip() if review_notes and review_notes.strip() else None
        updated = await self._document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.APPROVED,
            searchable=False,
            approved_by=admin_user_id,
            approved_at=now,
            review_notes=notes,
            clear_rejected=True,
        )
        if updated is None:
            raise DocumentApprovalError("Document not found.", status_code=404)

        if self._audit_repo is not None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_APPROVED,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": updated.document_id,
                    "company_id": updated.company_id,
                    "document_type": updated.document_type,
                },
            )
        return updated

    async def reject_document(
        self,
        document_id: str,
        *,
        admin_user_id: str,
        rejection_reason: str,
        review_notes: str | None = None,
    ) -> Document:
        reason = rejection_reason.strip() if rejection_reason else ""
        if not reason:
            raise DocumentApprovalError("Rejection reason is required.", status_code=400)

        document = await self._get_document_for_approval_action(document_id)
        status = ApprovalStatus(document.approval_status)

        if status == ApprovalStatus.REJECTED:
            raise DocumentApprovalError("Document is already rejected.", status_code=409)
        if status == ApprovalStatus.APPROVED:
            await self._log_approval_failed(
                user_id=admin_user_id,
                reason="Cannot reject an approved document.",
                document_id=document_id,
                company_id=document.company_id,
            )
            raise DocumentApprovalError("Cannot reject an approved document.", status_code=409)
        if status != ApprovalStatus.PENDING:
            await self._log_approval_failed(
                user_id=admin_user_id,
                reason=f"Document is not pending approval (status={status}).",
                document_id=document_id,
                company_id=document.company_id,
            )
            raise DocumentApprovalError(
                "Only pending documents can be rejected.",
                status_code=409,
            )

        now = datetime.now(UTC)
        notes = review_notes.strip() if review_notes and review_notes.strip() else None
        updated = await self._document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.REJECTED,
            lifecycle_status=DocumentLifecycleStatus.REJECTED,
            searchable=False,
            rejected_by=admin_user_id,
            rejected_at=now,
            rejection_reason=reason,
            review_notes=notes,
            clear_approved=True,
        )
        if updated is None:
            raise DocumentApprovalError("Document not found.", status_code=404)

        if self._audit_repo is not None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_REJECTED,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": updated.document_id,
                    "company_id": updated.company_id,
                    "document_type": updated.document_type,
                },
            )
        return updated

    async def mark_document_pending(
        self,
        document_id: str,
        *,
        admin_user_id: str,
        review_notes: str | None = None,
    ) -> Document:
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise DocumentApprovalError("Document not found.", status_code=404)

        status = ApprovalStatus(document.approval_status)
        if status != ApprovalStatus.REJECTED:
            raise DocumentApprovalError(
                "Only rejected documents can be marked pending.",
                status_code=409,
            )

        notes = review_notes.strip() if review_notes and review_notes.strip() else None
        updated = await self._document_repo.update_approval_review(
            document_id,
            approval_status=ApprovalStatus.PENDING,
            lifecycle_status=DocumentLifecycleStatus.APPROVAL_PENDING,
            searchable=False,
            review_notes=notes,
            clear_approved=True,
            clear_rejected=True,
        )
        if updated is None:
            raise DocumentApprovalError("Document not found.", status_code=404)

        if self._audit_repo is not None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_MARKED_PENDING,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": updated.document_id,
                    "company_id": updated.company_id,
                },
            )
        return updated

    async def apply_searchable_update(
        self,
        document: Document,
        *,
        requested_searchable: bool,
        approval_status: ApprovalStatus | None = None,
        lifecycle_status: DocumentLifecycleStatus | None = None,
        index_status: ProcessingStageStatus | None = None,
    ) -> Document:
        approval = approval_status or ApprovalStatus(document.approval_status)
        lifecycle = lifecycle_status or DocumentLifecycleStatus(document.lifecycle_status)
        index = index_status or ProcessingStageStatus(document.index_status)
        searchable = compute_searchable(
            searchable_flag=requested_searchable,
            approval_status=approval,
            lifecycle_status=lifecycle,
            index_status=index,
        )
        updated = await self._document_repo.update_searchable(
            document.document_id,
            searchable=searchable,
            approval_status=approval,
            lifecycle_status=lifecycle,
            index_status=index,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return updated

    def _validate_source_type(self, source_type: str | None) -> str | None:
        if source_type is None or not source_type.strip():
            return None
        normalized = source_type.strip().lower()
        allowed = {member.value for member in SourceType}
        if normalized not in allowed:
            raise DocumentRegistrationError(
                f"Source type '{source_type}' is not allowed. "
                f"Allowed: {', '.join(sorted(allowed))}."
            )
        return normalized

    def _validate_upload_file(self, upload: UploadFile, content: bytes) -> tuple[str, int]:
        if self._settings is None:
            raise DocumentRegistrationError("Storage settings are not configured.")
        if not content:
            raise DocumentRegistrationError("Uploaded file is empty.")
        max_bytes = self._settings.storage.max_upload_bytes
        if len(content) > max_bytes:
            raise DocumentRegistrationError(
                f"File exceeds maximum upload size of {max_bytes} bytes.",
                status_code=413,
            )
        filename = (upload.filename or "").strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise DocumentRegistrationError("Only PDF files are allowed.")
        if not content.startswith(PDF_MAGIC):
            raise DocumentRegistrationError("Uploaded file is not a valid PDF.")
        content_type = (upload.content_type or "application/pdf").split(";")[0].strip().lower()
        allowed_mimes = {mime.lower() for mime in self._settings.storage.allowed_upload_mime_types}
        if content_type not in allowed_mimes:
            raise DocumentRegistrationError("Uploaded file MIME type is not allowed.")
        return content_type, len(content)

    async def _log_registration_failed(self, *, user_id: str | None, reason: str, company_id: str | None) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.log_event(
            event_type=AuditEventType.ADMIN_DOCUMENT_REGISTRATION_FAILED,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
            details={"reason": reason, "company_id": company_id},
        )

    async def register_document_with_upload(
        self,
        *,
        payload: DocumentCreate,
        upload: UploadFile,
        admin_user_id: str | None = None,
    ) -> Document:
        if self._settings is None or self._company_repo is None:
            raise DocumentRegistrationError("Document registration is not fully configured.")

        try:
            validate_mvp_document_type(payload.document_type)
        except ValueError as exc:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason=str(exc),
                company_id=payload.company_id,
            )
            raise DocumentRegistrationError(str(exc), status_code=422) from exc

        try:
            source_type = self._validate_source_type(payload.source_type)
        except DocumentRegistrationError as exc:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason=exc.message,
                company_id=payload.company_id,
            )
            raise

        company = await self._company_repo.get_by_id(payload.company_id)
        if company is None:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason="Company not found.",
                company_id=payload.company_id,
            )
            raise DocumentRegistrationError("Company not found.", status_code=404)

        content = await upload.read()
        try:
            mime_type, file_size = self._validate_upload_file(upload, content)
        except DocumentRegistrationError as exc:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason=exc.message,
                company_id=payload.company_id,
            )
            raise

        file_hash = calculate_sha256_bytes(content)
        existing = await self._document_repo.get_by_file_hash(file_hash)
        if existing is not None:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason="duplicate_file_hash",
                company_id=payload.company_id,
            )
            raise DocumentRegistrationError(_DUPLICATE_FILE_MESSAGE, status_code=409)

        document_id = str(uuid4())
        base_path = Path(self._settings.storage.admin_pending_upload_path)
        try:
            storage_dir = resolve_pending_upload_dir(base_path, payload.company_id, document_id)
        except DocumentRegistrationError as exc:
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason=exc.message,
                company_id=payload.company_id,
            )
            raise
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_path = storage_dir / "original.pdf"
        file_path.write_bytes(content)

        raw_storage_path = f"{storage_dir.as_posix()}/"
        create_payload = payload.model_copy(
            update={
                "source_type": source_type,
                "searchable": False,
                "file_name": "original.pdf",
                "file_hash": file_hash,
                "file_size_bytes": file_size,
                "mime_type": mime_type,
                "raw_storage_path": raw_storage_path,
            }
        )

        try:
            document = await self._document_repo.create(create_payload, document_id=document_id)
        except DocumentAlreadyExistsError as exc:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            await self._log_registration_failed(
                user_id=admin_user_id,
                reason="duplicate_file_hash",
                company_id=payload.company_id,
            )
            raise DocumentRegistrationError(_DUPLICATE_FILE_MESSAGE, status_code=409) from exc

        if self._audit_repo is not None:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_REGISTERED,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": document.document_id,
                    "company_id": document.company_id,
                    "document_type": document.document_type,
                },
            )
            await self._audit_repo.log_event(
                event_type=AuditEventType.ADMIN_DOCUMENT_UPLOADED,
                user_id=admin_user_id,
                request_id=request_id_ctx_var.get(),
                details={
                    "document_id": document.document_id,
                    "file_size_bytes": file_size,
                    "mime_type": mime_type,
                },
            )

        return document
