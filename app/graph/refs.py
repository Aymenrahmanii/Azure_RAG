"""Deterministic cross-reference extraction: "Article 33", "Articles 5 to 9", "Annex III",
optionally qualified by "of Regulation (EU) 2016/679" (another document) or "of this Regulation"."""

import re

# CELEX-style regulation numbers -> document ids used in the corpus
DOC_BY_NUMBER = {
    "2016/679": "gdpr",
    "2024/1689": "eu_ai_act",
    "2022/2555": "nis2",
    "2022/2554": "dora",
}

ARTICLE_RE = re.compile(
    r"\bArticles?\s+(\d+[a-z]?(?:\(\d+\))?(?:\s*(?:,|and|or|to)\s*\d+[a-z]?(?:\(\d+\))?)*)"
)
ANNEX_RE = re.compile(r"\bAnnex(?:es)?\s+([IVXLC]+(?:\s*(?:,|and|or)\s*[IVXLC]+)*)\b")
QUALIFIER_RE = re.compile(
    r"^[^.;]{0,60}?\bof\s+(?:(?:this|that)\s+(?:Regulation|Directive)"
    r"|(?:Regulation|Directive|Decision)\s+(?:\(EU\)\s+)?(?:No\s+)?(?P<num>\d+/\d+))"
)
MENTION_RE = re.compile(r"(?:Regulation|Directive)\s+\(EU\)\s+(\d{4}/\d+)")
MAX_RANGE = 40


def _article_numbers(spec: str) -> list[str]:
    """'33, 34 and 35(2)' -> ['33', '34', '35'];  '5 to 9' -> ['5', ..., '9']."""
    out: list[str] = []
    tokens = re.findall(r"\d+[a-z]?|to", re.sub(r"\(\d+\)", "", spec))
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "to" and out and i + 1 < len(tokens):
            lo, hi = re.sub(r"\D", "", out[-1]), re.sub(r"\D", "", tokens[i + 1])
            if lo and hi and 0 < int(hi) - int(lo) <= MAX_RANGE:
                out.extend(str(n) for n in range(int(lo) + 1, int(hi)))
            out.append(tokens[i + 1])
            i += 2
            continue
        if tok != "to":
            out.append(tok)
        i += 1
    return out


def _target_doc(text: str, end: int, own_doc: str) -> str | None:
    """Document a reference points to: own document by default, another one when qualified,
    None when it points to something outside the corpus."""
    m = QUALIFIER_RE.match(text[end : end + 120])
    if not m or not m.group("num"):
        return own_doc
    return DOC_BY_NUMBER.get(m.group("num"))


def extract_refs(text: str, own_doc: str) -> set[tuple[str, str]]:
    """Section references as {(doc_id, section_label)}, e.g. {('gdpr', 'Article 33')}."""
    refs: set[tuple[str, str]] = set()
    for m in ARTICLE_RE.finditer(text):
        doc = _target_doc(text, m.end(), own_doc)
        if doc:
            refs.update((doc, f"Article {n}") for n in _article_numbers(m.group(1)))
    for m in ANNEX_RE.finditer(text):
        doc = _target_doc(text, m.end(), own_doc)
        if doc:
            numerals = re.findall(r"[IVXLC]+", m.group(1))
            refs.update((doc, f"Annex {n}") for n in numerals)
    return refs


def regulation_mentions(text: str, own_doc: str) -> set[str]:
    """Other corpus documents named explicitly, e.g. 'Regulation (EU) 2016/679' -> 'gdpr'."""
    docs = {DOC_BY_NUMBER.get(n) for n in MENTION_RE.findall(text)}
    return {d for d in docs if d and d != own_doc}
