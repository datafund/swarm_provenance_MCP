"""Wall-clock tests stay out of the default run (issue #192).

Timing assertions depend on machine load, so tests that make them carry the
``performance`` marker and run only with ``--run-performance``.
"""

import ast
from pathlib import Path

import pytest

from tests import conftest

TESTS_DIR = Path(__file__).parent
PERF_MODULE = TESTS_DIR / "test_performance_regression.py"
REGRESSION_WORKFLOW = (
    TESTS_DIR.parent / ".github" / "workflows" / "regression_tests.yml"
)
CLOCK_CALLS = {"time", "perf_counter", "monotonic", "process_time"}


class _Config:
    def __init__(self, **options):
        self._options = {
            "--run-integration": False,
            "--run-performance": False,
            **options,
        }

    def getoption(self, name):
        return self._options[name]


class _Item:
    def __init__(self, *markers):
        self.keywords = {m: True for m in markers}
        self.added = []

    def add_marker(self, marker):
        self.added.append(marker)


def _skip_reasons(item):
    return [m.kwargs.get("reason") for m in item.added if m.name == "skip"]


def test_performance_tests_skipped_without_flag():
    perf, plain = _Item("performance"), _Item()
    conftest.pytest_collection_modifyitems(_Config(), [perf, plain])
    assert _skip_reasons(perf) == ["performance test: pass --run-performance"]
    assert plain.added == []


def test_performance_tests_run_with_flag():
    perf, integ = _Item("performance"), _Item("integration")
    conftest.pytest_collection_modifyitems(
        _Config(**{"--run-performance": True}), [perf, integ]
    )
    assert perf.added == []
    # The integration rule is independent of the performance flag.
    assert _skip_reasons(integ) == ["integration test: pass --run-integration"]


def test_integration_flag_does_not_enable_performance_tests():
    perf, integ = _Item("performance"), _Item("integration")
    conftest.pytest_collection_modifyitems(
        _Config(**{"--run-integration": True}), [perf, integ]
    )
    assert _skip_reasons(perf) == ["performance test: pass --run-performance"]
    assert integ.added == []


def _has_performance_marker(func):
    for dec in func.decorator_list:
        if (
            isinstance(dec, ast.Attribute)
            and dec.attr == "performance"
            and isinstance(dec.value, ast.Attribute)
            and dec.value.attr == "mark"
        ):
            return True
    return False


def _reads_clock(func):
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CLOCK_CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            return True
    return False


def _test_functions(tree):
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            yield node


def test_every_wall_clock_test_is_marked_performance():
    tree = ast.parse(PERF_MODULE.read_text())
    timed = [f for f in _test_functions(tree) if _reads_clock(f)]
    # Guard the check itself: the module has seven wall-clock tests today.
    assert len(timed) >= 7
    unmarked = [f.name for f in timed if not _has_performance_marker(f)]
    assert unmarked == [], f"wall-clock tests without the performance marker: {unmarked}"


def test_performance_job_passes_run_performance():
    text = REGRESSION_WORKFLOW.read_text()
    runs = [
        line.strip()
        for line in text.splitlines()
        if "test_performance_regression.py" in line
    ]
    assert len(runs) == 2, runs
    for line in runs:
        assert "--run-performance" in line, line
