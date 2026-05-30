from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

_CSRF_ERROR = "Session expired. Please try again."


def _templates(request: Request):
    return request.app.state.templates


def _auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def _auth_form_response(
    request: Request,
    template_name: str,
    context: dict,
    *,
    status_code: int = status.HTTP_200_OK,
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


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return _auth_form_response(
        request,
        "auth/register.html",
        {"title": "Register", "error": None},
    )


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
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        return _auth_form_response(
            request,
            "auth/register.html",
            {"title": "Register", "error": _CSRF_ERROR},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if password != confirm_password:
        return _auth_form_response(
            request,
            "auth/register.html",
            {"title": "Register", "error": "Passwords do not match."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await auth_service.register(email=email, password=password, full_name=full_name)
    except HTTPException as exc:
        return _auth_form_response(
            request,
            "auth/register.html",
            {"title": "Register", "error": exc.detail},
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return _auth_form_response(
        request,
        "auth/login.html",
        {"title": "Login", "error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        return _auth_form_response(
            request,
            "auth/login.html",
            {"title": "Login", "error": _CSRF_ERROR},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        user = await auth_service.authenticate(email=email, password=password)
    except HTTPException:
        return _auth_form_response(
            request,
            "auth/login.html",
            {"title": "Login", "error": "Invalid email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    auth_service.set_login_cookie(response, user)
    return response


@router.post("/logout")
async def logout_submit(
    request: Request,
    csrf_token: str | None = Form(default=None),
):
    auth_service = _auth_service(request)
    if not auth_service.is_form_csrf_valid(request, csrf_token):
        return _auth_form_response(
            request,
            "auth/login.html",
            {"title": "Login", "error": _CSRF_ERROR},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user_id = None
    try:
        user = await auth_service.get_current_user(request)
        user_id = user.user_id
    except HTTPException:
        user_id = None

    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    await auth_service.logout(response, user_id=user_id)
    return response
