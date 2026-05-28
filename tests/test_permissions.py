import asyncio
import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app.core.permissions import require_admin
from app.models.user import User, UserRole, UserStatus


class _AuditRepo:
    def __init__(self):
        self.events = []

    async def log_event(self, **kwargs):
        self.events.append(kwargs)


def test_require_admin_rejects_non_admin():
    user = User.model_validate(
        {
            "_id": "user-1",
            "email": "user@example.com",
            "password_hash": "hash",
            "role": UserRole.FREE_USER.value,
            "status": UserStatus.ACTIVE.value,
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_repo=_AuditRepo())),
        headers={},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin(request=request, current_user=user))
    assert exc.value.status_code == 403


def test_require_admin_allows_admin():
    user = User.model_validate(
        {
            "_id": "admin-1",
            "email": "admin@example.com",
            "password_hash": "hash",
            "role": UserRole.ADMIN.value,
            "status": UserStatus.ACTIVE.value,
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_repo=_AuditRepo())),
        headers={},
    )
    result = asyncio.run(require_admin(request=request, current_user=user))
    assert result.role == UserRole.ADMIN
