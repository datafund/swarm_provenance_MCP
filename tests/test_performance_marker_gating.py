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
CLOCK_CALLS = {
    "time",
    "time_ns",
    "perf_counter",
    "perf_counter_ns",
    "monotonic",
    "monotonic_ns",
    "process_time",
    "process_time_ns",
}
LOOP_GETTERS = {"get_event_loop", "get_running_loop"}


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


def _clock_names(tree):
    """Return (names bound to the time module, names bound to clock functions)."""
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    modules.add(alias.asname or "time")
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            for alias in node.names:
                if alias.name in CLOCK_CALLS:
                    functions.add(alias.asname or alias.name)
    return modules, functions


def _is_event_loop(node):
    """True for ``loop``-style names and ``asyncio.get_event_loop()`` calls."""
    if isinstance(node, ast.Name):
        return "loop" in node.id.lower()
    if isinstance(node, ast.Call):
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(
            callee, "id", None
        )
        return name in LOOP_GETTERS
    return False


def _reads_clock(func, modules=frozenset({"time"}), functions=frozenset()):
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id in functions:
            return True
        if not isinstance(callee, ast.Attribute):
            continue
        receiver = callee.value
        if (
            callee.attr in CLOCK_CALLS
            and isinstance(receiver, ast.Name)
            and receiver.id in modules
        ):
            return True
        if callee.attr == "time" and _is_event_loop(receiver):
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
    modules, functions = _clock_names(tree)
    timed = [
        f for f in _test_functions(tree) if _reads_clock(f, modules, functions)
    ]
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


def _detects(source):
    tree = ast.parse(source)
    modules, functions = _clock_names(tree)
    (func,) = list(_test_functions(tree))
    return _reads_clock(func, modules, functions)


@pytest.mark.parametrize(
    "source",
    [
        "import time\ndef test_a():\n    time.time()\n",
        "import time\ndef test_a():\n    time.perf_counter()\n",
        "import time\ndef test_a():\n    time.monotonic()\n",
        "import time\ndef test_a():\n    time.process_time()\n",
        "import time\ndef test_a():\n    time.time_ns()\n",
        "import time\ndef test_a():\n    time.perf_counter_ns()\n",
        "import time\ndef test_a():\n    time.monotonic_ns()\n",
        "import time as t\ndef test_a():\n    t.monotonic()\n",
        "from time import perf_counter\ndef test_a():\n    perf_counter()\n",
        "from time import monotonic\ndef test_a():\n    monotonic()\n",
        "from time import time\ndef test_a():\n    time()\n",
        "from time import process_time\ndef test_a():\n    process_time()\n",
        "from time import perf_counter as pc\ndef test_a():\n    pc()\n",
        "async def test_a():\n    loop.time()\n",
        "async def test_a():\n    event_loop.time()\n",
        "import asyncio\ndef test_a():\n    asyncio.get_event_loop().time()\n",
        "import asyncio\nasync def test_a():\n    asyncio.get_running_loop().time()\n",
    ],
)
def test_clock_detector_finds_clock_reads(source):
    assert _detects(source)


@pytest.mark.parametrize(
    "source",
    [
        "import time\ndef test_a():\n    time.sleep(0)\n",
        "import asyncio\nasync def test_a():\n    await asyncio.sleep(0)\n",
        "from time import sleep\ndef test_a():\n    sleep(0)\n",
        "def test_a():\n    perf_counter()\n",
        "def test_a():\n    record.time()\n",
        "def test_a():\n    x = 1\n",
    ],
)
def test_clock_detector_ignores_non_clock_calls(source):
    assert not _detects(source)
