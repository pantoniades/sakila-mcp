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
- v0.1 is read-only, period. No write path exists in the codebase. (The long-term goal is
  a full read/write server with DBA features; when a write mode comes it is opt-in, uses
  separate tools, and read-only stays the default. Do not start it without being asked.)
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
- `read_only_hint=True` (wire: `readOnlyHint`) on every tool.
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
  `tool.output_schema`, `result.structured_content`, `result.is_error`. Construct them
  with snake_case too (`ToolAnnotations(read_only_hint=True)`; `populate_by_name=True`);
  the wire form is still camelCase (`readOnlyHint`).
- Sync `def` tools run on worker threads (right for the blocking connector).
- Output schemas must not contain a JSON Schema `format`. A `datetime` field emits
  `format: "date-time"`, MySQL's values have no UTC offset, and Claude Code then rejects the
  whole result. Temporal values are ISO 8601 strings; a unit test enforces this.
- `read_resource()` returns `Iterable[...] | InputRequiredResult`; narrow it.
- In-process `MCPServer.call_tool()` *raises* `ToolError`; only the protocol layer turns
  it into `isError: true` (text prefixed `Error executing tool <name>: `, no structured
  content). Test error paths through `mcp.client.Client(server)` (in-memory).
- A tool returning `Model | Refusal` gets an output schema wrapped as `{"result": ...}`.
- The stdio server exits on stdin EOF and abandons in-flight requests. Tests must keep
  stdin open until the response arrives.
- No async lifespan: `main()` builds `Database` and passes it to `build_server(config, database)`.
  Nothing connects at startup: `Database` connects and probes capabilities on the first call
  that needs MySQL (`_ensure_open`), so the server lists its tools with MySQL down (MCP
  directories introspect it that way). A failed connect is a tool error, not remembered, and
  retried by the next call. Anything reading `database.capabilities` can therefore raise
  `DatabaseError`: keep it inside `_mysql_errors()` (tools) or convert it (the resource), or
  the model sees a bare "Error executing tool".

sqlglot (30.x, MySQL dialect):
- Function names are normalized (`NVL`/`IFNULL` → `COALESCE`, `STRING_AGG` →
  `GroupConcat`) and special forms have no source `meta`. Never decide "is builtin"
  from the AST: the classifier detects calls from tokens (`name` followed by `(`).
- Not parsed: `TABLE t` (misparsed as an alias), `INTO` after `FROM`,
  `SHOW BINARY LOG STATUS`, `SHOW KEYS IN t IN db` (fall back to `Command`). Hence
  token-level INTO/locking checks and a hand-written SHOW matcher.
- Import `Token`, `TokenType` from `sqlglot.tokenizer_core`; type trees as `exp.Expr`.
- sqlglot starts a `--` comment before any Unicode whitespace; MySQL only before ASCII
  whitespace or a control character. `--<NBSP>` hid the rest of the line from the classifier
  but not from the server. `_check_lexing` re-scans the gaps between tokens with MySQL's
  rules and refuses (`ambiguous_comment`); never trust sqlglot's idea of a comment alone.
- Function-call names may be non-ASCII or start with `$`, and `"\u017f".upper() == "S"`:
  builtin/keyword matching is ASCII-only, everything else is an unknown function.
- The tokenizer assumes backslash escapes and `"` = string. Session init therefore
  strips `NO_BACKSLASH_ESCAPES`, `ANSI_QUOTES` and `ANSI` from the session sql_mode and
  fails closed if they stick.

mysql-connector-python (26.x):
- Do NOT use `MySQLConnectionPool`: its config check (C extension) mutates the shared
  `conn_attrs` dict, the attribute block exceeds 255 bytes, and the connector's 1-byte
  length encoding crashes. `pool.Database` is our own small pool with a fresh
  `connect_args()` per connection.
- `connect_args` removes `ClientFlag.MULTI_STATEMENTS`: the default flags allow it, and the
  server then runs a hidden second statement before the connector complains.
- Auth-plugin name on failure is read defensively from connector internals
  (`_authenticator._auth_strategy.name`, then `_handshake["auth_plugin"]`). Never make
  a second login attempt to diagnose (account lockout risk).
- `VECTOR` columns come back as `array('f', ...)`.
- The connector logs auth-plugin selection at INFO; `__main__` sets it to WARNING.
- Do NOT use the prepared cursor (`cursor(prepared=True)`, binary protocol): it crashes
  decoding `YEAR` ("unpack requires a buffer of 8 bytes") and returns JSON as bytes. It
  also rewrites `%s` to `?` with a quote-counting regex. `run_query` binds params with
  SQL-level `PREPARE ... FROM %s` / `SET @p = %s` / `EXECUTE ... USING` instead
  (text-protocol results; `max_execution_time` still applies, error 3024).
- A plain `cursor.execute(sql)` with no params argument does no `%` substitution.
- With rows left unread, `cursor.close()` and `cmd_reset_connection()` raise
  `InternalError("Unread result found")`; `cnx.close()` is instant and the server drops
  the statement. The pool discards connections with `cnx.unread_result` set.
- `cursor.description` entries carry flags at `[7]` (ENUM/SET) and the charset id at
  `[8]` (63 = binary); TEXT/BLOB and VARCHAR/VARBINARY share wire types.

## Dev environment
- Docker is forbidden on this machine; there is no docker-compose.yml.
- The user installs system tooling (uv, MySQL, …) themselves; ask, don't install.
- Integration tests run against MySQL 8.4.11 Community on a remote test server, prepared
  with `tests/integration/setup.sql` (schemas `sakila_mcp_test`, `sakila_mcp_other`;
  account `sakila_mcp_test@<client host>`, here the dev machine's IP address). Run it with
  `tests/integration/setup.sh`, which takes `SAKILA_MCP_TEST_CLIENT_HOST` and
  `SAKILA_MCP_TEST_PASSWORD` from the environment; never edit `setup.sql` in place. The DSN
  comes from `SAKILA_MCP_TEST_DSN` or the gitignored `.env.test`. Never print the DSN or
  password.
- That server: `require_secure_transport=OFF` (TLS still negotiated), `lower_case_table_names=0`,
  `explain_json_format_version=1` by default and session-settable to 2, so both EXPLAIN
  JSON shapes are testable. No VECTOR type (the `embeddings` fixture is 9.0+ only, so its
  tests skip). The test account cannot see `sys` or read `performance_schema` unless granted.
- A second target is a local MySQL 26.10.0 Community (Innovation) server: it has VECTOR
  (the `embeddings` test runs) and its EXPLAIN JSON v2 wraps the plan in `query_plan`
  (`json_schema_version` 2.1). Select a server with `SAKILA_MCP_TEST_DSN`; the pytest summary
  prints which server and DSN source a run used. No 9.0–9.6 or 9.7 server is available
  locally; 9.7 is covered by CI.
- CI (`.github/workflows/ci.yml`) runs the integration tests against the official `mysql`
  images 8.4, 9.7 and 26.10 (gating) and 8.0 (best-effort, never blocks). It uses no
  repository secrets. A missing DSN there is an error, not a skip.
- Releases: bump `version` in `pyproject.toml`, push, wait for green CI, then push the tag
  `v<version>`. `release.yml` publishes to PyPI by trusted publishing after a manual approval
  of the `pypi` environment. `v*` tags are locked on GitHub once pushed (ruleset, no bypass),
  so check `git ls-remote --tags origin` first. `v0.1.0` exists there on a pre-CI commit and
  was never published; the first PyPI release is 0.1.1.
- `uv run pytest -m llm` runs an opt-in smoke test through Claude Code (`claude -p`). Run it
  before a release: Claude Code validates tool results against the output schema more
  strictly than the Python client the other tests use.
- Host-specific details (server names, addresses) live in the gitignored `CLAUDE.local.md`.

## Working style
- Propose, then wait for OK, before any structural change.
- One deliverable per commit. Small commits. Conventional commit messages.
- Prefer boring code. No cleverness, no premature abstraction, no plugin systems.
- When tests fail, fix the code, not the test, unless the test is wrong — say which.
- End every work session with: what was done, what passes, what is open.
