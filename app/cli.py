"""CLI: python -m app.cli ingest | ask "question" """

import argparse
import asyncio
from pathlib import Path

from app.core.config import settings
from app.providers.factory import make_embedder, make_llm, make_store
from app.rag.chunking import chunk_document
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever

RAW = Path("data/raw")


def ingest(embedding_model: str) -> None:
    embedder = make_embedder(embedding_model, settings)
    store = make_store(settings, embedder, embedding_model)
    for path in sorted(RAW.glob("*.txt")):
        chunks = chunk_document(path.stem, path.read_text(encoding="utf-8"))
        store.delete_by_doc(path.stem)  # re-ingest replaces, never duplicates
        store.upsert(chunks, embedder.embed([c.text for c in chunks]))
        print(f"{path.stem}: {len(chunks)} chunks")
    print(f"total in store: {store.count()}")


def ask(question: str, k: int) -> None:
    llm = make_llm(settings)
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    # Best retrieval config from experiments: hybrid + no recitals + cross-encoder rerank.
    retriever = Retriever(embedder, store, RetrievalConfig(True, True, True))
    pipeline = RAGPipeline(retriever, llm)
    answer = asyncio.run(pipeline.ask(question, k))
    print(f"\n{answer.text}\n\nSources:")
    for i, s in enumerate(answer.sources, 1):
        m = s.chunk.metadata
        print(f"  [{i}] {m['source']} | {m['section']}: {m['title']}  (score {s.score:.3f})")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").add_argument("--embedding-model", default=settings.embedding_model)
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    ingest(args.embedding_model) if args.cmd == "ingest" else ask(args.question, args.k)


if __name__ == "__main__":
    main()
