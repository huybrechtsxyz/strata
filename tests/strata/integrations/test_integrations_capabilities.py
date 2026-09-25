#!/usr/bin/env python3
"""Tests for the capability ABCs and the pairing checker (ADR-0021 D1/D9)."""

from pathlib import Path

from strata.integrations.base import Integration
from strata.integrations.capabilities import (
    CAPABILITY_ABCS,
    InfraIntegration,
    StoreIntegration,
    find_capability_mismatches,
)
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.common_models import SourceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def test_store_integration_requires_resolve():
    """A StoreIntegration subclass that doesn't implement resolve() can't be instantiated."""

    class _Incomplete(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_infra_integration_requires_all_three_methods():
    class _Incomplete(InfraIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"infrastructure"})
        TRANSPORTS = frozenset({"cli"})

        def plan(self, path, **kwargs):  # only one of three
            raise NotImplementedError

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_store_integration_can_be_instantiated_when_complete():
    class _Complete(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    assert _Complete().resolve("k") == "value"


# ---------------------------------------------------------------------------
# find_capability_mismatches — the pairing checker Phase 4's registry test
# reuses against real classes
# ---------------------------------------------------------------------------


def test_no_mismatch_for_a_compliant_store():
    class _Compliant(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets", "variables"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    assert find_capability_mismatches(_Compliant) == []


def test_no_mismatch_for_a_compliant_infra_integration():
    class _Compliant(InfraIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"infrastructure"})
        TRANSPORTS = frozenset({"cli"})

        def plan(self, path, **kwargs):
            raise NotImplementedError

        def deploy(self, path, **kwargs):
            raise NotImplementedError

        def destroy(self, path, **kwargs):
            raise NotImplementedError

    assert find_capability_mismatches(_Compliant) == []


def test_mismatch_when_a_plain_integration_declares_a_core_capability():
    """A bare Integration (no ABC) declaring 'secrets' is the exact programming
    error this function exists to catch."""

    class _Wrong(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

    mismatches = find_capability_mismatches(_Wrong)
    assert len(mismatches) == 1
    assert "secrets" in mismatches[0]
    assert "StoreIntegration" in mismatches[0]


def test_no_mismatch_for_a_capability_with_no_abc_yet():
    """'sources' has no ABC (ADR-0021 D9) — declaring it alone is not an error."""

    class _SourceOnly(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"sources"})
        TRANSPORTS = frozenset({"http"})

    assert find_capability_mismatches(_SourceOnly) == []


def test_capability_abcs_covers_every_core_capability_with_a_real_consumer():
    """variables/secrets/features -> StoreIntegration; infrastructure/container -> InfraIntegration."""
    assert CAPABILITY_ABCS == {
        "variables": StoreIntegration,
        "secrets": StoreIntegration,
        "features": StoreIntegration,
        "infrastructure": InfraIntegration,
        "container": InfraIntegration,
    }


# ---------------------------------------------------------------------------
# InfraIntegration.prepare()/default_output() (ADR-0022 D1, ADR-0023 D5)
# ---------------------------------------------------------------------------


class _Bare(InfraIntegration):
    """A minimal concrete InfraIntegration that never overrides default_output()
    — Bicep's real "copy only, generate nothing" behaviour (ADR-0023)."""

    TYPE = "widget"
    CAPABILITIES = frozenset({"infrastructure"})
    TRANSPORTS = frozenset({"cli"})

    def plan(self, path, **kwargs):
        raise NotImplementedError

    def deploy(self, path, **kwargs):
        raise NotImplementedError

    def destroy(self, path, **kwargs):
        raise NotImplementedError


def _resolved_workspace_graph() -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def _provisioner() -> ProvisionerModel:
    return ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))


def test_default_output_base_default_is_empty():
    integration = _Bare()
    result = integration.default_output(ValueResolution(deployment="d"), _provisioner(), _resolved_workspace_graph())
    assert result == {}


def test_prepare_writes_nothing_when_default_output_is_empty(tmp_path: Path):
    """Bicep's real behaviour — prepare() writes zero files when default_output() is."""
    integration = _Bare()
    result_path = integration.prepare(
        tmp_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=_provisioner(),
        graph=_resolved_workspace_graph(),
    )
    assert result_path == tmp_path
    assert list(tmp_path.iterdir()) == []


def test_prepare_writes_default_output_files(tmp_path: Path):
    class _WithOutput(_Bare):
        def default_output(self, resolved, provisioner, graph):
            return {"workspace.auto.tfvars.json": '{"name": "ws"}'}

    integration = _WithOutput()
    integration.prepare(
        tmp_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=_provisioner(),
        graph=_resolved_workspace_graph(),
    )
    written = tmp_path / "workspace.auto.tfvars.json"
    assert written.read_text() == '{"name": "ws"}'


# ---------------------------------------------------------------------------
# InfraIntegration.prepare_namespace() (ADR-0022 D6/D7)
# ---------------------------------------------------------------------------


def test_prepare_namespace_base_default_raises_for_a_class_that_does_not_override_it():
    """`_Bare` never overrides prepare_namespace() (only Helm/Compose do) -
    this is the contract every other InfraIntegration subclass gets for
    free, including a hypothetical future one that also never implements
    it. Guards the ABC's own default behaviour, independent of which
    concrete registered types currently happen to lack an override."""
    import pytest

    from strata.integrations.errors import IntegrationError
    from strata.models.common_models import ModuleReferenceModel
    from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel

    namespace = NamespaceModel(
        meta=NamespaceMetaModel(name="apps"),
        spec=NamespaceSpecModel(default_labels={}, modules=[ModuleReferenceModel(name="x", module="x")]),
    )

    with pytest.raises(IntegrationError, match="does not support namespace-scoped module rendering"):
        _Bare().prepare_namespace(namespace, [], resolved=ValueResolution(deployment="d"))


def test_every_registered_class_is_compliant():
    """The first real exercise of this checker (Phase 3 could only test it against
    fakes — `_KNOWN` didn't exist yet). Every class Phase 4 registers must comply."""
    from strata.integrations.registry import _KNOWN, get

    for integration_type in _KNOWN:
        instance = get(integration_type)
        assert find_capability_mismatches(type(instance)) == [], integration_type
