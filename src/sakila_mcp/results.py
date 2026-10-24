"""Convert connector result cells to JSON values, with per-cell size limits.

Assumes the text protocol (plain cursor). The connector's binary-protocol (prepared)
cursor returns different Python types for the same column and crashes on YEAR, which
is why run_query binds parameters with SQL-level PREPARE instead.
"""

import json
from array import array
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from mysql.connector.constants import FieldFlag, FieldType
from pydantic import JsonValue

# Friendlier names for the connector's wire types (FieldType.get_info).
_TYPE_NAMES = {
    FieldType.TINY: "tinyint",
    FieldType.SHORT: "smallint",
    FieldType.INT24: "mediumint",
    FieldType.LONG: "int",
    FieldType.LONGLONG: "bigint",
    FieldType.DECIMAL: "decimal",
    FieldType.NEWDECIMAL: "decimal",
    FieldType.NEWDATE: "date",
}

# String wire types are shared: TEXT and BLOB are both BLOB on the wire, VARBINARY is
# VAR_STRING, ENUM and SET arrive as STRING. The column's charset (63 = binary) and
# its ENUM/SET flags tell them apart. Values are (text name, binary name).
_STRING_TYPES = {
    FieldType.VAR_STRING: ("varchar", "varbinary"),
    FieldType.VARCHAR: ("varchar", "varbinary"),
    FieldType.STRING: ("char", "binary"),
    FieldType.TINY_BLOB: ("text", "blob"),
    FieldType.MEDIUM_BLOB: ("text", "blob"),
    FieldType.LONG_BLOB: ("text", "blob"),
    FieldType.BLOB: ("text", "blob"),
}
_BINARY_CHARSET = 63


def type_name(field_type: int, flags: int = 0, charset: int | None = None) -> str:
    """Column type for the result header, from one cursor.description entry."""
    if field_type in _STRING_TYPES:
        if flags & FieldFlag.ENUM:
            return "enum"
        if flags & FieldFlag.SET:
            return "set"
        text, binary = _STRING_TYPES[field_type]
        return binary if charset == _BINARY_CHARSET else text
    return _TYPE_NAMES.get(field_type) or str(FieldType.get_info(field_type)).lower()


def _format_time(value: timedelta) -> str:
    """MySQL TIME as [-]HH:MM:SS[.ffffff]; hours may exceed 24 (range is ±838:59:59)."""
    # timedelta normalizes negatives as days=-35, seconds=3601; work on the magnitude.
    total_us = abs(value) // timedelta(microseconds=1)
    seconds, micros = divmod(total_us, 1_000_000)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{'-' if value < timedelta(0) else ''}{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{text}.{micros:06d}" if micros else text


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Cut on a byte budget, then drop any partial UTF-8 sequence left at the end.
    head = encoded[:max_bytes].decode("utf-8", "ignore")
    return f"{head}…[truncated: {len(encoded)} bytes]", True


def convert_cell(value: object, field_type: int, max_bytes: int) -> tuple[JsonValue, bool]:
    """Return (JSON value, was_truncated) for one cell."""
    if value is None or isinstance(value, bool | int | float):
        return value, False
    if isinstance(value, Decimal):
        return str(value), False  # a JSON number would lose DECIMAL precision
    if isinstance(value, datetime | date | time):
        return value.isoformat(), False
    if isinstance(value, timedelta):
        return _format_time(value), False
    if isinstance(value, array):  # VECTOR (9.0+): summarize like binary data
        return f"<vector dim={len(value)}>", False
    if isinstance(value, set | frozenset):  # SET column
        return _truncate(",".join(sorted(value)), max_bytes)
    if isinstance(value, bytes | bytearray):
        if field_type == FieldType.BIT:
            return int.from_bytes(value, "big"), False
        return f"<binary {len(value)} bytes>", False
    text = str(value)
    # Parse JSON only if the document fits; a truncated document would not be valid JSON,
    # so an oversized one is returned as truncated text instead.
    if field_type == FieldType.JSON and len(text.encode("utf-8")) <= max_bytes:
        parsed: JsonValue = json.loads(text)
        return parsed, False
    return _truncate(text, max_bytes)
