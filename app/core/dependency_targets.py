"""Connection target helpers and runtime profile detection (no secrets)."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

COMPOSE_SERVICE_HOSTS = frozenset({"mongodb", "redis", "qdrant"})
HOST_DEV_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

HOST_DEV_REMEDIATION = (
    "When running uvicorn on the host (PyCharm), use 127.0.0.1 in .env "
    "instead of Docker Compose service names (mongodb, redis, qdrant)."
)

HostnameClass = Literal["host", "compose", "unknown"]
DetectedProfile = Literal["host", "compose", "mixed", "unknown"]
DeclaredProfile = Literal["host", "compose", "auto"]


@dataclass(frozen=True)
class RuntimeProfileContext:
    declared: DeclaredProfile
    detected: DetectedProfile
    effective: str
    mongodb_target: str
    redis_target: str
    qdrant_target: str


def format_mongodb_target(uri: str) -> str:
    parsed = urlparse(uri)
    host = parsed.hostname or "unknown"
    port = parsed.port or 27017
    return f"{host}:{port}"


def format_redis_target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    port = parsed.port or 6379
    return f"{host}:{port}"


def format_qdrant_target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    port = parsed.port or 6333
    return f"{host}:{port}"


def classify_connection_url(url: str) -> HostnameClass:
    hostname = urlparse(url).hostname
    if hostname in HOST_DEV_HOSTNAMES:
        return "host"
    if hostname in COMPOSE_SERVICE_HOSTS:
        return "compose"
    return "unknown"


def collect_dependency_urls(
    mongodb_uri: str,
    redis_url: str,
    qdrant_url: str,
    celery_broker_url: str,
    celery_result_backend: str,
) -> list[str]:
    return [mongodb_uri, redis_url, qdrant_url, celery_broker_url, celery_result_backend]


def detect_profile_from_urls(urls: list[str]) -> DetectedProfile:
    classifications = {classify_connection_url(url) for url in urls}
    host_like = classifications & {"host", "compose"}
    if "host" in host_like and "compose" in host_like:
        return "mixed"
    if classifications == {"host"}:
        return "host"
    if classifications == {"compose"}:
        return "compose"
    if classifications == {"unknown"} or not classifications:
        return "unknown"
    if "unknown" in classifications and host_like:
        return "mixed"
    return "unknown"


def resolve_runtime_profile(declared: DeclaredProfile, detected: DetectedProfile) -> str:
    if declared == "auto":
        return detected
    return declared


def uses_compose_service_hostname(connection_url: str) -> bool:
    return classify_connection_url(connection_url) == "compose"


def profile_mismatch_remediation(declared: DeclaredProfile, detected: DetectedProfile) -> str:
    return (
        f"RUNTIME__PROFILE is '{declared}' but dependency URLs imply '{detected}'. "
        "Align RUNTIME__PROFILE with your URLs or switch dependency hostnames."
    )


def mixed_profile_remediation() -> str:
    return (
        "Dependency URLs mix host loopback (127.0.0.1) and Compose service names "
        "(mongodb, redis, qdrant). Use one profile consistently in .env."
    )


def build_runtime_profile_context(
    *,
    declared: DeclaredProfile,
    mongodb_uri: str,
    redis_url: str,
    qdrant_url: str,
    celery_broker_url: str,
    celery_result_backend: str,
) -> RuntimeProfileContext:
    detected = detect_profile_from_urls(
        collect_dependency_urls(
            mongodb_uri,
            redis_url,
            qdrant_url,
            celery_broker_url,
            celery_result_backend,
        )
    )
    effective = resolve_runtime_profile(declared, detected)
    return RuntimeProfileContext(
        declared=declared,
        detected=detected,
        effective=effective,
        mongodb_target=format_mongodb_target(mongodb_uri),
        redis_target=format_redis_target(redis_url),
        qdrant_target=format_qdrant_target(qdrant_url),
    )
