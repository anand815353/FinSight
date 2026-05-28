from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.permissions import require_admin
from app.models.user import User

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    try:
        current_user = await request.app.state.auth_service.get_current_user(request)
    except HTTPException:
        return RedirectResponse(url="/auth/login", status_code=303)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={"title": "Dashboard", "current_user": current_user},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    _current_admin: User = Depends(require_admin),
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={"title": "Admin", "current_user": _current_admin},
    )
