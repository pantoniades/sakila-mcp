"""The server starts, and lists its tools, with no MySQL behind it.

MCP directories and clients introspect a server without a database. Nothing may
connect before the first call that needs MySQL, a failed connect must be a tool error
(never a crash and never an exit), and the next call must try again.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from mcp import MCPError
from mcp.client import Client
from mcp.types import TextContent

from sakila_mcp.capabilities import Capabilities
from sakila_mcp.config import Config
from sakila_mcp.pool import Database, DatabaseError
from sakila_mcp.safety.redact import clear_secrets, register_secret
from sakila_mcp.server import CAPABILITIES_URI, build_server

PASSWORD = "pw-Lz93-secret"
TOOL_COUNT = 6

CAPS = Capabilities(
    version=(8, 4, 11),
    version_raw="8.4.11",
    version_comment="MySQL Community Server - GPL",
    release_line="8.4 LTS",
    explain_json_version=1,
    has_sys_schema=True,
)


@pytest.fixture(autouse=True)
def _secret() -> Iterator[None]:
    clear_secrets()
    register_secret(PASSWORD)
    yield
    clear_secrets()


def _closed_port() -> int:
    """A local port with nothing listening: connecting fails at once with errno 111."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _unreachable_config() -> Config:
    return Config(user="mcp", password=PASSWORD, host="127.0.0.1", port=_closed_port())


# --- Database -------------------------------------------------------------------------


def _fake_open(db: Database, failures: int, delay_s: float = 0.0) -> list[int]:
    """Replace db.open: the first `failures` calls raise, later ones succeed.

    Returns a list that gets one entry per call.
    """
    calls: list[int] = []

    def fake() -> Capabilities:
        calls.append(1)
        time.sleep(delay_s)
        if len(calls) <= failures:
            raise DatabaseError("cannot connect to MySQL: 2003: connection refused")
        db._capabilities = CAPS
        return CAPS

    db.open = fake  # type: ignore[method-assign]
    return calls


def test_constructing_does_not_connect() -> None:
    db = Database(_unreachable_config())
    calls = _fake_open(db, failures=0)
    assert calls == []


def test_failed_first_connect_is_retried_then_cached() -> None:
    db = Database(_unreachable_config())
    calls = _fake_open(db, failures=1)
    with pytest.raises(DatabaseError, match="cannot connect"):
        _ = db.capabilities
    assert db.capabilities is CAPS
    assert db.capabilities is CAPS
    assert len(calls) == 2  # the failure was not remembered; the success was


def test_connection_context_manager_connects_lazily() -> None:
    db = Database(_unreachable_config())
    calls = _fake_open(db, failures=1)
    with pytest.raises(DatabaseError, match="cannot connect"), db.connection():
        pytest.fail("no connection may be handed out while MySQL is unreachable")
    assert len(calls) == 1


def test_concurrent_first_calls_connect_once() -> None:
    db = Database(_unreachable_config())
    calls = _fake_open(db, failures=0, delay_s=0.05)
    results: list[Capabilities] = []

    def first_use() -> None:
        results.append(db.capabilities)

    threads = [threading.Thread(target=first_use) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 8
    assert len(calls) == 1


def test_closed_database_does_not_reconnect() -> None:
    db = Database(_unreachable_config())
    calls = _fake_open(db, failures=0)
    db.close()
    with pytest.raises(DatabaseError, match="closed"):
        _ = db.capabilities
    assert calls == []


# --- tools over an in-memory client ---------------------------------------------------

DB_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("server_info", {}),
    ("list_databases", {}),
    ("list_tables", {"database": "sakila"}),
    ("describe_table", {"database": "sakila", "table": "actor"}),
    ("run_query", {"sql": "SELECT 1"}),
    ("explain_query", {"sql": "SELECT 1"}),
]


def _text(content: list[Any]) -> str:
    return "".join(c.text for c in content if isinstance(c, TextContent))


@pytest.mark.anyio
async def test_tools_list_without_mysql() -> None:
    config = _unreachable_config()
    async with Client(build_server(config, Database(config))) as client:
        tools = await client.list_tools()
    assert len(tools.tools) == TOOL_COUNT


@pytest.mark.anyio
@pytest.mark.parametrize(("tool", "args"), DB_TOOLS)
async def test_tool_reports_unreachable_mysql_as_tool_error(
    tool: str, args: dict[str, Any]
) -> None:
    config = _unreachable_config()
    async with Client(build_server(config, Database(config))) as client:
        result = await client.call_tool(tool, args)
        # A second call connects again (and fails again) instead of caching the failure.
        again = await client.call_tool(tool, args)
    for outcome in (result, again):
        assert outcome.is_error
        text = _text(outcome.content)
        # A DatabaseError that escaped _mysql_errors would arrive as a bare
        # "Error executing tool <name>" with the cause withheld.
        assert "cannot connect to MySQL" in text
        assert PASSWORD not in text


@pytest.mark.anyio
async def test_policy_refusal_needs_no_database() -> None:
    config = _unreachable_config()
    async with Client(build_server(config, Database(config))) as client:
        result = await client.call_tool("run_query", {"sql": "DROP TABLE t"})
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["result"]["refused"] is True


@pytest.mark.anyio
async def test_capabilities_resource_reports_unreachable_mysql() -> None:
    config = _unreachable_config()
    async with Client(build_server(config, Database(config))) as client:
        with pytest.raises(MCPError) as info:
            await client.read_resource(CAPABILITIES_URI)
    assert "cannot connect to MySQL" in str(info.value)
    assert PASSWORD not in str(info.value)


# --- the real process over stdio ------------------------------------------------------


def _rpc(id_: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


def test_stdio_server_starts_lists_tools_and_survives_a_failed_call() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAKILA_MCP_")}
    env |= {
        "SAKILA_MCP_HOST": "127.0.0.1",
        "SAKILA_MCP_PORT": str(_closed_port()),
        "SAKILA_MCP_USER": "mcp",
        "SAKILA_MCP_PASSWORD": PASSWORD,
    }
    requests = [
        _rpc(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc(2, "tools/list", {}),
        _rpc(3, "tools/call", {"name": "server_info", "arguments": {}}),
        _rpc(4, "tools/call", {"name": "server_info", "arguments": {}}),
    ]
    # Keep stdin open until the last response arrives: a real client does, and the server
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
    responses: dict[int, dict[str, Any]] = {}
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in requests))
        proc.stdin.flush()
        while 4 not in responses:
            line = proc.stdout.readline()
            if not line:
                break
            message = json.loads(line)  # stdout must be pure JSON-RPC
            if "id" in message:
                responses[message["id"]] = message
        assert proc.poll() is None, "the server exited after a failed MySQL connect"
        proc.stdin.close()
        stdout_rest = proc.stdout.read()
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        proc.wait()
    finally:
        watchdog.cancel()
    assert set(responses) == {1, 2, 3, 4}, stderr
    assert len(responses[2]["result"]["tools"]) == TOOL_COUNT
    for id_ in (3, 4):
        assert responses[id_]["result"]["isError"] is True
        assert "cannot connect to MySQL" in responses[id_]["result"]["content"][0]["text"]
    everything = json.dumps(responses) + stdout_rest + stderr
    assert PASSWORD not in everything
    assert "MySQL is not available" in stderr  # the operator sees it too
