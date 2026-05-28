from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "finsight",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
)

celery_app.conf.update(task_default_queue="default")
