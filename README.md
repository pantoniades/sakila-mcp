# sakila-mcp

sakila-mcp is an MCP server that lets an LLM agent inspect and query any MySQL database,
read-only, with a fail-closed SQL classifier between the agent and the server.

> sakila-mcp is a general-purpose MySQL MCP server for any MySQL database. It is named
> after Sakila, the MySQL dolphin mascot, and is not specific to the Sakila sample database.
>
> Independent open-source project. Not affiliated with, sponsored by or endorsed by
> Oracle. MySQL is a trademark of Oracle and/or its affiliates.

Status: v0.1, the first release. What's described here works today. Expect the interface
to change.

## Quickstart

You need [uv](https://docs.astral.sh/uv/) and a MySQL account that can only read (the
grants are under [Safety model](#safety-model)).

```sh
cat > ~/.config/sakila-mcp.toml <<'EOF'
host = "db.example.internal"
user = "sakila_mcp"
password = "..."
database_allowlist = ["app_db"]
EOF
chmod 600 ~/.config/sakila-mcp.toml

claude mcp add sakila -- uvx sakila-mcp --config ~/.config/sakila-mcp.toml
```

Then ask "what's in app_db and how do orders relate to customers?" or "why is this query
slow?". Any MCP client that speaks stdio works; see [Use it](#use-it).

<!-- TODO before the first release: "## What it looks like", a short transcript of a real
agent session (tool calls and results verbatim, cuts marked with …). Do not write one by
hand. -->

## What makes it different

- It fails closed. A statement the classifier can't parse is refused, and so is any
  function that isn't a known MySQL built-in.
- It doesn't trust one lexer. Comments are re-scanned with MySQL's own rules, because a
  parser and the server can disagree about where a comment ends.
- A refusal is a result, not an error: a code, a reason and a suggestion, so the agent
  can rephrase instead of giving up.
- Everything is bounded without touching your SQL: rows, cell size and execution time
  are capped, and no `LIMIT` is ever added to a query.
- The classifier isn't the only layer. Sessions are read-only transactions, re-initialised
  on every checkout, and the connection can't send more than one statement.
- It knows MySQL's release lines (LTS, Innovation, calendar versions) and reads both
  `EXPLAIN FORMAT=JSON` shapes.
- It says where it stops: the database allowlist is a name filter, and the real boundary
  is the account's grants.

## What it does

Six tools, all read-only:

| Tool | Returns |
|---|---|
| `server_info` | MySQL version and release line (LTS / Innovation), uptime, key globals (buffer pool, binlog format, GTID mode, read_only, sql_mode, charset), and the sakila-mcp version. |
| `list_databases` | Databases you're allowed to query, system schemas flagged. |
| `list_tables` | Tables and views in one database: engine, approximate rows, data and index size. |
| `describe_table` | Columns, indexes (key parts in order, uniqueness, cardinality), foreign keys, engine, row format, collation. |
| `run_query` | Columns and rows for one statement, capped at `max_rows`. Parameters via `?` placeholders. |
| `explain_query` | `EXPLAIN FORMAT=JSON` plus a summary (full table scans, filesort, temporary tables, largest row estimate), or `FORMAT=TREE` text. Doesn't run the query. |

Plus one resource, `mysql://capabilities`: version, release line, EXPLAIN JSON format
version, and whether `sys` is visible.

Results are structured, not prose. DECIMAL comes back as a string so it stays exact,
dates and times as ISO 8601, JSON parsed, binary as `<binary N bytes>`. Long cells are
cut at `max_cell_bytes` with a marker saying how big they were.

When the server says no, the agent gets a result with `refused: true`, a reason and a
suggestion, not an error, so it can rephrase. That includes SQL the classifier can't
parse. Mistakes MySQL reports (unknown table or column, wrong parameter count) and
infrastructure failures are tool errors.

## Safety model

Every session, on every pool checkout:
- `SET SESSION TRANSACTION READ ONLY`
- `max_execution_time` = `statement_timeout_ms`, `lock_wait_timeout` = 5 s
- connection attributes `program_name=sakila-mcp` and `sakila_mcp_version`, and every
  statement is tagged `/* sakila-mcp tool=... */`, so you can find it in the processlist
  and performance_schema

Every statement from `run_query` and `explain_query` goes through a SQL classifier
(sqlglot plus token-level checks) before it reaches MySQL. It fails closed: if it can't
parse it, it refuses it.
- Allowed: one SELECT, WITH, TABLE, VALUES, DESCRIBE, EXPLAIN, or a SHOW from a fixed list.
- Refused: everything else, more than one statement, `/*! ... */` version comments,
  `INTO` (OUTFILE, DUMPFILE, variables), locking reads (`FOR UPDATE`, `FOR SHARE`),
  `:=` assignment, the `SET_VAR` hint, `EXPLAIN ANALYZE` (it executes), functions that
  block, lock, wait or read files (`SLEEP`, `BENCHMARK`, `GET_LOCK`, `LOAD_FILE`, ...),
  and any function that isn't a known built-in (stored functions can write).
- Parameters are bound by the server (`PREPARE` / `EXECUTE ... USING`), never spliced
  into the SQL.
- Results stop at `max_rows`; the query is cut off, not rewritten with a `LIMIT`.

The database allowlist is a filter on names, not an isolation boundary.
`information_schema`, `performance_schema` and `sys` are always readable, and
`SHOW PROCESSLIST`, `SHOW ENGINE INNODB STATUS` and performance_schema statement history
show other sessions' SQL, literal values included. The `mysql` schema is denied unless
you list it.

The real boundary is the account's grants. Give sakila-mcp its own account with only
what it needs:

```sql
CREATE USER 'sakila_mcp'@'%' IDENTIFIED BY '...';
GRANT SELECT, SHOW VIEW ON app_db.* TO 'sakila_mcp'@'%';
-- Optional: SHOW PROCESSLIST / SHOW REPLICA STATUS. PROCESS exposes other sessions' SQL.
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'sakila_mcp'@'%';
```

The password never appears in tool results, logs or error text. There's a test for that.

## Requirements

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/)
- MySQL 8.4 or later. CI runs the integration tests against 8.4, 9.7 and 26.10.
  8.0 is tested best-effort: failures there don't block a release, and functions that
  exist only in 8.0 are refused as unknown.

## Install

Nothing to install: `uvx sakila-mcp` downloads and runs the latest release. Pin a version
with `uvx sakila-mcp@0.1.0`, or install it with `uv tool install sakila-mcp`.

```sh
uvx sakila-mcp --help
```

To run from a checkout instead, see [Development](#development).

## Configure

Environment variables (`SAKILA_MCP_*`), a TOML file passed with `--config`, or both.
Environment variables win over the file. Keep the password in the file, `chmod 600` it,
and keep it out of version control.

```toml
# ~/.config/sakila-mcp.toml
host = "db.example.internal"
port = 3306
user = "sakila_mcp"
password = "..."
database_allowlist = ["app_db"]   # empty = every non-system database
```

| Key | Env var | Default | |
|---|---|---|---|
| `host` | `SAKILA_MCP_HOST` | `127.0.0.1` | |
| `port` | `SAKILA_MCP_PORT` | `3306` | |
| `user` | `SAKILA_MCP_USER` | (required) | |
| `password` | `SAKILA_MCP_PASSWORD` | empty | |
| `database_allowlist` | `SAKILA_MCP_DATABASE_ALLOWLIST` | empty (all) | comma-separated in the env var |
| `max_rows` | `SAKILA_MCP_MAX_ROWS` | `200` | rows returned per query |
| `max_cell_bytes` | `SAKILA_MCP_MAX_CELL_BYTES` | `4096` | longer cells are truncated |
| `statement_timeout_ms` | `SAKILA_MCP_STATEMENT_TIMEOUT_MS` | `10000` | session `max_execution_time` |
| `pool_size` | `SAKILA_MCP_POOL_SIZE` | `4` | |
| `read_only` | `SAKILA_MCP_READ_ONLY` | `true` | `false` refuses to start; there is no write mode |

## Use it

Claude Code:

```sh
claude mcp add sakila -- uvx sakila-mcp --config ~/.config/sakila-mcp.toml
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sakila": {
      "command": "uvx",
      "args": ["sakila-mcp", "--config", "/home/you/.config/sakila-mcp.toml"]
    }
  }
}
```

Ollama, with local models. Ollama runs the model but isn't an MCP client itself, so use
[ollmcp](https://github.com/jonigl/mcp-client-for-ollama) (`mcp-client-for-ollama`), a
terminal client that connects Ollama models to MCP servers:

```sh
uv tool install ollmcp
```

It reads the same `mcpServers` format as Claude Desktop. Save this as, say,
`~/.config/sakila-mcp.ollmcp.json`:

```json
{
  "mcpServers": {
    "sakila": {
      "command": "uvx",
      "args": ["sakila-mcp", "--config", "/home/you/.config/sakila-mcp.toml"]
    }
  }
}
```

and start it with a model that supports tool calling:

```sh
ollmcp -j ~/.config/sakila-mcp.ollmcp.json -m qwen3.5
```

`/tools` inside ollmcp turns individual tools on and off. Model size matters more here
than with Claude: small models (1–3B) often call the wrong tool, invent arguments, or stop
after one call. That's a model problem, not a safety one. The server enforces the same
rules whatever is calling it.

Then ask it things like "what's in app_db and how do orders relate to customers?" or
"why is this query slow?".

## Not in v0.1

Write mode, more than one server per process, HTTP transport, replication / InnoDB /
lock diagnostics, Enterprise Edition features, `EXPLAIN ANALYZE`, stored functions.

## Development

```sh
git clone https://github.com/pantoniades/sakila-mcp.git
cd sakila-mcp
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Integration tests need a MySQL server prepared once with `tests/integration/setup.sh`.
Give it the address the server sees the tests connect from and a letters-and-digits
password (extra arguments go to the `mysql` client), then point the tests at the server:

```sh
SAKILA_MCP_TEST_CLIENT_HOST=127.0.0.1 SAKILA_MCP_TEST_PASSWORD=<password> \
  tests/integration/setup.sh -u root -p
echo 'SAKILA_MCP_TEST_DSN=mysql://sakila_mcp_test:<password>@<host>:3306/sakila_mcp_test' > .env.test
```

`.env.test` is gitignored. Without it the integration tests skip.
