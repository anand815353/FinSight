from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.api.auth import router as auth_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.dependency_startup import probe_dependencies
from app.core.logging import configure_logging
from app.db.mongodb import MongoClientManager
from app.db.qdrant import QdrantClientManager
from app.db.redis import RedisClientManager
from app.middleware.request_id import RequestIdMiddleware
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)

    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.state.mongo = MongoClientManager(settings)
    app.state.redis = RedisClientManager(settings)
    app.state.qdrant = QdrantClientManager(settings)

    await app.state.mongo.connect()
    await app.state.redis.connect()
    await app.state.qdrant.connect()
    app.state.user_repo = UserRepository(app.state.mongo, settings)
    app.state.audit_repo = AuditRepository(app.state.mongo, settings)
    if settings.app.env != "test":
        await app.state.user_repo.ensure_indexes()
        await app.state.audit_repo.ensure_indexes()
    app.state.auth_service = AuthService(app.state.user_repo, app.state.audit_repo, settings)

    if settings.app.env in {"local", "development"}:
        await probe_dependencies(
            app.state.mongo,
            app.state.redis,
            app.state.qdrant,
            settings,
        )

    yield

    await app.state.qdrant.disconnect()
    await app.state.redis.disconnect()
    await app.state.mongo.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app.name, debug=settings.app.debug, lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
