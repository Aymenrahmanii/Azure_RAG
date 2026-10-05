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

    # Security. auth_mode "off" is only allowed when environment == "local" (the API refuses to
    # start otherwise). Tokens are checked against a static public key OR an Entra JWKS URL.
    auth_mode: str = "off"  # off | jwt
    auth_issuer: str = ""
    auth_audience: str = ""
    auth_public_key: str = ""  # PEM (newlines may be written as \n)
    auth_jwks_url: str = ""
    acl_restricted: str = ""  # JSON, e.g. {"dora": ["finance"]}: sources only these groups may read
    rate_limit_per_minute: int = 20  # per user; 0 disables
    audit_salt: str = ""  # salts the user/question hashes in the audit log

    # Semantic cache (in process memory, per replica). See app/rag/cache.py for the safety rules.
    cache_enabled: bool = True
    # Cosine similarity. 0.97 = near-exact repeats only: below it, hard negatives ("required" vs
    # "not required") hit. Measured by `python -m eval.cache_threshold` (docs/observability.md).
    cache_threshold: float = 0.97
    cache_ttl_seconds: float = 3600
    cache_max_entries: int = 500

    # Observability. Empty connection string = telemetry is a no-op.
    applicationinsights_connection_string: str = ""
    # USD per million tokens, used for the cost estimate. ASSUMPTION: check the Azure price sheet
    # for your deployment and override; the numbers in docs/observability.md use these values.
    price_input_per_mtok: float = 0.75
    price_output_per_mtok: float = 4.50

    azure_openai_endpoint: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_search_endpoint: str = ""
    azure_search_index: str = ""  # empty = derived from the embedding model name


settings = Settings()
