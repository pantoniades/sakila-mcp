# mysql_mcp — project rules for Claude Code

You are building a production-quality MySQL MCP server in Python with an experienced
Python developer and MySQL expert (InnoDB, binlog, GTID, group replication). Do not
explain MySQL concepts. Do explain MCP design choices when there is a judgment call.

## Non-negotiable first step

Your training data predates MCP Python SDK v2. Before writing any server code, read:
- https://py.sdk.modelcontextprotocol.io/whats-new/
- https://py.sdk.modelcontextprotocol.io/migration/
and use `mcp>=2,<3` with `MCPServer`. Never import `mcp.server.fastmcp` and never add
the standalone `fastmcp` package. If you are unsure of a v2 API, read the docs or
`uv run python -c "import mcp; help(...)"` — do not guess from v1 memory.

## Stack
- Python 3.12, `uv`, package `mysql_mcp`, src layout.
- `mcp>=2,<3`, `mysql-connector-python`, `sqlglot`, `pydantic>=2`, `pydantic-settings`.
- `ruff` (lint + format), `mypy --strict`, `pytest`. All three must pass before you
  say anything is done. Run them; do not claim you ran them.
- Logging to stderr only. Nothing writes to stdout except the MCP transport.
- Do not add a dependency without asking.

## Safety model (never weaken, never bypass "temporarily")
- v0.1 is read-only, period. No write path exists in the codebase.
- Every session: `SET SESSION TRANSACTION READ ONLY`, `max_execution_time`, small
  `lock_wait_timeout`, connection attributes identifying the server.
- Every statement from `run_query` passes the sqlglot classifier. Parse failure =
  refusal (fail closed). Multi-statement = refusal. `/*!` version comments = refusal.
- Result sets: fetch `max_rows + 1`, stop, set `truncated`. Never rewrite user SQL
  to add LIMIT.
- Database allowlist enforced on every tool. `information_schema`, `performance_schema`,
  `sys` always readable; `mysql` denied by default.
- Password / DSN never appears in any tool result, log line, or exception text.
  There is a redaction function and a test that proves it.

## MCP conventions
- Pydantic models for all tool inputs and outputs; structured results, not prose.
- Policy refusals are *results* (`refused: true`, `reason`, `suggestion`), not errors.
  Infrastructure failures (connection, auth) are tool errors.
- `readOnlyHint=True` on every tool.
- Tools are registered statically. Unsupported-on-this-server returns
  `{"supported": false, "reason": ..., "requires": [...]}`.
- Tool descriptions are written for an LLM caller: what it returns, when to use it,
  one sentence each. No marketing.

## MySQL version handling
- Version strings may be `8.0.36`, `8.4.11`, `9.7.2`, or calendar-versioned `26.10.0`.
  `versions.py` parses all of them to a tuple and tuple-compares. No semver library.
- Gate features by probing (table/plugin exists), not by version, except for pure
  syntax differences. Never gate on edition/license.
- When a MySQL behavior is version-dependent and you are not certain, STOP and ask.
  Do not guess. List it as an open question in your summary.

## Working style
- Propose, then wait for OK, before any structural change.
- One deliverable per commit. Small commits. Conventional commit messages.
- Prefer boring code. No cleverness, no premature abstraction, no plugin systems.
- When tests fail, fix the code, not the test, unless the test is wrong — say which.
- End every work session with: what was done, what passes, what is open.
