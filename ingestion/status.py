"""Per-document ingestion status: Cosmos DB in production, in-memory for tests."""

from datetime import UTC, datetime
from typing import Protocol


class StatusStore(Protocol):
    def get(self, doc_id: str) -> dict | None: ...

    def put(self, doc_id: str, **fields) -> dict: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class InMemoryStatusStore:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def get(self, doc_id: str) -> dict | None:
        return self.docs.get(doc_id)

    def put(self, doc_id: str, **fields) -> dict:
        doc = {**self.docs.get(doc_id, {}), "id": doc_id, **fields, "updated_at": _now()}
        self.docs[doc_id] = doc
        return doc


class CosmosStatusStore:
    """One document per ingested file (partition key = id). Entra auth, no keys."""

    def __init__(self, endpoint: str, database: str, container: str, credential):
        from azure.cosmos import CosmosClient

        client = CosmosClient(endpoint, credential=credential)
        self._container = client.get_database_client(database).get_container_client(container)

    def get(self, doc_id: str) -> dict | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return self._container.read_item(doc_id, partition_key=doc_id)
        except CosmosResourceNotFoundError:
            return None

    def put(self, doc_id: str, **fields) -> dict:
        doc = {**(self.get(doc_id) or {}), "id": doc_id, **fields, "updated_at": _now()}
        doc = {k: v for k, v in doc.items() if not k.startswith("_")}  # drop Cosmos system fields
        return self._container.upsert_item(doc)
