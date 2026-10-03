"""Azure Functions entry points (Python v2 model).

  on_blob_event    Event Grid (BlobCreated / BlobDeleted)  -> enqueue {blob_name} on Service Bus
  process_document Service Bus queue trigger                -> reconcile the index with the blob

Failures are re-raised so Service Bus redelivers (max 5 times) and then dead-letters the message.
"""

import json
import logging
from functools import lru_cache

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

from app.core.config import settings
from app.providers.factory import make_embedder, make_store
from ingestion.core import Ingestor, doc_id_for
from ingestion.status import CosmosStatusStore

MAX_DELIVERY = 5  # keep equal to max_delivery_count on the queue (infra/ingestion.tf)

# The Azure SDKs log every HTTP request at INFO; that floods Log Analytics (and the daily cap).
logging.getLogger("azure").setLevel(logging.WARNING)

app = func.FunctionApp()


@lru_cache(maxsize=1)
def deps() -> tuple[Ingestor, ContainerClient]:
    """Built once per worker instance, lazily, so cold starts do not pay for unused clients."""
    credential = DefaultAzureCredential()
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    status = CosmosStatusStore(
        settings.cosmos_endpoint, settings.cosmos_database, settings.cosmos_container, credential
    )
    container = ContainerClient(
        settings.storage_account_url, settings.documents_container, credential=credential
    )
    return Ingestor(embedder, store, status), container


def blob_name_from_subject(subject: str) -> str:
    """'/blobServices/default/containers/documents/blobs/a/b.txt' -> 'a/b.txt'"""
    return subject.split("/blobs/", 1)[1]


@app.function_name("on_blob_event")
@app.event_grid_trigger(arg_name="event")
@app.service_bus_queue_output(
    arg_name="out", connection="ServiceBusConnection", queue_name="%INGEST_QUEUE%"
)
def on_blob_event(event: func.EventGridEvent, out: func.Out[str]) -> None:
    ingestor, _ = deps()
    blob_name = blob_name_from_subject(event.subject)
    doc_id = doc_id_for(blob_name)
    prev = ingestor.status.get(doc_id)
    if prev is None or prev.get("status") != "indexed":
        ingestor.status.put(doc_id, status="queued", blob_name=blob_name)
    # An already-indexed document keeps status "indexed" until the worker sees changed content;
    # that is what makes a re-upload of identical bytes a no-op.
    out.set(json.dumps({"blob_name": blob_name, "event_type": event.event_type}))
    logging.info("queued %s (%s)", blob_name, event.event_type)


@app.function_name("process_document")
@app.service_bus_queue_trigger(
    arg_name="msg", connection="ServiceBusConnection", queue_name="%INGEST_QUEUE%"
)
def process_document(msg: func.ServiceBusMessage) -> None:
    blob_name = json.loads(msg.get_body().decode("utf-8"))["blob_name"]
    attempt = msg.delivery_count or 1
    ingestor, container = deps()

    def fetch(name: str) -> bytes | None:
        try:
            return container.download_blob(name).readall()
        except ResourceNotFoundError:
            return None

    try:
        outcome = ingestor.reconcile(blob_name, fetch, attempt)
        logging.info("%s: %s (%d chunks)", blob_name, outcome.action, outcome.chunks)
    except Exception as exc:
        ingestor.mark_failed(blob_name, repr(exc), attempt)
        if attempt >= MAX_DELIVERY:
            logging.error(
                "%s failed %d times; message goes to the dead-letter queue", blob_name, attempt
            )
        raise
