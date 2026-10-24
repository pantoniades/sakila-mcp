"""MCP server construction. Tools and resources are registered statically here."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated, Literal

import mysql.connector
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from sakila_mcp import __version__, catalog, query
from sakila_mcp.catalog import text as _text
from sakila_mcp.config import Config
from sakila_mcp.models.catalog import DatabaseList, TableDescription, TableList
from sakila_mcp.models.common import Refusal
from sakila_mcp.models.query import ExplainResult, QueryResult
from sakila_mcp.models.server import ServerInfo
from sakila_mcp.pool import Database, DatabaseError
from sakila_mcp.query import ParamValue
from sakila_mcp.safety.allowlist import check_access
from sakila_mcp.safety.classifier import Kind, TableRef
from sakila_mcp.safety.policy import evaluate
from sakila_mcp.safety.redact import redact

SERVER_NAME = "sakila-mcp"
CAPABILITIES_URI = "mysql://capabilities"

SERVER_TITLE = "Sakila MCP"

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

_SERVER_INFO_VARIABLES = (
    "innodb_buffer_pool_size",
    "binlog_format",
    "gtid_mode",
    "read_only",
    "super_read_only",
    "sql_mode",
    "max_connections",
    "default_storage_engine",
    "character_set_server",
    "collation_server",
)


# ER_QUERY_TIMEOUT: max_execution_time (session setting or hint) interrupted a SELECT.
_ER_QUERY_TIMEOUT = 3024


@contextmanager
def _mysql_errors(statement_timeout_ms: int = 0) -> Iterator[None]:
    """Turn MySQL failures into tool errors with redacted text.

    Covers infrastructure failures (connection, auth) and errors in the caller's SQL
    (syntax, unknown column, wrong parameter count); both are tool errors, unlike
    policy refusals, which are results.
    """
    try:
        yield
    except DatabaseError as exc:
        raise ToolError(str(exc)) from None
    except mysql.connector.Error as exc:
        if exc.errno == _ER_QUERY_TIMEOUT:
            raise ToolError(
                f"The query exceeded the {statement_timeout_ms} ms statement timeout and was "
                "stopped. Make it more selective, or check its plan with explain_query."
            ) from None
        raise ToolError(redact(f"MySQL error: {exc}")) from None


DatabaseName = Annotated[
    str, Field(min_length=1, max_length=64, description="Database (schema) name, exact case.")
]
TableName = Annotated[
    str, Field(min_length=1, max_length=64, description="Table or view name, exact case.")
]
DefaultDatabase = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=64,
        description="Default database for unqualified table names. Omit if every name "
        "is qualified as db.table.",
    ),
]
Sql = Annotated[str, Field(min_length=1, max_length=100_000, description="One SQL statement.")]
Params = Annotated[
    list[ParamValue] | None,
    Field(
        max_length=100,
        description="Values for ? placeholders, in order. Bound by the server, never "
        "spliced into the SQL. SELECT, TABLE and VALUES only.",
    ),
]

# Statement kinds that can carry parameters or be EXPLAINed. MySQL cannot PREPARE most
# SHOW forms with placeholders (SHOW TABLES LIKE ? is a syntax error).
_QUERY_KINDS = frozenset({Kind.SELECT, Kind.TABLE, Kind.VALUES})


# Unknown objects are caller errors (a tool error the caller can correct), not refusals.
def _database_not_found(database: str) -> ToolError:
    return ToolError(
        f"Database {database!r} does not exist or is not visible to this account. "
        "Call list_databases to see which databases you can query."
    )


def _table_not_found(database: str, table: str) -> ToolError:
    return ToolError(
        f"Table {database}.{table} does not exist or is not visible to this account. "
        f"Call list_tables with database={database!r} to see its tables."
    )


def _as_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.lstrip("-").isdigit() else None


def _as_bool(value: str | None) -> bool | None:
    return None if value is None else value.upper() in {"ON", "1"}


# One closure per tool keeps registration static and in one place; hence the length.
def build_server(config: Config, database: Database) -> MCPServer:  # noqa: PLR0915
    """Build the MCP server for one MySQL target. ``database`` must be opened before use."""
    allowlist = config.database_allowlist
    database_pool = database  # tools take a ``database`` argument that shadows the name
    server = MCPServer(
        SERVER_NAME,
        title=SERVER_TITLE,
        version=__version__,
        instructions="Read-only access to one MySQL server: schema inspection and bounded "
        "queries. Read mysql://capabilities for the server version and feature support.",
    )

    @server.tool(
        annotations=READ_ONLY,
        description="Returns the MySQL version, release line, uptime and key global settings "
        "(buffer pool, binlog/GTID, read_only, sql_mode, charset). "
        "Use it first to learn what kind of server you are talking to.",
    )
    def server_info() -> ServerInfo:
        caps = database.capabilities
        with _mysql_errors(), database.connection() as cnx:
            cursor = cnx.cursor()
            placeholders = ", ".join(["%s"] * len(_SERVER_INFO_VARIABLES))
            cursor.execute(
                "/* sakila-mcp tool=server_info */ SHOW GLOBAL VARIABLES "
                f"WHERE Variable_name IN ({placeholders})",
                _SERVER_INFO_VARIABLES,
            )
            values = {_text(name).lower(): _text(value) for name, value in cursor.fetchall()}
            cursor.execute("/* sakila-mcp tool=server_info */ SHOW GLOBAL STATUS LIKE 'Uptime'")
            row = cursor.fetchone()
            cursor.close()
        return ServerInfo(
            sakila_mcp_version=__version__,
            version_raw=caps.version_raw,
            version=caps.version,
            release_line=caps.release_line,
            version_comment=caps.version_comment,
            uptime_seconds=int(_text(row[1])) if row else 0,
            innodb_buffer_pool_size=_as_int(values.get("innodb_buffer_pool_size")),
            binlog_format=values.get("binlog_format"),
            gtid_mode=values.get("gtid_mode"),
            read_only=_as_bool(values.get("read_only")),
            super_read_only=_as_bool(values.get("super_read_only")),
            sql_mode=values.get("sql_mode"),
            max_connections=_as_int(values.get("max_connections")),
            default_storage_engine=values.get("default_storage_engine"),
            character_set_server=values.get("character_set_server"),
            collation_server=values.get("collation_server"),
        )

    @server.tool(
        annotations=READ_ONLY,
        description="Returns the databases you may query, each flagged if it is a system "
        "schema. Call it before list_tables when you do not know the database name.",
    )
    def list_databases() -> DatabaseList:
        with _mysql_errors(), database.connection() as cnx:
            cursor = cnx.cursor()
            result = catalog.list_databases(cursor, allowlist)
            cursor.close()
        return result

    @server.tool(
        annotations=READ_ONLY,
        description="Returns the tables and views in one database with engine, approximate "
        "row count and data/index size (statistics may be cached, not exact). "
        "Use it to find candidate tables before describe_table.",
    )
    def list_tables(database: DatabaseName) -> TableList | Refusal:
        refusal = check_access([], [database], None, allowlist)
        if refusal is not None:
            return refusal
        with _mysql_errors(), database_pool.connection() as cnx:
            cursor = cnx.cursor()
            exists = catalog.database_exists(cursor, database)
            tables = catalog.list_tables(cursor, database) if exists else []
            cursor.close()
        if not exists:
            raise _database_not_found(database)
        return TableList(database=database, tables=tables)

    @server.tool(
        annotations=READ_ONLY,
        description="Returns one table's or view's columns (type, nullability, default, "
        "collation), indexes (key parts in order, uniqueness, cardinality), foreign keys, "
        "engine and approximate row count. Use it before writing a query against the table.",
    )
    def describe_table(database: DatabaseName, table: TableName) -> TableDescription | Refusal:
        refusal = check_access([TableRef(database, table)], [], None, allowlist)
        if refusal is not None:
            return refusal
        with _mysql_errors(), database_pool.connection() as cnx:
            cursor = cnx.cursor()
            description = catalog.describe_table(cursor, database, table)
            cursor.close()
        if description is None:
            raise _table_not_found(database, table)
        return description

    @server.tool(
        annotations=READ_ONLY,
        description="Runs one read-only statement (SELECT, WITH, TABLE, VALUES, SHOW, "
        "DESCRIBE or EXPLAIN) and returns columns and rows, at most "
        f"{config.max_rows} rows with truncated=true if there were more. Use ? "
        "placeholders with params for values. Refused statements return refused=true "
        "with a reason and a suggestion.",
    )
    def run_query(
        sql: Sql, database: DefaultDatabase = None, params: Params = None
    ) -> QueryResult | Refusal:
        decision = evaluate(sql, config, database)
        if isinstance(decision, Refusal):
            return decision
        if params and decision.classification.kind not in _QUERY_KINDS:
            raise ToolError(
                "params are supported for SELECT, TABLE and VALUES statements only; "
                "write the value into this statement as a literal."
            )
        with _mysql_errors(config.statement_timeout_ms), database_pool.connection() as cnx:
            if database is not None:
                cnx.cmd_init_db(database)  # undone by the session reset on release
            return query.run(cnx, sql, params or [], config.max_rows, config.max_cell_bytes)

    @server.tool(
        annotations=READ_ONLY,
        description="Returns the execution plan for one SELECT, TABLE or VALUES statement "
        "without running it: the EXPLAIN FORMAT=JSON document plus a summary (full table "
        "scans, filesort, temporary table, max estimated rows), or the TREE text. "
        "Use it before running a query that may be slow.",
    )
    def explain_query(
        sql: Sql, database: DefaultDatabase = None, format: Literal["json", "tree"] = "json"
    ) -> ExplainResult | Refusal:
        decision = evaluate(sql, config, database)
        if isinstance(decision, Refusal):
            return decision
        if decision.classification.kind not in _QUERY_KINDS:
            raise ToolError(
                "explain_query explains SELECT, TABLE and VALUES statements, not "
                f"{decision.classification.kind.upper()}. Pass the query itself without "
                "EXPLAIN; run SHOW and DESCRIBE statements with run_query."
            )
        with _mysql_errors(config.statement_timeout_ms), database_pool.connection() as cnx:
            if database is not None:
                cnx.cmd_init_db(database)
            return query.run_explain(cnx, sql, format)

    @server.resource(
        CAPABILITIES_URI,
        name="capabilities",
        description="Probed server capabilities: version, release line, EXPLAIN JSON "
        "format version, and whether the sys schema is visible.",
        mime_type="application/json",
    )
    def capabilities() -> str:
        return json.dumps({"sakila_mcp_version": __version__, **asdict(database.capabilities)})

    return server
