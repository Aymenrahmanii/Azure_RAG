from app.core.config import settings
from app.providers.base import Chunk


def test_settings_load():
    assert settings.environment


def test_chunk_defaults():
    assert Chunk(id="1", doc_id="d", text="t").metadata == {}


def test_settings_strip_stray_whitespace_from_env(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com\r\n")
    monkeypatch.setenv("LLM_MODEL", "  gpt  ")
    cfg = Settings(_env_file=None)
    assert cfg.azure_openai_endpoint == "https://example.openai.azure.com"
    assert cfg.llm_model == "gpt"
