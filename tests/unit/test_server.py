import subprocess
import sys
from pathlib import Path

import pytest

from sakila_mcp.config import Config
from sakila_mcp.pool import Database
from sakila_mcp.server import CAPABILITIES_URI, SERVER_NAME, build_server


@pytest.mark.anyio
async def test_tools_registered_read_only() -> None:
    config = Config(user="mcp")
    server = build_server(config, Database(config))
    assert server.name == SERVER_NAME
    tools = await server.list_tools()
    assert [t.name for t in tools] == [
        "server_info",
        "list_databases",
        "list_tables",
        "describe_table",
    ]
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.description
        assert tool.output_schema is not None


@pytest.mark.anyio
async def test_capabilities_resource_registered() -> None:
    config = Config(user="mcp")
    server = build_server(config, Database(config))
    resources = await server.list_resources()
    assert [str(r.uri) for r in resources] == [CAPABILITIES_URI]


def test_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sakila_mcp", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout


def test_bad_config_never_echoes_input(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('user = "u"\npasword = "leaky-value"\n', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "sakila_mcp", "--config", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "pasword" in result.stderr
    assert "leaky-value" not in result.stderr
