import pytest

from sakila_mcp.query import statement_text


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("SELECT 1;", "SELECT 1"),
        ("SELECT 1 ;\n\n", "SELECT 1 "),
        ("SELECT ';'", "SELECT ';'"),
        ("SELECT 1 -- note", "SELECT 1 -- note"),
    ],
)
def test_statement_text(sql: str, expected: str) -> None:
    assert statement_text(sql) == expected
