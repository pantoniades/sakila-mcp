# Kickoff prompt — mysql_mcp v0.1 (paste into the first Claude Code session)

Read CLAUDE.md first and follow it. This session builds **v0.1, an MVP**. Resist scope
creep: everything under "Not in v0.1" is deferred on purpose, and a smaller correct
server beats a larger one.

## What v0.1 is

A read-only MySQL MCP server over stdio, pointed at ONE MySQL 8.0+ server, that an LLM
agent can use to inspect schema and run bounded SELECT/SHOW/EXPLAIN queries safely.
The differentiator even at v0.1 is the safety layer and the structured results, not
the tool count.

## Tools (exactly these six)

1. `list_databases()` → databases visible after allowlist filtering.
2. `list_tables(database)` → name, engine, approximate rows, data/index length,
   from `information_schema.TABLES`.
3. `describe_table(database, table)` → columns (name, type, nullable, default, extra,
   collation), indexes (name, unique, columns in order, cardinality), foreign keys,
   engine, row_format, collation, approximate row count, create_time. Treat a
   `VECTOR` column type as an ordinary type string; do not special-case it yet.
4. `run_query(sql, database=None, params=None)` → columns, rows, row_count,
   truncated, elapsed_ms. Parameterized via connector placeholders. Goes through the
   classifier and allowlist. Cells over `max_cell_bytes` are truncated with a marker;
   BLOB/binary cells are summarized as `<binary N bytes>`.
5. `explain_query(sql, format="json")` → the raw `EXPLAIN FORMAT=JSON` document plus a
   small `summary`: full table scans (table, est. rows), filesort present, temporary
   table present, max estimated rows. Accept both the v1 and v2 JSON shapes
   (`explain_json_format_version`); if the shape is unrecognized, return the raw
   document with `summary: null` and `summary_error`. `format="tree"` returns the
   TREE text with no summary. Inner statement must pass the classifier. Do NOT
   implement `EXPLAIN ANALYZE` in v0.1.
6. `server_info()` → version (raw + parsed tuple + line: "8.0 LTS", "8.4 LTS",
   "9.7 LTS", "Innovation"), version_comment, uptime, and these globals:
   innodb_buffer_pool_size, binlog_format, gtid_mode, read_only, super_read_only,
   sql_mode, max_connections, default_storage_engine, character_set_server,
   collation_server.

One resource: `mysql://capabilities` → the `Capabilities` object (see below).

No prompts in v0.1.

## Modules

```
src/mysql_mcp/
  __main__.py        # `python -m mysql_mcp` runs stdio; `--config path.toml` optional
  config.py          # pydantic-settings: env vars MYSQL_MCP_* and optional TOML
  server.py          # MCPServer; registers the six tools + one resource
  versions.py        # parse "8.0.36" / "9.7.2" / "26.10.0" → tuple; release line
  capabilities.py    # Capabilities dataclass + probe(conn)
  pool.py            # mysql.connector pool; session init on EVERY checkout
  safety/
    classifier.py    # sqlglot → Classification (pure; no config access)
    policy.py        # Classification + Config → Allowed | Refusal
    allowlist.py     # resolve table refs against allowlist
    limits.py        # row cap, cell truncation
    redact.py        # scrub secrets from strings/exceptions
  models/            # pydantic I/O models
tests/
  unit/              # classifier corpus, policy, versions, redact, explain summary
  integration/       # docker compose, single MySQL 8.4 service, marked `integration`
docker-compose.yml   # mysql:8.4 with a seed script creating a small schema
```

Config keys: host, port, user, password, database_allowlist (list; empty = all
non-system), max_rows (200), max_cell_bytes (4096), statement_timeout_ms (10000),
pool_size (4). `read_only` exists in config but only accepts `true` in v0.1; `false`
raises at startup with a message saying write mode is not implemented.

## Capabilities (minimal in v0.1)

```python
@dataclass(frozen=True)
class Capabilities:
    version: tuple[int, int, int]
    version_raw: str
    version_comment: str
    release_line: str  # "8.0 LTS" | "8.4 LTS" | "9.7 LTS" | "Innovation" | "unknown"
    explain_json_version: int  # 1 or 2 (from @@explain_json_format_version if present)
    has_sys_schema: bool
```
Probe once at startup, cache, expose as the resource. Keep it this small; it exists
so later versions have somewhere to put more.

## Classifier spec (write the tests FIRST, then the implementation)

`classify(sql: str) -> Classification | Refusal`, pure, no DB access, no config.

Accept (single statement only): `SELECT`, `WITH … SELECT`, `TABLE t`, `VALUES`,
`SHOW` (sub-allowlist: DATABASES, TABLES, FULL TABLES, COLUMNS, FULL COLUMNS, INDEX,
INDEXES, KEYS, CREATE TABLE, CREATE VIEW, VARIABLES, GLOBAL VARIABLES, SESSION
VARIABLES, STATUS, GLOBAL STATUS, ENGINE INNODB STATUS, PROCESSLIST, FULL PROCESSLIST,
REPLICA STATUS, BINARY LOG STATUS, MASTER STATUS, WARNINGS, ERRORS, TABLE STATUS,
PLUGINS, ENGINES, CHARSET, COLLATION), `DESCRIBE`/`DESC`, `EXPLAIN` (inner statement
classified recursively; `EXPLAIN ANALYZE` refused in v0.1).

Refuse: everything else (DML, DDL, CALL, DO, SET, HANDLER, LOAD, LOCK, SHOW GRANTS,
SHOW CREATE USER, SHOW CREATE PROCEDURE/FUNCTION, anything that doesn't parse).
Within accepted SELECTs refuse: `INTO` of any kind (OUTFILE, DUMPFILE, @var),
`FOR UPDATE`, `FOR SHARE`, `LOCK IN SHARE MODE`, `:=` assignment, and calls to:
SLEEP, BENCHMARK, GET_LOCK, RELEASE_LOCK, RELEASE_ALL_LOCKS, LOAD_FILE,
MASTER_POS_WAIT, SOURCE_POS_WAIT, WAIT_FOR_EXECUTED_GTID_SET,
WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS, UUID_TO_BIN is fine, SYS_EVAL, SYS_EXEC.

Comment handling: strip `--`, `#`, `/* */` before classification; any `/*!` version
comment anywhere in the input is an immediate refusal; `/*+ … */` optimizer hints are
allowed. Any statement separator outside a string literal (a second statement, or a
trailing `;` followed by non-whitespace) is a refusal.

Function calls that are not MySQL built-ins (possible stored functions) are reported
in `Classification.unknown_functions`; policy refuses them in v0.1 (no
`allow_stored_functions` option yet). Use a curated built-in list; it does not need
to be complete, just conservative.

`Classification` fields: `kind` (select/show/explain/describe/table/values),
`tables: list[TableRef(db|None, name)]` excluding CTE names and derived-table aliases,
`functions`, `unknown_functions`, `flags` (e.g. `has_into`, `has_locking`).

Test corpus must include, at minimum, with expected outcome:
- `SELECT 1`; CTE chains; `SELECT … UNION SELECT …`; window functions; JSON_TABLE;
  `TABLE t`; `VALUES ROW(1)`.
- `SELECT * FROM t INTO OUTFILE '/tmp/x'`; `SELECT … INTO @a`; `SELECT … FOR UPDATE`;
  `SELECT … LOCK IN SHARE MODE`; `SELECT @a := 1`.
- `SELECT 1; DROP TABLE t`; `SELECT 1;`; `SELECT ';' ; DROP TABLE t`; `SELECT 1 -- ;
  DROP`; `SELECT /*!50000 1 */`; `SELECT /*+ MAX_EXECUTION_TIME(1) */ 1`;
  `/* comment */ DELETE FROM t`.
- `WITH x AS (SELECT 1) DELETE FROM t`; `WITH x AS (DELETE FROM t RETURNING ...)`
  (invalid MySQL, must still refuse).
- `SELECT SLEEP(10)`; `SELECT BENCHMARK(1e9, MD5('x'))`; `SELECT GET_LOCK('a',10)`;
  `SELECT my_func(1)` → unknown function.
- `EXPLAIN SELECT 1`; `EXPLAIN FORMAT=JSON SELECT …`; `EXPLAIN DELETE FROM t`;
  `EXPLAIN ANALYZE SELECT 1`.
- `SHOW GRANTS`; `SHOW CREATE USER root`; `SHOW REPLICA STATUS`; `SHOW ENGINE INNODB
  STATUS`.
- `CALL p()`; `DO SLEEP(1)`; `SET @a = 1`; `HANDLER t OPEN`; `LOCK TABLES t READ`.
- Case/whitespace variants: `sElEcT`, leading newlines, tabs, trailing `\n\n`.
- Something sqlglot cannot parse (e.g. deliberately malformed) → refusal with
  `reason` mentioning parse failure, not a crash.

## Session init (on every pool checkout, not just connection creation)

```sql
SET SESSION TRANSACTION READ ONLY;
SET SESSION max_execution_time = <statement_timeout_ms>;
SET SESSION lock_wait_timeout = 5;
```
Connection attributes: `program_name=mysql-mcp`, `mysql_mcp_version=<pkg version>`.
Each tool prefixes its statement with `/* mysql-mcp tool=<name> */`.
Connect with `use_pure=True` acceptable; `caching_sha2_password` must work against a
TLS-less local docker instance (connector handles RSA key exchange — verify with the
integration test). If auth fails, the error returned to the agent must say which
auth plugin the server requested; it must never include the password.

## Redaction

`redact(text: str) -> str` removes the configured password, any `password=…`
pattern, and any `mysql://user:pass@` URL. Applied to every exception message and
every log record via a logging filter. Test: construct an error containing the
password three ways; assert it's gone.

## Order of work (one commit each; stop after step 2 and ask me to review)

1. Skeleton: pyproject, uv lock, ruff/mypy config, `versions.py` with tests, `config.py`
   with tests, `redact.py` with tests, empty `server.py` that starts and lists zero
   tools. Verify with `uv run python -m mysql_mcp --help`.
2. Classifier + policy + allowlist with the full unit corpus above. **Stop. Ask for
   review.**
3. Pool, session init, capabilities probe, `server_info`, `mysql://capabilities`.
   docker-compose + first integration test (version parse round-trip, read-only
   session enforced: `INSERT` via raw cursor must fail with 1792).
4. `list_databases`, `list_tables`, `describe_table` + integration tests.
5. `run_query` + `explain_query` + integration tests, including: truncation at
   max_rows, cell truncation, binary summarization, parameter binding, timeout
   (SELECT SLEEP is refused, so use a cross join that exceeds the timeout), and the
   allowlist blocking a query against a non-allowlisted schema.
6. README: install, env/TOML config, a Claude Desktop / Claude Code `mcp.json`
   example, the safety model stated honestly (what the classifier catches, what it
   relies on DB grants for, including a recommended `CREATE USER … GRANT SELECT,
   PROCESS, REPLICATION CLIENT, SHOW VIEW`), and a short "scope of v0.1" section.
7. Tag v0.1.0.

## Not in v0.1 (do not build, do not scaffold, do not leave TODO stubs for)

Write mode, multiple connection profiles, streamable-HTTP transport, replication /
InnoDB / session-diagnostic tools, Enterprise Edition tools, prompts, the
`SHOW ENGINE INNODB STATUS` parser, `EXPLAIN ANALYZE`, stored-function allowance,
multi-version docker matrix, OpenTelemetry configuration beyond whatever the SDK
does by default (just make sure it never touches stdout).

## Definition of done for each step

`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
all green (integration tests may be skipped when `MYSQL_MCP_TEST_DSN` is unset).
Report what you ran and the actual output summary, not a paraphrase.
