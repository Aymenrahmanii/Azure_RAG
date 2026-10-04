from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chroma_path: str = ".chroma"

    vector_store: str = "chroma"  # chroma | azure_search
    llm_auth: str = "key"  # key | entra (DefaultAzureCredential, no secrets)

    # Any OpenAI-compatible chat endpoint (Ollama: http://localhost:11434/v1)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    # Ingestion pipeline (set as app settings on the Function app)
    cosmos_endpoint: str = ""
    cosmos_database: str = "ragdb"
    cosmos_container: str = "documents"
    storage_account_url: str = ""
    documents_container: str = "documents"
    graph_container: str = ""  # blob container holding graph.json (empty = use the local file)
    ingest_queue: str = "ingest"
    servicebus_namespace: str = ""  # e.g. sb-azrag-dev-xxxxx.servicebus.windows.net

    # Retrieval (API). Cross-encoder rerank needs PyTorch, which the container image omits.
    retrieval_hybrid: bool = True
    retrieval_rerank: bool = False

    azure_openai_endpoint: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_search_endpoint: str = ""
    azure_search_index: str = ""  # empty = derived from the embedding model name


settings = Settings()
