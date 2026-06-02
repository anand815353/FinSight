from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _FakeCollectionInfo:
    name: str


@dataclass
class _FakeCollectionsResponse:
    collections: list[_FakeCollectionInfo] = field(default_factory=list)


@dataclass
class FakeScoredPoint:
    id: str
    score: float
    payload: dict[str, Any]


class FakeQdrantClient:
    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}
        self.points: dict[str, dict[str, Any]] = {}
        self.create_collection_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.search_calls: list[dict] = []

    async def get_collections(self) -> _FakeCollectionsResponse:
        return _FakeCollectionsResponse(
            collections=[_FakeCollectionInfo(name=name) for name in self.collections]
        )

    async def create_collection(self, collection_name: str, vectors_config) -> None:
        self.create_collection_calls.append(
            {"collection_name": collection_name, "vectors_config": vectors_config}
        )
        self.collections[collection_name] = {"vectors_config": vectors_config}

    async def delete(self, collection_name: str, points_selector) -> None:
        self.delete_calls.append(
            {"collection_name": collection_name, "points_selector": points_selector}
        )
        document_id = None
        must = getattr(points_selector, "must", None)
        if must:
            for condition in must:
                match = getattr(condition, "match", None)
                if match is not None and hasattr(match, "value"):
                    document_id = match.value
        if document_id is None:
            return
        to_remove = [
            point_id
            for point_id, payload in self.points.items()
            if payload.get("collection") == collection_name
            and payload.get("payload", {}).get("document_id") == document_id
        ]
        for point_id in to_remove:
            del self.points[point_id]

    async def upsert(self, collection_name: str, points: list) -> None:
        self.upsert_calls.append({"collection_name": collection_name, "points": points})
        for point in points:
            point_id = str(point.id)
            self.points[point_id] = {
                "collection": collection_name,
                "vector": point.vector,
                "payload": dict(point.payload or {}),
            }

    @staticmethod
    def _payload_matches_filter(payload: dict[str, Any], query_filter) -> bool:
        if query_filter is None:
            return True
        must = getattr(query_filter, "must", None) or []
        for condition in must:
            key = getattr(condition, "key", None)
            match = getattr(condition, "match", None)
            if key is None or match is None:
                continue
            value = payload.get(key)
            if hasattr(match, "value"):
                if value != match.value:
                    return False
            elif hasattr(match, "any"):
                if value not in match.any:
                    return False
        return True

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        *,
        limit: int = 10,
        query_filter=None,
        score_threshold: float | None = None,
        **kwargs,
    ) -> list[FakeScoredPoint]:
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "query_vector": query_vector,
                "limit": limit,
                "query_filter": query_filter,
                "score_threshold": score_threshold,
                **kwargs,
            }
        )
        hits: list[FakeScoredPoint] = []
        for point_id, data in self.points.items():
            if data.get("collection") != collection_name:
                continue
            payload = dict(data.get("payload") or {})
            if not self._payload_matches_filter(payload, query_filter):
                continue
            score = 0.95
            if score_threshold is not None and score < score_threshold:
                continue
            hits.append(FakeScoredPoint(id=point_id, score=score, payload=payload))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]


class FakeQdrantManager:
    def __init__(self, client: FakeQdrantClient | None = None) -> None:
        self._client = client or FakeQdrantClient()

    @property
    def client(self):
        return self._client
