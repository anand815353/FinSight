from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.permissions import require_admin
from app.models.company import CompanyPublic
from app.models.document import Document
from app.models.user import User

router = APIRouter(prefix="/api", tags=["companies"])


@router.get("/companies", response_model=list[CompanyPublic])
async def list_companies(request: Request, q: str | None = Query(default=None)):
    return await request.app.state.company_service.list_companies(q)


@router.get("/companies/{company_id}", response_model=CompanyPublic)
async def get_company(request: Request, company_id: str):
    company = await request.app.state.company_service.get_company(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    return request.app.state.company_service.to_public(company)


@router.get("/admin/documents", response_model=list[Document])
async def list_admin_documents(
    request: Request,
    _current_admin: User = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
):
    return await request.app.state.document_service.list_all_documents(limit=limit)
