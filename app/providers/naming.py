"""Collection / index naming, kept free of heavy imports so Functions can use it."""

DEFAULT_EMBEDDING = "sentence-transformers/all-MiniLM-L6-v2"


def collection_name(embedding_model: str) -> str:
    """One collection/index per embedding model, so vectors are never mixed."""
    if embedding_model == DEFAULT_EMBEDDING:
        return "regulations"
    return "regulations_" + "".join(c if c.isalnum() else "_" for c in embedding_model.lower())
