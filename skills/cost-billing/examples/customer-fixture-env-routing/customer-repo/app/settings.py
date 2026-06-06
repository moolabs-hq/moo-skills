"""Customer's settings module — pydantic-settings v2 pattern."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings sourced from environment / .env."""

    moolabs_api_key: SecretStr
    database_url: str
    feature_flag_billing: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
