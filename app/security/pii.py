"""Mask obvious personal identifiers in a user's question before it reaches search, the LLM or logs.

Users paste personal data into compliance questions ("our customer X, IBAN ..., had a breach"),
and the answer never needs the value itself. Regex + checksum detection is deliberately narrow: it
masks what it can recognise with low false-positive risk (checksums for IBAN and cards, a leading
+/00 for phone numbers) and does not claim to find names or free-form identifiers. A trained PII
service would catch more; this is data minimisation, not anonymisation.
"""

import re
from collections import Counter

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
PHONE = re.compile(r"(?<![\w+])(?:\+|00)\d{1,3}(?:[ .()/-]?\d){7,12}(?!\d)")
IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


def _iban_ok(candidate: str) -> bool:
    iban = candidate.replace(" ", "")
    if not 15 <= len(iban) <= 34:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # A=10 ... Z=35
    return int(digits) % 97 == 1


def _phone_ok(candidate: str) -> bool:
    digits = {c for c in candidate if c.isdigit()}
    return len(digits) > 1  # "00 0000 0000" is padding, not a number


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:  # 0000 0000 ... passes Luhn
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    """(masked text, counts by kind). Order matters: IBAN and card checks run before phone, so a
    long digit run is claimed by the most specific detector first."""
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern, label: str, valid=None):
        def repl(m: re.Match) -> str:
            if valid is not None and not valid(m.group(0)):
                return m.group(0)
            counts[label] += 1
            return f"[{label}]"

        return lambda s: pattern.sub(repl, s)

    for step in (
        sub(EMAIL, "EMAIL"),
        sub(IBAN, "IBAN", _iban_ok),
        sub(CARD, "CARD", _luhn_ok),
        sub(PHONE, "PHONE", _phone_ok),
        sub(IPV4, "IP"),
    ):
        text = step(text)
    return text, dict(counts)
