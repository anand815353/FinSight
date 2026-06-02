import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.company import Company, CompanyStatus


class _AuthServiceDenied:
    async def get_current_user(self, _request):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Not authenticated")


class _AuditRepoStub:
    async def log_event(self, **_kwargs):
        return None


@pytest.fixture
def client_setup(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    app = create_app()
    app.state.company_service = _CompanyServiceStub()
    app.state.document_service = _DocumentServiceStub()
    app.state.auth_service = _AuthServiceDenied()
    app.state.audit_repo = _AuditRepoStub()
    return TestClient(app)


def _seed_company(company_id: str, symbol: str) -> Company:
    return Company(
        _id=company_id,
        name=f"{symbol} Ltd",
        display_name=symbol,
        nse_symbol=symbol,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    )


class _CompanyServiceStub:
    async def list_companies(self, query=None):
        if query and query.lower() == "missing":
            return []
        return [
            _seed_company("reliance_industries", "RELIANCE"),
            _seed_company("infosys", "INFY"),
        ]

    def to_public(self, company):
        from app.models.company import CompanyPublic

        return CompanyPublic(
            company_id=company.company_id,
            name=company.name,
            display_name=company.display_name,
            nse_symbol=company.nse_symbol,
            is_seed_company=company.is_seed_company,
            status=company.status,
        )

    async def get_company(self, company_id: str):
        if company_id == "unknown":
            return None
        return _seed_company(company_id, "RELIANCE")


class _DocumentServiceStub:
    async def list_all_documents(self, limit=100):
        return []


def test_list_companies_public(client_setup):
    client = client_setup
    response = client.get("/api/companies")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["nse_symbol"] == "RELIANCE"


def test_get_company_not_found(client_setup):
    client = client_setup
    response = client.get("/api/companies/unknown")
    assert response.status_code == 404


def test_admin_documents_requires_admin(client_setup):
    client = client_setup
    response = client.get("/api/admin/documents")
    assert response.status_code in {401, 403, 422}
