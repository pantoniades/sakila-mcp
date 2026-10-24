"""Execute classified statements and shape their results. Policy checks happen first.

The SQL that reaches MySQL is the text the classifier accepted, with two additions:
a leading ``/* sakila-mcp tool=... */`` tag, and EXPLAIN FORMAT=... for explain_query.
Text is only ever *prefixed*: a trailing ``--`` comment in the user's SQL would
swallow anything appended after it.
"""

import json
import time
from typing import Any

from mysql.connector.connection import MySQLConnection
from pydantic import JsonValue

from sakila_mcp import explain
from sakila_mcp.models.query import ExplainResult, QueryColumn, QueryResult
from sakila_mcp.results import convert_cell, type_name

ParamValue = str | int | float | bool | None

_STATEMENT = "sakila_mcp_stmt"


def _tag(tool: str) -> str:
    return f"/* sakila-mcp tool={tool} */ "


def statement_text(sql: str) -> str:
    """The SQL without its optional trailing ';' (the classifier allows only whitespace
    after it), so it can be embedded in PREPARE ... FROM and EXPLAIN."""
    body = sql.rstrip()
    return body[:-1] if body.endswith(";") else body


def _execute(cnx: MySQLConnection, cursor: Any, sql: str, params: list[ParamValue]) -> None:
    text = _tag("run_query") + statement_text(sql)
    if not params:
        # No params argument at all: the connector does no %-substitution, so the
        # server receives exactly this text (e.g. DATE_FORMAT(d, '%s') is untouched).
        cursor.execute(text)
        return
    # Parameters are bound by the server via SQL-level PREPARE, not the connector's
    # prepared cursor: that cursor uses the binary protocol, which crashes on YEAR
    # columns and returns JSON as bytes. Here the user's SQL travels as one escaped
    # string value, so the server prepares exactly the classified text, the values
    # are bound by EXECUTE ... USING (never spliced into SQL), and the result comes
    # back in the text protocol. Escaping is correct because session init removes
    # NO_BACKSLASH_ESCAPES. The statement and @variables vanish when the pool resets
    # the session, so nothing is deallocated here.
    names = [f"@sakila_mcp_p{i}" for i in range(len(params))]
    setup = cnx.cursor()
    try:
        setup.execute(f"{_tag('run_query')}PREPARE {_STATEMENT} FROM %s", (text,))
        assignments = ", ".join(f"{name} = %s" for name in names)
        setup.execute(f"{_tag('run_query')}SET {assignments}", tuple(params))
    finally:
        setup.close()
    cursor.execute(f"{_tag('run_query')}EXECUTE {_STATEMENT} USING {', '.join(names)}")


def run(
    cnx: MySQLConnection,
    sql: str,
    params: list[ParamValue],
    max_rows: int,
    max_cell_bytes: int,
) -> QueryResult:
    # Unbuffered cursor: rows stream from the server, so stopping after max_rows + 1
    # bounds memory. The pool discards the connection if rows are left unread.
    cursor = cnx.cursor()
    started = time.monotonic()
    _execute(cnx, cursor, sql, params)
    description = cursor.description or []  # None for a statement with no result set
    fetched = cursor.fetchmany(max_rows + 1) if description else []
    elapsed_ms = round((time.monotonic() - started) * 1000)
    truncated = len(fetched) > max_rows
    if not truncated:
        cursor.close()  # fully read; with rows pending, close() would raise instead

    field_types = [column[1] for column in description]
    rows: list[list[JsonValue]] = []
    truncated_cells = 0
    for raw in fetched[:max_rows]:
        row: list[JsonValue] = []
        for value, field_type in zip(raw, field_types, strict=True):
            cell, cut = convert_cell(value, field_type, max_cell_bytes)
            row.append(cell)
            truncated_cells += cut
        rows.append(row)
    return QueryResult(
        # description entries: (name, type, ..., flags at [7], charset id at [8])
        columns=[
            QueryColumn(name=str(c[0]), type=type_name(c[1], c[7], c[8])) for c in description
        ],
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        truncated_cells=truncated_cells,
        elapsed_ms=elapsed_ms,
    )


def run_explain(cnx: MySQLConnection, sql: str, fmt: str) -> ExplainResult:
    """``sql`` must already be classified as SELECT, TABLE or VALUES."""
    cursor = cnx.cursor()
    try:
        cursor.execute(f"{_tag('explain_query')}EXPLAIN FORMAT={fmt.upper()} {statement_text(sql)}")
        row = cursor.fetchone()
    finally:
        cursor.close()
    document = "" if row is None else str(row[0])
    if fmt == "tree":
        return ExplainResult(
            format="tree",
            explain_json_version=None,
            plan=None,
            tree=document,
            summary=None,
            summary_error=None,
        )
    plan: dict[str, JsonValue] = json.loads(document)
    try:
        version = explain.json_version(plan)
        summary, error = explain.summarize(plan), None
    except explain.UnrecognizedPlan as exc:
        version, summary, error = None, None, str(exc)
    return ExplainResult(
        format="json",
        explain_json_version=version,
        plan=plan,
        tree=None,
        summary=summary,
        summary_error=error,
    )
