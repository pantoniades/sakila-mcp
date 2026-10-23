import pytest

from sakila_mcp.versions import parse_version, release_line


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8.0.36", (8, 0, 36)),
        ("8.4.11", (8, 4, 11)),
        ("9.7.2", (9, 7, 2)),
        ("26.10.0", (26, 10, 0)),
        ("8.0.36-log", (8, 0, 36)),
        ("8.4.0-commercial", (8, 4, 0)),
        ("8.0.36-0ubuntu0.22.04.1", (8, 0, 36)),
        (" 9.1.0 ", (9, 1, 0)),
    ],
)
def test_parse_version(raw: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "8.0", "MySQL 8.0.36", "v8.0.36", "abc"])
def test_parse_version_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="unrecognized MySQL version"):
        parse_version(raw)


def test_tuple_comparison_orders_calendar_after_classic() -> None:
    assert parse_version("8.0.36") < parse_version("8.4.0") < parse_version("9.7.2")
    assert parse_version("9.7.2") < parse_version("26.10.0")
    assert parse_version("8.0.9") < parse_version("8.0.10")


@pytest.mark.parametrize(
    ("version", "line"),
    [
        ((8, 0, 36), "8.0 LTS"),
        ((8, 4, 11), "8.4 LTS"),
        ((9, 7, 2), "9.7 LTS"),
        ((8, 1, 0), "Innovation"),
        ((8, 3, 0), "Innovation"),
        ((9, 0, 1), "Innovation"),
        ((9, 6, 0), "Innovation"),
        ((26, 10, 0), "Innovation"),
        ((27, 4, 1), "Innovation"),
        ((28, 1, 0), "Innovation"),
        ((29, 1, 0), "unknown"),
        ((5, 7, 44), "unknown"),
    ],
)
def test_release_line(version: tuple[int, int, int], line: str) -> None:
    assert release_line(version) == line
