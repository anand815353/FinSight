from app.core.dependency_targets import classify_connection_url, uses_compose_service_hostname


def test_host_profile_urls_are_not_compose_hostnames():
    assert uses_compose_service_hostname("mongodb://127.0.0.1:27017") is False
    assert uses_compose_service_hostname("redis://127.0.0.1:6379/0") is False
    assert uses_compose_service_hostname("http://127.0.0.1:6333") is False
    assert uses_compose_service_hostname("mongodb://localhost:27017") is False
    assert uses_compose_service_hostname("redis://localhost:6379/0") is False
    assert uses_compose_service_hostname("http://localhost:6333") is False


def test_compose_service_names_trigger_compose_hostname_detection():
    assert uses_compose_service_hostname("mongodb://mongodb:27017") is True
    assert uses_compose_service_hostname("redis://redis:6379/0") is True
    assert uses_compose_service_hostname("http://qdrant:6333") is True


def test_classify_connection_url_host_compose_unknown():
    assert classify_connection_url("mongodb://127.0.0.1:27017") == "host"
    assert classify_connection_url("redis://redis:6379/0") == "compose"
    assert classify_connection_url("mongodb://mydb.example.com:27017") == "unknown"
