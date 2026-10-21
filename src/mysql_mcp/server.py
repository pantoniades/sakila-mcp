"""MCP server construction. Tools and resources are registered statically here."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

import mysql.connector
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from mysql_mcp import __version__
from mysql_mcp.config import Config
from mysql_mcp.models.server import ServerInfo
from mysql_mcp.pool import Database, DatabaseError
from mysql_mcp.safety.redact import redact

SERVER_NAME = "mysql-mcp"
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


def _text(value: object) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes | bytearray) else str(value)


def _as_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.lstrip("-").isdigit() else None


def _as_bool(value: str | None) -> bool | None:
    return None if value is None else value.upper() in {"ON", "1"}


def build_server(config: Config, database: Database) -> MCPServer:
    """Build the MCP server for one MySQL target. ``database`` must be opened before use."""
    del config  # used by query tools in later steps
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
                "/* mysql-mcp tool=server_info */ SHOW GLOBAL VARIABLES "
                f"WHERE Variable_name IN ({placeholders})",
                _SERVER_INFO_VARIABLES,
            )
            values = {_text(name).lower(): _text(value) for name, value in cursor.fetchall()}
            cursor.execute("/* mysql-mcp tool=server_info */ SHOW GLOBAL STATUS LIKE 'Uptime'")
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
