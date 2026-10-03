"""Azure AI Search implementation of VectorStore. Keyless: authenticates via Entra ID."""

import re

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from app.providers.base import Chunk, RetrievedChunk

META_FIELDS = ("source", "section", "title", "chapter", "part")
BATCH = 500


def search_key(chunk_id: str) -> str:
    """Document keys may only contain letters, digits, '_', '-', '='."""
    return re.sub(r"[^A-Za-z0-9_\-=]", "_", chunk_id)


def odata_filter(filters: dict | None) -> str | None:
    """Translate the store-agnostic filter dict ({"f": v} or {"f": {"$ne": v}}) to OData."""
    if not filters:
        return None
    parts = []
    for field, cond in filters.items():
        op, value = ("ne", cond["$ne"]) if isinstance(cond, dict) else ("eq", cond)
        parts.append(f"{field} {op} '{str(value).replace(chr(39), chr(39) * 2)}'")
    return " and ".join(parts)


class AzureSearchStore:
    def __init__(self, endpoint: str, index_name: str, dims: int, credential: TokenCredential):
        self._index_name, self._dims = index_name, dims
        self._index_client = SearchIndexClient(endpoint, credential)
        self._client = SearchClient(endpoint, index_name, credential)
        self._ensure_index()

    def _ensure_index(self) -> None:
        try:
            self._index_client.get_index(self._index_name)
            return
        except ResourceNotFoundError:
            pass
        filterable = dict(filterable=True)
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="chunk_id", type=SearchFieldDataType.String),
            SimpleField(name="doc_id", type=SearchFieldDataType.String, **filterable),
            SimpleField(name="source", type=SearchFieldDataType.String, **filterable),
            SimpleField(name="section", type=SearchFieldDataType.String, **filterable),
            SimpleField(name="title", type=SearchFieldDataType.String),
            SimpleField(name="chapter", type=SearchFieldDataType.String),
            SimpleField(name="part", type=SearchFieldDataType.Int32),
            SearchField(name="text", type=SearchFieldDataType.String, searchable=True),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self._dims,
                vector_search_profile_name="hnsw-profile",
            ),
        ]
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="hnsw",
                    parameters={"metric": VectorSearchAlgorithmMetric.COSINE},
                )
            ],
            profiles=[
                VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")
            ],
        )
        self._index_client.create_index(
            SearchIndex(name=self._index_name, fields=fields, vector_search=vector_search)
        )

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        for i in range(0, len(chunks), BATCH):
            docs = [
                {
                    "id": search_key(c.id),
                    "chunk_id": c.id,
                    "doc_id": c.doc_id,
                    "text": c.text,
                    "embedding": emb,
                    **{f: c.metadata.get(f, "" if f != "part" else 0) for f in META_FIELDS},
                }
                for c, emb in zip(chunks[i : i + BATCH], embeddings[i : i + BATCH], strict=True)
            ]
            results = self._client.merge_or_upload_documents(docs)
            failed = [r.key for r in results if not r.succeeded]
            if failed:
                raise RuntimeError(f"{len(failed)} documents failed to index, e.g. {failed[:3]}")

    @staticmethod
    def _to_chunk(doc: dict) -> Chunk:
        meta = {f: doc[f] for f in META_FIELDS}
        return Chunk(doc["chunk_id"], doc["doc_id"], doc["text"], meta)

    def search(
        self, query_embedding: list[float], k: int, filters: dict | None = None
    ) -> list[RetrievedChunk]:
        query = VectorizedQuery(vector=query_embedding, k_nearest_neighbors=k, fields="embedding")
        results = self._client.search(
            search_text=None,
            vector_queries=[query],
            filter=odata_filter(filters),
            top=k,
            select=["chunk_id", "doc_id", "text", *META_FIELDS],
        )
        return [RetrievedChunk(self._to_chunk(r), float(r["@search.score"])) for r in results]

    def delete_by_doc(self, doc_id: str) -> None:
        hits = self._client.search(
            search_text="*", filter=odata_filter({"doc_id": doc_id}), select=["id"], top=100000
        )
        keys = [{"id": h["id"]} for h in hits]
        for i in range(0, len(keys), BATCH):
            self._client.delete_documents(keys[i : i + BATCH])

    def all_chunks(self) -> list[Chunk]:
        hits = self._client.search(
            search_text="*", select=["chunk_id", "doc_id", "text", *META_FIELDS]
        )
        return [self._to_chunk(h) for h in hits]

    def count(self) -> int:
        return self._client.get_document_count()
