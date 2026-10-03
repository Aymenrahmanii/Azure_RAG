"""Build providers from settings, so switching local <-> Azure is configuration, not code."""

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from app.core.config import Settings
from app.providers.azure_search import AzureSearchStore
from app.providers.base import Embedder, VectorStore
from app.providers.local import ChromaStore, SentenceTransformerEmbedder, collection_name
from app.providers.openai_compat import OpenAICompatLLM

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def make_embedder(model: str) -> Embedder:
    return SentenceTransformerEmbedder(model)


def make_store(settings: Settings, embedder: Embedder, embedding_model: str) -> VectorStore:
    name = collection_name(embedding_model)
    if settings.vector_store == "chroma":
        return ChromaStore(settings.chroma_path, name)
    if settings.vector_store == "azure_search":
        dims = len(embedder.embed(["dimension probe"])[0])
        index = settings.azure_search_index or name.replace("_", "-")
        return AzureSearchStore(
            settings.azure_search_endpoint, index, dims, DefaultAzureCredential()
        )
    raise ValueError(f"unknown vector_store: {settings.vector_store}")


def make_llm(settings: Settings) -> OpenAICompatLLM | None:
    if not (settings.llm_base_url and settings.llm_model):
        return None
    if settings.llm_auth == "entra":
        provider = get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SCOPE)
        return OpenAICompatLLM(settings.llm_base_url, settings.llm_model, token_provider=provider)
    return OpenAICompatLLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
