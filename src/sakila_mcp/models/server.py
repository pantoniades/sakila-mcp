"""Output models for server-level tools."""

from pydantic import BaseModel, Field


class ServerInfo(BaseModel):
    # This MCP server's own version; every other field describes the MySQL server.
    sakila_mcp_version: str
    version_raw: str
    version: tuple[int, int, int]
    release_line: str
    version_comment: str
    uptime_seconds: int
    # Temporal values without an offset (describe_table's create_time, DATETIME and
    # TIMESTAMP cells from run_query) are in this session time zone.
    time_zone: str = Field(description="Session time zone, e.g. SYSTEM or +00:00.")
    system_time_zone: str = Field(description="The server host's zone, used when SYSTEM.")
    utc_offset: str | None = Field(
        description="Current offset of session time from UTC, e.g. +01:00 or -05:30."
    )
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
