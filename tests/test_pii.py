import pytest

from app.security.pii import redact_pii


@pytest.mark.parametrize(
    ("text", "expected", "counts"),
    [
        ("Mail jane.doe+x@corp.example.com now", "Mail [EMAIL] now", {"EMAIL": 1}),
        ("IBAN DE89 3704 0044 0532 0130 00 was exposed", "IBAN [IBAN] was exposed", {"IBAN": 1}),
        ("account DE89370400440532013000.", "account [IBAN].", {"IBAN": 1}),
        ("TN59 1000 6035 1835 9847 8831 is ours", "[IBAN] is ours", {"IBAN": 1}),
        ("card 4111 1111 1111 1111 leaked", "card [CARD] leaked", {"CARD": 1}),
        ("card 4111-1111-1111-1111", "card [CARD]", {"CARD": 1}),
        ("call +216 20 123 456 or +33 6 12 34 56 78", "call [PHONE] or [PHONE]", {"PHONE": 2}),
        ("from 192.168.10.25 and 8.8.8.8", "from [IP] and [IP]", {"IP": 2}),
    ],
)
def test_masks_identifiers(text, expected, counts):
    assert redact_pii(text) == (expected, counts)


@pytest.mark.parametrize(
    "text",
    [
        "What does Article 33 of Regulation (EU) 2016/679 require within 72 hours?",
        "Directive (EU) 2022/2555, Article 23(4), point (b)",
        "Fines up to 20 000 000 EUR or 4 % of turnover under Article 83(5)",
        "Annex III, section 5, points 1 to 4",
        "version 1.2.3 of the standard",  # three dots-separated numbers, not an IPv4
        "AB12 is not an IBAN and neither is DE00 0000 0000 0000 0000 00",  # fails the checksum
        "number 1234 5678 9012 3456 fails Luhn",
        "Call 0123456789 tomorrow",  # no international prefix: left alone (documented limit)
    ],
)
def test_legal_text_and_near_misses_are_left_alone(text):
    assert redact_pii(text) == (text, {})


def test_multiple_kinds_and_idempotence():
    text = "Report from ops@corp.example, IBAN DE89 3704 0044 0532 0130 00, host 10.0.0.7"
    masked, counts = redact_pii(text)
    assert masked == "Report from [EMAIL], IBAN [IBAN], host [IP]"
    assert counts == {"EMAIL": 1, "IBAN": 1, "IP": 1}
    assert redact_pii(masked) == (masked, {})  # running twice changes nothing
