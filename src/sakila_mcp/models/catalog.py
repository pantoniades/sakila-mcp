"""Output models for schema-inspection tools."""

from datetime import datetime

from pydantic import BaseModel


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
    create_time: datetime | None
    comment: str
    columns: list[Column]
    indexes: list[Index]
    foreign_keys: list[ForeignKey]
