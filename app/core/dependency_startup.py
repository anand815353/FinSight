"""Startup dependency probes for local development (warn and continue)."""

import logging

from app.core.config import Settings
from app.core.dependency_targets import (
    HOST_DEV_REMEDIATION,
    build_runtime_profile_context,
    mixed_profile_remediation,
    profile_mismatch_remediation,
    uses_compose_service_hostname,
)
from app.db.mongodb import MongoClientManager
from app.db.qdrant import QdrantClientManager
from app.db.redis import RedisClientManager

logger = logging.getLogger("finsight")

_QDRANT_LOCAL_KEY_HINT = " Local Docker Qdrant does not require an API key."


def _log_profile_warnings(ctx, settings: Settings) -> None:
    if settings.app.env not in {"local", "development"}:
        return
    if ctx.detected == "mixed":
        logger.warning(
            "runtime_profile_inconsistent detected=%s remediation=%s",
            ctx.detected,
            mixed_profile_remediation(),
        )
    elif (
        ctx.declared in {"host", "compose"}
        and ctx.detected in {"host", "compose"}
        and ctx.declared != ctx.detected
    ):
        logger.warning(
            "runtime_profile_inconsistent declared=%s detected=%s remediation=%s",
            ctx.declared,
            ctx.detected,
            profile_mismatch_remediation(ctx.declared, ctx.detected),
        )


async def probe_dependencies(
    mongo: MongoClientManager,
    redis: RedisClientManager,
    qdrant: QdrantClientManager,
    settings: Settings,
) -> dict[str, bool]:
    ctx = build_runtime_profile_context(
        declared=settings.runtime.profile,
        mongodb_uri=settings.mongodb.uri,
        redis_url=settings.redis.url,
        qdrant_url=settings.qdrant.url,
        celery_broker_url=settings.celery.broker_url,
        celery_result_backend=settings.celery.result_backend,
    )
    _log_profile_warnings(ctx, settings)

    logger.info(
        "runtime_profile_detected profile=%s declared=%s detected=%s "
        "mongodb_target=%s redis_target=%s qdrant_target=%s",
        ctx.effective,
        ctx.declared,
        ctx.detected,
        ctx.mongodb_target,
        ctx.redis_target,
        ctx.qdrant_target,
    )

    checks: list[tuple[str, object, str, str]] = [
        ("mongodb", mongo, settings.mongodb.uri, ctx.mongodb_target),
        ("redis", redis, settings.redis.url, ctx.redis_target),
        ("qdrant", qdrant, settings.qdrant.url, ctx.qdrant_target),
    ]

    results: dict[str, bool] = {}
    failed: list[str] = []
    remediation_parts: list[str] = []

    for name, manager, connection_url, target in checks:
        ok = await manager.ping(quiet=True)
        results[name] = ok
        if ok:
            continue

        failed.append(name)
        hint = HOST_DEV_REMEDIATION if uses_compose_service_hostname(connection_url) else (
            "Ensure the service is running and reachable at the configured URL."
        )
        if name == "qdrant":
            hint = f"{hint}{_QDRANT_LOCAL_KEY_HINT}"
        remediation_parts.append(f"{name}: {hint}")

        logger.warning(
            "dependency_unreachable at startup: dependency=%s target=%s remediation=%s",
            name,
            target,
            hint,
        )

    if not failed:
        logger.info(
            "runtime_profile_ready profile=%s mongodb=true redis=true qdrant=true",
            ctx.effective,
        )
        return results

    remediation = " | ".join(remediation_parts)
    logger.warning(
        "runtime_profile_degraded profile=%s failed_dependencies=%s remediation=%s",
        ctx.effective,
        ",".join(failed),
        remediation,
    )
    return results
