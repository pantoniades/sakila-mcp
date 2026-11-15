"""Smoke test through a real MCP client driven by a real model (Claude Code, headless).

Opt-in and local only: ``uv run pytest -m llm``. It costs money, is not deterministic and
needs the ``claude`` CLI, so tests/conftest.py skips it unless the ``-m`` expression names
``llm``, and it never runs in CI.

Why it exists: Claude Code validates every tool result against the tool's output schema,
which the Python client used by the other tests does not do as strictly. A result that
passes every other test can still be rejected there (it happened: a "date-time" field
without a UTC offset made describe_table unusable).

The assertions are about the protocol, not about the model's wording: every tool was
called, the client accepted every result, and a write was refused by the server.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sakila_mcp.safety.redact import redact

from .conftest import TEST_DB, Dsn

pytestmark = pytest.mark.llm

SERVER = "mysql"
TOOLS = (
    "server_info",
    "list_databases",
    "list_tables",
    "describe_table",
    "run_query",
    "explain_query",
)
WRITE = f"DELETE FROM {TEST_DB}.write_probe WHERE id = 1"
# Deliberately not SAKILA_MCP_*: the server must only see it under the name set below.
PASSWORD_VAR = "SAKILA_LLM_SMOKE_PASSWORD"
TIMEOUT_S = 600

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
                    # Expanded by Claude Code from its own environment, so the password
                    # is never written to this file, which pytest keeps on disk.
                    "SAKILA_MCP_PASSWORD": "${" + PASSWORD_VAR + "}",
                    "SAKILA_MCP_DATABASE_ALLOWLIST": TEST_DB,
                },
            }
        }
    }
    config_path = workdir / "mcp.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    command = [
        "claude",
        "-p",
        PROMPT,
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
        # No built-in tools at all (shell, file access): --allowedTools alone only
        # pre-approves the server's tools, it does not take the others away.
        "--tools",
        "",
        "--allowedTools",
        f"mcp__{SERVER}",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    model = os.environ.get("SAKILA_MCP_LLM_MODEL")
    if model:
        command += ["--model", model]
    # The server must run with exactly the settings above, not with whatever
    # SAKILA_MCP_* happens to be exported in the developer's shell.
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAKILA_MCP_")}
    env[PASSWORD_VAR] = dsn.password
    try:
        # An empty working directory, so the agent does not read this project's CLAUDE.md
        # and behaves like a client that has never seen the server.
        done = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"claude did not finish within {TIMEOUT_S} s")
    assert done.returncode == 0, redact(done.stderr[-2000:])
    return done.stdout


def test_claude_code_accepts_every_tool_result(dsn: Dsn, tmp_path: Path) -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found")
    stdout = _run_claude(dsn, tmp_path)
    # A bool first: asserting on the strings would print the password on failure.
    leaked = dsn.password in stdout
    assert not leaked, "the test password appears in Claude Code's output"

    calls: dict[str, tuple[str, dict[str, Any]]] = {}  # tool_use id -> (tool, input)
    results: dict[str, tuple[str, bool]] = {}  # tool_use id -> (text, is_error)
    prefix = f"mcp__{SERVER}__"
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # not part of the stream (blank line, stray diagnostic)
        if not isinstance(message, dict) or message.get("type") not in ("assistant", "user"):
            continue
        content = message.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                if name.startswith(prefix):
                    calls[str(block["id"])] = (name.removeprefix(prefix), block.get("input") or {})
            elif block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = (
                    _text(block.get("content")),
                    block.get("is_error") is True,
                )

    called = [tool for tool, _ in calls.values()]
    missing = [tool for tool in TOOLS if tool not in called]
    assert not missing, f"the model never called {missing}; it called {called}"

    # Every call here is valid, so any error result is a failure: a result the client
    # rejected against the output schema, a tool error, or no result at all.
    problems = []
    for call_id, (tool, _) in calls.items():
        text, is_error = results.get(call_id, ("<no result>", True))
        if is_error:
            problems.append(f"{tool}: {redact(text[:300])}")
    assert not problems, "these calls did not return a result:\n" + "\n".join(problems)

    writes = [
        results[call_id][0]
        for call_id, (tool, args) in calls.items()
        if tool == "run_query" and "DELETE" in str(args.get("sql", "")).upper()
    ]
    assert writes, "the model never sent the DELETE statement"
    for text in writes:
        try:
            refusal = json.loads(text)
        except json.JSONDecodeError:
            pytest.fail(f"the DELETE did not return a structured result: {redact(text[:300])}")
        # Tools that can refuse return a union, which the SDK wraps as {"result": ...}.
        refusal = refusal.get("result", refusal)
        assert refusal.get("refused") is True
        assert refusal.get("code") == "statement_not_allowed"
