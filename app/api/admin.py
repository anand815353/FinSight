from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi import status as http_status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.permissions import require_admin
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.company import CompanyCreate, CompanyStatus
from app.models.document import DocumentCreate, MVP_DOCUMENT_TYPES, validate_mvp_document_type
from app.models.source_registry import SourceType
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.company_service import CompanyService, slugify_company_id
from app.services.document_service import DocumentRegistrationError, DocumentService

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
