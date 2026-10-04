"""Ingestion logic, free of Azure Functions specifics so it is unit-testable.

Level-triggered reconciliation: the queue message only names a blob. The worker looks at the blob's
CURRENT state (exists -> index it, missing -> remove it), so duplicate, delayed or out-of-order
events can never leave the index in the wrong state.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from bs4 import BeautifulSoup

from app.providers.base import Embedder, VectorStore
from app.rag.chunking import chunk_document
from app.security.injection_scan import scan_text
from ingestion.status import StatusStore

SUPPORTED = {".txt", ".md", ".html", ".htm"}


class UnsupportedDocument(Exception):
    """Not retryable: the file type can never succeed, so do not burn retries on it."""


def doc_id_for(blob_name: str) -> str:
    """Stable id from the blob path: 'folder/My File.txt' -> 'folder_my_file'."""
    stem = str(PurePosixPath(blob_name).with_suffix(""))
    return "".join(c if c.isalnum() else "_" for c in stem.lower()).strip("_")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_document(blob_name: str, data: bytes) -> str:
    suffix = PurePosixPath(blob_name).suffix.lower()
    if suffix not in SUPPORTED:
        raise UnsupportedDocument(f"unsupported file type {suffix!r}")
    text = data.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        text = BeautifulSoup(text, "html.parser").get_text("\n", strip=True)
    return text


@dataclass
class Outcome:
    action: str  # indexed | skipped_unchanged | deleted | rejected | quarantined
    chunks: int = 0


class Ingestor:
    def __init__(self, embedder: Embedder, store: VectorStore, status: StatusStore):
        self.embedder, self.store, self.status = embedder, store, status

    def upsert(self, blob_name: str, data: bytes, attempt: int = 1) -> Outcome:
        doc_id, digest = doc_id_for(blob_name), content_hash(data)
        prev = self.status.get(doc_id)
        if prev and prev.get("status") == "indexed" and prev.get("hash") == digest:
            self.status.put(doc_id, last_seen_attempt=attempt)
            return Outcome("skipped_unchanged", prev.get("chunks", 0))

        self.status.put(
            doc_id, status="processing", hash=digest, blob_name=blob_name, attempts=attempt
        )
        try:
            text = parse_document(blob_name, data)
        except UnsupportedDocument as exc:
            self.status.put(doc_id, status="rejected", error=str(exc))
            return Outcome("rejected")

        # Anything indexed is later pasted into prompts, so look for injected instructions first.
        # A quarantined upload is not indexed; a previously indexed clean version stays as it was.
        if findings := scan_text(text):
            reasons = "; ".join(f"{f.rule}: {f.snippet}" for f in findings)
            self.status.put(doc_id, status="quarantined", error=reasons[:500])
            return Outcome("quarantined")

        chunks = chunk_document(doc_id, text)
        if not chunks:
            self.status.put(doc_id, status="rejected", error="no extractable text")
            return Outcome("rejected")
        embeddings = self.embedder.embed([c.text for c in chunks])

        # Write the new version first, then drop chunks that no longer exist: there is never a
        # moment with zero chunks for this document.
        old_ids = set(self.store.chunk_ids_for_doc(doc_id))
        self.store.upsert(chunks, embeddings)
        self.store.delete_chunks(sorted(old_ids - {c.id for c in chunks}))

        self.status.put(doc_id, status="indexed", chunks=len(chunks), error=None)
        return Outcome("indexed", len(chunks))

    def delete(self, blob_name: str) -> Outcome:
        doc_id = doc_id_for(blob_name)
        self.store.delete_by_doc(doc_id)
        self.status.put(doc_id, status="deleted", chunks=0, error=None)
        return Outcome("deleted")

    def reconcile(
        self, blob_name: str, fetch: Callable[[str], bytes | None], attempt: int = 1
    ) -> Outcome:
        """Make the index match the blob's current state. `fetch` returns None if it is gone."""
        data = fetch(blob_name)
        if data is None:
            return self.delete(blob_name)
        return self.upsert(blob_name, data, attempt)

    def mark_failed(self, blob_name: str, error: str, attempt: int) -> None:
        self.status.put(doc_id_for(blob_name), status="failed", error=error[:500], attempts=attempt)
