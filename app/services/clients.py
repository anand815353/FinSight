from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ServiceClients:
    mongodb_uri: str
    redis_url: str
    qdrant_url: str
    celery_broker_url: str
    celery_result_backend: str


def build_service_clients(settings: Settings) -> ServiceClients:
    return ServiceClients(
        mongodb_uri=settings.MONGODB_URI,
        redis_url=settings.REDIS_URL,
        qdrant_url=settings.QDRANT_URL,
        celery_broker_url=settings.CELERY_BROKER_URL,
        celery_result_backend=settings.CELERY_RESULT_BACKEND,
    )
