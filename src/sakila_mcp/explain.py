"""Summarize EXPLAIN FORMAT=JSON documents in both shapes MySQL produces.

explain_json_format_version=1 (the classic shape) nests "query_block" /
"nested_loop" / "table" objects. Version 2 (8.3+) is the iterator tree: every node
has "operation" and "access_type", children under "inputs". The shape is detected
from the document, not from the server variable, because a session can override it.

Both walkers visit every dict in the document, so they do not depend on where MySQL
nests subqueries, unions or derived tables. Table names starting with "<" are
internal (<temporary>, <union1,2>, <derived2>) and are never reported as scans.
The rules were derived from real documents in tests/unit/fixtures/explain.
"""

from collections.abc import Iterator

from pydantic import JsonValue

from sakila_mcp.models.query import ExplainSummary, FullTableScan


class UnrecognizedPlan(ValueError):
    """The document is neither the v1 nor the v2 EXPLAIN JSON shape."""


def _dicts(node: JsonValue) -> Iterator[dict[str, JsonValue]]:
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _dicts(child)
    elif isinstance(node, list):
        for child in node:
            yield from _dicts(child)


def _rows(value: JsonValue) -> int | None:
    # v1 reports integers; v2 reports float estimates (e.g. 333.33343).
    return round(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def json_version(doc: dict[str, JsonValue]) -> int:
    if "query_block" in doc:
        return 1
    if "operation" in doc and "access_type" in doc:
        return 2
    raise UnrecognizedPlan(f"unrecognized EXPLAIN JSON shape (top-level keys: {sorted(doc)})")


def summarize(doc: dict[str, JsonValue]) -> ExplainSummary:
    """Raise UnrecognizedPlan if the shape is unknown."""
    return _summarize_v1(doc) if json_version(doc) == 1 else _summarize_v2(doc)


def _max(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return value if current is None else max(current, value)


def _summarize_v1(doc: dict[str, JsonValue]) -> ExplainSummary:
    scans: list[FullTableScan] = []
    filesort = temporary = False
    max_rows: int | None = None
    for node in _dicts(doc):
        # These flags sit on ordering_operation / grouping_operation /
        # duplicates_removal / materialized_from_subquery, not on the table.
        filesort |= node.get("using_filesort") is True
        temporary |= node.get("using_temporary_table") is True
        name = node.get("table_name")
        if not isinstance(name, str):
            continue
        examined = _rows(node.get("rows_examined_per_scan"))
        max_rows = _max(max_rows, examined)
        max_rows = _max(max_rows, _rows(node.get("rows_produced_per_join")))
        # v1 table_name is already the alias when one is used.
        if node.get("access_type") == "ALL" and not name.startswith("<"):
            scans.append(FullTableScan(table=name, estimated_rows=examined))
    return ExplainSummary(
        full_table_scans=scans,
        filesort=filesort,
        temporary_table=temporary,
        max_estimated_rows=max_rows,
    )


# v2 access_type values that mean an internal temporary table is built.
_V2_TEMPORARY = frozenset({"temp_table_aggregate", "materialize"})


def _summarize_v2(doc: dict[str, JsonValue]) -> ExplainSummary:
    scans: list[FullTableScan] = []
    filesort = temporary = False
    max_rows: int | None = None
    for node in _dicts(doc):
        access = node.get("access_type")
        name = node.get("table_name")
        estimated = _rows(node.get("estimated_rows"))
        max_rows = _max(max_rows, estimated)
        # v2 has no using_filesort flag: a "sort" node is the filesort.
        filesort |= access == "sort"
        temporary |= access in _V2_TEMPORARY or name == "<temporary>"
        # "table" is a full table scan; index scans and lookups are "index".
        if access == "table" and isinstance(name, str) and not name.startswith("<"):
            alias = node.get("alias")
            scans.append(
                FullTableScan(
                    table=alias if isinstance(alias, str) else name, estimated_rows=estimated
                )
            )
    return ExplainSummary(
        full_table_scans=scans,
        filesort=filesort,
        temporary_table=temporary,
        max_estimated_rows=max_rows,
    )
