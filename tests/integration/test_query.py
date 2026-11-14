"""Step 5: run_query and explain_query against a live server."""

from typing import Any

import pytest

from sakila_mcp.config import Config
from sakila_mcp.pool import Database

from .conftest import OTHER_DB, TEST_DB, call_tool, call_tool_error

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _run(pool: Database, config: Config, sql: str, **args: Any) -> dict[str, Any]:
    # "pool", not "database": args may carry the tool's own database= argument.
    return await call_tool(pool, config, "run_query", {"sql": sql, **args})


async def test_select_with_default_database(database: Database, config: Config) -> None:
    result = await _run(
        database, config, "SELECT id, email, created_at, profile FROM customer ORDER BY id LIMIT 3",
        database=TEST_DB,
    )  # fmt: skip
    assert [(c["name"], c["type"]) for c in result["columns"]] == [
        ("id", "int"),
        ("email", "varchar"),
        ("created_at", "datetime"),
        ("profile", "json"),
    ]
    assert result["row_count"] == 3
    assert result["truncated"] is False
    first = result["rows"][0]
    assert first[0] == 1
    assert first[1] == "user1@example.com"
    assert "T" in first[2]  # ISO 8601
    assert first[3] == {"n": 1, "tier": "pro"}  # JSON is parsed
    assert result["elapsed_ms"] >= 0


async def test_empty_result(database: Database, config: Config) -> None:
    result = await _run(database, config, f"SELECT n FROM {TEST_DB}.numbers WHERE n < 0")
    assert result["rows"] == []
    assert result["columns"] == [{"name": "n", "type": "int"}]


async def test_row_cap_truncates_and_discards_connection(
    database: Database, config: Config
) -> None:
    small = config.model_copy(update={"max_rows": 10})
    sql = f"SELECT a.n, b.n FROM {TEST_DB}.numbers a CROSS JOIN {TEST_DB}.numbers b"
    result = await _run(database, small, sql)  # 1,000,000 rows on the server
    assert result["truncated"] is True
    assert result["row_count"] == 10
    # The pool threw the connection away instead of draining it: nothing is left
    # running, and every pooled connection still works.
    for _ in range(config.pool_size + 1):
        check = await _run(
            database,
            config,
            "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
            "WHERE INFO LIKE '%CROSS JOIN%' AND ID <> CONNECTION_ID()",
        )
        assert check["rows"] == [[0]]


async def test_cells_truncated_and_binary_summarized(database: Database, config: Config) -> None:
    result = await _run(
        database, config, f"SELECT payload, raw_bin, big_text FROM {TEST_DB}.blobs WHERE id = 1"
    )
    assert [c["type"] for c in result["columns"]] == ["blob", "varbinary", "text"]
    payload, raw_bin, big_text = result["rows"][0]
    assert payload == "<binary 1024 bytes>"
    assert raw_bin == "<binary 8 bytes>"
    assert big_text == "x" * config.max_cell_bytes + "…[truncated: 10000 bytes]"
    assert result["truncated_cells"] == 1


async def test_decimal_enum_and_time(database: Database, config: Config) -> None:
    result = await _run(
        database,
        config,
        f"SELECT amount, status, TIME '-838:59:59' FROM {TEST_DB}.orders WHERE id = 7",
    )
    assert [c["type"] for c in result["columns"]] == ["decimal", "enum", "time"]
    assert result["rows"] == [["9.59", "paid", "-838:59:59"]]


async def test_params_bound_by_server(database: Database, config: Config) -> None:
    result = await _run(
        database, config, "SELECT id, email FROM customer WHERE id = ? OR email = ?",
        database=TEST_DB, params=[7, "x' OR '1'='1"],
    )  # fmt: skip
    # The injection attempt is a plain value, so only id 7 matches.
    assert result["rows"] == [[7, "user7@example.com"]]


async def test_params_leave_percent_literals_alone(database: Database, config: Config) -> None:
    result = await _run(
        database, config, "SELECT DATE_FORMAT('2024-01-02 03:04:05', '%H:%i:%s'), ?", params=["x"]
    )
    assert result["rows"] == [["03:04:05", "x"]]


async def test_params_with_year_column(database: Database, config: Config) -> None:
    # Regression: the connector's binary-protocol cursor crashes decoding YEAR.
    # CAST, not YEAR(): the function's result is typed YEAR on 8.4+ but BIGINT on 8.0,
    # where the test would not exercise a YEAR column at all.
    result = await _run(database, config, "SELECT CAST(2024 AS YEAR), ?", params=[None])
    assert result["columns"][0]["type"] == "year"
    assert result["rows"] == [[2024, None]]


async def test_params_count_mismatch(database: Database, config: Config) -> None:
    message = await call_tool_error(
        database, config, "run_query", {"sql": "SELECT ?, ?", "params": [1]}
    )
    assert "1210" in message


async def test_params_rejected_for_show(database: Database, config: Config) -> None:
    message = await call_tool_error(
        database, config, "run_query", {"sql": "SHOW DATABASES", "params": [1]}
    )
    assert "SELECT, TABLE and VALUES" in message


async def test_statement_timeout(database: Database, config: Config) -> None:
    sql = (
        f"SELECT COUNT(*) FROM {TEST_DB}.numbers a, {TEST_DB}.numbers b, {TEST_DB}.numbers c "
        "WHERE a.n + b.n + c.n < 0"
    )
    message = await call_tool_error(database, config, "run_query", {"sql": sql})
    assert f"{config.statement_timeout_ms} ms statement timeout" in message


async def test_mysql_error_is_tool_error(database: Database, config: Config) -> None:
    message = await call_tool_error(
        database, config, "run_query", {"sql": f"SELECT nope FROM {TEST_DB}.customer"}
    )
    assert "1054" in message


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (f"INSERT INTO {TEST_DB}.write_probe VALUES (1)", "statement_not_allowed"),
        ("SELECT SLEEP(1)", "denied_function"),
        (f"SELECT * FROM {OTHER_DB}.secret", "database_not_allowed"),
        ("SELECT n FROM numbers", "no_database_selected"),
        ("SELECT 1; SELECT 2", "multi_statement"),
    ],
)
async def test_refusals(database: Database, config: Config, sql: str, code: str) -> None:
    result = await _run(database, config, sql)
    assert result["refused"] is True
    assert result["code"] == code


async def test_show_statement(database: Database, config: Config) -> None:
    result = await _run(database, config, "SHOW TABLES", database=TEST_DB)
    assert ["customer"] in result["rows"]


async def test_vector_cells(database: Database, config: Config) -> None:
    found = await _run(
        database,
        config,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{TEST_DB}' AND TABLE_NAME = 'embeddings'",
    )
    if found["rows"] == [[0]]:
        pytest.skip("no embeddings table (VECTOR needs MySQL 9.0+)")
    result = await _run(database, config, f"SELECT v FROM {TEST_DB}.embeddings ORDER BY id")
    assert result["rows"] == [["<vector dim=3>"], ["<vector dim=3>"]]


async def _explain(pool: Database, config: Config, sql: str, **args: Any) -> dict[str, Any]:
    # "pool", not "database": args may carry the tool's own database= argument.
    return await call_tool(pool, config, "explain_query", {"sql": sql, **args})


async def test_explain_json_summary(database: Database, config: Config) -> None:
    result = await _explain(
        database, config, "SELECT * FROM orders ORDER BY amount;", database=TEST_DB
    )
    assert result["format"] == "json"
    assert result["explain_json_version"] == database.capabilities.explain_json_version
    assert result["plan"]
    assert result["summary_error"] is None
    summary = result["summary"]
    assert summary["full_table_scans"] == [{"table": "orders", "estimated_rows": 1000}]
    assert summary["filesort"] is True
    assert summary["temporary_table"] is False


async def test_explain_tree(database: Database, config: Config) -> None:
    result = await _explain(
        database,
        config,
        f"SELECT c.email FROM {TEST_DB}.customer c JOIN {TEST_DB}.orders o ON o.customer_id = c.id",
        format="tree",
    )
    assert result["format"] == "tree"
    assert result["tree"].startswith("-> ")
    assert result["plan"] is None
    assert result["summary"] is None


async def test_explain_refuses_non_allowlisted(database: Database, config: Config) -> None:
    result = await _explain(database, config, f"SELECT * FROM {OTHER_DB}.secret")
    assert result["refused"] is True
    assert result["code"] == "database_not_allowed"


async def test_explain_rejects_show(database: Database, config: Config) -> None:
    message = await call_tool_error(
        database, config, "explain_query", {"sql": "SHOW TABLES", "database": TEST_DB}
    )
    assert "not SHOW" in message
