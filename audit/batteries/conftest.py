"""Shared setup for the standing batteries.

These are slower than the suite in `tests/` -- they run real backtests and, in one
case, call live public APIs -- so they are marked and run on demand:

    uv run pytest audit/batteries -m audit
    uv run pytest audit/batteries -m "audit and not network"
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.audit)
