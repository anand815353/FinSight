import asyncio

import pytest

from app.core.config import Settings
from app.models.source_registry import SourceExchange, SourceRegistryCreate, SourceType
from app.repositories.source_registry_repo import (
    SourceRegistryDuplicateError,
    SourceRegistryRepository,
)


class _FakeSourceCollection:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.index_calls: list = []

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return "ok"

    async def insert_one(self, doc):
        for item in self.items.values():
            if (
                item["company_id"] == doc["company_id"]
                and item["source_type"] == doc["source_type"]
                and item["source_url"] == doc["source_url"]
            ):
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("duplicate")
        self.items[doc["_id"]] = doc

    async def replace_one(self, query, doc, upsert=False):
        key = query["_id"]
        if key in self.items or upsert:
            self.items[key] = doc

    async def find_one(self, query):
        if "_id" in query:
            return self.items.get(query["_id"])
        for item in self.items.values():
            if all(item.get(k) == v for k, v in query.items()):
                return item
        return None

    def find(self, query):
        return _FakeSourceCursor(self.items.values(), query)


class _FakeSourceCursor:
    def __init__(self, values, query):
        self._values = [v for v in values if all(v.get(k) == qv for k, qv in query.items())]
        self._index = 0

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._values):
            raise StopAsyncIteration
        item = self._values[self._index]
        self._index += 1
        return item


class _FakeClient:
    def __init__(self):
        self.source_registry = _FakeSourceCollection()

    def __getitem__(self, _db_name):
        return {"source_registry": self.source_registry}


class _FakeMongoManager:
    def __init__(self):
        self._client = _FakeClient()


def test_source_registry_repo_create_and_list(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = SourceRegistryRepository(_FakeMongoManager(), Settings())
    asyncio.run(repo.ensure_indexes())
    payload = SourceRegistryCreate(
        company_id="infosys",
        source_type=SourceType.NSE_CORPORATE_FILINGS,
        source_name="NSE",
        source_url="https://example.invalid/infosys/nse",
        exchange=SourceExchange.NSE,
    )
    entry = asyncio.run(repo.create(payload))
    assert entry.company_id == "infosys"
    entries = asyncio.run(repo.list_by_company("infosys"))
    assert len(entries) == 1


def test_source_registry_duplicate(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = SourceRegistryRepository(_FakeMongoManager(), Settings())
    payload = SourceRegistryCreate(
        company_id="itc",
        source_type=SourceType.BSE_CORPORATE_FILINGS,
        source_name="BSE",
        source_url="https://example.invalid/itc/bse",
        exchange=SourceExchange.BSE,
    )
    asyncio.run(repo.create(payload))
    with pytest.raises(SourceRegistryDuplicateError):
        asyncio.run(repo.create(payload))
