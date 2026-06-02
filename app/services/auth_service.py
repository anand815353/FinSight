from __future__ import annotations

from fastapi import HTTPException, Request, Response, status

from app.core.config import Settings
from app.core.security import (
    clear_auth_cookie,
    create_session_token,
    decode_session_token,
    generate_csrf_token,
    hash_password,
    issue_csrf_token,
    set_auth_cookie,
    set_csrf_cookie,
    verify_csrf_token,
    verify_password,
)
from app.middleware.request_id import request_id_ctx_var
from app.models.audit_log import AuditEventType
from app.models.user import User, UserStatus
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserAlreadyExistsError, UserRepository


class AuthService:
    def __init__(self, user_repo: UserRepository, audit_repo: AuditRepository, settings: Settings):
        self._user_repo = user_repo
        self._audit_repo = audit_repo
        self._settings = settings

    async def register(self, *, email: str, password: str, full_name: str | None) -> User:
        hashed_password = hash_password(password)
        try:
            user = await self._user_repo.create_user(
                email=email,
                password_hash=hashed_password,
                full_name=full_name,
            )
        except UserAlreadyExistsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered.") from exc
        await self._audit_repo.log_event(
            event_type=AuditEventType.REGISTER_SUCCESS,
            user_id=user.user_id,
            request_id=request_id_ctx_var.get(),
        )
        return user

    async def authenticate(self, *, email: str, password: str) -> User:
        user = await self._user_repo.get_by_email(email)
        generic_error = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
        if not user:
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                user_id=None,
                request_id=request_id_ctx_var.get(),
                details={"reason": "missing_user"},
            )
            raise generic_error

        if user.status != UserStatus.ACTIVE:
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                user_id=user.user_id,
                request_id=request_id_ctx_var.get(),
                details={"reason": "inactive_user"},
            )
            raise generic_error

        if not verify_password(password, user.password_hash):
            await self._user_repo.update_login_metadata(user.user_id, success=False)
            await self._audit_repo.log_event(
                event_type=AuditEventType.LOGIN_FAILED,
                user_id=user.user_id,
                request_id=request_id_ctx_var.get(),
                details={"reason": "password_mismatch"},
            )
            raise generic_error

        await self._user_repo.update_login_metadata(user.user_id, success=True)
        await self._audit_repo.log_event(
            event_type=AuditEventType.LOGIN_SUCCESS,
            user_id=user.user_id,
            request_id=request_id_ctx_var.get(),
        )
        return user

    def set_login_cookie(self, response: Response, user: User) -> None:
        role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
        token = create_session_token(user_id=user.user_id, role=role_value, settings=self._settings)
        set_auth_cookie(response, token, self._settings)

    async def logout(self, response: Response, user_id: str | None) -> None:
        clear_auth_cookie(response, self._settings)
        await self._audit_repo.log_event(
            event_type=AuditEventType.LOGOUT,
            user_id=user_id,
            request_id=request_id_ctx_var.get(),
        )

    async def get_current_user(self, request: Request) -> User:
        token = request.cookies.get(self._settings.session.cookie_name)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

        payload = decode_session_token(token, self._settings)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")

        user = await self._user_repo.get_by_id(user_id)
        if not user or user.status != UserStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
        return user

    def issue_form_csrf(self, response: Response) -> str:
        return issue_csrf_token(response, self._settings)

    def prepare_form_csrf(self) -> str:
        return generate_csrf_token()

    def attach_form_csrf(self, response: Response, token: str) -> None:
        set_csrf_cookie(response, token, self._settings)

    def is_form_csrf_valid(self, request: Request, submitted_token: str | None) -> bool:
        try:
            verify_csrf_token(request, submitted_token, self._settings)
        except HTTPException:
            return False
        return True

    def validate_form_csrf(self, request: Request, submitted_token: str | None) -> None:
        verify_csrf_token(request, submitted_token, self._settings)
