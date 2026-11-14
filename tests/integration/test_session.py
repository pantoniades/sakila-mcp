"""Step 3: pool, session init, capabilities, server_info, startup over stdio."""

import json
import os
import subprocess
import sys
import threading
from typing import Any

import mysql.connector
import pytest
from mcp.types import CallToolResult, InputRequiredResult
from mysql.connector.connection import MySQLConnection

from sakila_mcp import __version__
from sakila_mcp.config import Config
from sakila_mcp.pool import STRIPPED_SQL_MODES, Database, DatabaseError
from sakila_mcp.server import CAPABILITIES_URI, build_server
from sakila_mcp.versions import parse_version, release_line

from .conftest import TEST_DB, Dsn

pytestmark = pytest.mark.integration


def _scalar(database: Database, sql: str) -> Any:
    with database.connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute(sql)
        row = cursor.fetchone()
        cursor.close()
    assert row is not None
    return row[0]


def test_version_round_trip(database: Database) -> None:
    raw = _scalar(database, "SELECT VERSION()")
    caps = database.capabilities
    assert caps.version_raw == raw
    assert caps.version == parse_version(raw)
    assert caps.release_line == release_line(caps.version)
    assert caps.explain_json_version in (1, 2)


def test_insert_refused_by_read_only_session(database: Database) -> None:
    with database.connection() as cnx:
        cursor = cnx.cursor()
        with pytest.raises(mysql.connector.Error) as info:
            cursor.execute("INSERT INTO sakila_mcp_test.write_probe (id) VALUES (1)")
        cursor.close()
    assert info.value.errno == 1792


def test_second_statement_refused_by_the_server(database: Database) -> None:
    # Protocol-level backstop: whatever the classifier decided, the server must not run
    # a second statement.
    with database.connection() as cnx:
        cursor = cnx.cursor()
        with pytest.raises(mysql.connector.Error) as info:
            cursor.execute("SELECT 1; SELECT 2")
    assert info.value.errno == 1064


def test_session_variables(database: Database, config: Config) -> None:
    with database.connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute(
            "SELECT @@SESSION.transaction_read_only, @@SESSION.max_execution_time, "
            "@@SESSION.lock_wait_timeout, @@SESSION.sql_mode, @@SESSION.autocommit"
        )
        row: Any = cursor.fetchone()
        read_only, max_exec, lock_wait, sql_mode, autocommit = row
        cursor.close()
    assert int(read_only) == 1
    assert int(max_exec) == config.statement_timeout_ms
    assert int(lock_wait) == 5
    assert not STRIPPED_SQL_MODES & set(str(sql_mode).split(","))
    assert int(autocommit) == 1


def test_session_reinitialized_on_every_checkout(database: Database, config: Config) -> None:
    with database.connection() as cnx:
        cursor = cnx.cursor()
        cursor.execute("SET SESSION TRANSACTION READ WRITE")
        cursor.execute("SET SESSION max_execution_time = 0")
        cursor.execute("SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES,ANSI_QUOTES'")
        cursor.close()
    for _ in range(config.pool_size + 1):
        test_session_variables(database, config)


def test_connection_attributes(database: Database) -> None:
    with database.connection() as cnx:
        cursor = cnx.cursor()
        try:
            cursor.execute(
                "SELECT ATTR_NAME, ATTR_VALUE FROM performance_schema.session_connect_attrs "
                "WHERE PROCESSLIST_ID = CONNECTION_ID()"
            )
        except mysql.connector.Error as exc:
            if exc.errno == 1142:
                pytest.skip("test account lacks SELECT on performance_schema")
            raise
        attrs: dict[Any, Any] = dict(cursor.fetchall())  # type: ignore[arg-type]
        cursor.close()
    assert attrs.get("program_name") == "sakila-mcp"
    assert "sakila_mcp_version" in attrs


def test_auth_failure_names_plugin(dsn: Dsn, config: Config) -> None:
    wrong = "not-the-password-71x"
    bad = config.model_copy(update={"password": config.password.__class__(wrong)})
    with pytest.raises(DatabaseError) as info:
        Database(bad).open()
    message = str(info.value)
    assert "auth plugin: caching_sha2_password" in message
    assert wrong not in message
    assert dsn.password not in message


def test_caching_sha2_without_tls(dsn: Dsn) -> None:
    cnx = MySQLConnection()
    try:
        cnx.connect(
            host=dsn.host,
            port=dsn.port,
            user=dsn.user,
            password=dsn.password,
            use_pure=True,
            ssl_disabled=True,
            connection_timeout=5,
        )
    except mysql.connector.Error as exc:
        if exc.errno == 3159:
            pytest.skip("server has require_secure_transport=ON; TLS-less path untestable")
        raise
    try:
        cursor = cnx.cursor()
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        row: Any = cursor.fetchone()
        assert row[1] == ""
    finally:
        cnx.close()


@pytest.mark.anyio
async def test_server_info_tool(database: Database, config: Config) -> None:
    server = build_server(config, database)
    result = await server.call_tool("server_info", {})
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    info = result.structured_content
    assert info is not None
    assert info["sakila_mcp_version"] == __version__
    assert info["version_raw"] == database.capabilities.version_raw
    assert info["version"] == list(database.capabilities.version)
    assert info["uptime_seconds"] > 0
    assert info["max_connections"] > 0
    assert isinstance(info["read_only"], bool)
    assert info["character_set_server"]


@pytest.mark.anyio
async def test_capabilities_resource(database: Database, config: Config) -> None:
    server = build_server(config, database)
    result = await server.read_resource(CAPABILITIES_URI)
    assert not isinstance(result, InputRequiredResult)
    contents = list(result)
    assert len(contents) == 1
    data = json.loads(str(contents[0].content))
    assert data["version_raw"] == database.capabilities.version_raw
    assert data["release_line"] == database.capabilities.release_line
    assert data["sakila_mcp_version"] == __version__
    assert set(data) == {
        "sakila_mcp_version",
        "version",
        "version_raw",
        "version_comment",
        "release_line",
        "explain_json_version",
        "has_sys_schema",
    }


def test_stdio_end_to_end(dsn: Dsn) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "server_info", "arguments": {}},
        },
    ]
    env = {
        **os.environ,
        "SAKILA_MCP_HOST": dsn.host,
        "SAKILA_MCP_PORT": str(dsn.port),
        "SAKILA_MCP_USER": dsn.user,
        "SAKILA_MCP_PASSWORD": dsn.password,
        "SAKILA_MCP_DATABASE_ALLOWLIST": TEST_DB,
    }
    # Keep stdin open until the response arrives: a real client does, and the server
    # exits on EOF without finishing in-flight requests.
    proc = subprocess.Popen(
        [sys.executable, "-m", "sakila_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
        proc.stdin.flush()
        lines: list[str] = []
        response: dict[str, Any] | None = None
        while response is None:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            message = json.loads(line)  # stdout must be pure JSON-RPC
            if message.get("id") == 2:
                response = message
        proc.stdin.close()
        rest = proc.stdout.read()
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        proc.wait()
    finally:
        watchdog.cancel()
    stdout = "".join(lines) + rest
    assert response is not None, stderr
    assert response["result"]["structuredContent"]["version_raw"]
    assert dsn.password not in stdout
    assert dsn.password not in stderr
