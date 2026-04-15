from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379"
    CHROMA_PERSIST_DIR: str = "./data/chroma"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    UPLOAD_DIR: str = "./uploads"
    DOC_STORE_PATH: str = "./data/documents.json"
    CODEX_BIN: str = "codex"
    CODEX_MODEL: str = "gpt-5.4"
    CODEX_SANDBOX: str = "read-only"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
