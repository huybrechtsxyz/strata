#!/usr/bin/env python3
"""Tests for `integration_readiness.py` (docs/work/integration-readiness.md D5-D8)."""

import sys

import pytest

from strata.controllers.integration_readiness import assess_integration
from strata.integrations.aikido import AikidoIntegration
from strata.integrations.base import Integration
from strata.models.integration_model import IntegrationModel


def _document(**spec) -> IntegrationModel:
    return IntegrationModel.model_validate(
        {"meta": {"name": "tool-doc"}, "spec": {"type": "widget", "capabilities": ["x-test"], **spec}}
    )


def _cli(version_output: str | None = "1.9.2", command: str = sys.executable) -> type[Integration]:
    class _Tool(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})
        COMMAND = command
        VERSION_ARGS = ("-c", f"print('{version_output}')" if version_output is not None else "raise SystemExit(1)")

    return _Tool


def _codes(readiness) -> list[str]:
    return [problem.code for problem in readiness.problems]


def test_an_available_tool_without_a_constraint_is_ready_and_not_run():
    readiness = assess_integration(_cli()(None))
    assert readiness.ok and readiness.local_tool and readiness.available
    assert readiness.installed_version is None and not readiness.declared and readiness.enabled
    assert readiness.name == "widget" and readiness.command == sys.executable


def test_a_tool_missing_from_path_keeps_the_preflight_message():
    readiness = assess_integration(_cli(command="no-such-tool-xyz")(_document(version=">= 1.0")))
    assert _codes(readiness) == ["integration_unavailable"]
    assert readiness.problems[0].message == "'no-such-tool-xyz' is not available on PATH."
    assert readiness.installed_version is None


@pytest.mark.parametrize(
    ("installed", "codes"),
    [("1.9.2", []), ("1.5.0", ["integration_version_mismatch"]), (None, ["integration_version_mismatch"])],
)
def test_the_installed_version_is_read_once_and_compared(installed, codes):
    readiness = assess_integration(_cli(installed)(_document(version="~> 1.7")))
    assert _codes(readiness) == codes
    assert readiness.installed_version == installed and readiness.required_version == "~> 1.7"
    if installed is None:
        assert "could not determine" in readiness.problems[0].message


def test_read_version_false_never_runs_the_tool():
    readiness = assess_integration(_cli("0.0.1")(_document(version=">= 1.0")), read_version=False)
    assert readiness.ok and readiness.installed_version is None


def test_a_disabled_document_is_a_problem():
    readiness = assess_integration(_cli()(_document(enabled=False)))
    assert _codes(readiness) == ["integration_disabled"]
    assert not readiness.enabled and readiness.declared


def test_an_unsupported_transport_is_a_configuration_problem():
    readiness = assess_integration(_cli()(_document(transport="http")))
    assert _codes(readiness) == ["integration_configuration_invalid"]
    assert not readiness.local_tool and readiness.available is None


def test_a_networked_integration_is_not_a_local_tool_and_cannot_pin_a_version(monkeypatch):
    document = IntegrationModel.model_validate(
        {"meta": {"name": "aikido-ci"}, "spec": {"type": "aikido", "capabilities": ["release_gate"], "version": ">= 1"}}
    )
    readiness = assess_integration(AikidoIntegration(document))
    assert not readiness.local_tool and readiness.available is None and readiness.command is None
    assert _codes(readiness) == ["integration_version_unenforceable"]
    assert readiness.connection_check
