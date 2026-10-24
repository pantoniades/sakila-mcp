"""Output models for run_query and explain_query."""

from typing import Literal

from pydantic import BaseModel, JsonValue


class QueryColumn(BaseModel):
    name: str
    # MySQL wire type, e.g. "bigint", "decimal", "varchar", "datetime", "json", "blob".
    type: str


class QueryResult(BaseModel):
    columns: list[QueryColumn]
    # One list per row, in column order. DECIMAL is a string (exact), temporal types are
    # ISO 8601 strings, JSON is parsed, binary is "<binary N bytes>".
    rows: list[list[JsonValue]]
    row_count: int
    # True when the statement produced more than max_rows rows; only max_rows are returned.
    truncated: bool
    # Cells shortened to max_cell_bytes (each ends with a "…[truncated: N bytes]" marker).
    truncated_cells: int
    elapsed_ms: int


class FullTableScan(BaseModel):
    # The alias when the query gives one, else the table name.
    table: str
    estimated_rows: int | None


class ExplainSummary(BaseModel):
    full_table_scans: list[FullTableScan]
    filesort: bool
    temporary_table: bool
    # Largest row estimate on any plan node; the two JSON formats estimate differently.
    max_estimated_rows: int | None


class ExplainResult(BaseModel):
    format: Literal["json", "tree"]
    # 1 or 2, detected from the document (explain_json_format_version); None for TREE.
    explain_json_version: int | None
    plan: dict[str, JsonValue] | None
    tree: str | None
    summary: ExplainSummary | None
    # Set instead of summary when the JSON document has a shape we do not recognize.
    summary_error: str | None
