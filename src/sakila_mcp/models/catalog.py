"""Output models for schema-inspection tools."""

from pydantic import BaseModel, Field


class DatabaseEntry(BaseModel):
    name: str
    # information_schema, performance_schema or sys: server metadata, not application data.
    system: bool


class DatabaseList(BaseModel):
    databases: list[DatabaseEntry]


class TableSummary(BaseModel):
    name: str
    table_type: str  # BASE TABLE | VIEW | SYSTEM VIEW
    engine: str | None
    # Statistics from information_schema; approximate and possibly cached.
    approx_rows: int | None
    data_length: int | None
    index_length: int | None
    comment: str


class TableList(BaseModel):
    database: str
    tables: list[TableSummary]


class Column(BaseModel):
    name: str
    type: str
    nullable: bool
    default: str | None
    extra: str
    collation: str | None
    comment: str
    generation_expression: str | None


class Index(BaseModel):
    name: str
    unique: bool
    # Key parts in order: "col", "col(10)" for a prefix, "col DESC", "(expr)" for functional.
    columns: list[str]
    index_type: str
    # Estimated distinct values for the full key (the last key part's cardinality).
    cardinality: int | None
    visible: bool


class ForeignKey(BaseModel):
    name: str
    columns: list[str]
    referenced_database: str
    referenced_table: str
    referenced_columns: list[str]
    on_update: str
    on_delete: str


class TableDescription(BaseModel):
    database: str
    name: str
    table_type: str
    engine: str | None
    row_format: str | None
    collation: str | None
    approx_rows: int | None
    # A string, not datetime: MySQL reports this without a time zone, and a schema of
    # format "date-time" (RFC 3339, offset required) makes strict clients such as
    # Claude Code reject the whole result.
    create_time: str | None = Field(
        description="ISO 8601, in the server session's time zone (no offset)."
    )
    comment: str
    columns: list[Column]
    indexes: list[Index]
    foreign_keys: list[ForeignKey]
