import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from sakila_mcp.config import Config, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("SAKILA_MCP_"):
            monkeypatch.delenv(key)


def test_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAKILA_MCP_USER", "mcp")
    config = load_config()
    assert config.user == "mcp"
    assert config.host == "127.0.0.1"
    assert config.port == 3306
    assert config.database_allowlist == []
    assert config.max_rows == 200
    assert config.max_cell_bytes == 4096
    assert config.statement_timeout_ms == 10_000
    assert config.pool_size == 4
    assert config.read_only is True


def test_user_required() -> None:
    with pytest.raises(ValidationError, match="user"):
        load_config()


def test_allowlist_env_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAKILA_MCP_USER", "mcp")
    monkeypatch.setenv("SAKILA_MCP_DATABASE_ALLOWLIST", "sakila, world ,,")
    assert load_config().database_allowlist == ["sakila", "world"]


def test_password_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAKILA_MCP_USER", "mcp")
    monkeypatch.setenv("SAKILA_MCP_PASSWORD", "hunter2-secret")
    config = load_config()
    assert config.password.get_secret_value() == "hunter2-secret"
    assert "hunter2-secret" not in repr(config)
    assert "hunter2-secret" not in str(config)


def test_read_only_false_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAKILA_MCP_USER", "mcp")
    monkeypatch.setenv("SAKILA_MCP_READ_ONLY", "false")
    with pytest.raises(ValidationError, match="write mode is not implemented"):
        load_config()


def test_toml_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text(
        'user = "reader"\nport = 3307\ndatabase_allowlist = ["sakila"]\nmax_rows = 50\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert isinstance(config, Config)
    assert (config.user, config.port, config.max_rows) == ("reader", 3307, 50)
    assert config.database_allowlist == ["sakila"]


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text('user = "reader"\nmax_rows = 50\n', encoding="utf-8")
    monkeypatch.setenv("SAKILA_MCP_MAX_ROWS", "75")
    assert load_config(path).max_rows == 75


def test_toml_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text('user = "reader"\nmax_row = 50\n', encoding="utf-8")
    with pytest.raises(ValidationError, match="max_row"):
        load_config(path)


def test_toml_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


@pytest.mark.parametrize(
    ("key", "value"), [("max_rows", "0"), ("port", "70000"), ("pool_size", "0")]
)
def test_bounds(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv("SAKILA_MCP_USER", "mcp")
    monkeypatch.setenv(f"SAKILA_MCP_{key.upper()}", value)
    with pytest.raises(ValidationError):
        load_config()
