"""Inspect and replay the ingestion dead-letter queue (Entra auth).

python scripts/dlq.py poison     # send an invalid message to prove retry -> dead-letter
python scripts/dlq.py peek       # list dead-lettered messages without removing them
python scripts/dlq.py counts     # active / dead-letter message counts
python scripts/dlq.py requeue    # move dead-lettered messages back to the main queue
  python scripts/dlq.py purge      # delete dead-lettered messages (after inspecting them)
"""

import sys

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage, ServiceBusSubQueue
from azure.servicebus.management import ServiceBusAdministrationClient

from app.core.config import settings

NAMESPACE = settings.servicebus_namespace


def client() -> ServiceBusClient:
    return ServiceBusClient(NAMESPACE, DefaultAzureCredential())


def main(cmd: str) -> None:
    q = settings.ingest_queue
    if cmd == "poison":
        with client() as c, c.get_queue_sender(q) as sender:
            sender.send_messages(ServiceBusMessage("this is not json"))
        print("sent 1 poison message")
    elif cmd == "counts":
        admin = ServiceBusAdministrationClient(NAMESPACE, DefaultAzureCredential())
        rt = admin.get_queue_runtime_properties(q)
        print(f"active={rt.active_message_count} dead_letter={rt.dead_letter_message_count}")
    elif cmd in {"peek", "requeue", "purge"}:
        with client() as c:
            receiver = c.get_queue_receiver(q, sub_queue=ServiceBusSubQueue.DEAD_LETTER)
            with receiver:
                if cmd == "peek":
                    for m in receiver.peek_messages(max_message_count=20):
                        print(
                            f"delivery_count={m.delivery_count} reason={m.dead_letter_reason!r} "
                            f"body={str(m)[:80]!r}"
                        )
                elif cmd == "purge":
                    for m in receiver.receive_messages(max_message_count=100, max_wait_time=5):
                        receiver.complete_message(m)
                        print("purged", str(m)[:80])
                else:
                    with c.get_queue_sender(q) as sender:
                        for m in receiver.receive_messages(max_message_count=20, max_wait_time=5):
                            sender.send_messages(ServiceBusMessage(str(m)))
                            receiver.complete_message(m)
                            print("requeued", str(m)[:80])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
