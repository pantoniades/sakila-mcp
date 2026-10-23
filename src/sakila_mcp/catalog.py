"""Schema inspection via information_schema. Allowlist checks happen in the caller."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sakila_mcp.models.catalog import (
    Column,
    DatabaseEntry,
    DatabaseList,
    ForeignKey,
    Index,
    TableDescription,
    TableSummary,
)
from sakila_mcp.safety.allowlist import SYSTEM_SCHEMAS, filter_databases


def text(value: object) -> str:
    """Decode a value the connector may return as bytes (common for I_S columns)."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes | bytearray) else str(value)


def _opt_text(value: object) -> str | None:
    return None if value is None else text(value)


def _opt_int(value: object) -> int | None:
    return None if value is None else int(text(value))


def list_databases(cursor: Any, allowlist: Sequence[str]) -> DatabaseList:
    cursor.execute(
        "/* sakila-mcp tool=list_databases */ "
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME"
    )
    names = filter_databases((text(row[0]) for row in cursor.fetchall()), allowlist)
    return DatabaseList(
        databases=[DatabaseEntry(name=n, system=n.lower() in SYSTEM_SCHEMAS) for n in names]
    )


def database_exists(cursor: Any, database: str) -> bool:
    cursor.execute(
        "/* sakila-mcp tool=list_tables */ "
        "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
        (database,),
    )
    (count,) = cursor.fetchone()
    return int(count) > 0


def list_tables(cursor: Any, database: str) -> list[TableSummary]:
    cursor.execute(
        "/* sakila-mcp tool=list_tables */ "
        "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, "
        "TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
        "ORDER BY TABLE_NAME",
        (database,),
    )
    tables = []
    for name, table_type, engine, rows, data_len, index_len, comment in cursor.fetchall():
        kind = text(table_type)
        tables.append(
            TableSummary(
                name=text(name),
                table_type=kind,
                engine=_opt_text(engine),
                approx_rows=_opt_int(rows),
                data_length=_opt_int(data_len),
                index_length=_opt_int(index_len),
                # Views carry the literal comment "VIEW"; it says nothing table_type doesn't.
                comment="" if kind == "VIEW" else text(comment or ""),
            )
        )
    return tables


def key_part(
    column: str | None, sub_part: int | None, collation: str | None, expression: str | None
) -> str:
    """Render one index key part the way it would be written in CREATE INDEX."""
    part = f"({expression})" if column is None else column
    if sub_part is not None:
        part += f"({sub_part})"
    if collation == "D":
        part += " DESC"
    return part


def describe_table(cursor: Any, database: str, table: str) -> TableDescription | None:
    """Describe one table or view; None if it does not exist or is not visible."""
    params = (database, table)
    cursor.execute(
        "/* sakila-mcp tool=describe_table */ "
        "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, ROW_FORMAT, TABLE_COLLATION, TABLE_ROWS, "
        "CREATE_TIME, TABLE_COMMENT FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        params,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    name, table_type, engine, row_format, collation, rows, create_time, comment = row
    kind = text(table_type)
    return TableDescription(
        database=database,
        name=text(name),
        table_type=kind,
        engine=_opt_text(engine),
        row_format=_opt_text(row_format),
        collation=_opt_text(collation),
        approx_rows=_opt_int(rows),
        create_time=create_time if isinstance(create_time, datetime) else None,
        comment="" if kind == "VIEW" else text(comment or ""),
        columns=_columns(cursor, params),
        indexes=_indexes(cursor, params),
        foreign_keys=_foreign_keys(cursor, params),
    )


def _columns(cursor: Any, params: tuple[str, str]) -> list[Column]:
    cursor.execute(
        "/* sakila-mcp tool=describe_table */ "
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, COLLATION_NAME, "
        "COLUMN_COMMENT, GENERATION_EXPRESSION FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        params,
    )
    columns = []
    for row in cursor.fetchall():
        name, col_type, nullable, default, extra, collation, comment, generated = row
        columns.append(
            Column(
                name=text(name),
                type=text(col_type),
                nullable=text(nullable) == "YES",
                default=_opt_text(default),
                extra=text(extra or ""),
                collation=_opt_text(collation),
                comment=text(comment or ""),
                generation_expression=text(generated) if generated else None,
            )
        )
    return columns


def _indexes(cursor: Any, params: tuple[str, str]) -> list[Index]:
    cursor.execute(
        "/* sakila-mcp tool=describe_table */ "
        "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SUB_PART, COLLATION, EXPRESSION, "
        "INDEX_TYPE, CARDINALITY, IS_VISIBLE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
        "ORDER BY INDEX_NAME = 'PRIMARY' DESC, INDEX_NAME, SEQ_IN_INDEX",
        params,
    )
    indexes: dict[str, Index] = {}
    for row in cursor.fetchall():
        name, non_unique, column, sub_part, collation, expr, idx_type, card, visible = row
        key = text(name)
        index = indexes.get(key)
        if index is None:
            index = indexes[key] = Index(
                name=key,
                unique=int(non_unique) == 0,
                columns=[],
                index_type=text(idx_type),
                cardinality=None,
                visible=text(visible) == "YES",
            )
        index.columns.append(
            key_part(_opt_text(column), _opt_int(sub_part), _opt_text(collation), _opt_text(expr))
        )
        index.cardinality = _opt_int(card)
    return list(indexes.values())


def _foreign_keys(cursor: Any, params: tuple[str, str]) -> list[ForeignKey]:
    cursor.execute(
        "/* sakila-mcp tool=describe_table */ "
        "SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_SCHEMA, "
        "k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, r.UPDATE_RULE, r.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
        "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
        "WHERE k.TABLE_SCHEMA = %s AND k.TABLE_NAME = %s "
        "AND k.REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION",
        params,
    )
    keys: dict[str, ForeignKey] = {}
    for name, column, ref_db, ref_table, ref_column, on_update, on_delete in cursor.fetchall():
        key = text(name)
        fk = keys.get(key)
        if fk is None:
            fk = keys[key] = ForeignKey(
                name=key,
                columns=[],
                referenced_database=text(ref_db),
                referenced_table=text(ref_table),
                referenced_columns=[],
                on_update=text(on_update),
                on_delete=text(on_delete),
            )
        fk.columns.append(text(column))
        fk.referenced_columns.append(text(ref_column))
    return list(keys.values())
