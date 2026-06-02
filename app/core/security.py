from __future__ import annotations

import re
import secrets
import hashlib
import hmac
import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, Request, Response, status

from app.core.config import Settings

try:
    from pwdlib import PasswordHash  # type: ignore

    _password_hasher = PasswordHash.recommended()
except Exception:  # pragma: no cover - fallback path for local environments without pwdlib
    _password_hasher = None
_password_pattern = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")
_weak_passwords = {"password", "password123", "12345678", "qwerty123", "changeme"}


def validate_password_strength(password: str) -> None:
    normalized = password.strip()
    if not _password_pattern.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters and include letters and numbers.",
        )
    if normalized.lower() in _weak_passwords:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is too weak.",
        )


def hash_password(password: str) -> str:
    validate_password_strength(password)
    if _password_hasher is not None:
        return _password_hasher.hash(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return f"pbkdf2_sha256${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, hashed_password: str) -> bool:
    if _password_hasher is not None:
        return _password_hasher.verify(password, hashed_password)
    if not hashed_password.startswith("pbkdf2_sha256$"):
        return False
    _, salt_b64, digest_b64 = hashed_password.split("$", maxsplit=2)
    salt = base64.b64decode(salt_b64.encode())
    expected = base64.b64decode(digest_b64.encode())
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return hmac.compare_digest(actual, expected)


def create_session_token(
    *, user_id: str, role: str, settings: Settings, expires_at: datetime | None = None
) -> str:
    expires = expires_at or (datetime.now(UTC) + timedelta(minutes=settings.session.access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "exp": expires,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(
        payload,
        settings.app.secret_key.get_secret_value(),
        algorithm="HS256",
    )


def decode_session_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.app.secret_key.get_secret_value(),
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials.",
        ) from exc


def set_auth_cookie(response: Response, token: str, settings: Settings) -> None:
    max_age = settings.session.access_token_expire_minutes * 60
    response.set_cookie(
        key=settings.session.cookie_name,
        value=token,
        httponly=settings.session.cookie_http_only,
        secure=settings.session.cookie_secure,
        samesite=settings.session.cookie_same_site,
        max_age=max_age,
        expires=max_age,
    )


def clear_auth_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session.cookie_name,
        secure=settings.session.cookie_secure,
        samesite=settings.session.cookie_same_site,
    )


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session.csrf_cookie_name,
        value=token,
        httponly=False,
        secure=settings.session.cookie_secure,
        samesite=settings.session.cookie_same_site,
        max_age=settings.session.access_token_expire_minutes * 60,
    )


def issue_csrf_token(response: Response, settings: Settings) -> str:
    token = generate_csrf_token()
    set_csrf_cookie(response, token, settings)
    return token


def verify_csrf_token(request: Request, submitted_token: str | None, settings: Settings) -> None:
    cookie_token = request.cookies.get(settings.session.csrf_cookie_name)
    if not cookie_token or not submitted_token or submitted_token != cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )
