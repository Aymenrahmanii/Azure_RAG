import pytest

from app.providers.base import Chunk, RetrievedChunk
from ingestion.core import Ingestor, UnsupportedDocument, content_hash, doc_id_for, parse_document
from ingestion.status import InMemoryStatusStore


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[float(len(t))] for t in texts]


class FakeStore:
    def __init__(self):
        self.chunks: dict[str, Chunk] = {}

    def upsert(self, chunks, embeddings):
        for c in chunks:
            self.chunks[c.id] = c

    def search(self, query_embedding, k, filters=None) -> list[RetrievedChunk]:
        return []

    def delete_by_doc(self, doc_id):
        self.chunks = {i: c for i, c in self.chunks.items() if c.doc_id != doc_id}

    def chunk_ids_for_doc(self, doc_id):
        return [i for i, c in self.chunks.items() if c.doc_id == doc_id]

    def delete_chunks(self, chunk_ids):
        for i in chunk_ids:
            self.chunks.pop(i, None)


LONG_BODY = b"\n".join(b"Paragraph %d " % i + b"x" * 400 for i in range(8))
DOC_V1 = b"Article 1\nSubject\n" + LONG_BODY
DOC_V2 = b"Article 1\nSubject\nA much shorter second version."


@pytest.fixture
def ing():
    return Ingestor(FakeEmbedder(), FakeStore(), InMemoryStatusStore())


def test_doc_id_and_hash():
    assert doc_id_for("Folder/My File.txt") == "folder_my_file"
    assert content_hash(b"a") == content_hash(b"a") != content_hash(b"b")


def test_reupload_of_same_content_is_idempotent(ing):
    first = ing.upsert("a.txt", DOC_V1)
    n = len(ing.store.chunks)
    second = ing.upsert("a.txt", DOC_V1, attempt=2)
    assert first.action == "indexed" and second.action == "skipped_unchanged"
    assert ing.embedder.calls == 1  # no re-embedding, no duplicate chunks
    assert len(ing.store.chunks) == n


def test_update_replaces_stale_chunks(ing):
    ing.upsert("a.txt", DOC_V1)
    assert len(ing.store.chunks) > 1
    ing.upsert("a.txt", DOC_V2)
    assert len(ing.store.chunks) == 1
    assert ing.status.get("a")["status"] == "indexed"


def test_delete_removes_chunks_and_marks_status(ing):
    ing.upsert("a.txt", DOC_V1)
    ing.upsert("b.txt", DOC_V2)
    ing.delete("a.txt")
    assert {c.doc_id for c in ing.store.chunks.values()} == {"b"}
    assert ing.status.get("a")["status"] == "deleted"


def test_reupload_after_delete_reindexes(ing):
    ing.upsert("a.txt", DOC_V1)
    ing.delete("a.txt")
    assert ing.upsert("a.txt", DOC_V1).action == "indexed"


def test_unsupported_type_is_rejected_not_retried(ing):
    assert ing.upsert("scan.exe", b"MZ").action == "rejected"
    assert ing.status.get("scan")["status"] == "rejected"
    with pytest.raises(UnsupportedDocument):
        parse_document("x.bin", b"")


def test_empty_document_is_rejected(ing):
    assert ing.upsert("e.txt", b"   \n  ").action == "rejected"


def test_generic_document_is_not_labelled_recitals(ing):
    ing.upsert("notes.txt", b"Just a plain note about onboarding.\nSecond line.")
    assert {c.metadata["section"] for c in ing.store.chunks.values()} == {"Document"}


def test_html_is_converted_to_text():
    assert "Hello" in parse_document("p.html", b"<html><body><h1>Hello</h1></body></html>")


def test_reconcile_follows_current_blob_state(ing):
    blobs = {"a.txt": DOC_V1}
    fetch = blobs.get
    assert ing.reconcile("a.txt", fetch).action == "indexed"
    blobs.pop("a.txt")  # a stale "created" event arriving after the blob was deleted
    assert ing.reconcile("a.txt", fetch).action == "deleted"
    assert ing.store.chunks == {}
    assert ing.reconcile("a.txt", fetch).action == "deleted"  # duplicate event: harmless
