class _FakeDocumentChunkCollection:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.index_calls: list = []

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return "ok"

    async def insert_many(self, docs):
        for doc in docs:
            if doc["_id"] in self.items:
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("duplicate")
            self.items[doc["_id"]] = doc

    async def delete_many(self, query):
        if "document_id" not in query:
            return _FakeDeleteResult(0)
        to_delete = [
            key
            for key, item in self.items.items()
            if item.get("document_id") == query["document_id"]
        ]
        for key in to_delete:
            del self.items[key]
        return _FakeDeleteResult(len(to_delete))

    async def update_one(self, query, update):
        doc_id = query.get("_id")
        if doc_id not in self.items:
            return _FakeUpdateResult(0)
        if "$set" in update:
            self.items[doc_id].update(update["$set"])
        return _FakeUpdateResult(1)

    async def count_documents(self, query):
        if "document_id" not in query:
            return 0
        return sum(
            1 for item in self.items.values() if item.get("document_id") == query["document_id"]
        )

    def find(self, query):
        return _FakeDocumentChunkCursor(self.items.values(), query)


class _FakeDeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class _FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class _FakeDocumentChunkCursor:
    def __init__(self, values, query):
        self._values = list(values)
        self._query = query
        self._sort_key = None
        self._index = 0

    def sort(self, key, direction):
        self._sort_key = key
        reverse = direction == -1
        if key == "chunk_index":
            self._values.sort(key=lambda d: d.get("chunk_index", 0), reverse=reverse)
        return self

    def __aiter__(self):
        filtered = []
        for item in self._values:
            if not self._query:
                filtered.append(item)
                continue
            if "document_id" in self._query:
                if item.get("document_id") != self._query["document_id"]:
                    continue
            if "_id" in self._query:
                expected = self._query["_id"]
                if isinstance(expected, dict) and "$in" in expected:
                    if item.get("_id") not in expected["$in"]:
                        continue
                elif item.get("_id") != expected:
                    continue
            filtered.append(item)
        self._values = filtered
        return self

    async def __anext__(self):
        if self._index >= len(self._values):
            raise StopAsyncIteration
        item = self._values[self._index]
        self._index += 1
        return item


class _FakeDocumentChunkClient:
    def __init__(self):
        self.document_chunks = _FakeDocumentChunkCollection()

    def __getitem__(self, _db_name):
        return {"document_chunks": self.document_chunks}


class _FakeDocumentChunkMongoManager:
    def __init__(self):
        self._client = _FakeDocumentChunkClient()


_FakeMongoManager = _FakeDocumentChunkMongoManager
