from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.models.user import User, UserRole


class UserAlreadyExistsError(Exception):
    pass


class UserRepository:
    def __init__(self, mongo_manager, settings: Settings):
        self._mongo_manager = mongo_manager
        self._settings = settings

    @property
    def _collection(self):
        client = getattr(self._mongo_manager, "_client", None)
        if client is None:
            raise RuntimeError("Mongo client not initialized.")
        return client[self._settings.mongodb.db_name]["users"]

    async def ensure_indexes(self) -> None:
        try:
            await self._collection.create_index([("email", ASCENDING)], unique=True)
            await self._collection.create_index([("role", ASCENDING)])
            await self._collection.create_index([("status", ASCENDING)])
        except Exception:
            # Tests and local startup may run without a live MongoDB instance.
            return

    async def create_user(
        self, *, email: str, password_hash: str, full_name: str | None, role: UserRole = UserRole.FREE_USER
    ) -> User:
        now = datetime.now(UTC)
        user_doc = {
            "_id": str(uuid4()),
            "email": email.lower(),
            "password_hash": password_hash,
            "full_name": full_name,
            "role": role.value,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
            "failed_login_count": 0,
            "preferences": {},
        }
        try:
            await self._collection.insert_one(user_doc)
        except DuplicateKeyError as exc:
            raise UserAlreadyExistsError("Email already exists.") from exc
        return User.model_validate(user_doc)

    async def get_by_email(self, email: str) -> User | None:
        doc = await self._collection.find_one({"email": email.lower()})
        return User.model_validate(doc) if doc else None

    async def get_by_id(self, user_id: str) -> User | None:
        doc = await self._collection.find_one({"_id": user_id})
        return User.model_validate(doc) if doc else None

    async def update_login_metadata(self, user_id: str, *, success: bool) -> None:
        update = {"$set": {"updated_at": datetime.now(UTC)}}
        if success:
            update["$set"]["last_login_at"] = datetime.now(UTC)
            update["$set"]["failed_login_count"] = 0
        else:
            update["$inc"] = {"failed_login_count": 1}
        await self._collection.update_one({"_id": user_id}, update)
