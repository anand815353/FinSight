class _FakeProcessingJobCollection:
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
        self.items[doc["_id"]] = doc

    async def find_one(self, query):
        if "_id" in query:
            return self.items.get(query.get("_id"))
        if "document_id" in query and "status" in query:
            status_filter = query["status"]
            if isinstance(status_filter, dict) and "$in" in status_filter:
                allowed = set(status_filter["$in"])
                for item in sorted(
                    self.items.values(),
                    key=lambda d: d.get("created_at", ""),
                    reverse=True,
                ):
                    if item.get("document_id") == query["document_id"] and item.get("status") in allowed:
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
        return _FakeProcessingJobCursor(self.items.values(), query)


class _FakeProcessingJobCursor:
    def __init__(self, values, query):
        self._values = list(values)
        self._query = query
        self._limit = None
        self._index = 0

    def sort(self, key, direction):
        reverse = direction == -1
        if key == "created_at":
            self._values.sort(key=lambda d: d.get("created_at", ""), reverse=reverse)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        filtered = []
        for item in self._values:
            if self._query:
                if "document_id" in self._query and item.get("document_id") != self._query["document_id"]:
                    continue
                if "status" in self._query:
                    status_q = self._query["status"]
                    if isinstance(status_q, str) and item.get("status") != status_q:
                        continue
                    if isinstance(status_q, dict) and "$in" in status_q:
                        if item.get("status") not in status_q["$in"]:
                            continue
            filtered.append(item)
        self._values = filtered
        if self._limit is not None:
            self._values = self._values[: self._limit]
        return self

    async def __anext__(self):
        if self._index >= len(self._values):
            raise StopAsyncIteration
        item = self._values[self._index]
        self._index += 1
        return item


class _FakeProcessingJobClient:
    def __init__(self):
        self.document_processing_jobs = _FakeProcessingJobCollection()

    def __getitem__(self, _db_name):
        return {"document_processing_jobs": self.document_processing_jobs}


class _FakeProcessingJobMongoManager:
    def __init__(self):
        self._client = _FakeProcessingJobClient()


_FakeMongoManager = _FakeProcessingJobMongoManager
