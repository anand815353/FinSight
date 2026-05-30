from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.dependency_targets import (
    collect_dependency_urls,
    detect_profile_from_urls,
)

VALID_ENVS = {"local", "development", "staging", "production", "test"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
WEAK_SECRETS = {"change-me-local", "changeme", "default", "secret", "password"}


class AppSettings(BaseModel):
    name: str = "FinSight"
    env: Literal["local", "development", "staging", "production", "test"] = "local"
    debug: bool = False
    secret_key: SecretStr = Field(..., min_length=8)


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_logs: bool = True

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        level = value.upper()
        if level not in VALID_LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}")
        return level


class RuntimeSettings(BaseModel):
    profile: Literal["host", "compose", "auto"] = "auto"
    strict_profile_validation: bool = True


class FeatureSettings(BaseModel):
    enable_rag: bool = False
    enable_ingestion: bool = False
    enable_red_flag_scanner: bool = False
    enable_exports: bool = False


class MongoSettings(BaseModel):
    uri: str = "mongodb://mongodb:27017"
    db_name: str = "finsight"


class RedisSettings(BaseModel):
    url: str = "redis://redis:6379/0"


class QdrantSettings(BaseModel):
    url: str = "http://qdrant:6333"
    api_key: SecretStr | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def normalize_empty_api_key(cls, value):
        if value in ("", None):
            return None
        return value


class CelerySettings(BaseModel):
    broker_url: str = "redis://redis:6379/1"
    result_backend: str = "redis://redis:6379/2"


class SessionSettings(BaseModel):
    cookie_name: str = "finsight_session"
    cookie_secure: bool = False
    cookie_http_only: bool = True
    cookie_same_site: str = "lax"
    access_token_expire_minutes: int = 480
    csrf_cookie_name: str = "finsight_csrf"

    @field_validator("cookie_same_site")
    @classmethod
    def validate_same_site(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("SESSION cookie_same_site must be one of: lax, strict, none")
        return normalized

    @field_validator("access_token_expire_minutes")
    @classmethod
    def validate_access_token_expiry(cls, value: int) -> int:
        if value < 5:
            raise ValueError("SESSION access_token_expire_minutes must be >= 5")
        return value


class LLMSettings(BaseModel):
    provider: str = "gemini"
    model_name: str = "placeholder-model"
    api_key: SecretStr | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def normalize_empty_api_key(cls, value):
        if value in ("", None):
            return None
        return value


class EmbeddingSettings(BaseModel):
    collection_name: str = "finsight_chunks_gemini_v1"
    model_version: str = "v1"


class UsageSettings(BaseModel):
    free_daily_question_limit: int = 3
    beta_daily_question_limit: int = 5
    monthly_budget_inr: int = 5000


class StorageSettings(BaseModel):
    document_storage_path: str = "data/filings"
    admin_pending_upload_path: str = "storage/admin_uploads/pending"
    max_upload_bytes: int = 52_428_800
    allowed_upload_mime_types: list[str] = Field(default_factory=lambda: ["application/pdf"])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    mongodb: MongoSettings = Field(default_factory=MongoSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.app.env not in VALID_ENVS:
            raise ValueError(f"APP_ENV must be one of {sorted(VALID_ENVS)}")
        if self.app.env == "production":
            if self.app.debug:
                raise ValueError("APP_DEBUG must be false in production")
            secret_value = self.app.secret_key.get_secret_value()
            if len(secret_value) < 32:
                raise ValueError("APP_SECRET_KEY must be at least 32 chars in production")
            if secret_value.lower() in WEAK_SECRETS:
                raise ValueError("APP_SECRET_KEY is weak/default for production")
            if not self.session.cookie_secure:
                raise ValueError("SESSION cookie_secure must be true in production")
        return self

    @model_validator(mode="after")
    def validate_runtime_profile(self) -> "Settings":
        if self.app.env == "test":
            return self

        urls = collect_dependency_urls(
            self.mongodb.uri,
            self.redis.url,
            self.qdrant.url,
            self.celery.broker_url,
            self.celery.result_backend,
        )
        detected = detect_profile_from_urls(urls)
        declared = self.runtime.profile

        mismatch = (
            declared in {"host", "compose"}
            and detected in {"host", "compose"}
            and declared != detected
        )
        is_inconsistent = detected == "mixed" or mismatch

        if self.app.env in {"production", "staging"}:
            if is_inconsistent:
                if detected == "mixed":
                    raise ValueError(
                        "Runtime profile URLs are mixed (host and compose hostnames). "
                        "Use a single consistent profile in production."
                    )
                raise ValueError(
                    f"RUNTIME__PROFILE '{declared}' does not match detected profile '{detected}'."
                )

        if self.runtime.strict_profile_validation and is_inconsistent:
            if detected == "mixed":
                raise ValueError(
                    "Runtime profile URLs are mixed. Set RUNTIME__STRICT_PROFILE_VALIDATION=false "
                    "to warn only in local development."
                )
            raise ValueError(
                f"RUNTIME__PROFILE '{declared}' does not match detected profile '{detected}'."
            )

        return self

    def safe_summary(self) -> dict[str, str | int | bool]:
        return {
            "app.name": self.app.name,
            "app.env": self.app.env,
            "app.debug": self.app.debug,
            "logging.level": self.logging.level,
            "logging.json_logs": self.logging.json_logs,
            "runtime.profile": self.runtime.profile,
            "runtime.strict_profile_validation": self.runtime.strict_profile_validation,
            "features.enable_rag": self.features.enable_rag,
            "features.enable_ingestion": self.features.enable_ingestion,
            "features.enable_red_flag_scanner": self.features.enable_red_flag_scanner,
            "features.enable_exports": self.features.enable_exports,
            "mongodb.uri": "<redacted>",
            "mongodb.db_name": self.mongodb.db_name,
            "redis.url": "<redacted>",
            "qdrant.url": "<redacted>",
            "qdrant.api_key": "<redacted>" if self.qdrant.api_key else None,
            "celery.broker_url": "<redacted>",
            "celery.result_backend": "<redacted>",
            "session.cookie_name": self.session.cookie_name,
            "session.cookie_secure": self.session.cookie_secure,
            "session.cookie_same_site": self.session.cookie_same_site,
            "session.access_token_expire_minutes": self.session.access_token_expire_minutes,
            "session.csrf_cookie_name": self.session.csrf_cookie_name,
            "llm.provider": self.llm.provider,
            "llm.model_name": self.llm.model_name,
            "llm.api_key": "<redacted>" if self.llm.api_key else None,
            "embedding.collection_name": self.embedding.collection_name,
            "embedding.model_version": self.embedding.model_version,
            "usage.free_daily_question_limit": self.usage.free_daily_question_limit,
            "usage.beta_daily_question_limit": self.usage.beta_daily_question_limit,
            "usage.monthly_budget_inr": self.usage.monthly_budget_inr,
            "storage.document_storage_path": self.storage.document_storage_path,
            "storage.admin_pending_upload_path": self.storage.admin_pending_upload_path,
            "storage.max_upload_bytes": self.storage.max_upload_bytes,
            "app.secret_key": "<redacted>",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
