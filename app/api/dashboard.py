from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

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
