#!/usr/bin/env python3
"""Suite-wide fixtures."""

import pytest

from strata.controllers import workload_controller


def pytest_configure(config):
    config.addinivalue_line("markers", "chart_pulls: let build run pull registry Helm charts (tests/conftest.py)")


@pytest.fixture(autouse=True)
def _no_registry_chart_pulls(request, monkeypatch):
    """`build run` leaves registry charts unpinned unless a test is marked `chart_pulls`.

    A build pulls registry charts with `helm` when it is installed (docs/work/helm-registry-chart-checks.md), so
    without this a test's result would depend on whether the machine running it has `helm` and network access.
    """
    if request.node.get_closest_marker("chart_pulls") is None:
        monkeypatch.setattr(workload_controller, "can_pull_charts", lambda integration: False)
