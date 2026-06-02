from __future__ import annotations

from typing import Any

from app.models.retrieval import RetrievalFilters


def build_retrieval_filter(filters: RetrievalFilters):
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    must: list[FieldCondition] = [
        FieldCondition(key="company_id", match=MatchValue(value=filters.company_id)),
    ]
    if filters.document_ids:
        must.append(
            FieldCondition(key="document_id", match=MatchAny(any=filters.document_ids)),
        )
    if filters.document_types:
        must.append(
            FieldCondition(key="document_type", match=MatchAny(any=filters.document_types)),
        )
    if filters.fiscal_year is not None:
        must.append(
            FieldCondition(key="fiscal_year", match=MatchValue(value=filters.fiscal_year)),
        )
    if filters.quarter is not None:
        must.append(FieldCondition(key="quarter", match=MatchValue(value=filters.quarter)))
    if filters.period is not None:
        must.append(FieldCondition(key="period", match=MatchValue(value=filters.period)))
    return Filter(must=must)


async def search_points(
    client,
    *,
    collection_name: str,
    query_vector: list[float],
    limit: int,
    query_filter: RetrievalFilters | None = None,
    filters: RetrievalFilters | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    active_filters = filters if filters is not None else query_filter
    qdrant_filter = build_retrieval_filter(active_filters) if active_filters else None
    kwargs: dict[str, Any] = {
        "collection_name": collection_name,
        "query_vector": query_vector,
        "limit": limit,
    }
    if qdrant_filter is not None:
        kwargs["query_filter"] = qdrant_filter
    if score_threshold is not None:
        kwargs["score_threshold"] = score_threshold

    results = await client.search(**kwargs)
    normalized: list[dict[str, Any]] = []
    for point in results:
        payload = dict(point.payload or {})
        chunk_id = payload.get("chunk_id")
        if not chunk_id:
            continue
        normalized.append(
            {
                "chunk_id": str(chunk_id),
                "score": float(point.score),
                "payload": payload,
                "qdrant_point_id": str(point.id),
            }
        )
    return normalized
