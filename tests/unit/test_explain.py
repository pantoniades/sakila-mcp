"""EXPLAIN summaries against real documents captured from MySQL 8.4.11.

The fixtures hold the same nine queries in both JSON formats (explain_json_format_version
1 and 2), so the two summarizers can be checked against each other.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from sakila_mcp.explain import UnrecognizedPlan, json_version, summarize
from sakila_mcp.models.query import FullTableScan

FIXTURES = Path(__file__).parent / "fixtures" / "explain"
QUERIES = sorted(p.stem.removeprefix("v1_") for p in FIXTURES.glob("v1_*.json"))


def _plan(version: int, name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"v{version}_{name}.json").read_text())
    plan: dict[str, Any] = document["explain"]
    return plan


def test_fixtures_present() -> None:
    assert len(QUERIES) == 9


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("name", QUERIES)
def test_version_detected(version: int, name: str) -> None:
    assert json_version(_plan(version, name)) == version


@pytest.mark.parametrize("name", QUERIES)
def test_formats_agree(name: str) -> None:
    # Row estimates differ between formats (v2 also estimates union/aggregate nodes),
    # but what is scanned, sorted and materialized must not.
    v1, v2 = summarize(_plan(1, name)), summarize(_plan(2, name))
    assert v1.full_table_scans == v2.full_table_scans
    assert v1.filesort == v2.filesort
    assert v1.temporary_table == v2.temporary_table


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    ("name", "scans", "filesort", "temporary"),
    [
        ("full_scan", [("orders", 1000)], False, False),
        ("pk_lookup", [], False, False),
        ("filesort", [("orders", 1000)], True, False),
        ("temp_group", [("orders", 1000)], False, True),
        ("join", [("o", 1000)], False, False),  # reported by alias
        ("union", [], False, True),  # <union1,2> is internal, not a table scan
        ("subquery", [("customer", 500), ("orders", 1000)], False, True),
        ("derived", [], True, True),
        ("no_table", [], False, False),
    ],
)
def test_summary(
    version: int, name: str, scans: list[tuple[str, int]], filesort: bool, temporary: bool
) -> None:
    summary = summarize(_plan(version, name))
    assert sorted(summary.full_table_scans, key=lambda s: s.table) == [
        FullTableScan(table=t, estimated_rows=r) for t, r in scans
    ]
    assert summary.filesort is filesort
    assert summary.temporary_table is temporary


def test_max_estimated_rows() -> None:
    assert summarize(_plan(1, "join")).max_estimated_rows == 1000
    assert summarize(_plan(2, "union")).max_estimated_rows == 1500
    assert summarize(_plan(1, "no_table")).max_estimated_rows is None


def test_unrecognized_shape() -> None:
    with pytest.raises(UnrecognizedPlan, match="unrecognized EXPLAIN JSON shape"):
        summarize({"something_else": {}})
