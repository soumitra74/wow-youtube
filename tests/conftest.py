from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="run browser end-to-end tests",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--e2e"):
        return
    expr = "".join((config.option.markexpr or "").split())
    if expr == "e2e" or "e2e" in expr and "note2e" not in expr:
        return
    if items and all(item.get_closest_marker("e2e") for item in items):
        return
    skip = pytest.mark.skip(reason="pass --e2e (or pytest tests/e2e) to run browser tests")
    for item in items:
        if item.get_closest_marker("e2e"):
            item.add_marker(skip)
