from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _templates(request: Request):
    return request.app.state.templates


def _auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    response = _templates(request).TemplateResponse(
        request=request,
        name="auth/register.html",
        context={"title": "Register", "error": None},
    )
    csrf_token = _auth_service(request).issue_form_csrf(response)
    response.context["csrf_token"] = csrf_token
    return response


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    full_name: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    auth_service.validate_form_csrf(request, csrf_token)

    if password != confirm_password:
        response = _templates(request).TemplateResponse(
            request=request,
            name="auth/register.html",
            context={"title": "Register", "error": "Passwords do not match.", "csrf_token": ""},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        response.context["csrf_token"] = auth_service.issue_form_csrf(response)
        return response

    try:
        await auth_service.register(email=email, password=password, full_name=full_name)
    except HTTPException as exc:
        response = _templates(request).TemplateResponse(
            request=request,
            name="auth/register.html",
            context={"title": "Register", "error": exc.detail, "csrf_token": ""},
            status_code=exc.status_code,
        )
        response.context["csrf_token"] = auth_service.issue_form_csrf(response)
        return response
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    response = _templates(request).TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"title": "Login", "error": None},
    )
    response.context["csrf_token"] = _auth_service(request).issue_form_csrf(response)
    return response


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    auth_service.validate_form_csrf(request, csrf_token)

    try:
        user = await auth_service.authenticate(email=email, password=password)
    except HTTPException:
        response = _templates(request).TemplateResponse(
            request=request,
            name="auth/login.html",
            context={"title": "Login", "error": "Invalid email or password.", "csrf_token": ""},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        response.context["csrf_token"] = auth_service.issue_form_csrf(response)
        return response

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    auth_service.set_login_cookie(response, user)
    return response


@router.post("/logout")
async def logout_submit(
    request: Request,
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    auth_service.validate_form_csrf(request, csrf_token)
    user_id = None
    try:
        user = await auth_service.get_current_user(request)
        user_id = user.user_id
    except HTTPException:
        user_id = None

    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    await auth_service.logout(response, user_id=user_id)
    return response
