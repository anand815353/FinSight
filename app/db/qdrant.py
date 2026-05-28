import logging

from app.core.config import Settings
from app.core.dependency_targets import format_qdrant_target

logger = logging.getLogger("finsight")


class QdrantClientManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = None

    async def connect(self) -> None:
        try:
            from qdrant_client import AsyncQdrantClient
        except ModuleNotFoundError:
            self._client = None
            return

        # Empty api_key is correct for local Docker Qdrant (no auth by default).
        api_key = (
            self._settings.qdrant.api_key.get_secret_value()
            if self._settings.qdrant.api_key
            else None
        )
        check_compatibility = self._settings.app.env not in {
            "local",
            "development",
            "test",
        }
        self._client = AsyncQdrantClient(
            url=self._settings.qdrant.url,
            api_key=api_key,
            prefer_grpc=False,
            timeout=5,
            check_compatibility=check_compatibility,
        )

    async def ping(self, *, quiet: bool = False) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.get_collections()
            return True
        except Exception as exc:
            if not quiet:
                target = format_qdrant_target(self._settings.qdrant.url)
                logger.warning(
                    "dependency_ping_failed dependency=qdrant target=%s error_type=%s",
                    target,
                    type(exc).__name__,
                )
            return False

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
