from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import Settings
from app.core.security import (
    _password_hasher,
    create_session_token,
    decode_session_token,
    hash_password,
    set_auth_cookie,
    verify_password,
)


def test_password_hash_and_verify(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    hashed = hash_password("StrongPass123")
    assert verify_password("StrongPass123", hashed) is True
    assert verify_password("WrongPass123", hashed) is False


def test_pwdlib_hasher_active_on_python312(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    assert _password_hasher is not None
    hashed = hash_password("StrongPass123")
    assert not hashed.startswith("pbkdf2_sha256$")


def test_weak_password_rejected(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    with pytest.raises(HTTPException):
        hash_password("weak")


def test_session_token_create_and_decode(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    settings = Settings()
    token = create_session_token(user_id="u1", role="free_user", settings=settings)
    payload = decode_session_token(token, settings)
    assert payload["sub"] == "u1"
    assert payload["role"] == "free_user"


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    settings = Settings()
    expired = datetime.now(UTC) - timedelta(minutes=1)
    token = create_session_token(
        user_id="u1",
        role="free_user",
        settings=settings,
        expires_at=expired,
    )
    with pytest.raises(HTTPException):
        decode_session_token(token, settings)


def test_auth_cookie_flags(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    settings = Settings()
    response = Response()
    set_auth_cookie(response, "token-value", settings)
    cookie_header = response.headers.get("set-cookie", "")
    assert settings.session.cookie_name in cookie_header
    assert "HttpOnly" in cookie_header
