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
from strata.models.integration_model import Capability
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
# InfraIntegration.prepare() with output.template (ADR-0023 D3, docs/design/
# value-token-resolution.md option C) — validated only, never rendered.
# ---------------------------------------------------------------------------


def _graph_with_refs() -> ResolvedWorkspaceGraph:
    from strata.integrations.resolved_context import ValueReference

    workspace = _resolved_workspace_graph().workspace
    return ResolvedWorkspaceGraph(
        workspace=workspace,
        variable_refs=[ValueReference(key="REGION", store="constant", value="westeurope")],
    )


def _provisioner_with_template(template: str) -> ProvisionerModel:
    from strata.models.provisioning_model import OutputModel

    return ProvisionerModel(
        name="prov", tool="terraform", source=SourceModel(source_path="infra"), output=OutputModel(template=template)
    )


def test_prepare_writes_nothing_when_output_template_is_set_and_valid(tmp_path: Path):
    template_path = tmp_path / "variables.json.j2"
    template_path.write_text('{"region": "{{ variables.REGION }}"}')

    integration = _Bare()
    result_path = integration.prepare(
        tmp_path / "out",
        resolved=ValueResolution(deployment="d"),
        provisioner=_provisioner_with_template("variables.json.j2"),
        graph=_graph_with_refs(),
        template_path=template_path,
    )
    assert result_path == tmp_path / "out"


def test_prepare_raises_integration_error_for_an_unknown_reference(tmp_path: Path):
    import pytest

    from strata.integrations.errors import IntegrationError

    template_path = tmp_path / "variables.json.j2"
    template_path.write_text('{"region": "{{ variables.REGOIN }}"}')

    integration = _Bare()
    with pytest.raises(IntegrationError, match="variables.REGOIN"):
        integration.prepare(
            tmp_path / "out",
            resolved=ValueResolution(deployment="d"),
            provisioner=_provisioner_with_template("variables.json.j2"),
            graph=_graph_with_refs(),
            template_path=template_path,
        )


def test_prepare_output_template_skips_default_output_entirely(tmp_path: Path):
    """When output.template is set, default_output() is never even called —
    the two are mutually exclusive, not layered."""

    class _WithOutput(_Bare):
        def default_output(self, resolved, provisioner, graph):
            raise AssertionError("default_output() should not be called when output.template is set")

    template_path = tmp_path / "variables.json.j2"
    template_path.write_text('{"region": "{{ variables.REGION }}"}')

    integration = _WithOutput()
    integration.prepare(
        tmp_path / "out",
        resolved=ValueResolution(deployment="d"),
        provisioner=_provisioner_with_template("variables.json.j2"),
        graph=_graph_with_refs(),
        template_path=template_path,
    )


# ---------------------------------------------------------------------------
# InfraIntegration.render_output_template() (docs/design/deploy-command.md
# Implementation Plan phase 8) — the deploy-time actual-render counterpart
# to prepare()'s build-time validate-only branch above.
# ---------------------------------------------------------------------------


def test_render_output_template_writes_the_rendered_file_stripping_j2(tmp_path: Path):
    template_path = tmp_path / "variables.json.j2"
    template_path.write_text('{"region": "{{ variables.REGION }}"}')
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    integration = _Bare()
    output_path = integration.render_output_template(
        out_dir,
        resolved=ValueResolution(deployment="d", values={"REGION": "westeurope"}),
        provisioner=_provisioner_with_template("variables.json.j2"),
        graph=_graph_with_refs(),
        template_path=template_path,
    )

    assert output_path == out_dir / "variables.json"
    assert output_path.read_text() == '{"region": "westeurope"}'


def test_render_output_template_uses_resolved_values_not_ref_value(tmp_path: Path):
    """`graph.variable_refs[].value` stays build-time-safe-only (None here,
    matching `build_value_references()`'s real restriction) — the render
    must come from `resolved.values`, never from the ref's own `.value`."""
    template_path = tmp_path / "variables.json.j2"
    template_path.write_text('{"region": "{{ variables.REGION }}"}')
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    integration = _Bare()
    output_path = integration.render_output_template(
        out_dir,
        resolved=ValueResolution(deployment="d", values={"REGION": "westeurope"}),
        provisioner=_provisioner_with_template("variables.json.j2"),
        graph=_graph_with_refs(),  # its ValueReference(key="REGION", value="westeurope") is unused for the render
        template_path=template_path,
    )

    assert output_path.read_text() == '{"region": "westeurope"}'


# ---------------------------------------------------------------------------
# Guardrail: every real InfraIntegration must declare a resolved-value
# delivery mechanism (docs/design/value-token-resolution.md's "Guardrail:
# prevent this regressing on the tool axis too" section, Phase 0 of
# docs/design/cross-document-value-references.md's Implementation Plan).
#
# Mirrors tests/strata/commands/test_commands_exit_codes.py's
# _all_error_types()/test_every_error_type_is_mapped() shape exactly: walk
# every subclass recursively, fail if any of them is unaccounted for.
# ---------------------------------------------------------------------------


def _all_infra_integration_types() -> list[type]:
    """Every InfraIntegration subclass strata itself registers as a real
    integration, recursively — test-only fixture subclasses (this file's
    own `_Bare`/`_Compliant`/`_Incomplete`/`_WithOutput`, and any future
    ones anywhere else in the test suite) are deliberately excluded by
    module: they are incomplete-by-design test doubles (this file's own
    `_Bare` docstring literally models "Bicep's real... behaviour" as a
    minimal stand-in), never a real integration strata dispatches a
    resolved value to. Filtering by `__module__` rather than by name
    means a *future* test fixture is excluded automatically too, with no
    per-class allowlist to maintain.

    Registered classes are lazily imported (`registry.py`'s own module
    docstring: "the only thing standing between 'add a Vault integration'
    and every strata invocation hard-depending on hvac") — a class that
    was never imported never registers as a subclass at all, so every
    known type is force-loaded (class only, never instantiated) via
    `registry._resolve_class()` first. Iterating `registry._KNOWN` rather
    than hardcoding today's three names means a future registration is
    force-loaded and checked automatically too.
    """
    from strata.integrations import registry

    for known_type in registry._KNOWN:
        registry._resolve_class(known_type)

    found: list[type] = []
    stack: list[type] = list(InfraIntegration.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls.__module__.startswith("strata."):
            found.append(cls)
        stack.extend(cls.__subclasses__())
    return found


def test_every_infra_integration_declares_a_resolved_value_delivery_mechanism():
    """A new tool (e.g. a future BicepIntegration — ProvisionerType.BICEP
    already exists in builtin_types.py, anticipated in registry.py's own
    _KNOWN_V1_TYPES comment) that ships with neither ENV_VAR_PREFIX nor
    Capability.CONTAINER declared would silently never receive a resolved
    var/secret/feature/output value at all — this fails CI the moment that
    happens, forcing the delivery-mechanism decision at review time
    instead of discovering it in production the first time someone's real
    deployment gets a literal '${var:X}' string instead of the resolved
    value.

    Passes today against the three real registered integrations
    (Terraform via ENV_VAR_PREFIX, Helm/Compose via Capability.CONTAINER) —
    a pure safety net, zero behaviour change.
    """
    checked = _all_infra_integration_types()
    assert checked, "No real InfraIntegration subclasses found — the walk itself is broken."

    for cls in checked:
        has_env_var_delivery = cls.ENV_VAR_PREFIX is not None
        has_container_delivery = Capability.CONTAINER in cls.CAPABILITIES
        assert has_env_var_delivery or has_container_delivery, (
            f"{cls.__module__}.{cls.__name__} declares neither ENV_VAR_PREFIX nor "
            "Capability.CONTAINER — how does it receive resolved var/secret/feature/"
            "output values? If genuinely no mechanism applies yet, this test needs an "
            "explicit, commented exception — not a silent pass."
        )


def test_every_infra_integration_type_is_a_known_real_integration():
    """Names the three integrations this guard currently covers explicitly,
    so adding a fourth real one is visible in a diff here too, not just
    implicitly covered by the generic walk above."""
    names = {cls.__name__ for cls in _all_infra_integration_types()}
    assert names == {"TerraformIntegration", "HelmIntegration", "ComposeIntegration"}


def test_render_output_template_raises_on_unresolvable_reference(tmp_path: Path):
    """A dynamic/unresolved reference `validate_template_references()`
    couldn't check statically still fails, loudly, at render time."""
    import jinja2
    import pytest

    template_path = tmp_path / "variables.json.j2"
    template_path.write_text("{{ graph.workspace.meta.ghost_field }}")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    integration = _Bare()
    with pytest.raises(jinja2.TemplateError):
        integration.render_output_template(
            out_dir,
            resolved=ValueResolution(deployment="d"),
            provisioner=_provisioner_with_template("variables.json.j2"),
            graph=_graph_with_refs(),
            template_path=template_path,
        )


def test_render_output_template_leaves_non_j2_filenames_unchanged(tmp_path: Path):
    template_path = tmp_path / "variables.json"
    template_path.write_text('{"region": "{{ variables.REGION }}"}')
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    integration = _Bare()
    output_path = integration.render_output_template(
        out_dir,
        resolved=ValueResolution(deployment="d", values={"REGION": "westeurope"}),
        provisioner=_provisioner_with_template("variables.json"),
        graph=_graph_with_refs(),
        template_path=template_path,
    )

    assert output_path == out_dir / "variables.json"


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
