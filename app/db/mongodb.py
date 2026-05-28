import logging

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import Settings
from app.core.dependency_targets import format_mongodb_target

logger = logging.getLogger("finsight")


class MongoClientManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: AsyncIOMotorClient | None = None

    async def connect(self) -> None:
        self._client = AsyncIOMotorClient(self._settings.mongodb.uri)

    async def ping(self, *, quiet: bool = False) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as exc:
            if not quiet:
                target = format_mongodb_target(self._settings.mongodb.uri)
                logger.warning(
                    "dependency_ping_failed dependency=mongodb target=%s error_type=%s",
                    target,
                    type(exc).__name__,
                )
            return False

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
