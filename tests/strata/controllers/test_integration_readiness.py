#!/usr/bin/env python3
"""Tests for `integration_readiness.py` (docs/work/integration-readiness.md D5-D18, R1-R11)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.controllers.integration_readiness import (
    assess_document,
    assess_integration,
    assess_named,
    integration_preflight,
    list_integrations,
    readiness_diagnostics,
)
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.aikido import AikidoIntegration
from strata.integrations.base import Integration
from strata.models.common_models import PlatformKind
from strata.models.integration_model import IntegrationModel
from strata.utils.errors import UsageError


def _doc(name: str, integration_type: str, capabilities: list[str], **spec) -> IntegrationModel:
    return IntegrationModel.model_validate(
        {"meta": {"name": name}, "spec": {"type": integration_type, "capabilities": capabilities, **spec}}
    )


def _index(*documents: IntegrationModel) -> DocumentIndex:
    index = DocumentIndex()
    for document in documents:
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.INTEGRATION, name=document.meta.name),
                model=document,
                source=Path(f"{document.meta.name}.yaml"),
            )
        )
    return index


def test_an_unregistered_sources_only_document_is_a_credential_holder():
    readiness = assess_document(_doc("forge", "forgejo", ["sources"])).readiness
    assert readiness.ok and readiness.credential_holder and not readiness.registered


def test_a_disabled_credential_holder_is_reported_disabled_not_as_a_holder():
    readiness = assess_document(_doc("forge", "forgejo", ["sources"], enabled=False)).readiness
    assert [p.code for p in readiness.problems] == ["integration_disabled"]
    assert not readiness.credential_holder


def test_an_unregistered_document_with_other_capabilities_is_a_problem():
    readiness = assess_document(_doc("old", "ansible", ["infrastructure"])).readiness
    assert [p.code for p in readiness.problems] == ["integration_type_unregistered"]
    assert not readiness.registered


def test_a_registered_type_rejecting_its_configuration_is_still_registered():
    subject = assess_document(_doc("aikido-ci", "aikido", ["release_gate"], configuration={"nope": 1}))
    assert subject.integration is None
    assert subject.readiness.registered
    assert [p.code for p in subject.readiness.problems] == ["integration_configuration_invalid"]


def test_a_git_document_is_the_git_tool():
    subject = assess_document(_doc("git-creds", "git", ["sources"]), read_version=False)
    assert subject.readiness.registered and subject.readiness.local_tool
    assert subject.readiness.command == "git"


def test_assess_named_prefers_the_document_then_the_type_default():
    index = _index(_doc("tf", "terraform", ["infrastructure"]))
    assert assess_named(index, "tf", read_version=False).readiness.declared
    helm = assess_named(index, "helm", read_version=False).readiness
    assert helm.name == "helm" and not helm.declared


def test_assess_named_by_type_binds_the_single_enabled_document():
    index = _index(_doc("tf", "terraform", ["infrastructure"]))
    assert assess_named(index, "terraform", read_version=False).readiness.name == "tf"


def test_assess_named_refuses_an_ambiguous_type_and_an_unknown_name():
    index = _index(_doc("a", "terraform", ["infrastructure"]), _doc("b", "terraform", ["infrastructure"]))
    with pytest.raises(UsageError, match=r"Several enabled 'terraform' Integration documents \(a, b\)"):
        assess_named(index, "terraform")
    with pytest.raises(UsageError, match="not a known integration type"):
        assess_named(index, "nope")


def _readiness(*, required: bool = False, enabled: bool = True):
    return assess_document(_doc("tool", "forgejo", ["infrastructure"], required=required, enabled=enabled)).readiness


@pytest.mark.parametrize(
    ("required", "used", "severity"),
    [(True, False, "error"), (False, True, "error"), (False, False, "warning")],
)
def test_readiness_diagnostics_severity_follows_required_and_used(required, used, severity):
    diagnostics = readiness_diagnostics(_readiness(required=required), used=used)
    [finding] = list(diagnostics)
    assert finding.severity.value == severity and finding.code == "integration_type_unregistered"
    assert ("(required)" in finding.message) is required


def test_a_disabled_integration_nothing_uses_reports_nothing():
    readiness = _readiness(enabled=False, required=True)
    assert list(readiness_diagnostics(readiness)) == []
    assert readiness_diagnostics(readiness, used=True).errors


def test_integration_preflight_skips_disabled_and_excluded_documents():
    index = _index(
        _doc("broken-required", "forgejo", ["infrastructure"], required=True),
        _doc("broken-optional", "forgejo", ["infrastructure"]),
        _doc("broken-disabled", "forgejo", ["infrastructure"], required=True, enabled=False),
        _doc("broken-excluded", "forgejo", ["infrastructure"], required=True),
    )
    diagnostics = integration_preflight(index, exclude=frozenset({"broken-excluded"}))
    assert [d.location for d in diagnostics.errors] == ["broken-required"]
    assert [d.location for d in diagnostics.warnings] == ["broken-optional"]


def test_list_integrations_without_a_deployment_lists_every_document_sorted():
    context = SimpleNamespace(
        controller=SimpleNamespace(
            index=_index(_doc("z", "forgejo", ["sources"]), _doc("a", "aikido", ["release_gate"]))
        )
    )
    rows = list_integrations(context)
    assert [row.readiness.name for row in rows] == ["a", "z"]
    assert all(row.used_for == () for row in rows)


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
