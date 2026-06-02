from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.document import (
    MVP_DOCUMENT_TYPES,
    ApprovalStatus,
    Document,
    DocumentLifecycleStatus,
    DocumentReadinessStatus,
    ProcessingStageStatus,
    validate_mvp_document_type,
    validate_source_type,
)
from app.models.document_chunk import DocumentChunk
from app.models.document_validation import (
    DocumentReadinessValidation,
    DocumentReadinessValidationCreate,
    ReadinessCheckOutcome,
    ValidationStatus,
)
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.document_validation_repo import DocumentValidationRepository
from app.repositories.audit_repo import AuditRepository
from app.services.qdrant_indexing_service import QdrantIndexingService


class DocumentReadinessError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "readiness_failed"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class DocumentReadinessService:
    @staticmethod
    def _resolve_raw_file_path(document: Document) -> Path | None:
        if not document.raw_storage_path:
            return None
        base = Path(document.raw_storage_path)
        if base.is_file():
            return base
        if document.file_name:
            candidate = base / document.file_name
            if candidate.is_file():
                return candidate
        if base.is_dir():
            for name in (document.file_name, "original.pdf"):
                if not name:
                    continue
                candidate = base / name
                if candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _check_chunk_index_status(
        chunk: DocumentChunk,
        outcome: ReadinessCheckOutcome,
        prefix: str,
    ) -> bool:
        if chunk.index_status is None:
            outcome.add_fatal(f"{prefix}_index_status_missing")
            return False
        try:
            index_status = ProcessingStageStatus(chunk.index_status)
        except ValueError:
            outcome.add_fatal(f"{prefix}_index_status_invalid")
            return False
        if index_status != ProcessingStageStatus.COMPLETED:
            outcome.add_fatal(f"{prefix}_index_status_not_completed")
            return False
        return True

    def __init__(
        self,
        document_repo: DocumentRepository,
        document_chunk_repo: DocumentChunkRepository,
        company_repo: CompanyRepository,
        document_validation_repo: DocumentValidationRepository,
        *,
        audit_repo: AuditRepository | None = None,
        settings: Settings | None = None,
    ):
        self._document_repo = document_repo
        self._document_chunk_repo = document_chunk_repo
        self._company_repo = company_repo
        self._document_validation_repo = document_validation_repo
        self._audit_repo = audit_repo
        self._settings = settings

    async def _audit(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None,
        document_id: str,
        company_id: str,
        details: dict | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.log_event(
            event_type=event_type,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
            details={
                "document_id": document_id,
                "company_id": company_id,
                **(details or {}),
            },
        )

    def _run_checks(
        self,
        document: Document,
        *,
        chunks: list,
        expected_collection: str,
    ) -> ReadinessCheckOutcome:
        outcome = ReadinessCheckOutcome()

        doc_type = str(document.document_type)
        if doc_type not in MVP_DOCUMENT_TYPES:
            outcome.add_fatal("unsupported_document_type", doc_type)
        else:
            try:
                validate_mvp_document_type(doc_type)
                outcome.add_pass("document_type_supported")
            except ValueError as exc:
                outcome.add_fatal("unsupported_document_type", str(exc))

        if not document.source_type:
            outcome.add_fatal("source_type_missing")
        else:
            try:
                validate_source_type(document.source_type)
                outcome.add_pass("source_type_valid")
            except ValueError as exc:
                outcome.add_fatal("invalid_source_type", str(exc))

        approval = ApprovalStatus(document.approval_status)
        if approval == ApprovalStatus.REJECTED:
            outcome.add_fatal("approval_rejected")
        elif approval != ApprovalStatus.APPROVED:
            outcome.add_fatal("approval_not_approved")
        else:
            outcome.add_pass("approval_approved")

        if not document.raw_storage_path:
            outcome.add_fatal("raw_storage_missing")
        elif self._resolve_raw_file_path(document) is None:
            outcome.add_fatal("raw_file_missing")
        else:
            outcome.add_pass("raw_storage_present")

        if not document.file_hash:
            outcome.add_fatal("file_hash_missing")
        else:
            outcome.add_pass("file_hash_present")

        if ProcessingStageStatus(document.parse_status) != ProcessingStageStatus.COMPLETED:
            outcome.add_fatal("parse_not_completed")
        else:
            outcome.add_pass("parse_completed")

        if document.page_count is None or document.page_count <= 0:
            outcome.add_fatal("page_count_invalid")
        else:
            outcome.add_pass("page_count_valid")

        if not document.parsed_pages_path:
            outcome.add_fatal("parsed_pages_missing")
        elif not Path(document.parsed_pages_path).is_file():
            outcome.add_fatal("parsed_pages_file_missing")
        else:
            outcome.add_pass("parsed_pages_present")

        if not document.page_map_path:
            outcome.add_fatal("page_map_missing")
        elif not Path(document.page_map_path).is_file():
            outcome.add_fatal("page_map_file_missing")
        else:
            outcome.add_pass("page_map_present")

        if ProcessingStageStatus(document.chunk_status) != ProcessingStageStatus.COMPLETED:
            outcome.add_fatal("chunk_not_completed")
        else:
            outcome.add_pass("chunk_completed")

        if document.chunk_count is None or document.chunk_count <= 0:
            outcome.add_fatal("chunk_count_invalid")
        else:
            outcome.add_pass("chunk_count_valid")

        if not chunks:
            outcome.add_fatal("chunks_missing")
        elif len(chunks) != document.chunk_count:
            outcome.add_fatal(
                "chunk_count_mismatch",
                f"expected {document.chunk_count}, found {len(chunks)}",
            )
        else:
            outcome.add_pass("chunks_present")

        if document.chunk_artifact_path and not Path(document.chunk_artifact_path).is_file():
            outcome.add_fatal("chunk_artifact_missing")
        elif document.chunk_artifact_path:
            outcome.add_pass("chunk_artifact_present")

        if ProcessingStageStatus(document.index_status) != ProcessingStageStatus.COMPLETED:
            outcome.add_fatal("index_not_completed")
        else:
            outcome.add_pass("index_completed")

        if document.indexed_chunk_count is None:
            outcome.add_fatal("indexed_chunk_count_missing")
        elif document.indexed_chunk_count != len(chunks):
            outcome.add_fatal(
                "indexed_chunk_count_mismatch",
                f"expected {len(chunks)}, got {document.indexed_chunk_count}",
            )
        else:
            outcome.add_pass("indexed_chunk_count_valid")

        if chunks:
            chunk_ok = True
            for chunk in chunks:
                prefix = f"chunk_{chunk.chunk_index}"
                if chunk.document_id != document.document_id:
                    outcome.add_fatal(f"{prefix}_document_id_mismatch")
                    chunk_ok = False
                if chunk.company_id != document.company_id:
                    outcome.add_fatal(f"{prefix}_company_id_mismatch")
                    chunk_ok = False
                if not chunk.document_type:
                    outcome.add_fatal(f"{prefix}_document_type_missing")
                    chunk_ok = False
                if chunk.page_start < 1 or chunk.page_end < chunk.page_start:
                    outcome.add_fatal(f"{prefix}_page_range_invalid")
                    chunk_ok = False
                if not chunk.page_numbers:
                    outcome.add_fatal(f"{prefix}_page_numbers_missing")
                    chunk_ok = False
                if not chunk.text or not chunk.text.strip():
                    outcome.add_fatal(f"{prefix}_text_missing")
                    chunk_ok = False
                if not chunk.text_preview or not chunk.text_preview.strip():
                    outcome.add_fatal(f"{prefix}_text_preview_missing")
                    chunk_ok = False
                if not chunk.source_document_title or not chunk.source_document_title.strip():
                    outcome.add_fatal(f"{prefix}_source_document_title_missing")
                    chunk_ok = False
                if not chunk.qdrant_collection:
                    outcome.add_fatal(f"{prefix}_qdrant_collection_missing")
                    chunk_ok = False
                elif chunk.qdrant_collection != expected_collection:
                    outcome.add_fatal(f"{prefix}_collection_mismatch")
                    chunk_ok = False
                if not chunk.qdrant_point_id:
                    outcome.add_fatal(f"{prefix}_qdrant_point_id_missing")
                    chunk_ok = False
                elif chunk.qdrant_point_id != QdrantIndexingService.point_id_for_chunk(
                    chunk.chunk_id
                ):
                    outcome.add_fatal(f"{prefix}_invalid_point_id")
                    chunk_ok = False
                if not chunk.embedding_provider:
                    outcome.add_fatal(f"{prefix}_embedding_provider_missing")
                    chunk_ok = False
                if not chunk.embedding_model:
                    outcome.add_fatal(f"{prefix}_embedding_model_missing")
                    chunk_ok = False
                if not self._check_chunk_index_status(chunk, outcome, prefix):
                    chunk_ok = False
                if not chunk.section_title:
                    outcome.add_warning(f"{prefix}_missing_section_title")
                if not chunk.source_url:
                    outcome.add_warning(f"{prefix}_missing_source_url")
            if chunk_ok:
                outcome.add_pass("chunk_citation_metadata_valid")

        return outcome

    async def validate_document_readiness(
        self,
        document_id: str,
        *,
        admin_user_id: str,
    ) -> DocumentReadinessValidation:
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise DocumentReadinessError(
                "Document not found.",
                status_code=404,
                code="document_not_found",
            )

        company = await self._company_repo.get_by_id(document.company_id)
        if company is None:
            raise DocumentReadinessError(
                "Company not found.",
                status_code=404,
                code="company_not_found",
            )

        await self._audit(
            AuditEventType.ADMIN_DOCUMENT_READINESS_VALIDATION_STARTED,
            user_id=admin_user_id,
            document_id=document.document_id,
            company_id=document.company_id,
        )

        expected_collection = (
            self._settings.embedding.collection_name
            if self._settings is not None
            else "finsight_chunks_hf_minilm_l6_v2"
        )
        chunks = await self._document_chunk_repo.list_by_document_id(document_id)
        outcome = self._run_checks(
            document,
            chunks=chunks,
            expected_collection=expected_collection,
        )

        validated_at = datetime.now(UTC)
        status = ValidationStatus.PASSED if outcome.passed else ValidationStatus.FAILED
        validation = await self._document_validation_repo.create(
            DocumentReadinessValidationCreate(
                document_id=document.document_id,
                company_id=document.company_id,
                status=status,
                fatal_errors=outcome.fatal_errors,
                warnings=outcome.warnings,
                checks=outcome.checks,
                validated_by=admin_user_id,
                validated_at=validated_at,
                searchability_enabled=False,
            )
        )

        if outcome.passed:
            await self._enable_searchability(
                document,
                validation_id=validation.validation_id,
                validated_at=validated_at,
                admin_user_id=admin_user_id,
            )
            validation = validation.model_copy(update={"searchability_enabled": True})
            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_READINESS_VALIDATION_PASSED,
                user_id=admin_user_id,
                document_id=document.document_id,
                company_id=document.company_id,
                details=outcome.to_audit_summary(),
            )
            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_SEARCHABILITY_ENABLED,
                user_id=admin_user_id,
                document_id=document.document_id,
                company_id=document.company_id,
                details={"validation_id": validation.validation_id},
            )
        else:
            await self._document_repo.update_readiness_result(
                document.document_id,
                readiness_status=DocumentReadinessStatus.FAILED,
                readiness_validation_id=validation.validation_id,
                readiness_validated_at=validated_at,
                searchable=False,
            )
            await self._audit(
                AuditEventType.ADMIN_DOCUMENT_READINESS_VALIDATION_FAILED,
                user_id=admin_user_id,
                document_id=document.document_id,
                company_id=document.company_id,
                details=outcome.to_audit_summary(),
            )

        return validation

    async def _enable_searchability(
        self,
        document: Document,
        *,
        validation_id: str,
        validated_at: datetime,
        admin_user_id: str,
    ) -> None:
        await self._document_repo.update_searchable(
            document.document_id,
            searchable=True,
            approval_status=ApprovalStatus.APPROVED,
            lifecycle_status=DocumentLifecycleStatus.READY,
            index_status=ProcessingStageStatus.COMPLETED,
        )
        await self._document_repo.update_readiness_result(
            document.document_id,
            readiness_status=DocumentReadinessStatus.PASSED,
            readiness_validation_id=validation_id,
            readiness_validated_at=validated_at,
            searchable_at=validated_at,
            searchable_by=admin_user_id,
        )

    async def get_latest_validation(
        self, document_id: str
    ) -> DocumentReadinessValidation | None:
        return await self._document_validation_repo.get_latest_by_document_id(document_id)
