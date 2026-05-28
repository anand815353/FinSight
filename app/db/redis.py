import logging

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.dependency_targets import format_redis_target

logger = logging.getLogger("finsight")


class RedisClientManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Redis | None = None

    async def connect(self) -> None:
        self._client = Redis.from_url(self._settings.redis.url)

    async def ping(self, *, quiet: bool = False) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception as exc:
            if not quiet:
                target = format_redis_target(self._settings.redis.url)
                logger.warning(
                    "dependency_ping_failed dependency=redis target=%s error_type=%s",
                    target,
                    type(exc).__name__,
                )
            return False

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
