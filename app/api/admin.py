from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi import status as http_status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.permissions import require_admin
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import (
    ApprovalStatus,
    DocumentCreate,
    MVP_DOCUMENT_TYPES,
    ProcessingStageStatus,
    validate_mvp_document_type,
)
from app.models.source_registry import SourceType
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.company_service import CompanyService, slugify_company_id
from app.services.document_service import (
    DocumentApprovalError,
    DocumentRegistrationError,
    DocumentService,
)
from app.models.processing_job import ProcessingJobStage, ProcessingJobStatus
from app.services.document_chunk_service import DocumentChunkError, DocumentChunkService
from app.services.document_parse_service import DocumentParseError, DocumentParseService
from app.services.processing_job_service import ProcessingJobError, ProcessingJobService
from app.services.document_readiness_service import DocumentReadinessError, DocumentReadinessService
from app.models.retrieval import RetrievalFilters, RetrievalRequest
from app.services.qdrant_indexing_service import QdrantIndexingError, QdrantIndexingService
from app.services.retrieval_service import RetrievalService

router = APIRouter(tags=["admin"])

_CSRF_ERROR = "Session expired. Please try again."


def _templates(request: Request):
    return request.app.state.templates


def _auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def _admin_form_response(
    request: Request,
    template_name: str,
    context: dict,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> HTMLResponse:
    auth_service = _auth_service(request)
    csrf_token = auth_service.prepare_form_csrf()
    response = _templates(request).TemplateResponse(
        request=request,
        name=template_name,
        context={**context, "csrf_token": csrf_token},
        status_code=status_code,
    )
    auth_service.attach_form_csrf(response, csrf_token)
    return response


def _document_type_choices() -> list[tuple[str, str]]:
    labels = {
        "annual_report": "Annual Report",
        "financial_result": "Financial Result",
        "investor_presentation": "Investor Presentation",
    }
    return [(value, labels.get(value, value)) for value in sorted(MVP_DOCUMENT_TYPES)]


def _source_type_choices() -> list[tuple[str, str]]:
    labels = {
        "company_investor_relations": "Company Investor Relations",
        "nse_corporate_filings": "NSE Corporate Filings",
        "bse_corporate_filings": "BSE Corporate Filings",
        "admin_uploaded_official_source_document": "Admin Uploaded Official Source",
    }
    return [(member.value, labels.get(member.value, member.value)) for member in SourceType]


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    return _admin_form_response(
        request,
        "admin/index.html",
        {"title": "Admin Dashboard", "current_user": current_admin},
    )


@router.get("/admin/companies", response_class=HTMLResponse)
async def admin_companies_list(
    request: Request,
    current_admin: User = Depends(require_admin),
    q: str | None = Query(default=None),
):
    companies = await request.app.state.company_service.list_companies_raw(q)
    return _admin_form_response(
        request,
        "admin/companies.html",
        {
            "title": "Companies",
            "current_user": current_admin,
            "companies": companies,
            "query": q or "",
        },
    )


@router.get("/admin/companies/partial", response_class=HTMLResponse)
async def admin_companies_partial(
    request: Request,
    _current_admin: User = Depends(require_admin),
    q: str | None = Query(default=None),
):
    companies = await request.app.state.company_service.list_companies_raw(q)
    return _templates(request).TemplateResponse(
        request=request,
        name="partials/company_rows.html",
        context={"companies": companies},
    )


@router.get("/admin/companies/new", response_class=HTMLResponse)
async def admin_company_new_form(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    return _admin_form_response(
        request,
        "admin/company_form.html",
        {
            "title": "Create Company",
            "current_user": current_admin,
            "errors": [],
            "form": {},
        },
    )


@router.post("/admin/companies")
async def admin_company_create(
    request: Request,
    current_admin: User = Depends(require_admin),
    company_id: str = Form(default=""),
    name: str = Form(...),
    display_name: str = Form(...),
    nse_symbol: str = Form(...),
    bse_scrip_code: str | None = Form(default=None),
    isin: str | None = Form(default=None),
    sector: str | None = Form(default=None),
    industry: str | None = Form(default=None),
    market_index: str | None = Form(default=None),
    priority_rank: int | None = Form(default=None),
    is_seed_company: bool = Form(default=False),
    company_status: str = Form(default=CompanyStatus.ACTIVE.value),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        return _admin_form_response(
            request,
            "admin/company_form.html",
            {
                "title": "Create Company",
                "current_user": current_admin,
                "errors": [_CSRF_ERROR],
                "form": {},
            },
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    resolved_id = company_id.strip() or slugify_company_id(nse_symbol)
    form_data = {
        "company_id": resolved_id,
        "name": name,
        "display_name": display_name,
        "nse_symbol": nse_symbol,
        "bse_scrip_code": bse_scrip_code or None,
        "isin": isin or None,
        "sector": sector or None,
        "industry": industry or None,
        "market_index": market_index or None,
        "priority_rank": priority_rank,
        "is_seed_company": is_seed_company,
        "status": company_status,
    }

    try:
        company_status_enum = CompanyStatus(company_status)
    except ValueError:
        return _admin_form_response(
            request,
            "admin/company_form.html",
            {
                "title": "Create Company",
                "current_user": current_admin,
                "errors": ["Invalid company status."],
                "form": form_data,
            },
            status_code=http_status.HTTP_400_BAD_REQUEST,
        )

    payload = CompanyCreate(
        company_id=resolved_id,
        name=name.strip(),
        display_name=display_name.strip(),
        nse_symbol=nse_symbol.strip(),
        bse_scrip_code=(bse_scrip_code or None),
        isin=(isin or None),
        sector=(sector or None),
        industry=(industry or None),
        market_index=(market_index or None),
        priority_rank=priority_rank,
        is_seed_company=is_seed_company,
        status=company_status_enum,
    )

    company_service: CompanyService = request.app.state.company_service
    try:
        company = await company_service.create_company(payload)
    except HTTPException as exc:
        return _admin_form_response(
            request,
            "admin/company_form.html",
            {
                "title": "Create Company",
                "current_user": current_admin,
                "errors": [str(exc.detail)],
                "form": form_data,
            },
            status_code=exc.status_code,
        )

    await request.app.state.audit_repo.log_event(
        event_type=AuditEventType.ADMIN_COMPANY_CREATED,
        user_id=current_admin.user_id,
        request_id=request_id_ctx_var.get(),
        details={"company_id": company.company_id, "nse_symbol": company.nse_symbol},
    )

    return RedirectResponse(url=f"/admin/companies/{company.company_id}", status_code=http_status.HTTP_303_SEE_OTHER)


@router.get("/admin/companies/{company_id}", response_class=HTMLResponse)
async def admin_company_detail(
    request: Request,
    company_id: str,
    current_admin: User = Depends(require_admin),
):
    company = await request.app.state.company_service.get_company(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    documents = await request.app.state.document_service.list_company_documents(
        company_id,
        admin_view=True,
    )
    return _admin_form_response(
        request,
        "admin/company_detail.html",
        {
            "title": company.display_name,
            "current_user": current_admin,
            "company": company,
            "documents": documents,
        },
    )


@router.get("/admin/documents", response_class=HTMLResponse)
async def admin_documents_list(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    documents = await request.app.state.document_service.list_all_documents(limit=200)
    return _admin_form_response(
        request,
        "admin/documents.html",
        {
            "title": "Documents",
            "current_user": current_admin,
            "documents": documents,
        },
    )


@router.get("/admin/documents/pending", response_class=HTMLResponse)
async def admin_documents_pending(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    documents = await request.app.state.document_service.list_pending_approval_documents()
    return _admin_form_response(
        request,
        "admin/documents_pending.html",
        {
            "title": "Documents Pending Approval",
            "current_user": current_admin,
            "documents": documents,
        },
    )


@router.get("/admin/documents/approved-for-processing", response_class=HTMLResponse)
async def admin_documents_eligible_for_processing(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    documents = await processing_job_service.list_documents_eligible_for_processing()
    return _admin_form_response(
        request,
        "admin/documents_eligible_processing.html",
        {
            "title": "Documents Ready for Processing",
            "current_user": current_admin,
            "documents": documents,
        },
    )


def _document_form_context(
    *,
    request: Request,
    current_admin: User,
    companies: list,
    selected_company_id: str | None,
    errors: list[str],
    form: dict,
) -> dict:
    return {
        "title": "Register Official Document",
        "current_user": current_admin,
        "companies": companies,
        "selected_company_id": selected_company_id or "",
        "document_types": _document_type_choices(),
        "source_types": _source_type_choices(),
        "errors": errors,
        "form": form,
    }


@router.get("/admin/documents/new", response_class=HTMLResponse)
async def admin_document_new_form(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    companies = await request.app.state.company_service.list_companies_raw()
    return _admin_form_response(
        request,
        "admin/document_form.html",
        _document_form_context(
            request=request,
            current_admin=current_admin,
            companies=companies,
            selected_company_id=None,
            errors=[],
            form={},
        ),
    )


@router.get("/admin/companies/{company_id}/documents/new", response_class=HTMLResponse)
async def admin_document_new_for_company(
    request: Request,
    company_id: str,
    current_admin: User = Depends(require_admin),
):
    company = await request.app.state.company_service.get_company(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    companies = await request.app.state.company_service.list_companies_raw()
    return _admin_form_response(
        request,
        "admin/document_form.html",
        _document_form_context(
            request=request,
            current_admin=current_admin,
            companies=companies,
            selected_company_id=company_id,
            errors=[],
            form={"company_id": company_id},
        ),
    )


def _document_review_context(
    *,
    current_admin: User,
    document,
    company_display_name: str | None,
    errors: list[str],
    can_request_processing: bool = False,
    qdrant_collection_name: str | None = None,
    latest_validation=None,
    can_validate_readiness: bool = False,
    validation_messages: list[str] | None = None,
) -> dict:
    return {
        "title": f"Review: {document.title}",
        "current_user": current_admin,
        "document": document,
        "company_display_name": company_display_name or document.company_id,
        "qdrant_collection_name": qdrant_collection_name,
        "errors": errors,
        "approval_pending": document.approval_status == ApprovalStatus.PENDING.value
        or document.approval_status == ApprovalStatus.PENDING,
        "approval_rejected": document.approval_status == ApprovalStatus.REJECTED.value
        or document.approval_status == ApprovalStatus.REJECTED,
        "approval_approved": document.approval_status == ApprovalStatus.APPROVED.value
        or document.approval_status == ApprovalStatus.APPROVED,
        "can_request_processing": can_request_processing,
        "latest_validation": latest_validation,
        "can_validate_readiness": can_validate_readiness,
        "validation_messages": validation_messages or [],
    }


async def _load_document_review_context(
    request: Request,
    document_id: str,
    current_admin: User,
    *,
    errors: list[str] | None = None,
) -> dict | None:
    document_service: DocumentService = request.app.state.document_service
    document = await document_service.get_document(document_id)
    if document is None:
        return None
    company = await request.app.state.company_service.get_company(document.company_id)
    company_display_name = company.display_name if company else None
    can_request_processing = await request.app.state.processing_job_service.can_request_processing(
        document_id
    )
    settings = request.app.state.settings
    readiness_service: DocumentReadinessService = request.app.state.document_readiness_service
    latest_validation = await readiness_service.get_latest_validation(document_id)
    can_validate_readiness = (
        document.approval_status == ApprovalStatus.APPROVED.value
        or document.approval_status == ApprovalStatus.APPROVED
    ) and (
        document.index_status == ProcessingStageStatus.COMPLETED.value
        or document.index_status == ProcessingStageStatus.COMPLETED
    ) and (
        document.chunk_status == ProcessingStageStatus.COMPLETED.value
        or document.chunk_status == ProcessingStageStatus.COMPLETED
    )
    return _document_review_context(
        current_admin=current_admin,
        document=document,
        company_display_name=company_display_name,
        errors=errors or [],
        can_request_processing=can_request_processing,
        qdrant_collection_name=settings.embedding.collection_name,
        latest_validation=latest_validation,
        can_validate_readiness=can_validate_readiness,
    )


@router.get("/admin/documents/{document_id}/review", response_class=HTMLResponse)
async def admin_document_review(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
):
    if document_id in ("new", "pending"):
        raise HTTPException(status_code=404, detail="Document not found.")
    context = await _load_document_review_context(request, document_id, current_admin)
    if context is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    await request.app.state.audit_repo.log_event(
        event_type=AuditEventType.ADMIN_DOCUMENT_REVIEW_OPENED,
        user_id=current_admin.user_id,
        request_id=request_id_ctx_var.get(),
        details={"document_id": document_id},
    )
    return _admin_form_response(request, "admin/document_review.html", context)


@router.post("/admin/documents/{document_id}/approve")
async def admin_document_approve(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
    review_notes: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[_CSRF_ERROR]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    document_service: DocumentService = request.app.state.document_service
    try:
        await document_service.approve_document(
            document_id,
            admin_user_id=current_admin.user_id,
            review_notes=review_notes,
        )
    except DocumentApprovalError as exc:
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[exc.message]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.") from exc
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url="/admin/documents/pending",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/documents/{document_id}/reject")
async def admin_document_reject(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
    rejection_reason: str = Form(default=""),
    review_notes: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[_CSRF_ERROR]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    document_service: DocumentService = request.app.state.document_service
    try:
        await document_service.reject_document(
            document_id,
            admin_user_id=current_admin.user_id,
            rejection_reason=rejection_reason,
            review_notes=review_notes,
        )
    except DocumentApprovalError as exc:
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[exc.message]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.") from exc
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url="/admin/documents/pending",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/documents/{document_id}/mark-pending")
async def admin_document_mark_pending(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
    review_notes: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[_CSRF_ERROR]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    document_service: DocumentService = request.app.state.document_service
    try:
        await document_service.mark_document_pending(
            document_id,
            admin_user_id=current_admin.user_id,
            review_notes=review_notes,
        )
    except DocumentApprovalError as exc:
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[exc.message]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.") from exc
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/admin/documents/{document_id}/review",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.get("/admin/processing")
async def admin_processing_hub(
    current_admin: User = Depends(require_admin),
):
    _ = current_admin
    return RedirectResponse(
        url="/admin/processing/jobs",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.get("/admin/processing/jobs", response_class=HTMLResponse)
async def admin_processing_jobs_list(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    jobs = await processing_job_service.list_processing_jobs()
    return _admin_form_response(
        request,
        "admin/processing_jobs.html",
        {
            "title": "Processing Jobs",
            "current_user": current_admin,
            "jobs": jobs,
        },
    )


def _processing_job_detail_context(
    *,
    current_admin: User,
    job,
    document,
    errors: list[str] | None = None,
    qdrant_collection_name: str | None = None,
) -> dict:
    parse_runnable_statuses = {ProcessingJobStatus.QUEUED.value, ProcessingJobStatus.RUNNING.value}
    parse_runnable_stages = {
        ProcessingJobStage.PARSE_PENDING.value,
        ProcessingJobStage.PARSE_RUNNING.value,
    }
    chunk_runnable_stages = {
        ProcessingJobStage.PARSE_COMPLETED.value,
        ProcessingJobStage.CHUNK_FAILED.value,
        ProcessingJobStage.CHUNK_COMPLETED.value,
        ProcessingJobStage.INDEX_FAILED.value,
    }
    chunk_runnable_statuses = {
        ProcessingJobStatus.QUEUED.value,
        ProcessingJobStatus.COMPLETED.value,
        ProcessingJobStatus.FAILED.value,
    }
    index_runnable_stages = {
        ProcessingJobStage.INDEX_PENDING.value,
        ProcessingJobStage.INDEX_FAILED.value,
        ProcessingJobStage.INDEX_COMPLETED.value,
    }
    index_runnable_statuses = {
        ProcessingJobStatus.QUEUED.value,
        ProcessingJobStatus.COMPLETED.value,
        ProcessingJobStatus.FAILED.value,
    }
    can_run_parse = job.status in parse_runnable_statuses and job.stage in parse_runnable_stages
    can_run_chunk = (
        job.stage in chunk_runnable_stages
        and job.status in chunk_runnable_statuses
        and document is not None
        and str(document.parse_status) == "completed"
        and str(document.chunk_status) != "completed"
    )
    can_run_index = (
        job.stage in index_runnable_stages
        and job.status in index_runnable_statuses
        and document is not None
        and str(document.chunk_status) == "completed"
    )
    parsed_pages_basename = None
    page_map_basename = None
    chunk_artifact_basename = None
    if document and document.parsed_pages_path:
        parsed_pages_basename = Path(document.parsed_pages_path).name
    if document and document.page_map_path:
        page_map_basename = Path(document.page_map_path).name
    if document and document.chunk_artifact_path:
        chunk_artifact_basename = Path(document.chunk_artifact_path).name
    return {
        "title": f"Processing Job {job.job_id}",
        "current_user": current_admin,
        "job": job,
        "document": document,
        "can_run_parse": can_run_parse,
        "can_run_chunk": can_run_chunk,
        "can_run_index": can_run_index,
        "qdrant_collection_name": qdrant_collection_name,
        "parsed_pages_basename": parsed_pages_basename,
        "page_map_basename": page_map_basename,
        "chunk_artifact_basename": chunk_artifact_basename,
        "errors": errors or [],
    }


@router.get("/admin/processing/jobs/{job_id}", response_class=HTMLResponse)
async def admin_processing_job_detail(
    request: Request,
    job_id: str,
    current_admin: User = Depends(require_admin),
):
    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    try:
        job = await processing_job_service.get_processing_job(job_id)
    except ProcessingJobError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    await request.app.state.audit_repo.log_event(
        event_type=AuditEventType.ADMIN_DOCUMENT_PROCESSING_VIEWED,
        user_id=current_admin.user_id,
        request_id=request_id_ctx_var.get(),
        details={
            "document_id": job.document_id,
            "company_id": job.company_id,
            "job_id": job.job_id,
        },
    )
    document = await request.app.state.document_service.get_document(job.document_id)
    settings = request.app.state.settings
    return _admin_form_response(
        request,
        "admin/processing_job_detail.html",
        _processing_job_detail_context(
            current_admin=current_admin,
            job=job,
            document=document,
            qdrant_collection_name=settings.embedding.collection_name,
        ),
    )


@router.post("/admin/processing/jobs/{job_id}/parse")
async def admin_processing_job_parse(
    request: Request,
    job_id: str,
    current_admin: User = Depends(require_admin),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        processing_job_service: ProcessingJobService = request.app.state.processing_job_service
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[_CSRF_ERROR],
            ),
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    document_parse_service: DocumentParseService = request.app.state.document_parse_service
    try:
        await document_parse_service.parse_document_for_processing_job(
            job_id,
            admin_user_id=current_admin.user_id,
        )
    except DocumentParseError as exc:
        processing_job_service = request.app.state.processing_job_service
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as job_exc:
            raise HTTPException(status_code=job_exc.status_code, detail=job_exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[exc.message],
            ),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/admin/processing/jobs/{job_id}",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/processing/jobs/{job_id}/chunk")
async def admin_processing_job_chunk(
    request: Request,
    job_id: str,
    current_admin: User = Depends(require_admin),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[_CSRF_ERROR],
            ),
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    document_chunk_service: DocumentChunkService = request.app.state.document_chunk_service
    try:
        await document_chunk_service.chunk_document_for_processing_job(
            job_id,
            admin_user_id=current_admin.user_id,
        )
    except DocumentChunkError as exc:
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as job_exc:
            raise HTTPException(status_code=job_exc.status_code, detail=job_exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[exc.message],
            ),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/admin/processing/jobs/{job_id}",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/processing/jobs/{job_id}/index")
async def admin_processing_job_index(
    request: Request,
    job_id: str,
    current_admin: User = Depends(require_admin),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    settings = request.app.state.settings
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[_CSRF_ERROR],
                qdrant_collection_name=settings.embedding.collection_name,
            ),
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    qdrant_indexing_service: QdrantIndexingService = request.app.state.qdrant_indexing_service
    try:
        await qdrant_indexing_service.index_document_for_processing_job(
            job_id,
            admin_user_id=current_admin.user_id,
        )
    except QdrantIndexingError as exc:
        try:
            job = await processing_job_service.get_processing_job(job_id)
        except ProcessingJobError as job_exc:
            raise HTTPException(status_code=job_exc.status_code, detail=job_exc.message) from exc
        document = await request.app.state.document_service.get_document(job.document_id)
        return _admin_form_response(
            request,
            "admin/processing_job_detail.html",
            _processing_job_detail_context(
                current_admin=current_admin,
                job=job,
                document=document,
                errors=[exc.message],
                qdrant_collection_name=settings.embedding.collection_name,
            ),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/admin/processing/jobs/{job_id}",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/documents/{document_id}/validate-readiness")
async def admin_document_validate_readiness(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
    csrf_token: str | None = Form(default=None),
):
    if document_id in ("new", "pending", "approved-for-processing"):
        raise HTTPException(status_code=404, detail="Document not found.")

    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[_CSRF_ERROR]
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    readiness_service: DocumentReadinessService = request.app.state.document_readiness_service
    try:
        validation = await readiness_service.validate_document_readiness(
            document_id,
            admin_user_id=current_admin.user_id,
        )
    except DocumentReadinessError as exc:
        context = await _load_document_review_context(
            request, document_id, current_admin, errors=[exc.message]
        )
        if context is None:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=exc.status_code,
        )

    messages: list[str] = []
    if validation.status == "passed":
        messages.append("Readiness validation passed. Document is now searchable for future retrieval.")
    else:
        messages.extend(validation.fatal_errors)
        if validation.warnings:
            messages.append("Warnings:")
            messages.extend(validation.warnings)

    context = await _load_document_review_context(request, document_id, current_admin)
    if context is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    context["validation_messages"] = messages
    return _admin_form_response(request, "admin/document_review.html", context)


@router.post("/admin/documents/{document_id}/process")
async def admin_document_request_processing(
    request: Request,
    document_id: str,
    current_admin: User = Depends(require_admin),
    csrf_token: str | None = Form(default=None),
):
    if document_id in ("new", "pending", "approved-for-processing"):
        raise HTTPException(status_code=404, detail="Document not found.")

    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        context = await _load_document_review_context(
            request,
            document_id,
            current_admin,
            errors=[_CSRF_ERROR],
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    processing_job_service: ProcessingJobService = request.app.state.processing_job_service
    try:
        job = await processing_job_service.request_document_processing(
            document_id,
            admin_user_id=current_admin.user_id,
        )
    except ProcessingJobError as exc:
        context = await _load_document_review_context(
            request,
            document_id,
            current_admin,
            errors=[exc.message],
        )
        if context is None:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return _admin_form_response(
            request,
            "admin/document_review.html",
            context,
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/admin/processing/jobs/{job.job_id}",
        status_code=http_status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/documents")
async def admin_document_register(
    request: Request,
    current_admin: User = Depends(require_admin),
    company_id: str = Form(...),
    document_type: str = Form(...),
    title: str = Form(...),
    fiscal_year: int | None = Form(default=None),
    quarter: str | None = Form(default=None),
    period: str | None = Form(default=None),
    filing_date: str | None = Form(default=None),
    source_type: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        companies = await request.app.state.company_service.list_companies_raw()
        return _admin_form_response(
            request,
            "admin/document_form.html",
            _document_form_context(
                request=request,
                current_admin=current_admin,
                companies=companies,
                selected_company_id=company_id,
                errors=[_CSRF_ERROR],
                form={
                    "company_id": company_id,
                    "document_type": document_type,
                    "title": title,
                },
            ),
            status_code=http_status.HTTP_403_FORBIDDEN,
        )

    form_data = {
        "company_id": company_id,
        "document_type": document_type,
        "title": title,
        "fiscal_year": fiscal_year,
        "quarter": quarter or "",
        "period": period or "",
        "filing_date": filing_date or "",
        "source_type": source_type or "",
        "source_url": source_url or "",
        "notes": notes or "",
    }

    if file is None or not file.filename:
        companies = await request.app.state.company_service.list_companies_raw()
        return _admin_form_response(
            request,
            "admin/document_form.html",
            _document_form_context(
                request=request,
                current_admin=current_admin,
                companies=companies,
                selected_company_id=company_id,
                errors=["PDF file upload is required."],
                form=form_data,
            ),
            status_code=http_status.HTTP_400_BAD_REQUEST,
        )

    parsed_filing_date: date | None = None
    if filing_date and filing_date.strip():
        try:
            parsed_filing_date = date.fromisoformat(filing_date.strip())
        except ValueError:
            companies = await request.app.state.company_service.list_companies_raw()
            return _admin_form_response(
                request,
                "admin/document_form.html",
                _document_form_context(
                    request=request,
                    current_admin=current_admin,
                    companies=companies,
                    selected_company_id=company_id,
                    errors=["Invalid filing date. Use YYYY-MM-DD."],
                    form=form_data,
                ),
                status_code=http_status.HTTP_400_BAD_REQUEST,
            )

    try:
        validate_mvp_document_type(document_type.strip())
    except ValueError as exc:
        companies = await request.app.state.company_service.list_companies_raw()
        response = _admin_form_response(
            request,
            "admin/document_form.html",
            _document_form_context(
                request=request,
                current_admin=current_admin,
                companies=companies,
                selected_company_id=company_id,
                errors=[str(exc)],
                form=form_data,
            ),
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
        await request.app.state.audit_repo.log_event(
            event_type=AuditEventType.ADMIN_DOCUMENT_REGISTRATION_FAILED,
            user_id=current_admin.user_id,
            request_id=request_id_ctx_var.get(),
            details={"reason": str(exc), "company_id": company_id},
        )
        return response

    payload = DocumentCreate(
        company_id=company_id.strip(),
        document_type=document_type.strip(),
        title=title.strip(),
        fiscal_year=fiscal_year,
        quarter=(quarter.strip() if quarter and quarter.strip() else None),
        period=(period.strip() if period and period.strip() else None),
        filing_date=parsed_filing_date,
        source_type=(source_type.strip() if source_type and source_type.strip() else None),
        source_url=(source_url.strip() if source_url and source_url.strip() else None),
        notes=(notes.strip() if notes and notes.strip() else None),
        searchable=False,
    )

    document_service: DocumentService = request.app.state.document_service
    try:
        await document_service.register_document_with_upload(
            payload=payload,
            upload=file,
            admin_user_id=current_admin.user_id,
        )
    except DocumentRegistrationError as exc:
        companies = await request.app.state.company_service.list_companies_raw()
        return _admin_form_response(
            request,
            "admin/document_form.html",
            _document_form_context(
                request=request,
                current_admin=current_admin,
                companies=companies,
                selected_company_id=company_id,
                errors=[exc.message],
                form=form_data,
            ),
            status_code=exc.status_code,
        )

    return RedirectResponse(url="/admin/documents", status_code=http_status.HTTP_303_SEE_OTHER)


@router.post("/admin/retrieval/test")
async def admin_retrieval_test(
    request: Request,
    query: str = Form(...),
    company_id: str = Form(...),
    document_ids: str | None = Form(default=None),
    document_types: str | None = Form(default=None),
    fiscal_year: int | None = Form(default=None),
    quarter: str | None = Form(default=None),
    period: str | None = Form(default=None),
    top_k: int = Form(default=8),
    score_threshold: float | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    current_admin: User = Depends(require_admin),
    auth_service: AuthService = Depends(_auth_service),
):
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        return JSONResponse(
            status_code=http_status.HTTP_403_FORBIDDEN,
            content={"error": _CSRF_ERROR},
        )

    parsed_document_ids: list[str] | None = None
    if document_ids and document_ids.strip():
        parsed_document_ids = [item.strip() for item in document_ids.split(",") if item.strip()]

    parsed_document_types: list[str] | None = None
    if document_types and document_types.strip():
        try:
            parsed_document_types = [
                validate_mvp_document_type(item.strip()).value
                for item in document_types.split(",")
                if item.strip()
            ]
        except ValueError as exc:
            return JSONResponse(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                content={"error": str(exc)},
            )

    retrieval_request = RetrievalRequest(
        query=query.strip(),
        filters=RetrievalFilters(
            company_id=company_id.strip(),
            document_ids=parsed_document_ids,
            document_types=parsed_document_types,
            fiscal_year=fiscal_year,
            quarter=(quarter.strip() if quarter and quarter.strip() else None),
            period=(period.strip() if period and period.strip() else None),
        ),
        top_k=top_k,
        score_threshold=score_threshold,
    )

    retrieval_service: RetrievalService = request.app.state.retrieval_service
    pack = await retrieval_service.retrieve_evidence(
        retrieval_request,
        actor_user_id=current_admin.user_id,
    )

    first_item = pack.evidence_items[0] if pack.evidence_items else None
    return JSONResponse(
        content={
            "status": pack.status.value,
            "missing_reason": pack.missing_reason,
            "evidence_count": len(pack.evidence_items),
            "retrieval_metadata": pack.retrieval_metadata,
            "first_item": (
                {
                    "chunk_id": first_item.chunk_id,
                    "document_id": first_item.document_id,
                    "page_start": first_item.page_start,
                    "page_end": first_item.page_end,
                    "section_title": first_item.section_title,
                    "source_document_title": first_item.source_document_title,
                    "score": first_item.score,
                }
                if first_item
                else None
            ),
        }
    )
