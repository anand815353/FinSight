import asyncio

from app.core.config import Settings
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.source_registry_repo import SourceRegistryRepository
from tests.test_company_repo import _FakeMongoManager as _CompanyMongo
from tests.test_document_repo_fakes import _FakeMongoManager as _DocumentMongo
from tests.test_source_registry_repo import _FakeMongoManager as _SourceMongo


def test_company_repo_index_definitions(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _CompanyMongo()
    repo = CompanyRepository(mongo, Settings())
    asyncio.run(repo.ensure_indexes())
    keys = [call[0] for call in mongo._client.companies.index_calls]
    assert [("_id", 1)] in keys
    assert [("nse_symbol", 1)] in keys


def test_source_registry_repo_index_definitions(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _SourceMongo()
    repo = SourceRegistryRepository(mongo, Settings())
    asyncio.run(repo.ensure_indexes())
    keys = [call[0] for call in mongo._client.source_registry.index_calls]
    assert [("_id", 1)] in keys
    assert ("company_id", 1) in keys[1]


def test_document_repo_index_definitions(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _DocumentMongo()
    repo = DocumentRepository(mongo, Settings())
    asyncio.run(repo.ensure_indexes())
    keys = [call[0] for call in mongo._client.documents.index_calls]
    assert [("_id", 1)] in keys
    assert ("company_id", 1) in keys[2]
