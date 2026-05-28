import json
import logging

from app.core.config import Settings
from app.core.logging import JsonFormatter, configure_logging, redact_log_message


def test_httpx_logger_warning_when_not_debug(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("LOGGING__LEVEL", "INFO")
    monkeypatch.setenv("APP__DEBUG", "false")
    settings = Settings()

    configure_logging(settings)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_non_json_logging_mode_does_not_crash(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("LOGGING__JSON_LOGS", "false")
    settings = Settings()

    configure_logging(settings)
    logger = logging.getLogger("third_party_like_logger")

    # Should not raise even if log record does not provide custom fields.
    logger.info("plain info message without extras")


def test_redact_log_message_preserves_operational_setting_names():
    message = (
        "dependency_unreachable at startup: dependency=qdrant target=qdrant:6333 "
        "remediation=Local Docker Qdrant does not require QDRANT__API_KEY."
    )
    redacted = redact_log_message(message)
    assert "QDRANT__API_KEY" in redacted
    assert "[redacted-sensitive-message]" not in redacted


def test_redact_log_message_redacts_sensitive_values():
    message = "login failed password=super-secret-value token=abc123xyz"
    redacted = redact_log_message(message)
    assert "super-secret-value" not in redacted
    assert "abc123xyz" not in redacted
    assert "password=[redacted]" in redacted
    assert "token=[redacted]" in redacted


def test_json_formatter_preserves_operational_dependency_warning(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    record = logging.LogRecord(
        name="finsight",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=(
            "dependency_unreachable at startup: dependency=qdrant target=qdrant:6333 "
            "remediation=Local Docker Qdrant does not require an API key."
        ),
        args=(),
        exc_info=None,
    )
    record.environment = "local"

    payload = json.loads(JsonFormatter().format(record))
    assert "dependency_unreachable" in payload["message"]
    assert "qdrant:6333" in payload["message"]
    assert payload["message"] != "[redacted-sensitive-message]"


def test_json_formatter_redacts_api_key_assignment_values(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    record = logging.LogRecord(
        name="finsight",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="configured api_key=sk-live-abcdef1234567890",
        args=(),
        exc_info=None,
    )
    record.environment = "local"

    payload = json.loads(JsonFormatter().format(record))
    assert "sk-live-abcdef1234567890" not in payload["message"]
    assert "[redacted]" in payload["message"]
