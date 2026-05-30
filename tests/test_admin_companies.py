import pytest
from fastapi.testclient import TestClient

from app.models.audit_log import AuditEventType
from tests.test_admin_helpers import (
    admin_test_client,
    get_csrf,
    get_csrf_from_html,
    login,
    seed_admin_user,
    seed_free_user,
)


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    upload_root = str(tmp_path / "uploads")
    with admin_test_client(monkeypatch, upload_root=upload_root) as (client, app, user_repo, audit_repo):
        import asyncio

        asyncio.run(seed_admin_user(user_repo))
        login(client, app, "admin@example.com", "StrongPass123")
        yield client, app, user_repo, audit_repo, upload_root


@pytest.fixture
def anonymous_client(monkeypatch, tmp_path):
    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (client, app, *_rest):
        yield client, app


def test_anonymous_cannot_access_admin_company_routes(anonymous_client):
    client, _app = anonymous_client
    for path in (
        "/admin",
        "/admin/companies",
        "/admin/companies/new",
        "/admin/documents",
        "/admin/documents/new",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in {401, 403, 422}


def test_non_admin_cannot_access_admin_company_routes(monkeypatch, tmp_path):
    import asyncio

    with admin_test_client(monkeypatch, upload_root=str(tmp_path / "uploads")) as (
        client,
        app,
        user_repo,
        _audit,
    ):
        asyncio.run(seed_free_user(user_repo))
        login(client, app, "user@example.com", "StrongPass123")
        response = client.get("/admin/companies")
        assert response.status_code == 403


def test_admin_can_access_dashboard(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Admin Dashboard" in response.text


def test_admin_can_open_company_create_form(admin_client):
    client, _app, *_rest = admin_client
    response = client.get("/admin/companies/new")
    assert response.status_code == 200
    assert "Create Company" in response.text
    assert 'name="csrf_token"' in response.text


def test_admin_can_create_company(admin_client):
    client, app, _user_repo, audit_repo, _upload_root = admin_client
    client.get("/admin/companies/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/companies",
        data={
            "company_id": "test_co",
            "name": "Test Co Ltd",
            "display_name": "Test Co",
            "nse_symbol": "TESTCO",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies/test_co"

    detail = client.get("/admin/companies/test_co")
    assert detail.status_code == 200
    assert "Test Co" in detail.text

    company_created_events = [
        event
        for event in audit_repo.events
        if event["event_type"] == AuditEventType.ADMIN_COMPANY_CREATED
    ]
    assert company_created_events
    assert company_created_events[-1]["details"]["company_id"] == "test_co"
    assert company_created_events[-1]["details"]["nse_symbol"] == "TESTCO"


def test_duplicate_company_is_rejected(admin_client):
    client, app, *_rest = admin_client
    client.get("/admin/companies/new")
    csrf = get_csrf(client, app)
    payload = {
        "company_id": "dup_co",
        "name": "Dup Co Ltd",
        "display_name": "Dup Co",
        "nse_symbol": "DUPCO",
        "csrf_token": csrf,
    }
    first = client.post("/admin/companies", data=payload, follow_redirects=False)
    assert first.status_code == 303

    client.get("/admin/companies/new")
    csrf = get_csrf(client, app)
    payload["csrf_token"] = csrf
    duplicate = client.post("/admin/companies", data=payload, follow_redirects=False)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.text.lower()


def test_admin_form_hidden_csrf_matches_cookie(admin_client):
    client, app, *_rest = admin_client
    response = client.get("/admin/companies/new")
    assert response.status_code == 200
    cookie = client.cookies.get(app.state.settings.session.csrf_cookie_name)
    hidden = get_csrf_from_html(response.text)
    assert hidden == cookie


def test_admin_company_create_requires_csrf(admin_client):
    client, _app, *_rest = admin_client
    response = client.post(
        "/admin/companies",
        data={
            "company_id": "no_csrf",
            "name": "No CSRF Ltd",
            "display_name": "No CSRF",
            "nse_symbol": "NOCSRF",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Session expired" in response.text
    assert "text/html" in response.headers.get("content-type", "")


def test_admin_company_id_generated_from_symbol(admin_client):
    client, app, *_rest = admin_client
    client.get("/admin/companies/new")
    csrf = get_csrf(client, app)
    response = client.post(
        "/admin/companies",
        data={
            "name": "Alpha Ltd",
            "display_name": "Alpha",
            "nse_symbol": "ALPHA",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies/alpha"
