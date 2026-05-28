import asyncio

from app.core.config import Settings
from app.repositories.audit_repo import AuditRepository


class _FakeAuditCollection:
    def __init__(self):
        self.index_calls: list = []

    async def create_index(self, keys, **_kwargs):
        self.index_calls.append(keys)
        return "ok"

    async def insert_one(self, _doc):
        return None


class _FakeClient:
    def __init__(self):
        self.audit_collection = _FakeAuditCollection()

    def __getitem__(self, _db_name):
        return {"audit_logs": self.audit_collection}


class _FakeMongoManager:
    def __init__(self):
        self._client = _FakeClient()


def test_audit_repo_ensure_indexes(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    mongo = _FakeMongoManager()
    repo = AuditRepository(mongo, Settings())

    asyncio.run(repo.ensure_indexes())

    keys = mongo._client.audit_collection.index_calls
    assert [("event_type", 1)] in keys
    assert [("user_id", 1)] in keys
    assert [("created_at", -1)] in keys
    assert [("request_id", 1)] in keys
