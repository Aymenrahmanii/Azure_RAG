from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_path: str = ".chroma"

    azure_openai_endpoint: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_search_endpoint: str = ""
    azure_search_index: str = ""


settings = Settings()
