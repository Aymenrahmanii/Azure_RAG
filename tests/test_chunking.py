from app.rag.chunking import MAX_CHARS, chunk_document, split_sections

SAMPLE = """Whereas this is a recital.
CHAPTER I
General provisions
Article 1
Subject-matter
1.
This Regulation lays down rules.
(a)
first point
Article 2
Scope
This applies to everything.
ANNEX I
List of things
Item one
"""


def test_sections_split_on_article_and_annex():
    labels = [s.label for s in split_sections(SAMPLE)]
    assert labels == ["Recitals", "Article 1", "Article 2", "Annex I"]


def test_chapter_metadata_and_markers_merged():
    chunks = {c.metadata["section"]: c for c in chunk_document("demo", SAMPLE)}
    assert chunks["Article 1"].metadata["chapter"] == "Chapter I: General provisions"
    assert "1. This Regulation lays down rules." in chunks["Article 1"].text
    assert chunks["Article 1"].text.startswith("[DEMO | Article 1: Subject-matter]")


def test_long_article_is_split_with_stable_ids():
    body = "\n".join(f"Paragraph {i} " + "x" * 300 for i in range(20))
    chunks = chunk_document("demo", f"Article 1\nTitle\n{body}")
    assert len(chunks) > 1
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(len(c.text) < MAX_CHARS + 600 for c in chunks)
