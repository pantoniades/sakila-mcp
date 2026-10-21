import subprocess
import sys
from pathlib import Path

import pytest

from mysql_mcp.config import Config
from mysql_mcp.server import SERVER_NAME, build_server


@pytest.mark.anyio
async def test_server_lists_zero_tools() -> None:
    server = build_server(Config(user="mcp"))
    assert server.name == SERVER_NAME
    assert await server.list_tools() == []


def test_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mysql_mcp", "--help"],
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
        [sys.executable, "-m", "mysql_mcp", "--config", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "pasword" in result.stderr
    assert "leaky-value" not in result.stderr
