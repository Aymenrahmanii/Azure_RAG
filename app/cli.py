"""CLI: python -m app.cli ingest | ask "question" """

import argparse
import asyncio
from pathlib import Path

from app.core.config import settings
from app.providers.local import ChromaStore, SentenceTransformerEmbedder
from app.providers.openai_compat import OpenAICompatLLM
from app.rag.chunking import chunk_document
from app.rag.pipeline import RAGPipeline

RAW = Path("data/raw")


def ingest() -> None:
    embedder = SentenceTransformerEmbedder(settings.embedding_model)
    store = ChromaStore(settings.chroma_path)
    for path in sorted(RAW.glob("*.txt")):
        chunks = chunk_document(path.stem, path.read_text(encoding="utf-8"))
        store.delete_by_doc(path.stem)  # re-ingest replaces, never duplicates
        store.upsert(chunks, embedder.embed([c.text for c in chunks]))
        print(f"{path.stem}: {len(chunks)} chunks")
    print(f"total in store: {store.count()}")


def ask(question: str, k: int) -> None:
    llm = None
    if settings.llm_base_url and settings.llm_model:
        llm = OpenAICompatLLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    pipeline = RAGPipeline(
        SentenceTransformerEmbedder(settings.embedding_model),
        ChromaStore(settings.chroma_path),
        llm,
    )
    answer = asyncio.run(pipeline.ask(question, k))
    print(f"\n{answer.text}\n\nSources:")
    for i, s in enumerate(answer.sources, 1):
        m = s.chunk.metadata
        print(f"  [{i}] {m['source']} | {m['section']}: {m['title']}  (score {s.score:.3f})")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    ingest() if args.cmd == "ingest" else ask(args.question, args.k)


if __name__ == "__main__":
    main()
