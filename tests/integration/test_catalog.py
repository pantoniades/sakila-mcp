"""Step 4: list_databases, list_tables, describe_table."""

from typing import Any

import pytest
from mcp.types import CallToolResult

from mysql_mcp.config import Config
from mysql_mcp.pool import Database
from mysql_mcp.server import build_server

from .conftest import OTHER_DB, TEST_DB

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _call(
    database: Database, config: Config, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    result = await build_server(config, database).call_tool(tool, args)
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content
    # Tools that can refuse return a union, which the SDK wraps as {"result": ...}.
    if set(content) == {"result"}:
        inner: dict[str, Any] = content["result"]
        return inner
    return content


async def test_list_databases_applies_allowlist(database: Database, config: Config) -> None:
    result = await _call(database, config, "list_databases", {})
    names = {d["name"]: d["system"] for d in result["databases"]}
    assert names[TEST_DB] is False
    assert names["information_schema"] is True
    assert OTHER_DB not in names
    assert "mysql" not in names


async def test_list_databases_empty_allowlist(database: Database, config: Config) -> None:
    open_config = config.model_copy(update={"database_allowlist": []})
    result = await _call(database, open_config, "list_databases", {})
    names = {d["name"] for d in result["databases"]}
    assert {TEST_DB, OTHER_DB} <= names
    assert "mysql" not in names


async def test_list_tables(database: Database, config: Config) -> None:
    result = await _call(database, config, "list_tables", {"database": TEST_DB})
    assert result["database"] == TEST_DB
    tables = {t["name"]: t for t in result["tables"]}
    assert {"customer", "orders", "numbers", "blobs", "customer_order_totals"} <= set(tables)
    customer = tables["customer"]
    assert customer["table_type"] == "BASE TABLE"
    assert customer["engine"] == "InnoDB"
    assert customer["approx_rows"] is not None
    assert customer["data_length"] > 0
    view = tables["customer_order_totals"]
    assert view["table_type"] == "VIEW"
    assert view["engine"] is None
    assert view["comment"] == ""


async def test_list_tables_system_schema(database: Database, config: Config) -> None:
    result = await _call(database, config, "list_tables", {"database": "information_schema"})
    assert "TABLES" in {t["name"] for t in result["tables"]}


async def test_list_tables_refuses_non_allowlisted(database: Database, config: Config) -> None:
    result = await _call(database, config, "list_tables", {"database": OTHER_DB})
    assert result["refused"] is True
    assert result["code"] == "database_not_allowed"


async def test_list_tables_missing_database(database: Database, config: Config) -> None:
    missing = "mysql_mcp_missing"
    wider = config.model_copy(update={"database_allowlist": [TEST_DB, missing]})
    result = await _call(database, wider, "list_tables", {"database": missing})
    assert result["refused"] is True
    assert result["code"] == "database_not_found"


async def test_describe_customer(database: Database, config: Config) -> None:
    result = await _call(
        database, config, "describe_table", {"database": TEST_DB, "table": "customer"}
    )
    assert result["table_type"] == "BASE TABLE"
    assert result["engine"] == "InnoDB"
    assert result["collation"] == "utf8mb4_0900_ai_ci"
    assert result["create_time"] is not None
    columns = {c["name"]: c for c in result["columns"]}
    assert [c["name"] for c in result["columns"]] == [
        "id",
        "email",
        "name",
        "created_at",
        "profile",
    ]
    assert columns["id"]["type"] == "int unsigned"
    assert columns["id"]["extra"] == "auto_increment"
    assert columns["id"]["nullable"] is False
    assert columns["created_at"]["default"] == "CURRENT_TIMESTAMP(6)"
    assert columns["profile"]["type"] == "json"
    assert columns["profile"]["nullable"] is True
    assert columns["email"]["collation"] == "utf8mb4_0900_ai_ci"
    indexes = {i["name"]: i for i in result["indexes"]}
    assert result["indexes"][0]["name"] == "PRIMARY"
    assert indexes["PRIMARY"]["unique"] is True
    assert indexes["uk_email"]["unique"] is True
    assert indexes["uk_email"]["columns"] == ["email"]
    assert indexes["idx_name_created"]["columns"] == ["name", "created_at"]
    assert indexes["idx_name_created"]["unique"] is False
    assert result["foreign_keys"] == []


async def test_describe_foreign_key(database: Database, config: Config) -> None:
    result = await _call(
        database, config, "describe_table", {"database": TEST_DB, "table": "orders"}
    )
    assert result["foreign_keys"] == [
        {
            "name": "fk_orders_customer",
            "columns": ["customer_id"],
            "referenced_database": TEST_DB,
            "referenced_table": "customer",
            "referenced_columns": ["id"],
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
        }
    ]
    status = next(c for c in result["columns"] if c["name"] == "status")
    assert status["type"] == "enum('new','paid','shipped')"
    assert status["default"] == "new"


async def test_describe_view(database: Database, config: Config) -> None:
    result = await _call(
        database,
        config,
        "describe_table",
        {"database": TEST_DB, "table": "customer_order_totals"},
    )
    assert result["table_type"] == "VIEW"
    assert result["indexes"] == []
    assert [c["name"] for c in result["columns"]] == [
        "customer_id",
        "email",
        "order_count",
        "total",
    ]


async def test_describe_missing_table(database: Database, config: Config) -> None:
    result = await _call(
        database, config, "describe_table", {"database": TEST_DB, "table": "no_such_table"}
    )
    assert result["refused"] is True
    assert result["code"] == "table_not_found"


async def test_describe_refuses_non_allowlisted(database: Database, config: Config) -> None:
    result = await _call(
        database, config, "describe_table", {"database": OTHER_DB, "table": "secret"}
    )
    assert result["refused"] is True
    assert result["code"] == "database_not_allowed"
