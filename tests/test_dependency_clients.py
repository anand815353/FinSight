import asyncio

from app.core.config import Settings
from app.db.mongodb import MongoClientManager
from app.db.qdrant import QdrantClientManager
from app.db.redis import RedisClientManager


class _MongoOk:
    class _Admin:
        @staticmethod
        async def command(_: str) -> dict[str, int]:
            return {"ok": 1}

    admin = _Admin()

    @staticmethod
    def close() -> None:
        return None


class _MongoFail:
    class _Admin:
        @staticmethod
        async def command(_: str) -> dict[str, int]:
            raise RuntimeError("boom")

    admin = _Admin()

    @staticmethod
    def close() -> None:
        return None


class _RedisOk:
    @staticmethod
    async def ping() -> bool:
        return True

    @staticmethod
    async def aclose() -> None:
        return None


class _RedisFail:
    @staticmethod
    async def ping() -> bool:
        raise RuntimeError("boom")

    @staticmethod
    async def aclose() -> None:
        return None


class _QdrantOk:
    @staticmethod
    async def get_collections() -> dict[str, list]:
        return {"collections": []}

    @staticmethod
    async def close() -> None:
        return None


class _QdrantFail:
    @staticmethod
    async def get_collections() -> dict[str, list]:
        raise RuntimeError("boom")

    @staticmethod
    async def close() -> None:
        return None


def test_dependency_client_ping_methods_handle_success_and_failure(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    settings = Settings()

    mongo = MongoClientManager(settings)
    mongo._client = _MongoOk()
    assert asyncio.run(mongo.ping()) is True
    mongo._client = _MongoFail()
    assert asyncio.run(mongo.ping()) is False

    redis = RedisClientManager(settings)
    redis._client = _RedisOk()
    assert asyncio.run(redis.ping()) is True
    redis._client = _RedisFail()
    assert asyncio.run(redis.ping()) is False

    qdrant = QdrantClientManager(settings)
    qdrant._client = _QdrantOk()
    assert asyncio.run(qdrant.ping()) is True
    qdrant._client = _QdrantFail()
    assert asyncio.run(qdrant.ping()) is False
