"""Print ingestion status (Cosmos) and indexed chunk count (AI Search) for documents.

python scripts/doc_status.py            # all documents
python scripts/doc_status.py my_doc     # one document id
"""

import json
import sys

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from app.core.config import settings
from app.providers.factory import make_embedder, make_store


def main() -> None:
    cred = DefaultAzureCredential()
    container = (
        CosmosClient(settings.cosmos_endpoint, credential=cred)
        .get_database_client(settings.cosmos_database)
        .get_container_client(settings.cosmos_container)
    )
    store = make_store(
        settings, make_embedder(settings.embedding_model, settings), settings.embedding_model
    )
    ids = sys.argv[1:]
    items = (
        [container.read_item(i, partition_key=i) for i in ids]
        if ids
        else list(container.read_all_items())
    )
    for it in sorted(items, key=lambda d: d["id"]):
        n = len(store.chunk_ids_for_doc(it["id"]))
        keep = {k: it.get(k) for k in ("status", "chunks", "attempts", "error", "updated_at")}
        print(f"{it['id']:<24} indexed_chunks_in_search={n:<4} {json.dumps(keep)}")


if __name__ == "__main__":
    main()
