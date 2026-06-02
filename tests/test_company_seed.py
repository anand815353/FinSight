import asyncio

import pytest

from app.core.config import Settings
from app.repositories.company_repo import CompanyRepository
from app.repositories.source_registry_repo import SourceRegistryRepository
from app.services.company_seed_data import MVP_SEED_COMPANIES
from app.services.company_service import CompanyService
from tests.test_company_repo import _FakeClient, _FakeMongoManager


class _MultiCollectionFakeClient(_FakeClient):
    def __init__(self):
        super().__init__()
        from tests.test_source_registry_repo import _FakeSourceCollection

        self.source_registry = _FakeSourceCollection()

    def __getitem__(self, _db_name):
        return {
            "companies": self.companies,
            "source_registry": self.source_registry,
        }


class _MultiMongoManager:
    def __init__(self):
        self._client = _MultiCollectionFakeClient()


def test_mvp_seed_companies_definition():
    assert len(MVP_SEED_COMPANIES) == 5
    symbols = {c.nse_symbol for c in MVP_SEED_COMPANIES}
    assert symbols == {"RELIANCE", "HDFCBANK", "TATAMOTORS", "ITC", "INFY"}
    assert all(c.is_seed_company for c in MVP_SEED_COMPANIES)


def test_seed_mvp_companies_idempotent(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _MultiMongoManager()
    settings = Settings()
    company_repo = CompanyRepository(mongo, settings)
    source_repo = SourceRegistryRepository(mongo, settings)
    service = CompanyService(company_repo, source_repo)

    first = asyncio.run(service.seed_mvp_companies())
    second = asyncio.run(service.seed_mvp_companies())
    assert len(first) == 5
    assert len(second) == 5
    assert len(mongo._client.companies.items) == 5
    assert len(mongo._client.source_registry.items) == 0
