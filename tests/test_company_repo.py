import asyncio

import pytest

from app.core.config import Settings
from app.models.company import CompanyCreate, CompanyStatus
from app.repositories.company_repo import CompanyAlreadyExistsError, CompanyRepository


class _FakeCollection:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.index_calls: list = []

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return "ok"

    async def insert_one(self, doc):
        if doc["_id"] in self.items:
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
        if "nse_symbol" in query:
            for item in self.items.values():
                if item.get("nse_symbol") == query["nse_symbol"]:
                    return item
        return None

    def find(self, query):
        return _FakeCursor(self.items.values(), query)

    async def limit(self, _n):
        return self


class _FakeCursor:
    def __init__(self, values, query):
        self._values = list(values)
        self._query = query
        self._sort_key = None
        self._limit = None

    def sort(self, key, _direction=1):
        self._sort_key = key
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        filtered = []
        for item in self._values:
            if self._matches(item):
                filtered.append(item)
        if self._sort_key:
            filtered.sort(key=lambda x: x.get(self._sort_key) or 0)
        if self._limit is not None:
            filtered = filtered[: self._limit]
        self._filtered = filtered
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._filtered):
            raise StopAsyncIteration
        item = self._filtered[self._index]
        self._index += 1
        return item

    def _matches(self, item):
        if self._query.get("status") and item.get("status") != self._query["status"]:
            return False
        if "$or" in self._query:
            import re

            for clause in self._query["$or"]:
                for field, pattern in clause.items():
                    if field in item and pattern.search(str(item[field])):
                        return True
            return False
        return True


class _FakeClient:
    def __init__(self):
        self.companies = _FakeCollection()

    def __getitem__(self, _db_name):
        return {"companies": self.companies}


class _FakeMongoManager:
    def __init__(self):
        self._client = _FakeClient()


def test_company_repo_crud_and_upsert(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = CompanyRepository(_FakeMongoManager(), Settings())
    asyncio.run(repo.ensure_indexes())
    assert repo._collection.index_calls

    payload = CompanyCreate(
        company_id="reliance_industries",
        name="Reliance Industries Ltd",
        display_name="Reliance Industries",
        nse_symbol="RELIANCE",
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    )
    created = asyncio.run(repo.create(payload))
    assert created.nse_symbol == "RELIANCE"

    by_id = asyncio.run(repo.get_by_id("reliance_industries"))
    assert by_id is not None

    by_symbol = asyncio.run(repo.get_by_symbol("reliance"))
    assert by_symbol is not None

    upserted = asyncio.run(repo.upsert(payload))
    assert upserted.company_id == "reliance_industries"


def test_company_repo_duplicate(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = CompanyRepository(_FakeMongoManager(), Settings())
    payload = CompanyCreate(
        company_id="itc",
        name="ITC Ltd",
        display_name="ITC",
        nse_symbol="ITC",
    )
    asyncio.run(repo.create(payload))
    with pytest.raises(CompanyAlreadyExistsError):
        asyncio.run(repo.create(payload))
