"""MCP Registry metadata: README marker, server.json and pyproject.toml must agree.

The registry verifies ownership of a PyPI package by finding ``mcp-name: <name>`` in the
package README (the PyPI description), where <name> equals the name in server.json. A
release whose published description lacks the marker cannot be listed, and PyPI metadata
cannot be fixed afterwards, so drift is caught here instead.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from sakila_mcp.config import Config

ROOT = Path(__file__).resolve().parents[2]
# The registry's limit; longer descriptions fail its validation.
MAX_DESCRIPTION = 100


@pytest.fixture(scope="module")
def server_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def project() -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]  # type: ignore[no-any-return]


def test_readme_names_the_server(server_json: dict[str, Any]) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # The registry wants the token followed by whitespace, a tag or "-->" (a trailing
    # period would break the match), and the name must be exactly server.json's.
    found = re.findall(r"mcp-name: (\S+?)(?:\s|-->|<|$)", readme)
    assert found == [server_json["name"]]


def test_name_is_in_the_github_namespace(server_json: dict[str, Any]) -> None:
    # GitHub authentication only allows names under the account's own prefix.
    assert server_json["name"].startswith("io.github.pantoniades/")


def test_versions_match_pyproject(server_json: dict[str, Any], project: dict[str, Any]) -> None:
    (package,) = server_json["packages"]
    assert server_json["version"] == package["version"] == project["version"]


def test_package_is_the_pypi_project(server_json: dict[str, Any], project: dict[str, Any]) -> None:
    (package,) = server_json["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == project["name"]
    assert package["transport"] == {"type": "stdio"}
    assert server_json["repository"]["url"] == project["urls"]["Repository"]


def test_description_fits_the_registry_limit(server_json: dict[str, Any]) -> None:
    assert len(server_json["description"]) <= MAX_DESCRIPTION


def test_environment_variables_match_config(server_json: dict[str, Any]) -> None:
    (package,) = server_json["packages"]
    variables = package["environmentVariables"]
    env_names = {f"SAKILA_MCP_{name.upper()}": field for name, field in Config.model_fields.items()}
    assert {v["name"] for v in variables} <= set(env_names)
    # Required in server.json exactly when Config has no default for it.
    required = {name for name, field in env_names.items() if field.is_required()}
    assert {v["name"] for v in variables if v["isRequired"]} == required
    assert {v["name"] for v in variables if v.get("isSecret")} == {"SAKILA_MCP_PASSWORD"}
