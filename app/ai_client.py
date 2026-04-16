from openai import AsyncOpenAI

from app.config import get_settings


def get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
