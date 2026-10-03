from app.providers.azure_search import odata_filter, search_key


def test_search_key_only_allowed_characters():
    key = search_key("eu_ai_act:Annex_III:0")
    assert key == "eu_ai_act_Annex_III_0"
    assert search_key("gdpr:Article 5:0") != search_key("gdpr:Article_5:1")


def test_odata_filter_translation():
    assert odata_filter(None) is None
    assert odata_filter({"section": {"$ne": "Recitals"}}) == "section ne 'Recitals'"
    assert odata_filter({"doc_id": "gdpr", "part": 0}) == "doc_id eq 'gdpr' and part eq '0'"
    assert odata_filter({"title": "it's"}) == "title eq 'it''s'"
