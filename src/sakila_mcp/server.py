"""MCP server construction. Tools and resources are registered statically here."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated

import mysql.connector
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from sakila_mcp import __version__, catalog
from sakila_mcp.catalog import text as _text
from sakila_mcp.config import Config
from sakila_mcp.models.catalog import DatabaseList, TableDescription, TableList
from sakila_mcp.models.common import Refusal
from sakila_mcp.models.server import ServerInfo
from sakila_mcp.pool import Database, DatabaseError
from sakila_mcp.safety.allowlist import check_access
from sakila_mcp.safety.classifier import TableRef
from sakila_mcp.safety.redact import redact

SERVER_NAME = "sakila-mcp"
CAPABILITIES_URI = "mysql://capabilities"

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

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


@contextmanager
def _infrastructure_errors() -> Iterator[None]:
    """Turn connection/server failures into tool errors with redacted text."""
    try:
        yield
    except DatabaseError as exc:
        raise ToolError(str(exc)) from None
    except mysql.connector.Error as exc:
        raise ToolError(redact(f"MySQL error: {exc}")) from None


DatabaseName = Annotated[
    str, Field(min_length=1, max_length=64, description="Database (schema) name, exact case.")
]
TableName = Annotated[
    str, Field(min_length=1, max_length=64, description="Table or view name, exact case.")
]


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


def build_server(config: Config, database: Database) -> MCPServer:
    """Build the MCP server for one MySQL target. ``database`` must be opened before use."""
    allowlist = config.database_allowlist
    database_pool = database  # tools take a ``database`` argument that shadows the name
    server = MCPServer(
        SERVER_NAME,
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
        with _infrastructure_errors(), database.connection() as cnx:
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
        with _infrastructure_errors(), database.connection() as cnx:
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
        with _infrastructure_errors(), database_pool.connection() as cnx:
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
        with _infrastructure_errors(), database_pool.connection() as cnx:
            cursor = cnx.cursor()
            description = catalog.describe_table(cursor, database, table)
            cursor.close()
        if description is None:
            raise _table_not_found(database, table)
        return description

    @server.resource(
        CAPABILITIES_URI,
        name="capabilities",
        description="Probed server capabilities: version, release line, EXPLAIN JSON "
        "format version, and whether the sys schema is visible.",
        mime_type="application/json",
    )
    def capabilities() -> str:
        return json.dumps(asdict(database.capabilities))

    return server
