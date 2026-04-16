from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_title: str = "Kurim Mystery"
    database_url: str
    openai_api_key: str
    openai_base_url: str = "https://kurim.ithope.eu/v1"
    openai_model: str = "gemma3:27b"


@lru_cache
def get_settings() -> Settings:
    return Settings()
