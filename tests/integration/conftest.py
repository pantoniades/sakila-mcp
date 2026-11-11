"""Integration fixtures. Requires SAKILA_MCP_TEST_DSN (env, or a gitignored .env.test).

The target server must have been prepared with tests/integration/setup.sql.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import mysql.connector
import pytest
from mcp.client import Client
from mcp.types import CallToolResult, TextContent

from sakila_mcp.config import Config
from sakila_mcp.pool import Database
from sakila_mcp.safety.redact import register_secret
from sakila_mcp.server import build_server

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.test"
TEST_DB = "sakila_mcp_test"
OTHER_DB = "sakila_mcp_other"


@dataclass(frozen=True)
class Dsn:
    host: str
    port: int
    user: str
    password: str


def _read_dsn_with_source() -> tuple[str, str] | None:
    value = os.environ.get("SAKILA_MCP_TEST_DSN")
    if value:
        return value, "environment"
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, rest = line.partition("=")
            if sep and key.strip() == "SAKILA_MCP_TEST_DSN":
                return rest.strip(), ENV_FILE.name
    return None


def _read_dsn() -> str | None:
    found = _read_dsn_with_source()
    return found[0] if found else None


def _describe_target() -> str:
    found = _read_dsn_with_source()
    if found is None:
        return "no SAKILA_MCP_TEST_DSN (tests skip)"
    raw, source = found
    parts = urlsplit(raw)
    target = f"{unquote(parts.username or '')}@{parts.hostname}:{parts.port or 3306}"
    try:
        cnx = mysql.connector.connect(
            host=parts.hostname or "127.0.0.1",
            port=parts.port or 3306,
            user=unquote(parts.username or ""),
            password=unquote(parts.password or ""),
            connection_timeout=5,
        )
        try:
            cursor = cnx.cursor()
            cursor.execute("SELECT VERSION(), @@version_comment")
            row: Any = cursor.fetchone()
            cursor.close()
        finally:
            cnx.close()
        server = f"{row[0]} ({row[1]})" if row else "unknown"
    except mysql.connector.Error as exc:
        server = f"unreachable (errno {exc.errno})"
    return f"{target} [DSN from {source}] server {server}"


def pytest_terminal_summary(terminalreporter: Any) -> None:
    # Summary, not report header: -q hides the header, and the .env.test fallback
    # (a different server than the one you exported) is easy to forget.
    terminalreporter.write_line(f"sakila-mcp integration target: {_describe_target()}")


@pytest.fixture(scope="session")
def dsn() -> Dsn:
    raw = _read_dsn()
    if not raw:
        pytest.skip("SAKILA_MCP_TEST_DSN not set (env or .env.test)")
    parts = urlsplit(raw)
    password = unquote(parts.password or "")
    register_secret(password)
    return Dsn(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 3306,
        user=unquote(parts.username or ""),
        password=password,
    )


@pytest.fixture(scope="session")
def config(dsn: Dsn) -> Config:
    return Config(
        host=dsn.host,
        port=dsn.port,
        user=dsn.user,
        password=dsn.password,
        database_allowlist=[TEST_DB],
        statement_timeout_ms=2000,
        pool_size=2,
    )


@pytest.fixture(scope="session")
def database(config: Config) -> Iterator[Database]:
    db = Database(config)
    db.open()
    yield db
    db.close()


async def call_tool(
    database: Database, config: Config, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Call a tool expected to succeed; return its structured result (unwrapped)."""
    result = await build_server(config, database).call_tool(tool, args)
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content
    # Tools that can refuse return a union, which the SDK wraps as {"result": ...}.
    if set(content) == {"result"}:
        inner: dict[str, Any] = content["result"]
        return inner
    return content


async def call_tool_error(
    database: Database, config: Config, tool: str, args: dict[str, Any]
) -> str:
    """Call a tool expected to fail with a tool error; return the error text.

    Goes through an in-memory client: in-process ``MCPServer.call_tool`` raises
    ``ToolError``, and only the protocol layer turns it into ``isError: true``.
    """
    async with Client(build_server(config, database)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert result.structured_content is None
    return "".join(c.text for c in result.content if isinstance(c, TextContent))
