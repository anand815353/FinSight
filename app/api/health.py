from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.dependency_targets import build_runtime_profile_context

router = APIRouter(prefix="/health", tags=["health"])

_LOCAL_DEV_HINTS = (
    "Host-run dev: set MONGODB__URI, REDIS__URL, and QDRANT__URL to 127.0.0.1 "
    "(not compose service names mongodb/redis/qdrant).",
    "Local Qdrant in Docker does not require QDRANT__API_KEY.",
    "Auth requires MongoDB: docker compose up -d mongodb redis qdrant",
)


def runtime_profile_payload(settings: Settings) -> dict[str, str]:
    ctx = build_runtime_profile_context(
        declared=settings.runtime.profile,
        mongodb_uri=settings.mongodb.uri,
        redis_url=settings.redis.url,
        qdrant_url=settings.qdrant.url,
        celery_broker_url=settings.celery.broker_url,
        celery_result_backend=settings.celery.result_backend,
    )
    return {
        "runtime_profile": ctx.effective,
        "runtime_profile_declared": ctx.declared,
        "runtime_profile_detected": ctx.detected,
    }


@router.get("/live")
def health_live(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    payload = {
        "status": "ok",
        "app": settings.app.name,
        "env": settings.app.env,
    }
    payload.update(runtime_profile_payload(settings))
    return payload


@router.get("/ready")
async def health_ready(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    mongo_ok = await request.app.state.mongo.ping()
    redis_ok = await request.app.state.redis.ping()
    qdrant_ok = await request.app.state.qdrant.ping()
    ready = mongo_ok and redis_ok and qdrant_ok
    payload: dict[str, object] = {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "dependencies": {
            "mongodb": mongo_ok,
            "redis": redis_ok,
            "qdrant": qdrant_ok,
        },
    }
    payload.update(runtime_profile_payload(settings))
    if not ready and settings.app.env in {"local", "development"}:
        payload["hints"] = list(_LOCAL_DEV_HINTS)
    if ready:
        return payload
    return JSONResponse(status_code=503, content=payload)
