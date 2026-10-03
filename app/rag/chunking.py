"""Structure-aware chunking for EU legal texts: one section per Article / Annex / recitals,
split on paragraph boundaries only when a section is too long."""

import re
from dataclasses import dataclass

from app.providers.base import Chunk

ARTICLE_RE = re.compile(r"^Article (\d+[a-z]?)$")
ANNEX_RE = re.compile(r"^ANNEX ([IVXLC]+)$")
CHAPTER_RE = re.compile(r"^(CHAPTER|SECTION) ([IVXLC\d]+)$")
MARKER_RE = re.compile(r"^(\(?[a-z0-9ivx]{1,4}\)|\d+\.)$")  # "(a)", "(iv)", "1."

MAX_CHARS = 1500
OVERLAP_MAX_CHARS = 400


@dataclass
class Section:
    label: str  # "Article 5", "Annex III", "Recitals"
    title: str
    chapter: str
    paragraphs: list[str]


def _merge_markers(lines: list[str]) -> list[str]:
    """Join a lone list marker like '(a)' with the line that follows it."""
    out: list[str] = []
    pending = ""
    for line in lines:
        if MARKER_RE.match(line):
            pending = f"{pending} {line}".strip()
            continue
        out.append(f"{pending} {line}".strip())
        pending = ""
    return out


def split_sections(text: str) -> list[Section]:
    # str.split() also collapses non-breaking spaces ( ), which EU texts use in headings
    lines = [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]
    sections: list[Section] = []
    current = Section("Recitals", "", "", [])
    chapter = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if m := CHAPTER_RE.match(line):
            chapter = f"{m.group(1).title()} {m.group(2)}"
            if i + 1 < len(lines):
                chapter += f": {lines[i + 1]}"
            i += 2  # skip the chapter/section title line
            continue
        art, annex = ARTICLE_RE.match(line), ANNEX_RE.match(line)
        if art or annex:
            sections.append(current)
            label = f"Article {art.group(1)}" if art else f"Annex {annex.group(1)}"
            title = lines[i + 1] if i + 1 < len(lines) else ""
            current = Section(label, title, chapter, [])
            i += 2
            continue
        current.paragraphs.append(line)
        i += 1
    sections.append(current)
    if len(sections) == 1:  # no Article/Annex headings: a generic document, not a regulation
        sections[0].label = "Document"

    # A label can appear twice (table of contents, cross-reference line): keep the longest.
    best: dict[str, Section] = {}
    for s in sections:
        size = sum(len(p) for p in s.paragraphs)
        if s.label not in best or size > sum(len(p) for p in best[s.label].paragraphs):
            best[s.label] = s
    return [s for s in sections if best[s.label] is s and s.paragraphs]


def _pack(paragraphs: list[str]) -> list[str]:
    """Pack paragraphs into pieces <= MAX_CHARS, repeating a short last paragraph as overlap."""
    pieces: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for p in paragraphs:
        if cur and size + len(p) > MAX_CHARS:
            pieces.append(cur)
            overlap = cur[-1:] if len(cur[-1]) <= OVERLAP_MAX_CHARS else []
            cur, size = list(overlap), sum(len(x) for x in overlap)
        cur.append(p)
        size += len(p)
    if cur:
        pieces.append(cur)
    return ["\n".join(p) for p in pieces]


def chunk_document(doc_id: str, text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in split_sections(text):
        paragraphs = _merge_markers(section.paragraphs)
        for n, body in enumerate(_pack(paragraphs)):
            header = f"[{doc_id.upper()} | {section.label}"
            header += f": {section.title}]" if section.title else "]"
            chunks.append(
                Chunk(
                    id=f"{doc_id}:{section.label.replace(' ', '_')}:{n}",
                    doc_id=doc_id,
                    text=f"{header}\n{body}",
                    metadata={
                        "source": doc_id,
                        "section": section.label,
                        "title": section.title,
                        "chapter": section.chapter,
                        "part": n,
                    },
                )
            )
    return chunks
