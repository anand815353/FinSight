import re

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.user import User, UserRole, UserStatus


def _get_csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match is not None, "csrf_token hidden field not found in HTML"
    token = match.group(1)
    assert token, "csrf_token hidden field is empty in HTML"
    return token


class _FakeUserRepo:
    def __init__(self):
        self.users = {}

    async def ensure_indexes(self) -> None:
        return None

    async def create_user(self, *, email, password_hash, full_name, role=UserRole.FREE_USER):
        key = email.lower()
        if key in self.users:
            from app.repositories.user_repo import UserAlreadyExistsError

            raise UserAlreadyExistsError("duplicate")
        user = User.model_validate(
            {
                "_id": f"user-{len(self.users)+1}",
                "email": key,
                "password_hash": password_hash,
                "full_name": full_name,
                "role": role.value,
                "status": UserStatus.ACTIVE.value,
                "preferences": {},
                "failed_login_count": 0,
            }
        )
        self.users[key] = user
        return user

    async def get_by_email(self, email: str):
        return self.users.get(email.lower())

    async def get_by_id(self, user_id: str):
        for user in self.users.values():
            if user.user_id == user_id:
                return user
        return None

    async def update_login_metadata(self, user_id: str, *, success: bool):
        return None


class _FakeAuditRepo:
    def __init__(self):
        self.events = []

    async def log_event(self, **kwargs):
        self.events.append(kwargs)


def _inject_auth_service(app):
    from app.services.auth_service import AuthService

    fake_user_repo = _FakeUserRepo()
    fake_audit_repo = _FakeAuditRepo()
    app.state.user_repo = fake_user_repo
    app.state.audit_repo = fake_audit_repo
    app.state.auth_service = AuthService(fake_user_repo, fake_audit_repo, app.state.settings)


def test_register_login_logout_and_dashboard_flow(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app
    with TestClient(app) as client:
        _inject_auth_service(app)
        client.get("/auth/register")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        register_response = client.post(
            "/auth/register",
            data={
                "email": "user@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
                "full_name": "Test User",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert register_response.status_code == 303

        client.get("/auth/login")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        login_response = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "StrongPass123", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert login_response.status_code == 303
        assert app.state.settings.session.cookie_name in login_response.cookies

        dashboard_response = client.get("/dashboard")
        assert dashboard_response.status_code == 200
        assert "Dashboard" in dashboard_response.text

        csrf_cookie_name = app.state.settings.session.csrf_cookie_name
        logout_response = client.post(
            "/auth/logout",
            data={"csrf_token": client.cookies.get(csrf_cookie_name)},
            follow_redirects=False,
        )
        assert logout_response.status_code == 303


def test_login_failure_is_generic(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app
    with TestClient(app) as client:
        _inject_auth_service(app)
        client.get("/auth/login")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        response = client.post(
            "/auth/login",
            data={"email": "missing@example.com", "password": "StrongPass123", "csrf_token": csrf},
        )
        assert response.status_code == 401
        assert "Invalid email or password." in response.text


def test_duplicate_email_and_weak_password_rejected(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app
    with TestClient(app) as client:
        _inject_auth_service(app)
        client.get("/auth/register")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        first = client.post(
            "/auth/register",
            data={
                "email": "dup@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert first.status_code == 303

        client.get("/auth/register")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        duplicate = client.post(
            "/auth/register",
            data={
                "email": "dup@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
                "csrf_token": csrf,
            },
        )
        assert duplicate.status_code == 400
        assert "Email already registered." in duplicate.text

        client.get("/auth/register")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        weak = client.post(
            "/auth/register",
            data={
                "email": "weak@example.com",
                "password": "weak",
                "confirm_password": "weak",
                "csrf_token": csrf,
            },
        )
        assert weak.status_code == 400
        assert "Password" in weak.text


def test_dashboard_redirects_anonymous_and_admin_rejects_non_admin(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app
    with TestClient(app) as client:
        _inject_auth_service(app)
        anonymous = client.get("/dashboard", follow_redirects=False)
        assert anonymous.status_code == 303
        assert anonymous.headers["location"] == "/auth/login"

        client.get("/auth/register")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        client.post(
            "/auth/register",
            data={
                "email": "user@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        client.get("/auth/login")
        csrf = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "StrongPass123", "csrf_token": csrf},
            follow_redirects=False,
        )
        admin_response = client.get("/admin")
        assert admin_response.status_code == 403


def test_register_page_hidden_csrf_matches_cookie(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app

    with TestClient(app) as client:
        _inject_auth_service(app)
        response = client.get("/auth/register")
        assert response.status_code == 200
        cookie = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        hidden = _get_csrf_from_html(response.text)
        assert hidden == cookie


def test_register_submit_with_html_csrf_token(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app

    with TestClient(app) as client:
        _inject_auth_service(app)
        page = client.get("/auth/register")
        html_csrf = _get_csrf_from_html(page.text)
        register_response = client.post(
            "/auth/register",
            data={
                "email": "htmlcsrf@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
                "full_name": "HTML CSRF User",
                "csrf_token": html_csrf,
            },
            follow_redirects=False,
        )
        assert register_response.status_code == 303
        assert register_response.headers["location"] == "/auth/login"


def test_register_csrf_failure_returns_html(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app

    with TestClient(app) as client:
        _inject_auth_service(app)
        client.get("/auth/register")
        response = client.post(
            "/auth/register",
            data={
                "email": "badcsrf@example.com",
                "password": "StrongPass123",
                "confirm_password": "StrongPass123",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403
        assert "Session expired" in response.text
        assert "text/html" in response.headers.get("content-type", "")
        assert "detail" not in response.text


def test_login_page_hidden_csrf_matches_cookie(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret-supersecret-supersecret")
    monkeypatch.setenv("APP__ENV", "test")
    from app.main import app

    with TestClient(app) as client:
        _inject_auth_service(app)
        response = client.get("/auth/login")
        assert response.status_code == 200
        cookie = client.cookies.get(app.state.settings.session.csrf_cookie_name)
        hidden = _get_csrf_from_html(response.text)
        assert hidden == cookie
