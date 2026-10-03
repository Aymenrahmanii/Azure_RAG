from app.core.config import settings
from app.providers.base import Chunk


def test_settings_load():
    assert settings.environment


def test_chunk_defaults():
    assert Chunk(id="1", doc_id="d", text="t").metadata == {}
