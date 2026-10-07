#!/usr/bin/env python3
"""Tests for `checkov_controller.evaluate_checkov_policy()` (docs/work/
checkov-integration.md Phase 2/3)."""

from pathlib import Path

from strata.controllers.checkov_controller import evaluate_checkov_policy
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.errors import IntegrationError
from strata.models.checkov_model import CheckovFindingModel, CheckovPolicyModel, CheckovScanResultModel
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceResourceModel, WorkspaceSpecModel


def _provisioner(name: str, *, tool: str = "terraform") -> ProvisionerModel:
    return ProvisionerModel(name=name, tool=tool, source=SourceModel(source_path=name))


def _step(name: str, provisioner: str, *, targets: list[str] | None = None) -> ProvisioningStepModel:
    return ProvisioningStepModel(name=name, provisioner=provisioner, targets=targets or ["r1"])


def _workspace(
    policy: dict | None = None,
    *,
    provisioners: list[ProvisionerModel] | None = None,
    execution: list[ProvisioningStepModel] | None = None,
) -> WorkspaceModel:
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=provisioners or [_provisioner("tf_main")],
            execution=execution,
            resources=[
                WorkspaceResourceModel(name="r1", resource="r1"),
                WorkspaceResourceModel(name="r2", resource="r1"),
            ],
            checkov_policy=CheckovPolicyModel.model_validate(policy) if policy is not None else None,
        ),
    )


def _finding(check_id: str, severity: str) -> CheckovFindingModel:
    return CheckovFindingModel(check_id=check_id, check_name="n", resource="r", file_path="f", severity=severity)


def _scan_result(*severities: str, framework: str = "terraform") -> CheckovScanResultModel:
    findings = [_finding(f"CKV_{i}", sev) for i, sev in enumerate(severities)]
    return CheckovScanResultModel(
        scanner_version="3.2.0",
        framework=framework,
        scanned_path="path",
        findings=findings,
        failed=len(findings),
    )


class _FakeScanner:
    """Stands in for `CheckovIntegration` — records every `scan()` call's
    directory and raises/returns canned results, matching
    `test_sbom_controller.py`'s own `_FakeScanner` precedent for
    `evaluate_cve_policy()`."""

    def __init__(self, *, result: CheckovScanResultModel | None = None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.scanned_directories: list[Path] = []

    def scan(self, directory, *, framework, skip_checks, include_checks, custom_checks_dir, timeout):
        self.scanned_directories.append(directory)
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _patch_scanner(monkeypatch, scanner: _FakeScanner):
    import strata.controllers.checkov_controller as checkov_controller_module

    monkeypatch.setattr(checkov_controller_module, "CheckovIntegration", lambda: scanner)


# ---------------------------------------------------------------------------
# No-op / unset
# ---------------------------------------------------------------------------


def test_evaluate_checkov_policy_is_a_noop_when_unset(tmp_path: Path):
    workspace = _workspace(None)
    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {})
    assert diagnostics.ok
    assert len(diagnostics.items) == 0


# ---------------------------------------------------------------------------
# Severity gate / enforcement
# ---------------------------------------------------------------------------


def test_evaluate_checkov_policy_passes_below_severity_gate(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(result=_scan_result("LOW")))
    workspace = _workspace({"severity_gate": "high"}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert diagnostics.ok
    assert len(diagnostics.items) == 0


def test_evaluate_checkov_policy_denies_on_breach_by_default(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(result=_scan_result("CRITICAL")))
    workspace = _workspace({"severity_gate": "high"}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert not diagnostics.ok
    assert "checkov_policy_violation" in {d.code for d in diagnostics.errors}


def test_evaluate_checkov_policy_warns_instead_of_erroring_when_enforcement_is_warn(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(result=_scan_result("CRITICAL")))
    workspace = _workspace(
        {"severity_gate": "high", "enforcement": "warn"}, execution=[_step("apply_infra", "tf_main")]
    )

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert diagnostics.ok  # warnings don't fail Diagnostics.ok
    assert len(diagnostics.warnings) == 1


# ---------------------------------------------------------------------------
# framework: helm (Phase 3) — namespace/module resolution, not provisioners
# ---------------------------------------------------------------------------


def _module(name: str, *, chart_repository: str | None = None, type: str = "helm") -> ModuleModel:
    source = (
        SourceModel(chart_name=name, chart_repository=chart_repository)
        if chart_repository is not None
        else SourceModel(source_path=f"charts/{name}")
    )
    return ModuleModel(meta=ModuleMetaModel(name=name), spec=ModuleSpecModel(source=source, type=type))


def _namespace(name: str, *refs: ModuleReferenceModel) -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name=name), spec=NamespaceSpecModel(type="dedicated", modules=list(refs))
    )


def _index(*modules: ModuleModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in modules:
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.MODULE, name=model.meta.name),
                model=model,
                source=Path(f"{model.meta.name}.yaml"),
            )
        )
    return index


def test_evaluate_checkov_policy_helm_scans_a_local_chart_module(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    (tmp_path / "apps" / "nginx").mkdir(parents=True)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx"))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm", "severity_gate": "high"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "apps" / "nginx"]


def test_evaluate_checkov_policy_helm_denies_on_breach(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(result=_scan_result("CRITICAL", framework="helm")))
    (tmp_path / "apps" / "nginx").mkdir(parents=True)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx"))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm", "severity_gate": "high"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok
    assert "checkov_policy_violation" in {d.code for d in diagnostics.errors}
    assert "namespace 'apps' module 'nginx'" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_helm_skips_a_registry_pulled_chart_via_on_missing_data(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    namespace = _namespace("apps", ModuleReferenceModel(name="authentik", module="authentik"))
    index = _index(_module("authentik", chart_repository="https://charts.goauthentik.io"))
    workspace = _workspace({"framework": "helm"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok  # on_missing_data defaults to block
    assert "no local source to scan" in diagnostics.errors[0].message
    assert scanner.scanned_directories == []  # never even attempted


def test_evaluate_checkov_policy_helm_registry_pulled_chart_warns_when_configured(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(result=_scan_result(framework="helm")))
    namespace = _namespace("apps", ModuleReferenceModel(name="authentik", module="authentik"))
    index = _index(_module("authentik", chart_repository="https://charts.goauthentik.io"))
    workspace = _workspace({"framework": "helm", "on_missing_data": "warn"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1


def test_evaluate_checkov_policy_helm_a_disabled_module_reference_reports_missing_data(tmp_path: Path, monkeypatch):
    """Regression test: a namespace with modules in scope, but every one
    disabled, used to silently produce zero diagnostics at all — the exact
    'never scanned looks the same as scanned clean' risk on_missing_data
    exists to prevent. Must now degrade like any other nothing-to-evaluate
    case."""
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx", enabled=False))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok  # on_missing_data defaults to block
    assert "no helm modules found" in diagnostics.errors[0].message
    assert scanner.scanned_directories == []


def test_evaluate_checkov_policy_helm_all_modules_skipped_warns_when_configured(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx", enabled=False))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm", "on_missing_data": "warn"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1


def test_evaluate_checkov_policy_helm_all_modules_skipped_silent_when_configured(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx", enabled=False))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm", "on_missing_data": "skip"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert diagnostics.ok
    assert len(diagnostics.items) == 0


def test_evaluate_checkov_policy_helm_a_non_helm_module_type_reports_missing_data(tmp_path: Path, monkeypatch):
    """Same regression as the disabled-module case above, for the
    'every module in scope is a non-helm type' path."""
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    (tmp_path / "apps" / "redis").mkdir(parents=True)
    namespace = _namespace("apps", ModuleReferenceModel(name="redis", module="redis"))
    index = _index(_module("redis", type="compose"))
    workspace = _workspace({"framework": "helm"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok  # on_missing_data defaults to block
    assert "no helm modules found" in diagnostics.errors[0].message
    assert scanner.scanned_directories == []


def test_evaluate_checkov_policy_helm_not_materialised_degrades_via_on_missing_data(tmp_path: Path):
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx"))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm"})

    # No directory created at tmp_path/apps/nginx at all.
    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok
    assert "not materialised during this build" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_helm_without_namespaces_or_index_degrades_via_on_missing_data(tmp_path: Path):
    workspace = _workspace({"framework": "helm"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {})  # namespaces/index both default to None

    assert not diagnostics.ok
    assert "no namespace data available" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_helm_scope_staged_and_all_are_equivalent(tmp_path: Path, monkeypatch):
    """`scope: staged` and `scope: all` both mean "every namespace this
    workspace references" for `framework: helm` (checkov_controller.py's
    own module docstring explains why — no narrower concept exists)."""
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    (tmp_path / "apps" / "nginx").mkdir(parents=True)
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx"))
    index = _index(_module("nginx"))

    for scope in ("staged", "all"):
        scanner.scanned_directories.clear()
        workspace = _workspace({"framework": "helm", "scope": scope})
        diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)
        assert diagnostics.ok
        assert scanner.scanned_directories == [tmp_path / "apps" / "nginx"]


def test_evaluate_checkov_policy_helm_scope_namespace_name_narrows_to_one_namespace(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result(framework="helm"))
    _patch_scanner(monkeypatch, scanner)
    (tmp_path / "apps" / "nginx").mkdir(parents=True)
    (tmp_path / "infra" / "cert-manager").mkdir(parents=True)
    namespaces = {
        "apps": _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx")),
        "infra": _namespace("infra", ModuleReferenceModel(name="cert-manager", module="cert-manager")),
    }
    index = _index(_module("nginx"), _module("cert-manager"))
    workspace = _workspace({"framework": "helm", "scope": "infra"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces=namespaces, index=index)

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "infra" / "cert-manager"]


def test_evaluate_checkov_policy_helm_scope_unknown_namespace_reports_missing_data(tmp_path: Path):
    namespace = _namespace("apps", ModuleReferenceModel(name="nginx", module="nginx"))
    index = _index(_module("nginx"))
    workspace = _workspace({"framework": "helm", "scope": "no_such_namespace"})

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {}, namespaces={"apps": namespace}, index=index)

    assert not diagnostics.ok
    assert "no namespaces found for scope 'no_such_namespace'" in diagnostics.errors[0].message


# ---------------------------------------------------------------------------
# scope resolution (build-time-native — see checkov_controller.py's own
# module docstring for why this differs from v1's deployment-stage concept)
# ---------------------------------------------------------------------------


def test_evaluate_checkov_policy_scope_staged_excludes_dependency_only_provisioners(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result())
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "staged"},
        provisioners=[_provisioner("tf_main"), _provisioner("iac_components")],
        execution=[_step("apply_infra", "tf_main")],
    )

    diagnostics = evaluate_checkov_policy(
        tmp_path, workspace, {"tf_main": tmp_path / "infra", "iac_components": tmp_path / "components"}
    )

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "infra"]


def test_evaluate_checkov_policy_scope_all_includes_dependency_only_provisioners(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result())
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "all"},
        provisioners=[_provisioner("tf_main"), _provisioner("iac_components")],
        execution=[_step("apply_infra", "tf_main")],
    )

    diagnostics = evaluate_checkov_policy(
        tmp_path, workspace, {"tf_main": tmp_path / "infra", "iac_components": tmp_path / "components"}
    )

    assert diagnostics.ok
    assert set(scanner.scanned_directories) == {tmp_path / "infra", tmp_path / "components"}


def test_evaluate_checkov_policy_scope_named_step_selects_one_provisioner(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result())
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "apply_b"},
        provisioners=[_provisioner("tf_a"), _provisioner("tf_b")],
        execution=[_step("apply_a", "tf_a", targets=["r1"]), _step("apply_b", "tf_b", targets=["r2"])],
    )

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_a": tmp_path / "tf_a", "tf_b": tmp_path / "tf_b"})

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "tf_b"]


def test_evaluate_checkov_policy_no_provisioners_match_scope_reports_missing_data(tmp_path: Path):
    workspace = _workspace({"scope": "no_such_step"}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert not diagnostics.ok
    assert "no 'terraform' provisioners found for scope 'no_such_step'" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_tool_filter_excludes_non_matching_framework(tmp_path: Path, monkeypatch):
    """A provisioner whose `tool` doesn't match `framework` is never scanned,
    even under scope: all."""
    scanner = _FakeScanner(result=_scan_result())
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "all", "framework": "ansible"},
        provisioners=[_provisioner("tf_main", tool="terraform"), _provisioner("site", tool="ansible")],
        execution=[_step("apply_infra", "tf_main")],
    )

    diagnostics = evaluate_checkov_policy(
        tmp_path, workspace, {"tf_main": tmp_path / "infra", "site": tmp_path / "site"}
    )

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "site"]


def test_evaluate_checkov_policy_opentofu_matches_terraform_framework(tmp_path: Path, monkeypatch):
    scanner = _FakeScanner(result=_scan_result())
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "all"},
        provisioners=[_provisioner("tf_main", tool="opentofu")],
    )

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "infra"]


# ---------------------------------------------------------------------------
# on_missing_data — per-provisioner degradation (the "v2 design decision"
# Part 1/Part 2 above — the behavior most likely to regress)
# ---------------------------------------------------------------------------


def test_evaluate_checkov_policy_on_missing_data_blocks_by_default_for_unmaterialised_provisioner(tmp_path: Path):
    workspace = _workspace({}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {})  # tf_main never materialised

    assert not diagnostics.ok
    assert "not materialised during this build" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_on_missing_data_warns_when_configured(tmp_path: Path):
    workspace = _workspace({"on_missing_data": "warn"}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {})

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1


def test_evaluate_checkov_policy_on_missing_data_skips_silently_when_configured(tmp_path: Path):
    workspace = _workspace({"on_missing_data": "skip"}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {})

    assert len(diagnostics.items) == 0


def test_evaluate_checkov_policy_scan_failure_degrades_via_on_missing_data(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(raises=IntegrationError("checkov not found on PATH")))
    workspace = _workspace({}, execution=[_step("apply_infra", "tf_main")])

    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_main": tmp_path / "infra"})

    assert not diagnostics.ok
    assert "checkov not found on PATH" in diagnostics.errors[0].message


def test_evaluate_checkov_policy_one_unscannable_provisioner_does_not_block_a_sibling(tmp_path: Path, monkeypatch):
    """The one behavior this whole per-provisioner design exists to
    preserve from v1: a missing/unscannable provisioner must not prevent a
    sibling, scannable provisioner in the same policy from being scanned
    and reported."""
    scanner = _FakeScanner(result=_scan_result("CRITICAL"))
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace(
        {"scope": "all", "severity_gate": "high"},
        provisioners=[_provisioner("tf_a"), _provisioner("tf_b")],
    )

    # tf_a was never materialised; tf_b was, and has a real breach.
    diagnostics = evaluate_checkov_policy(tmp_path, workspace, {"tf_b": tmp_path / "tf_b"})

    assert not diagnostics.ok
    assert scanner.scanned_directories == [tmp_path / "tf_b"]
    codes = {d.code for d in diagnostics.errors}
    assert "checkov_policy_missing_data" in codes
    assert "checkov_policy_violation" in codes
