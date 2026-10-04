from app.graph.refs import extract_refs, regulation_mentions


def test_single_and_list():
    refs = extract_refs("as referred to in Articles 33 and 34(1), the controller", "gdpr")
    assert refs == {("gdpr", "Article 33"), ("gdpr", "Article 34")}


def test_range_expands():
    refs = extract_refs("set out in Articles 5 to 8", "gdpr")
    assert refs == {("gdpr", f"Article {n}") for n in (5, 6, 7, 8)}


def test_letter_suffix_and_paragraph_points():
    assert extract_refs("pursuant to Article 6a(2), point (b)", "dora") == {("dora", "Article 6a")}


def test_annex_and_multiple_annexes():
    assert extract_refs("listed in Annex III", "eu_ai_act") == {("eu_ai_act", "Annex III")}
    assert extract_refs("Annexes I and II", "eu_ai_act") == {
        ("eu_ai_act", "Annex I"),
        ("eu_ai_act", "Annex II"),
    }


def test_cross_document_reference():
    text = "processing under Article 6(1) of Regulation (EU) 2016/679"
    assert extract_refs(text, "eu_ai_act") == {("gdpr", "Article 6")}


def test_this_regulation_stays_in_document():
    assert extract_refs("Article 9 of this Regulation applies", "nis2") == {("nis2", "Article 9")}


def test_reference_outside_corpus_is_dropped():
    assert extract_refs("Article 4 of Directive 2013/36/EU", "dora") == set()
    assert extract_refs("Article 3 of Regulation (EU) No 1024/2013", "dora") == set()


def test_regulation_mentions():
    text = "Regulation (EU) 2016/679 and Directive (EU) 2022/2555 and Regulation (EU) 2022/2554"
    assert regulation_mentions(text, "dora") == {"gdpr", "nis2"}
