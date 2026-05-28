from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.models.audit_log import AuditEventType
from app.models.user import User, UserRole
from app.services.auth_service import AuthService


def _get_auth_service_from_request(request: Request) -> AuthService:
    return request.app.state.auth_service


async def require_user(
    request: Request,
    auth_service: AuthService = Depends(_get_auth_service_from_request),
) -> User:
    try:
        return await auth_service.get_current_user(request)
    except HTTPException as exc:
        await request.app.state.audit_repo.log_event(
            event_type=AuditEventType.PROTECTED_ROUTE_DENIED,
            user_id=None,
            request_id=request.headers.get("X-Request-ID"),
            details={"status_code": exc.status_code},
        )
        raise


async def require_admin(
    request: Request,
    current_user: User = Depends(require_user),
) -> User:
    if current_user.role != UserRole.ADMIN:
        await request.app.state.audit_repo.log_event(
            event_type=AuditEventType.ADMIN_ACCESS_DENIED,
            user_id=current_user.user_id,
            request_id=request.headers.get("X-Request-ID"),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user
