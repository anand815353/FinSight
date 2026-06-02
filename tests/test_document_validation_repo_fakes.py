from __future__ import annotations

from datetime import UTC, datetime


class _FakeValidationCollection:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.index_calls: list = []

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return "ok"

    async def insert_one(self, doc):
        self.items[doc["_id"]] = doc

    def find(self, query):
        return _FakeValidationCursor(self.items.values(), query)


class _FakeValidationCursor:
    def __init__(self, values, query):
        self._values = [
            item
            for item in values
            if item.get("document_id") == query.get("document_id")
        ]
        self._sort_key = None
        self._sort_dir = -1
        self._limit = None
        self._index = 0

    def sort(self, key, direction):
        self._sort_key = key
        self._sort_dir = direction
        reverse = direction == -1
        self._values.sort(key=lambda d: d.get(key, ""), reverse=reverse)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def __aiter__(self):
        values = self._values[: self._limit] if self._limit else self._values
        self._values = values
        return self

    async def __anext__(self):
        if self._index >= len(self._values):
            raise StopAsyncIteration
        item = self._values[self._index]
        self._index += 1
        return item


class _FakeValidationClient:
    def __init__(self):
        self.document_readiness_validations = _FakeValidationCollection()

    def __getitem__(self, _db_name):
        return {"document_readiness_validations": self.document_readiness_validations}


class _FakeValidationMongoManager:
    def __init__(self):
        self._client = _FakeValidationClient()


_FakeMongoManager = _FakeValidationMongoManager
