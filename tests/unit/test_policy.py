import pytest

from sakila_mcp.config import Config
from sakila_mcp.models.common import Refusal
from sakila_mcp.safety.allowlist import filter_databases, is_database_allowed
from sakila_mcp.safety.classifier import Kind
from sakila_mcp.safety.policy import Allowed, evaluate


def _config(allowlist: list[str] | None = None, timeout_ms: int = 10_000) -> Config:
    return Config(user="mcp", database_allowlist=allowlist or [], statement_timeout_ms=timeout_ms)


def _code(result: Allowed | Refusal) -> str:
    assert isinstance(result, Refusal), f"expected refusal, got {result!r}"
    return result.code


# --------------------------------------------------------------------------- allowlist


@pytest.mark.parametrize(
    ("db", "allowlist", "expected"),
    [
        ("sakila", [], True),
        ("world", [], True),
        ("information_schema", [], True),
        ("INFORMATION_SCHEMA", ["sakila"], True),
        ("performance_schema", ["sakila"], True),
        ("sys", ["sakila"], True),
        ("mysql", [], False),
        ("MySQL", [], False),
        ("mysql", ["sakila"], False),
        ("mysql", ["mysql"], True),
        ("MYSQL", ["mysql"], False),
        ("sakila", ["sakila"], True),
        ("world", ["sakila"], False),
        ("Sakila", ["sakila"], False),
    ],
)
def test_is_database_allowed(db: str, allowlist: list[str], expected: bool) -> None:
    assert is_database_allowed(db, allowlist) is expected


def test_filter_databases() -> None:
    names = ["information_schema", "mysql", "performance_schema", "sakila", "sys", "world"]
    assert filter_databases(names, []) == [
        "information_schema",
        "performance_schema",
        "sakila",
        "sys",
        "world",
    ]
    assert filter_databases(names, ["sakila"]) == [
        "information_schema",
        "performance_schema",
        "sakila",
        "sys",
    ]


# --------------------------------------------------------------------------- policy


def test_allowed_select_with_default_db() -> None:
    result = evaluate("SELECT * FROM film", _config(["sakila"]), database="sakila")
    assert isinstance(result, Allowed)
    assert result.classification.kind is Kind.SELECT


def test_classifier_refusal_passes_through() -> None:
    assert _code(evaluate("DELETE FROM t", _config(), database=None)) == "statement_not_allowed"


@pytest.mark.parametrize(
    ("sql", "database", "allowlist", "code"),
    [
        ("SELECT * FROM world.city", None, ["sakila"], "database_not_allowed"),
        ("SELECT * FROM mysql.user", None, [], "database_not_allowed"),
        ("SELECT * FROM MYSQL.user", None, [], "database_not_allowed"),
        ("SELECT * FROM film JOIN world.city", "sakila", ["sakila"], "database_not_allowed"),
        ("SELECT * FROM city", "world", ["sakila"], "database_not_allowed"),
        ("SELECT 1", "world", ["sakila"], "database_not_allowed"),
        ("SHOW TABLES FROM world", None, ["sakila"], "database_not_allowed"),
        ("SHOW COLUMNS FROM user FROM mysql", None, [], "database_not_allowed"),
        ("SHOW CREATE TABLE mysql.user", None, [], "database_not_allowed"),
        ("DESCRIBE mysql.user", None, [], "database_not_allowed"),
        ("EXPLAIN SELECT * FROM mysql.user", None, [], "database_not_allowed"),
        ("TABLE mysql.user", None, [], "database_not_allowed"),
        ("SELECT * FROM film", None, [], "no_database_selected"),
        ("SHOW TABLES", None, [], "no_database_selected"),
        ("SELECT my_func(1)", None, [], "unknown_function"),
        ("SELECT sys.format_bytes(1)", None, [], "unknown_function"),
        ("SELECT /*+ MAX_EXECUTION_TIME(99999999) */ 1", None, [], "hint_exceeds_timeout"),
        ("SELECT /*+ MAX_EXECUTION_TIME(0) */ 1", None, [], "hint_exceeds_timeout"),
    ],
)
def test_refusals(sql: str, database: str | None, allowlist: list[str], code: str) -> None:
    assert _code(evaluate(sql, _config(allowlist), database=database)) == code


@pytest.mark.parametrize(
    ("sql", "database", "allowlist"),
    [
        ("SELECT 1", None, ["sakila"]),
        ("SELECT * FROM information_schema.TABLES", None, ["sakila"]),
        ("SELECT * FROM performance_schema.threads", None, ["sakila"]),
        ("SELECT * FROM sys.schema_table_statistics", None, ["sakila"]),
        ("SELECT * FROM sakila.film", None, ["sakila"]),
        ("SELECT * FROM film JOIN sakila.actor", "sakila", ["sakila"]),
        ("SELECT * FROM mysql.user", None, ["mysql"]),
        ("SHOW PROCESSLIST", None, ["sakila"]),
        ("SHOW TABLES", "sakila", ["sakila"]),
        ("SHOW COLUMNS FROM film", "sakila", []),
        ("WITH c AS (SELECT 1) SELECT * FROM c", None, []),
        ("SELECT /*+ MAX_EXECUTION_TIME(5000) */ 1", None, []),
        ("SELECT /*+ MAX_EXECUTION_TIME(10000) */ 1", None, []),
    ],
)
def test_allowed(sql: str, database: str | None, allowlist: list[str]) -> None:
    assert isinstance(evaluate(sql, _config(allowlist), database=database), Allowed)


def test_unknown_function_reason_lists_names() -> None:
    result = evaluate("SELECT f1(1), db.f2(2)", _config(), database=None)
    assert isinstance(result, Refusal)
    assert "F1" in result.reason
    assert "DB.F2" in result.reason


def test_refusal_never_lists_other_allowed_databases() -> None:
    result = evaluate("SELECT * FROM world.city", _config(["sakila", "hr"]), database=None)
    assert isinstance(result, Refusal)
    assert "world" in result.reason
    assert "hr" not in result.reason
    assert "hr" not in result.suggestion
