from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.db.qdrant import QdrantClientManager


@pytest.mark.asyncio
async def test_qdrant_connect_disables_compatibility_check_in_local(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "local")
    settings = Settings()

    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    with patch("qdrant_client.AsyncQdrantClient", return_value=mock_client) as mock_ctor:
        manager = QdrantClientManager(settings)
        await manager.connect()

    mock_ctor.assert_called_once()
    kwargs = mock_ctor.call_args.kwargs
    assert kwargs["check_compatibility"] is False
    assert kwargs["api_key"] is None


@pytest.mark.asyncio
async def test_qdrant_connect_keeps_compatibility_check_in_staging(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    monkeypatch.setenv("APP__ENV", "staging")
    settings = Settings()

    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    with patch("qdrant_client.AsyncQdrantClient", return_value=mock_client) as mock_ctor:
        manager = QdrantClientManager(settings)
        await manager.connect()

    kwargs = mock_ctor.call_args.kwargs
    assert kwargs["check_compatibility"] is True
