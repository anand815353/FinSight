import json
import logging
import re
from datetime import datetime, timezone

from app.core.config import Settings
from app.middleware.request_id import request_id_ctx_var

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b("
    r"password|passwd|secret|token|api[_-]?key|cookie|authorization|auth|bearer"
    r")\b\s*[:=]\s*(\S+)"
)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bBearer\s+\S+")
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_REDACTED_VALUE = "[redacted]"


def redact_log_message(message: str) -> str:
    """Redact secret values in log text while preserving operational setting names."""
    redacted = _SENSITIVE_KEY_PATTERN.sub(
        lambda match: f"{match.group(1)}={_REDACTED_VALUE}",
        message,
    )
    redacted = _BEARER_TOKEN_PATTERN.sub(f"Bearer {_REDACTED_VALUE}", redacted)

    def _redact_long_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.isupper() and "__" in token:
            return token
        return _REDACTED_VALUE

    return _LONG_TOKEN_PATTERN.sub(_redact_long_token, redacted)


class ContextDefaultsFilter(logging.Filter):
    def __init__(self, environment: str):
        super().__init__()
        self._environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "environment"):
            record.environment = self._environment
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact_log_message(record.getMessage())

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": "finsight-api",
            "environment": getattr(record, "environment", "unknown"),
            "message": message,
            "request_id": request_id_ctx_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()  # Prevent duplicate handlers in reload/tests.

    handler = logging.StreamHandler()
    handler.addFilter(ContextDefaultsFilter(settings.app.env))
    if settings.logging.json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] [%(environment)s] [%(request_id)s] %(message)s"
            )
        )
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.logging.level)
    if settings.logging.level != "DEBUG" and not settings.app.debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("finsight").info("logging configured")
