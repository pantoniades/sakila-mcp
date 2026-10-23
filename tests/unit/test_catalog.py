import pytest

from mysql_mcp.catalog import key_part, text


@pytest.mark.parametrize(
    ("column", "sub_part", "collation", "expression", "expected"),
    [
        ("email", None, "A", None, "email"),
        ("note", 10, "A", None, "note(10)"),
        ("created_at", None, "D", None, "created_at DESC"),
        (None, None, "A", "lower(`email`)", "(lower(`email`))"),
        ("geom", None, None, None, "geom"),  # SPATIAL / FULLTEXT: no collation
    ],
)
def test_key_part(
    column: str | None,
    sub_part: int | None,
    collation: str | None,
    expression: str | None,
    expected: str,
) -> None:
    assert key_part(column, sub_part, collation, expression) == expected


def test_text_decodes_bytes() -> None:
    assert text(b"InnoDB") == "InnoDB"
    assert text(bytearray(b"VIEW")) == "VIEW"
    assert text(500) == "500"
