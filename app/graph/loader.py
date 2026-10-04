"""Where the API gets its knowledge graph: Blob Storage in the cloud (managed identity), a local
file in development, nothing if neither exists (the API then runs without graph features)."""

import logging
from pathlib import Path

from app.core.config import Settings
from app.graph.store import NetworkxGraphStore

log = logging.getLogger("graph")
GRAPH_BLOB = "graph.json"
LOCAL_PATH = Path("data/processed/graph.json")


def load_graph(settings: Settings, local_path: Path = LOCAL_PATH) -> NetworkxGraphStore | None:
    if settings.storage_account_url and settings.graph_container:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        client = BlobClient(
            settings.storage_account_url,
            settings.graph_container,
            GRAPH_BLOB,
            credential=DefaultAzureCredential(),
        )
        try:
            return NetworkxGraphStore.loads(client.download_blob().readall().decode("utf-8"))
        except Exception:  # noqa: BLE001 - a missing graph must degrade the API, not kill it
            log.exception("could not load %s from blob storage", GRAPH_BLOB)
            return None
    if local_path.exists():
        return NetworkxGraphStore.load(local_path)
    return None
