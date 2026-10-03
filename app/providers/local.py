"""Local implementations used during prototyping (weeks 1-3)."""

import chromadb
from sentence_transformers import SentenceTransformer

from app.providers.base import Chunk, RetrievedChunk
from app.providers.naming import collection_name  # noqa: F401  (re-export)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


class ChromaStore:
    def __init__(self, path: str, collection: str = "regulations"):
        client = chromadb.PersistentClient(path=path)
        self._col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        batch = 500
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            self._col.upsert(
                ids=[c.id for c in part],
                documents=[c.text for c in part],
                embeddings=embeddings[i : i + batch],
                metadatas=[{**c.metadata, "doc_id": c.doc_id} for c in part],
            )

    def search(
        self, query_embedding: list[float], k: int, filters: dict | None = None
    ) -> list[RetrievedChunk]:
        res = self._col.query(query_embeddings=[query_embedding], n_results=k, where=filters)
        out = []
        for id_, doc, meta, dist in zip(
            res["ids"][0],
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
            strict=True,
        ):
            doc_id = meta.pop("doc_id")
            out.append(RetrievedChunk(Chunk(id_, doc_id, doc, meta), score=1 - dist))
        return out

    def delete_by_doc(self, doc_id: str) -> None:
        self._col.delete(where={"doc_id": doc_id})

    def chunk_ids_for_doc(self, doc_id: str) -> list[str]:
        return self._col.get(where={"doc_id": doc_id}, include=[])["ids"]

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self._col.delete(ids=chunk_ids)

    def all_chunks(self) -> list[Chunk]:
        res = self._col.get(include=["documents", "metadatas"])
        out = []
        for id_, doc, meta in zip(res["ids"], res["documents"], res["metadatas"], strict=True):
            meta = dict(meta)
            out.append(Chunk(id_, meta.pop("doc_id"), doc, meta))
        return out

    def count(self) -> int:
        return self._col.count()
