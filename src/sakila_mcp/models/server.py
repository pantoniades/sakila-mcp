"""Output models for server-level tools."""

from pydantic import BaseModel


class ServerInfo(BaseModel):
    # This MCP server's own version; every other field describes the MySQL server.
    sakila_mcp_version: str
    version_raw: str
    version: tuple[int, int, int]
    release_line: str
    version_comment: str
    uptime_seconds: int
    # Global variables; None when the variable does not exist on this server.
    innodb_buffer_pool_size: int | None
    binlog_format: str | None
    gtid_mode: str | None
    read_only: bool | None
    super_read_only: bool | None
    sql_mode: str | None
    max_connections: int | None
    default_storage_engine: str | None
    character_set_server: str | None
    collation_server: str | None
