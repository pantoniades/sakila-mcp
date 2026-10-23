# sakila_mcp — project rules for Claude Code

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
- Python 3.12, `uv`, package `sakila_mcp`, src layout.
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
  Caller errors (unknown database/table, bad arguments) are tool errors whose message
  says how to correct the call. Infrastructure failures (connection, auth) are tool errors.
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
- Release lines (confirmed by the user): 8.0, 8.4, 9.7 = LTS; 8.1–8.3 and 9.0–9.6 =
  Innovation; calendar lines 26.x–28.x = Innovation (through 2028); 29+ = "unknown"
  until told otherwise.

## Verified facts (learned the hard way — do not re-derive)

MCP SDK v2 (mcp 2.3.0):
- `from mcp.server import MCPServer`; `ToolError` from `mcp.server.mcpserver.exceptions`.
- Pydantic models are snake_case at runtime: `tool.annotations.read_only_hint`,
  `tool.output_schema`, `result.structured_content`, `result.is_error`. camelCase
  (`readOnlyHint=`) only works as a constructor alias.
- Sync `def` tools run on worker threads (right for the blocking connector).
- `read_resource()` returns `Iterable[...] | InputRequiredResult`; narrow it.
- In-process `MCPServer.call_tool()` *raises* `ToolError`; only the protocol layer turns
  it into `isError: true` (text prefixed `Error executing tool <name>: `, no structured
  content). Test error paths through `mcp.client.Client(server)` (in-memory).
- A tool returning `Model | Refusal` gets an output schema wrapped as `{"result": ...}`.
- The stdio server exits on stdin EOF and abandons in-flight requests. Tests must keep
  stdin open until the response arrives.
- No async lifespan: `main()` opens `Database` before `run("stdio")` and passes it to
  `build_server(config, database)`.

sqlglot (30.x, MySQL dialect):
- Function names are normalized (`NVL`/`IFNULL` → `COALESCE`, `STRING_AGG` →
  `GroupConcat`) and special forms have no source `meta`. Never decide "is builtin"
  from the AST: the classifier detects calls from tokens (`name` followed by `(`).
- Not parsed: `TABLE t` (misparsed as an alias), `INTO` after `FROM`,
  `SHOW BINARY LOG STATUS`, `SHOW KEYS IN t IN db` (fall back to `Command`). Hence
  token-level INTO/locking checks and a hand-written SHOW matcher.
- Import `Token`, `TokenType` from `sqlglot.tokenizer_core`; type trees as `exp.Expr`.
- The tokenizer assumes backslash escapes and `"` = string. Session init therefore
  strips `NO_BACKSLASH_ESCAPES`, `ANSI_QUOTES` and `ANSI` from the session sql_mode and
  fails closed if they stick.

mysql-connector-python (26.x):
- Do NOT use `MySQLConnectionPool`: its config check (C extension) mutates the shared
  `conn_attrs` dict, the attribute block exceeds 255 bytes, and the connector's 1-byte
  length encoding crashes. `pool.Database` is our own small pool with a fresh
  `connect_args()` per connection.
- Auth-plugin name on failure is read defensively from connector internals
  (`_authenticator._auth_strategy.name`, then `_handshake["auth_plugin"]`). Never make
  a second login attempt to diagnose (account lockout risk).
- `VECTOR` columns come back as `array('f', ...)`.
- The connector logs auth-plugin selection at INFO; `__main__` sets it to WARNING.

## Dev environment
- Docker is forbidden on this machine; there is no docker-compose.yml.
- The user installs system tooling (uv, MySQL, …) themselves; ask, don't install.
- Integration tests run against MySQL 8.4.11 Community on a remote test server, prepared
  with `tests/integration/setup.sql` (schemas `sakila_mcp_test`, `sakila_mcp_other`;
  account `sakila_mcp_test@CLIENT_HOST`, here the dev machine's IP address). The DSN comes
  from `SAKILA_MCP_TEST_DSN` or the gitignored `.env.test`. Never print the DSN or
  password; keep the `CHANGE_ME` and `CLIENT_HOST` placeholders in `setup.sql`.
- That server: `require_secure_transport=OFF` (TLS still negotiated), `lower_case_table_names=0`,
  `explain_json_format_version=1` by default and session-settable to 2, so both EXPLAIN
  JSON shapes are testable. No VECTOR type (the `embeddings` fixture is 9.0+ only, so its
  tests skip). The test account cannot see `sys` or read `performance_schema` unless granted.
- No 9.x server is available to the tests at present.
- Host-specific details (server names, addresses) live in the gitignored `CLAUDE.local.md`.

## Working style
- Propose, then wait for OK, before any structural change.
- One deliverable per commit. Small commits. Conventional commit messages.
- Prefer boring code. No cleverness, no premature abstraction, no plugin systems.
- When tests fail, fix the code, not the test, unless the test is wrong — say which.
- End every work session with: what was done, what passes, what is open.
