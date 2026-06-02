from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.companies import router as companies_router
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
from app.repositories.company_repo import CompanyRepository
from app.repositories.document_chunk_repo import DocumentChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.document_validation_repo import DocumentValidationRepository
from app.repositories.processing_job_repo import ProcessingJobRepository
from app.repositories.source_registry_repo import SourceRegistryRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.company_service import CompanyService
from app.services.document_chunk_service import DocumentChunkService
from app.services.document_readiness_service import DocumentReadinessService
from app.services.document_parse_service import DocumentParseService
from app.services.document_service import DocumentService
from app.services.embedding_provider import get_embedding_provider
from app.services.processing_job_service import ProcessingJobService
from app.services.qdrant_indexing_service import QdrantIndexingService
from app.services.retrieval_service import RetrievalService
from app.services.source_registry_service import SourceRegistryService


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
    app.state.company_repo = CompanyRepository(app.state.mongo, settings)
    app.state.source_registry_repo = SourceRegistryRepository(app.state.mongo, settings)
    app.state.document_repo = DocumentRepository(app.state.mongo, settings)
    app.state.processing_job_repo = ProcessingJobRepository(app.state.mongo, settings)
    app.state.document_chunk_repo = DocumentChunkRepository(app.state.mongo, settings)
    app.state.document_validation_repo = DocumentValidationRepository(app.state.mongo, settings)
    if settings.app.env != "test":
        await app.state.user_repo.ensure_indexes()
        await app.state.audit_repo.ensure_indexes()
        await app.state.company_repo.ensure_indexes()
        await app.state.source_registry_repo.ensure_indexes()
        await app.state.document_repo.ensure_indexes()
        await app.state.processing_job_repo.ensure_indexes()
        await app.state.document_chunk_repo.ensure_indexes()
        await app.state.document_validation_repo.ensure_indexes()
    app.state.auth_service = AuthService(app.state.user_repo, app.state.audit_repo, settings)
    app.state.company_service = CompanyService(
        app.state.company_repo,
        app.state.source_registry_repo,
    )
    app.state.source_registry_service = SourceRegistryService(app.state.source_registry_repo)
    app.state.document_service = DocumentService(
        app.state.document_repo,
        company_repo=app.state.company_repo,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.processing_job_service = ProcessingJobService(
        app.state.processing_job_repo,
        app.state.document_repo,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.document_parse_service = DocumentParseService(
        app.state.document_repo,
        app.state.processing_job_repo,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.document_chunk_service = DocumentChunkService(
        app.state.document_repo,
        app.state.document_chunk_repo,
        app.state.processing_job_repo,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.embedding_provider = get_embedding_provider(settings)
    app.state.qdrant_indexing_service = QdrantIndexingService(
        app.state.document_repo,
        app.state.document_chunk_repo,
        app.state.processing_job_repo,
        app.state.qdrant,
        app.state.embedding_provider,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.document_readiness_service = DocumentReadinessService(
        app.state.document_repo,
        app.state.document_chunk_repo,
        app.state.company_repo,
        app.state.document_validation_repo,
        audit_repo=app.state.audit_repo,
        settings=settings,
    )
    app.state.retrieval_service = RetrievalService(
        app.state.qdrant,
        app.state.embedding_provider,
        app.state.document_repo,
        app.state.document_chunk_repo,
        settings=settings,
    )

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
    app.include_router(companies_router)
    app.include_router(admin_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
