from array import array
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from mysql.connector.constants import FieldFlag, FieldType

from sakila_mcp.results import convert_cell, type_name

MAX = 32


@pytest.mark.parametrize(
    ("value", "field_type", "expected"),
    [
        (None, FieldType.VAR_STRING, None),
        (42, FieldType.LONGLONG, 42),
        (18446744073709551615, FieldType.LONGLONG, 18446744073709551615),
        (1.5, FieldType.DOUBLE, 1.5),
        (Decimal("12.30"), FieldType.NEWDECIMAL, "12.30"),
        (date(2024, 2, 29), FieldType.DATE, "2024-02-29"),
        (datetime(2024, 2, 29, 13, 14, 15, 123456), FieldType.DATETIME,
         "2024-02-29T13:14:15.123456"),
        (timedelta(hours=1, minutes=2, seconds=3), FieldType.TIME, "01:02:03"),
        (timedelta(hours=838, minutes=59, seconds=59), FieldType.TIME, "838:59:59"),
        # The connector returns TIME '-838:59:59' as timedelta(days=-35, seconds=3601).
        (timedelta(days=-35, seconds=3601), FieldType.TIME, "-838:59:59"),
        (-timedelta(hours=1, minutes=2, seconds=3, microseconds=500000), FieldType.TIME,
         "-01:02:03.500000"),
        ('{"a": [1, 2]}', FieldType.JSON, {"a": [1, 2]}),
        (b"\x00\xff", FieldType.VAR_STRING, "<binary 2 bytes>"),
        (b"\x05", FieldType.BIT, 5),
        (array("f", [1.0, 2.0, 3.0]), FieldType.VECTOR, "<vector dim=3>"),
        ({"b", "a"}, FieldType.STRING, "a,b"),
        ("héllo", FieldType.VAR_STRING, "héllo"),
    ],
)  # fmt: skip
def test_convert_cell(value: object, field_type: int, expected: object) -> None:
    assert convert_cell(value, field_type, MAX) == (expected, False)


def test_long_text_truncated_with_marker() -> None:
    cell, cut = convert_cell("x" * 100, FieldType.BLOB, MAX)
    assert cut is True
    assert cell == "x" * MAX + "…[truncated: 100 bytes]"


def test_truncation_never_splits_a_utf8_character() -> None:
    # "é" is 2 bytes; a 32-byte budget falls in the middle of the 17th one.
    cell, cut = convert_cell("é" * 40, FieldType.VAR_STRING, 33)
    assert cut is True
    assert isinstance(cell, str)
    assert cell.startswith("é" * 16 + "…")


def test_oversized_json_is_returned_as_truncated_text() -> None:
    document = '{"k": "' + "v" * 100 + '"}'
    cell, cut = convert_cell(document, FieldType.JSON, MAX)
    assert cut is True
    assert isinstance(cell, str)
    assert cell.endswith(f"…[truncated: {len(document)} bytes]")


@pytest.mark.parametrize(
    ("field_type", "flags", "charset", "expected"),
    [
        (FieldType.LONG, 0, 63, "int"),
        (FieldType.NEWDECIMAL, 0, 63, "decimal"),
        (FieldType.DATETIME, 0, 63, "datetime"),
        (FieldType.JSON, 0, 63, "json"),
        (FieldType.BLOB, 0, 255, "text"),
        (FieldType.BLOB, FieldFlag.BINARY, 63, "blob"),
        (FieldType.VAR_STRING, 0, 255, "varchar"),
        (FieldType.VAR_STRING, FieldFlag.BINARY, 63, "varbinary"),
        (FieldType.STRING, FieldFlag.ENUM, 255, "enum"),
        (FieldType.STRING, FieldFlag.SET, 255, "set"),
        (FieldType.YEAR, 0, 63, "year"),
    ],
)
def test_type_name(field_type: int, flags: int, charset: int, expected: str) -> None:
    assert type_name(field_type, flags, charset) == expected
