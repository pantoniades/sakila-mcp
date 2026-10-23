"""Server configuration from ``SAKILA_MCP_*`` env vars and an optional TOML file.

Precedence (highest first): explicit init kwargs, environment, TOML file, defaults.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAKILA_MCP_",
        extra="forbid",
        toml_file=None,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=3306, ge=1, le=65535)
    user: str
    password: SecretStr = SecretStr("")
    # Empty means every non-system schema. Env form: comma-separated names.
    database_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_rows: int = Field(default=200, ge=1)
    max_cell_bytes: int = Field(default=4096, ge=16)
    statement_timeout_ms: int = Field(default=10_000, ge=1)
    pool_size: int = Field(default=4, ge=1, le=32)
    read_only: bool = True

    @field_validator("database_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, value: object) -> object:
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value

    @field_validator("read_only")
    @classmethod
    def _require_read_only(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "write mode is not implemented in sakila-mcp v0.1; read_only must be true"
            )
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: tuple[PydanticBaseSettingsSource, ...] = (init_settings, env_settings)
        if settings_cls.model_config.get("toml_file") is not None:
            sources += (TomlConfigSettingsSource(settings_cls),)
        return sources


def load_config(toml_path: Path | None = None) -> Config:
    """Load configuration; ``toml_path`` must exist when given."""
    if toml_path is None:
        return Config()
    if not toml_path.is_file():
        raise FileNotFoundError(f"config file not found: {toml_path}")

    class _FileConfig(Config):
        model_config = SettingsConfigDict(toml_file=toml_path)

    return _FileConfig()
