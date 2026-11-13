#!/usr/bin/env python3
"""Suite-wide fixtures."""

import os
from pathlib import Path

import pytest

from strata.controllers import workload_controller

# Every git a test starts, directly or through strata, reads only this file: no developer hooks (a pre-commit hook
# that starts a shell costs every commit), credential helpers or signing, the same as on a CI runner.
_GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": (Path(__file__).parent / "gitconfig").as_posix(),
    "GIT_CONFIG_NOSYSTEM": "1",
}
_saved_git_environment: dict[str, str | None] = {}

# A test not marked `slow` that takes longer than this is listed at the end of a `-m "not slow"` run. It sits well
# above the 0.2s the marks were chosen at, so timing noise on a busy machine does not list a test that is fine.
_UNMARKED_SLOW_SECONDS = 1.0

# Setup + call + teardown seconds per test not marked `slow`. Under pytest-xdist the controller receives every
# worker's reports, so this fills in the process that prints the summary.
_unmarked_durations: dict[str, float] = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "chart_pulls: let build run pull registry Helm charts (tests/conftest.py)")
    config.addinivalue_line(
        "markers",
        "slow: takes 0.2s or more, mostly by running real git, helm, terraform or scripts; "
        'skip with -m "not slow" (tests/conftest.py)',
    )
    for name, value in _GIT_ENVIRONMENT.items():
        _saved_git_environment[name] = os.environ.get(name)
        os.environ[name] = value


def pytest_unconfigure(config):
    for name, value in _saved_git_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def pytest_runtest_logreport(report):
    if "slow" not in report.keywords:
        _unmarked_durations[report.nodeid] = _unmarked_durations.get(report.nodeid, 0.0) + report.duration


def pytest_terminal_summary(terminalreporter, config):
    """In a `-m "not slow"` run, lists the tests that took long enough to be marked `slow`."""
    if "slow" not in (config.option.markexpr or ""):
        return
    unmarked = sorted(
        ((d, nodeid) for nodeid, d in _unmarked_durations.items() if d >= _UNMARKED_SLOW_SECONDS),
        reverse=True,
    )
    if unmarked:
        terminalreporter.section("tests that could be marked slow")
        for seconds, nodeid in unmarked:
            terminalreporter.write_line(f"{seconds:6.2f}s {nodeid}")


@pytest.fixture(autouse=True)
def _no_registry_chart_pulls(request, monkeypatch):
    """`build run` leaves registry charts unpinned unless a test is marked `chart_pulls`.

    A build pulls registry charts with `helm` when it is installed (docs/design/helm-chart-deployment.md), so
    without this a test's result would depend on whether the machine running it has `helm` and network access.
    """
    if request.node.get_closest_marker("chart_pulls") is None:
        monkeypatch.setattr(workload_controller, "can_pull_charts", lambda integration: False)
