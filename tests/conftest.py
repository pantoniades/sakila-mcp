"""Suite-wide pytest configuration."""

import re

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Tests marked "llm" drive a real model: they cost money and are not deterministic.
    # They run only when the -m expression names them. A default "-m 'not llm'" in
    # addopts would not do: any -m on the command line replaces it.
    # The marker name as a whole word, so a marker such as "llm_free" does not count.
    if re.search(r"(?<!\w)llm(?!\w)", str(config.getoption("-m") or "")):
        return
    skip = pytest.mark.skip(reason="opt-in: run with -m llm")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
