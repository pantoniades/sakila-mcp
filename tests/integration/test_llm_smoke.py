"""Smoke test through a real MCP client driven by a real model (Claude Code, headless).

Opt-in and local only: ``uv run pytest -m llm``. It costs money, is not deterministic and
needs the ``claude`` CLI, so it is deselected by default and never runs in CI.

Why it exists: Claude Code validates every tool result against the tool's output schema,
which the Python client used by the other tests does not do as strictly. A result that
passes every other test can still be rejected there (it happened: a "date-time" field
without a UTC offset made describe_table unusable).

The assertions are about the protocol, not about the model's wording: every tool was
called, no result was rejected by the client, and a write was refused by the server.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from .conftest import TEST_DB, Dsn

pytestmark = [pytest.mark.integration, pytest.mark.llm]

SERVER = "mysql"
TOOLS = (
    "server_info",
    "list_databases",
    "list_tables",
    "describe_table",
    "run_query",
    "explain_query",
)
# What Claude Code puts in a tool result when the structured content fails validation.
SCHEMA_ERROR = "does not match the tool's output schema"
WRITE = f"DELETE FROM {TEST_DB}.write_probe WHERE id = 1"

PROMPT = f"""\
This is a compatibility test of the "{SERVER}" MCP server, which is read-only. Make exactly
these tool calls, in this order, even if one of them fails, and do not skip any:

1. server_info
2. list_databases
3. list_tables with database "{TEST_DB}"
4. describe_table for "customer" and again for "orders" in "{TEST_DB}"
5. run_query with sql "SELECT id, email, created_at FROM customer ORDER BY id LIMIT 3" and
   database "{TEST_DB}"
6. explain_query with sql "SELECT * FROM orders ORDER BY amount" and database "{TEST_DB}"
7. run_query with sql "{WRITE}". The server is expected to refuse this statement; sending
   it is the point of the test, so send it exactly as written.

Then reply with one line per call: the tool name and "ok", "refused" or "error".
"""


def _text(content: Any) -> str:
    """A tool result's content is either a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return "" if content is None else str(content)


def _run_claude(dsn: Dsn, workdir: Path) -> str:
    config = {
        "mcpServers": {
            SERVER: {
                "command": sys.executable,
                "args": ["-m", "sakila_mcp"],
                "env": {
                    "SAKILA_MCP_HOST": dsn.host,
                    "SAKILA_MCP_PORT": str(dsn.port),
                    "SAKILA_MCP_USER": dsn.user,
                    "SAKILA_MCP_PASSWORD": dsn.password,
                    "SAKILA_MCP_DATABASE_ALLOWLIST": TEST_DB,
                },
            }
        }
    }
    config_path = workdir / "mcp.json"
    config_path.touch(mode=0o600)  # holds the test password
    config_path.write_text(json.dumps(config), encoding="utf-8")
    command = [
        "claude",
        "-p",
        PROMPT,
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
        # Only this server's tools: the model must not fall back on a shell or files.
        "--allowedTools",
        f"mcp__{SERVER}",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    model = os.environ.get("SAKILA_MCP_LLM_MODEL")
    if model:
        command += ["--model", model]
    # An empty working directory, so the agent does not read this project's CLAUDE.md
    # and behaves like a client that has never seen the server.
    done = subprocess.run(
        command, cwd=workdir, capture_output=True, text=True, timeout=600, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_claude_code_accepts_every_tool_result(dsn: Dsn, tmp_path: Path) -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found")
    stdout = _run_claude(dsn, tmp_path)
    assert dsn.password not in stdout

    calls: dict[str, tuple[str, dict[str, Any]]] = {}  # tool_use id -> (tool, input)
    results: dict[str, str] = {}  # tool_use id -> result text
    for line in stdout.splitlines():
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("type") not in ("assistant", "user"):
            continue
        content = message.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                prefix = f"mcp__{SERVER}__"
                if name.startswith(prefix):
                    calls[str(block["id"])] = (name.removeprefix(prefix), block.get("input") or {})
            elif block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = _text(block.get("content"))

    called = [tool for tool, _ in calls.values()]
    missing = [tool for tool in TOOLS if tool not in called]
    assert not missing, f"the model never called {missing}; it called {called}"

    rejected = [
        f"{calls[call_id][0]}: {text[:300]}"
        for call_id, text in results.items()
        if call_id in calls and SCHEMA_ERROR in text
    ]
    assert not rejected, "the client rejected these results:\n" + "\n".join(rejected)

    writes = [
        results.get(call_id, "")
        for call_id, (tool, args) in calls.items()
        if tool == "run_query" and "DELETE" in str(args.get("sql", "")).upper()
    ]
    assert writes, "the model never sent the DELETE statement"
    for text in writes:
        refusal = json.loads(text)
        # Tools that can refuse return a union, which the SDK wraps as {"result": ...}.
        refusal = refusal.get("result", refusal)
        assert refusal["refused"] is True
        assert refusal["code"] == "statement_not_allowed"
