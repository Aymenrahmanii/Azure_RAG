"""Structured audit log: who asked what kind of question, what the system returned, at what cost.

One JSON line per request on the `audit` logger. It deliberately does NOT contain the question
text or the answer, because users paste personal data into questions: only a salted hash of the
user id, the question length and a hash of the question (to spot repeats without reading them).
"""

import hashlib
import json
import logging
import time

log = logging.getLogger("audit")


def short_hash(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:12]


def record(
    *,
    user_id: str,
    question: str,
    status: str,
    pipeline: str | None = None,
    kind: str | None = None,
    sections: list[str] | None = None,
    tokens: int = 0,
    llm_calls: int = 0,
    seconds: float = 0.0,
    pii: dict | None = None,
    salt: str = "",
) -> dict:
    entry = {
        "ts": round(time.time(), 3),
        "event": "chat",
        "user": short_hash(user_id, salt),
        "question_len": len(question),
        "question_hash": short_hash(question, salt),
        "status": status,
        "pipeline": pipeline,
        "kind": kind,
        "sections": sections or [],
        "tokens": tokens,
        "llm_calls": llm_calls,
        "seconds": round(seconds, 2),
        "pii_masked": pii or {},  # kinds and counts only, never the values
    }
    log.info(json.dumps(entry, ensure_ascii=False))
    return entry
