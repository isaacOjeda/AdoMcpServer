from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="ADO_", extra="ignore"
    )

    organization: str
    pat: SecretStr
    default_project: str | None = None
    read_only: bool = True
    api_version: str = "7.1"
