from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="movieclaw", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")
    reload: bool = Field(default=True, alias="APP_RELOAD")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    access_log_enabled: bool = Field(default=True, alias="APP_ACCESS_LOG_ENABLED")
    api_v1_prefix: str = "/api/v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
