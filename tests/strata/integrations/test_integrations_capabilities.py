#!/usr/bin/env python3
"""Tests for the capability ABCs and the pairing checker (ADR-0021 D1/D9)."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from strata.integrations.base import Integration
from strata.integrations.capabilities import (
    CAPABILITY_ABCS,
    NEW_KEY_VERSION,
    AuditSinkIntegration,
    ConnectionCheckIntegration,
    CredentialedIntegration,
    ImageFindingsIntegration,
    InfraIntegration,
    KeyValueStoreIntegration,
    ReleaseGateIntegration,
    ScannerIntegration,
    StoreIntegration,
    find_capability_mismatches,
)
from strata.integrations.resolved_context import KeyValueEntry, ResolvedWorkspaceGraph, ValueResolution
from strata.integrations.security_findings import ImageLookup, ImageRef, ReleaseGateOutcome, ReleaseGateRequest
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
# StoreIntegration.set()/.metadata() — base-implemented, not abstract (D3):
# a subclass that never overrides either still instantiates fine, but both
# calls raise a clear IntegrationError rather than silently no-op'ing.
# ---------------------------------------------------------------------------


def test_store_integration_set_base_default_raises_integration_error():
    class _ReadOnly(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    import pytest

    from strata.integrations.errors import IntegrationError

    with pytest.raises(IntegrationError, match="does not support writes"):
        _ReadOnly().set("k", "v")


def test_store_integration_metadata_base_default_raises_integration_error():
    class _ReadOnly(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    import pytest

    from strata.integrations.errors import IntegrationError

    with pytest.raises(IntegrationError, match="does not report secret metadata"):
        _ReadOnly().metadata("k")


def test_store_integration_can_override_set_and_metadata():
    from strata.integrations.resolved_context import SecretMetadata

    class _Writable(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

        def set(self, key: str, value: str) -> None:
            self.written = (key, value)  # type: ignore[attr-defined]

        def metadata(self, key: str) -> SecretMetadata | None:
            return SecretMetadata()

    instance = _Writable()
    instance.set("k", "v")
    assert instance.written == ("k", "v")  # type: ignore[attr-defined]
    assert instance.metadata("k") == SecretMetadata()


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
    """variables/secrets/features -> StoreIntegration; infrastructure/container ->
    InfraIntegration; audit -> AuditSinkIntegration; scanner -> ScannerIntegration;
    key_value -> KeyValueStoreIntegration; release_gate -> ReleaseGateIntegration;
    image_findings -> ImageFindingsIntegration."""
    assert CAPABILITY_ABCS == {
        "variables": StoreIntegration,
        "secrets": StoreIntegration,
        "features": StoreIntegration,
        "infrastructure": InfraIntegration,
        "container": InfraIntegration,
        "audit": AuditSinkIntegration,
        "scanner": ScannerIntegration,
        "key_value": KeyValueStoreIntegration,
        "release_gate": ReleaseGateIntegration,
        "image_findings": ImageFindingsIntegration,
    }


# ---------------------------------------------------------------------------
# KeyValueStoreIntegration (docs/work/key-value-store-integration.md) —
# get/put-with-CAS, modeled alongside StoreIntegration (not merged into it,
# shape mismatch: single-value resolve vs. versioned get/put).
# ---------------------------------------------------------------------------


def test_keyvalue_store_integration_requires_both_methods():
    class _Incomplete(KeyValueStoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

        def get(self, key: str) -> KeyValueEntry | None:
            return None

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_keyvalue_store_integration_can_be_instantiated_when_complete():
    class _Complete(KeyValueStoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

        def __init__(self):
            self._store: dict[str, bytes] = {}

        def get(self, key: str) -> KeyValueEntry | None:
            if key not in self._store:
                return None
            return KeyValueEntry(value=self._store[key], version="1")

        def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
            self._store[key] = value
            return "1"

    instance = _Complete()
    assert instance.get("k") is None
    assert instance.put("k", b"v") == "1"
    assert instance.get("k") == KeyValueEntry(value=b"v", version="1")


def test_new_key_version_sentinel_is_the_zero_string():
    """Matches Consul's/etcd's own documented CAS convention for 'does not exist yet'."""
    assert NEW_KEY_VERSION == "0"


def test_supports_cas_defaults_to_true():
    """Every real backend except one (AzureKeyVaultResolver) supports CAS
    natively — a class that doesn't override this attribute is assumed to."""

    class _Complete(KeyValueStoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

        def get(self, key: str) -> KeyValueEntry | None:
            return None

        def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
            return "1"

    assert _Complete.SUPPORTS_CAS is True


def test_no_mismatch_for_a_compliant_keyvalue_store():
    class _Compliant(KeyValueStoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

        def get(self, key: str) -> KeyValueEntry | None:
            return None

        def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
            return "1"

    assert find_capability_mismatches(_Compliant) == []


def test_mismatch_when_a_plain_integration_declares_key_value_capability():
    class _Wrong(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

    mismatches = find_capability_mismatches(_Wrong)
    assert len(mismatches) == 1
    assert "key_value" in mismatches[0]
    assert "KeyValueStoreIntegration" in mismatches[0]


def test_key_value_entry_is_frozen_and_value_equal():
    assert KeyValueEntry(value=b"v", version="1") == KeyValueEntry(value=b"v", version="1")
    assert KeyValueEntry(value=b"v", version="1") != KeyValueEntry(value=b"v", version="2")

    import pytest

    with pytest.raises(FrozenInstanceError):
        KeyValueEntry(value=b"v", version="1").version = "2"  # type: ignore[misc]


def test_key_value_conflict_error_is_an_integration_error():
    """Subclass, not a sibling — every existing 'except IntegrationError' call
    site still catches it unchanged (docs/work/key-value-store-integration.md)."""
    from strata.integrations.errors import IntegrationError, KeyValueConflictError

    assert issubclass(KeyValueConflictError, IntegrationError)


# ---------------------------------------------------------------------------
# ScannerIntegration (Capability.SCANNER) — deliberately thin: only
# ensure_available() is abstract, no shared scan() signature (docs/archive/
# cve-scanner-integration.md, docs/archive/checkov-integration.md).
# ---------------------------------------------------------------------------


def test_scanner_integration_requires_ensure_available():
    class _Incomplete(ScannerIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"scanner"})
        TRANSPORTS = frozenset({"cli"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_scanner_integration_can_be_instantiated_when_complete():
    class _Complete(ScannerIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"scanner"})
        TRANSPORTS = frozenset({"cli"})

        def ensure_available(self) -> None:
            pass

    _Complete().ensure_available()  # does not raise


def test_no_mismatch_for_a_compliant_scanner_integration():
    class _Compliant(ScannerIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"scanner"})
        TRANSPORTS = frozenset({"cli"})

        def ensure_available(self) -> None:
            pass

    assert find_capability_mismatches(_Compliant) == []


# ---------------------------------------------------------------------------
# Security gate capabilities (docs/work/security-gates-design.md D1, D2, D7)
# ---------------------------------------------------------------------------


class _BothGates(ReleaseGateIntegration, ImageFindingsIntegration, CredentialedIntegration, ConnectionCheckIntegration):
    """One class serving both capabilities plus both mixins, as Aikido will."""

    TYPE = "widget"
    CAPABILITIES = frozenset({"release_gate", "image_findings"})
    TRANSPORTS = frozenset({"http"})

    def run_release_gate(self, request, *, on_poll=None):
        return ReleaseGateOutcome(passed=True, reference=request.commit[:7])

    def find_image_issues(self, image, severities):
        return ImageLookup(status="found", platform_name=image.path)

    def check_connection(self):
        return ["ok"]


def test_release_gate_integration_requires_run_release_gate():
    class _Incomplete(ReleaseGateIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"release_gate"})
        TRANSPORTS = frozenset({"http"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_image_findings_integration_requires_find_image_issues():
    class _Incomplete(ImageFindingsIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"image_findings"})
        TRANSPORTS = frozenset({"http"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_connection_check_integration_requires_check_connection():
    class _Incomplete(ConnectionCheckIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"http"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_one_class_can_serve_both_gates_and_constructs_with_only_config():
    """No ABC or mixin defines __init__, so the registry's `cls(config)` keeps working."""
    integration = _BothGates(None)

    request = ReleaseGateRequest(repository="org/repo", commit="a" * 40, timeout=900, poll_interval=10)
    assert integration.run_release_gate(request).passed is True
    image = ImageRef(service="api", registry="ghcr.io", repository="org/api", tag="1.0")
    assert integration.find_image_issues(image, frozenset({"HIGH"})).platform_name == "ghcr.io/org/api"
    assert integration.check_connection() == ["ok"]
    assert find_capability_mismatches(_BothGates) == []


def test_bind_credentials_default_is_a_no_op():
    _BothGates(None).bind_credentials({"AIKIDO_CI_TOKEN": "value"})  # does not raise


@pytest.mark.parametrize("capability", ["release_gate", "image_findings"])
def test_mismatch_when_a_plain_integration_declares_a_gate_capability(capability):
    class _Wrong(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({capability})
        TRANSPORTS = frozenset({"http"})

    mismatches = find_capability_mismatches(_Wrong)
    assert len(mismatches) == 1
    assert capability in mismatches[0]


def test_release_gate_alone_does_not_satisfy_image_findings():
    class _ReleaseOnly(ReleaseGateIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"release_gate", "image_findings"})
        TRANSPORTS = frozenset({"http"})

        def run_release_gate(self, request, *, on_poll=None):
            return ReleaseGateOutcome(passed=True)

    mismatches = find_capability_mismatches(_ReleaseOnly)
    assert mismatches == ["_ReleaseOnly declares 'image_findings' but does not implement ImageFindingsIntegration"]


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


def test_default_output_base_default_is_empty(tmp_path: Path):
    integration = _Bare()
    result = integration.default_output(
        tmp_path, ValueResolution(deployment="d"), _provisioner(), _resolved_workspace_graph()
    )
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
        def default_output(self, path, resolved, provisioner, graph):
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
        def default_output(self, path, resolved, provisioner, graph):
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
    `registry.get_class()` first. Iterating `registry._KNOWN` rather
    than hardcoding today's three names means a future registration is
    force-loaded and checked automatically too.
    """
    from strata.integrations import registry

    for known_type in registry._KNOWN:
        registry.get_class(known_type)

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

    Passes today against the real registered integrations (Terraform via
    ENV_VAR_PREFIX, Helm/Compose via Capability.CONTAINER) — a pure safety
    net, zero behaviour change.
    """
    #: GitOps (docs/design/gitops-integration.md Phase 3) is the one real,
    #: deliberate exception: `BaseGitOpsIntegration.default_output()` raises
    #: unless `provisioner.output.template` is set (there is no default
    #: values/config projection for a git push), which makes `output.template`
    #: itself — not ENV_VAR_PREFIX, not Capability.CONTAINER — this tool's
    #: *only* resolved-value delivery mechanism, via the same Jinja context
    #: (`variables`/`flags`/`secrets`/`properties`/`custom`/`graph`)
    #: `render_output_template()` already builds for Terraform's own
    #: `output.template` escape hatch (ADR-0023 D3 — tool-agnostic by design).
    gitops_exception = {"BaseGitOpsIntegration", "ArgoCDIntegration", "FluxIntegration"}

    #: ScriptIntegration (docs/design/script-provisioner.md, shipped)
    #: has a real mechanism — a `STRATA_VALUES` env var carrying the whole
    #: resolved `resolved_values` map, built in `deploy()`/`plan()` — but
    #: it's neither of the two this test guards for: `ENV_VAR_PREFIX` is a
    #: one-env-var-*per-key* convention reserved for a tool's own *native*
    #: contract (Terraform's real `TF_VAR_<name>`, which script has none
    #: of); `STRATA_VALUES` is one whole-map env var instead, deliberately
    #: a third pattern, not a gap. Permanent exception, not "remove once
    #: shipped" — nothing about Phase 4 landing changes which of these two
    #: specific mechanisms applies.
    script_exception = {"ScriptIntegration"}

    checked = _all_infra_integration_types()
    assert checked, "No real InfraIntegration subclasses found — the walk itself is broken."

    for cls in checked:
        if cls.__name__ in gitops_exception or cls.__name__ in script_exception:
            continue
        has_env_var_delivery = cls.ENV_VAR_PREFIX is not None
        has_container_delivery = Capability.CONTAINER in cls.CAPABILITIES
        assert has_env_var_delivery or has_container_delivery, (
            f"{cls.__module__}.{cls.__name__} declares neither ENV_VAR_PREFIX nor "
            "Capability.CONTAINER — how does it receive resolved var/secret/feature/"
            "output values? If genuinely no mechanism applies yet, this test needs an "
            "explicit, commented exception — not a silent pass."
        )


def test_every_infra_integration_type_is_a_known_real_integration():
    """Names the integrations this guard currently covers explicitly, so
    adding a new one is visible in a diff here too, not just implicitly
    covered by the generic walk above. `BaseGitOpsIntegration` is itself a
    real, shared base (not a test fixture — those are already excluded by
    module, see `_all_infra_integration_types()`'s own docstring) providing
    real `plan`/`deploy`/`destroy`/`output` behaviour both `ArgoCDIntegration`/
    `FluxIntegration` inherit unchanged."""
    names = {cls.__name__ for cls in _all_infra_integration_types()}
    assert names == {
        "TerraformIntegration",
        "HelmIntegration",
        "ComposeIntegration",
        "ScriptIntegration",
        "BaseGitOpsIntegration",
        "ArgoCDIntegration",
        "FluxIntegration",
    }


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


# ---------------------------------------------------------------------------
# InfraIntegration.plan_or_warn() — graceful degradation: "preview if the
# tool supports it, otherwise not".
# ---------------------------------------------------------------------------


def test_plan_or_warn_returns_a_successful_plan_result_unchanged():
    from strata.utils.diagnostics import Diagnostics
    from strata.utils.transport import CommandResult

    class _Planner(_Bare):
        def plan(self, path, **kwargs):
            return CommandResult(returncode=0, stdout="no changes", stderr="")

    diagnostics = Diagnostics()
    result = _Planner().plan_or_warn(Path("."), diagnostics=diagnostics, location="step1")

    assert result is not None
    assert result.is_successful
    assert result.stdout == "no changes"
    assert diagnostics.warnings == []


def test_plan_or_warn_returns_a_failed_plan_result_for_the_caller_to_report():
    """A `plan()` that runs but fails for a real, tool-specific reason stays
    a real result — `plan_or_warn()` itself never turns that into a
    warning or raises; the caller still decides severity (a hard error,
    matching a real plan() failure on the apply path)."""
    from strata.utils.diagnostics import Diagnostics
    from strata.utils.transport import CommandResult

    class _Planner(_Bare):
        def plan(self, path, **kwargs):
            return CommandResult(returncode=1, stdout="", stderr="invalid HCL")

    diagnostics = Diagnostics()
    result = _Planner().plan_or_warn(Path("."), diagnostics=diagnostics, location="step1")

    assert result is not None
    assert not result.is_successful
    assert result.stderr == "invalid HCL"
    assert diagnostics.warnings == []  # not plan_or_warn()'s job to report this


def test_plan_or_warn_turns_integration_error_into_a_warning_not_a_raise():
    from strata.integrations.errors import IntegrationError
    from strata.utils.diagnostics import Diagnostics

    class _NoPreview(_Bare):
        def plan(self, path, **kwargs):
            raise IntegrationError("no preview mechanism for this tool")

    diagnostics = Diagnostics()
    result = _NoPreview().plan_or_warn(Path("."), diagnostics=diagnostics, location="step1")

    assert result is None
    assert len(diagnostics.warnings) == 1
    warning = diagnostics.warnings[0]
    assert "no plan preview available" in warning.message
    assert "no preview mechanism for this tool" in warning.message
    assert warning.location == "step1"


def test_plan_or_warn_forwards_kwargs_to_plan():
    from strata.utils.diagnostics import Diagnostics
    from strata.utils.transport import CommandResult

    class _Planner(_Bare):
        def plan(self, path, **kwargs):
            self.received = kwargs  # type: ignore[attr-defined]
            return CommandResult(returncode=0, stdout="", stderr="")

    integration = _Planner()
    integration.plan_or_warn(
        Path("."), diagnostics=Diagnostics(), location="step1", out_file="step1.tfplan", env={"K": "V"}
    )

    assert integration.received == {"out_file": "step1.tfplan", "env": {"K": "V"}}  # type: ignore[attr-defined]
