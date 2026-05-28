import asyncio
import pytest

from app.core.config import Settings
from app.models.user import UserRole
from app.repositories.user_repo import UserAlreadyExistsError, UserRepository


class _FakeCollection:
    def __init__(self):
        self.items = {}

    async def create_index(self, *_args, **_kwargs):
        return "ok"

    async def insert_one(self, doc):
        if doc["email"] in self.items:
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate")
        self.items[doc["email"]] = doc

    async def find_one(self, query):
        if "email" in query:
            return self.items.get(query["email"])
        if "_id" in query:
            for item in self.items.values():
                if item["_id"] == query["_id"]:
                    return item
        return None

    async def update_one(self, query, update):
        item = await self.find_one(query)
        if not item:
            return
        if "$set" in update:
            item.update(update["$set"])
        if "$inc" in update:
            for key, value in update["$inc"].items():
                item[key] = item.get(key, 0) + value


class _FakeClient:
    def __init__(self):
        self.collection = _FakeCollection()

    def __getitem__(self, _db_name):
        return {"users": self.collection}


class _FakeMongoManager:
    def __init__(self):
        self._client = _FakeClient()


def test_user_repo_create_get_update(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = UserRepository(_FakeMongoManager(), Settings())
    asyncio.run(repo.ensure_indexes())
    user = asyncio.run(
        repo.create_user(
        email="repo@example.com",
        password_hash="hashed",
        full_name="Repo User",
        role=UserRole.FREE_USER,
        )
    )
    assert user.email == "repo@example.com"

    by_email = asyncio.run(repo.get_by_email("repo@example.com"))
    assert by_email is not None
    assert by_email.user_id == user.user_id

    asyncio.run(repo.update_login_metadata(user.user_id, success=False))
    failed = asyncio.run(repo.get_by_id(user.user_id))
    assert failed.failed_login_count == 1

    asyncio.run(repo.update_login_metadata(user.user_id, success=True))
    success = asyncio.run(repo.get_by_id(user.user_id))
    assert success.failed_login_count == 0
    assert success.last_login_at is not None


def test_user_repo_duplicate_email(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    repo = UserRepository(_FakeMongoManager(), Settings())
    asyncio.run(repo.create_user(email="dup@example.com", password_hash="x", full_name=None))
    with pytest.raises(UserAlreadyExistsError):
        asyncio.run(repo.create_user(email="dup@example.com", password_hash="y", full_name=None))
