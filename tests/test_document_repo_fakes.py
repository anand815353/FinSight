class _FakeDocumentCollection:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.index_calls: list = []

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return "ok"

    async def insert_one(self, doc):
        if doc["_id"] in self.items:
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate")
        if doc.get("file_hash"):
            for item in self.items.values():
                if item.get("file_hash") == doc["file_hash"]:
                    from pymongo.errors import DuplicateKeyError

                    raise DuplicateKeyError("duplicate hash")
        self.items[doc["_id"]] = doc

    async def find_one(self, query):
        if "_id" in query:
            return self.items.get(query.get("_id"))
        if "file_hash" in query:
            for item in self.items.values():
                if item.get("file_hash") == query["file_hash"]:
                    return item
            return None
        return None

    async def update_one(self, query, update):
        item = self.items.get(query["_id"])
        if not item:
            from types import SimpleNamespace

            return SimpleNamespace(matched_count=0)
        if "$set" in update:
            item.update(update["$set"])
        from types import SimpleNamespace

        return SimpleNamespace(matched_count=1)

    def find(self, query):
        return _FakeDocumentCursor(self.items.values(), query)


def _document_matches_query(item: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            continue
        value = item.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
        elif value != expected:
            return False
    return True


class _FakeDocumentCursor:
    def __init__(self, values, query):
        self._values = [v for v in values if _document_matches_query(v, query)]
        self._limit = None
        self._index = 0

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        if self._limit is not None:
            self._values = self._values[: self._limit]
        return self

    async def __anext__(self):
        if self._index >= len(self._values):
            raise StopAsyncIteration
        item = self._values[self._index]
        self._index += 1
        return item


class _FakeClient:
    def __init__(self):
        self.documents = _FakeDocumentCollection()

    def __getitem__(self, _db_name):
        return {"documents": self.documents}


class _FakeMongoManager:
    def __init__(self):
        self._client = _FakeClient()
