"""Settings, resolved once at import.

A bad configuration must stop the service from starting rather than surface as a failed
request an hour later, so there are no defaults for values that have no safe default."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    cms_base_url: str = "https://api.coverage.cms.gov/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
