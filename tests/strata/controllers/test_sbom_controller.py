#!/usr/bin/env python3
"""Tests for `sbom_controller.write_sbom()` (docs/design/sbom-generation.md
Phase 1)."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from strata.controllers.sbom_controller import evaluate_cve_policy, write_sbom
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.configuration_model import ConfigurationMetaModel, ConfigurationModel, ConfigurationSpecModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.sbom_model import (
    CveAllowedEntryModel,
    CveAuditResultModel,
    CveFindingModel,
    CveMaxSeverityPolicyModel,
)
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def _module(name: str, *, services: list[ModuleServiceModel] | None = None) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"services/{name}"),
            type="compose",
            services=services if services is not None else [ModuleServiceModel(name=name, image=f"{name}:1.0")],
            default_labels={},
        ),
    )


def _index(*modules: ModuleModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in modules:
        ref = DocumentRef(kind=PlatformKind.MODULE, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


def _namespace(*refs: ModuleReferenceModel) -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name="apps"),
        spec=NamespaceSpecModel(type="dedicated", default_labels={}, modules=list(refs)),
    )


def _graph(*namespaces: NamespaceModel) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(
        workspace=workspace, namespaces={namespace.meta.name: namespace for namespace in namespaces}
    )


def test_write_sbom_writes_a_valid_cyclonedx_file(tmp_path: Path):
    module = _module("redis")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="redis", module="redis"))
    graph = _graph(namespace)

    diagnostics = write_sbom(tmp_path, graph, index)

    assert diagnostics.ok
    sbom_path = tmp_path / "sbom.json"
    assert sbom_path.is_file()

    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.6"
    names = {c["name"] for c in data["components"]}
    assert "redis" in names


def test_write_sbom_skips_a_disabled_module_reference(tmp_path: Path):
    module = _module("redis")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="redis", module="redis", enabled=False))
    graph = _graph(namespace)

    write_sbom(tmp_path, graph, index)

    data = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    assert data.get("components", []) == []


def test_write_sbom_merges_warnings_from_every_collector(tmp_path: Path):
    module = _module("app", services=[ModuleServiceModel(name="app", image="acme/app:latest")])
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="app", module="app"))
    graph = _graph(namespace)

    diagnostics = write_sbom(tmp_path, graph, index)

    assert diagnostics.ok  # a floating tag is a warning, never an error
    assert len(diagnostics.warnings) >= 1


def test_write_sbom_with_no_namespaces_writes_an_empty_bom(tmp_path: Path):
    graph = _graph()
    diagnostics = write_sbom(tmp_path, graph, _index())

    assert diagnostics.ok
    data = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    assert data.get("components", []) == []


def test_write_sbom_raises_when_the_bom_fails_schema_validation(tmp_path: Path, monkeypatch):
    import strata.controllers.sbom_controller as sbom_controller_module

    monkeypatch.setattr(sbom_controller_module, "_build_cyclonedx_json", lambda components: "{}")

    with pytest.raises(RuntimeError, match="failed CycloneDX schema validation"):
        write_sbom(tmp_path, _graph(), _index())


# ---------------------------------------------------------------------------
# evaluate_cve_policy (docs/work/cve-scanner-integration.md)
# ---------------------------------------------------------------------------


def _workspace_with_cve_policy(policy: dict) -> WorkspaceModel:
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
            cve_policy=CveMaxSeverityPolicyModel.model_validate(policy),
        ),
    )


class _FakeScanner:
    def __init__(self, *, available: bool, result: CveAuditResultModel | None = None, raises: Exception | None = None):
        self._available = available
        self._result = result
        self._raises = raises
        self.received_threshold: str | None = None

    def is_available(self) -> bool:
        return self._available

    def scan_sbom(self, sbom_path: Path, *, severity_threshold: str) -> CveAuditResultModel:
        self.received_threshold = severity_threshold
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _patch_scanner(monkeypatch, scanner: _FakeScanner):
    import strata.controllers.sbom_controller as sbom_controller_module

    monkeypatch.setattr(sbom_controller_module, "CveScannerIntegration", lambda: scanner)


def _index_with_configuration(cve_allowed: list[CveAllowedEntryModel]) -> DocumentIndex:
    configuration = ConfigurationModel(
        meta=ConfigurationMetaModel(name="config"), spec=ConfigurationSpecModel(cve_allowed=cve_allowed)
    )
    index = DocumentIndex()
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.CONFIGURATION, name=configuration.meta.name),
            model=configuration,
            source=Path("configuration.yaml"),
        )
    )
    return index


def test_evaluate_cve_policy_is_a_noop_when_unset(tmp_path: Path):
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())
    assert diagnostics.ok
    assert len(diagnostics.items) == 0


def test_evaluate_cve_policy_blocks_on_missing_scanner_by_default(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(available=False))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert not diagnostics.ok
    assert "no CVE scanner" in diagnostics.errors[0].message


def test_evaluate_cve_policy_warns_on_missing_scanner_when_configured(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(available=False))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "on_missing_data": "warn"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert diagnostics.ok  # warnings don't fail Diagnostics.ok
    assert len(diagnostics.warnings) == 1


def test_evaluate_cve_policy_skips_silently_when_configured(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(available=False))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "on_missing_data": "skip"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert len(diagnostics.items) == 0


def test_evaluate_cve_policy_blocks_when_no_sbom_is_written(tmp_path: Path, monkeypatch):
    _patch_scanner(monkeypatch, _FakeScanner(available=True))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())  # no sbom.json written to tmp_path

    assert not diagnostics.ok
    assert "no SBOM found" in diagnostics.errors[0].message


def test_evaluate_cve_policy_passes_below_threshold(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path=str(tmp_path / "sbom.json"), total_findings=1, low=1
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert diagnostics.ok
    assert len(diagnostics.items) == 0


def test_evaluate_cve_policy_denies_when_breaching_count_exceeds_max(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=2,
        critical=1,
        high=1,
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert not diagnostics.ok
    assert "cve_policy_violation" in {d.code for d in diagnostics.errors}


def test_evaluate_cve_policy_warns_instead_of_erroring_when_enforcement_is_warn(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path=str(tmp_path / "sbom.json"), total_findings=1, critical=1
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0, "enforcement": "warn"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1


def test_evaluate_cve_policy_counts_only_severities_at_or_above_max_severity(tmp_path: Path, monkeypatch):
    """A LOW finding doesn't count toward a max_severity=HIGH breach."""
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path=str(tmp_path / "sbom.json"), total_findings=1, low=1
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert diagnostics.ok


def test_evaluate_cve_policy_blocks_when_scan_raises(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    _patch_scanner(monkeypatch, _FakeScanner(available=True, raises=IntegrationError("scanner crashed")))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())

    assert not diagnostics.ok
    assert "scanner crashed" in diagnostics.errors[0].message


def test_evaluate_cve_policy_defaults_the_scan_threshold_to_max_severity(tmp_path: Path, monkeypatch):
    """With no explicit severity_threshold, the scanner must still be asked to scan down to
    max_severity itself — otherwise a max_severity=LOW policy would never see LOW findings at all."""
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path=str(tmp_path / "sbom.json"), total_findings=0
    )
    scanner = _FakeScanner(available=True, result=result)
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace_with_cve_policy({"max_severity": "LOW"})

    evaluate_cve_policy(tmp_path, workspace, _index())

    assert scanner.received_threshold == "LOW"


def test_evaluate_cve_policy_honors_an_explicit_severity_threshold(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path=str(tmp_path / "sbom.json"), total_findings=0
    )
    scanner = _FakeScanner(available=True, result=result)
    _patch_scanner(monkeypatch, scanner)
    workspace = _workspace_with_cve_policy({"max_severity": "LOW", "severity_threshold": "UNKNOWN"})

    evaluate_cve_policy(tmp_path, workspace, _index())

    assert scanner.received_threshold == "UNKNOWN"


# ---------------------------------------------------------------------------
# allowlist (docs/work/cve-scanner-integration.md Phase 3)
# ---------------------------------------------------------------------------


def _finding(vulnerability_id: str, *, severity: str, package_name: str) -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id=vulnerability_id, severity=severity, package_name=package_name, installed_version="1.0"
    )


def test_evaluate_cve_policy_allowlist_suppresses_an_unscoped_entry(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=1,
        critical=1,
        findings=[_finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl")],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})
    index = _index_with_configuration([CveAllowedEntryModel(id="CVE-2024-1234", reason="false positive")])

    diagnostics = evaluate_cve_policy(tmp_path, workspace, index)

    assert diagnostics.ok  # the only breaching finding was allowlisted — no violation


def test_evaluate_cve_policy_allowlist_package_scope_only_suppresses_the_matching_package(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=2,
        critical=2,
        findings=[
            _finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl"),
            _finding("CVE-2024-1234", severity="CRITICAL", package_name="curl"),
        ],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})
    index = _index_with_configuration(
        [CveAllowedEntryModel(id="CVE-2024-1234", reason="only openssl is a false positive", package="openssl")]
    )

    diagnostics = evaluate_cve_policy(tmp_path, workspace, index)

    # curl's instance of the same CVE id is not package-scoped away — still breaches
    assert not diagnostics.ok
    assert "1 finding" in diagnostics.errors[0].message


def test_evaluate_cve_policy_allowlist_expired_entry_does_not_suppress(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=1,
        critical=1,
        findings=[_finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl")],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})
    expired = (date.today() - timedelta(days=1)).isoformat()
    index = _index_with_configuration(
        [CveAllowedEntryModel(id="CVE-2024-1234", reason="was fine, but expired", expires=expired)]
    )

    diagnostics = evaluate_cve_policy(tmp_path, workspace, index)

    assert not diagnostics.ok  # expired entry is treated as if it were never declared
    assert "cve_policy_violation" in {d.code for d in diagnostics.errors}


def test_evaluate_cve_policy_allowlist_not_yet_expired_entry_still_suppresses(tmp_path: Path, monkeypatch):
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=1,
        critical=1,
        findings=[_finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl")],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})
    not_yet_expired = (date.today() + timedelta(days=30)).isoformat()
    index = _index_with_configuration(
        [CveAllowedEntryModel(id="CVE-2024-1234", reason="tracked, expires later", expires=not_yet_expired)]
    )

    diagnostics = evaluate_cve_policy(tmp_path, workspace, index)

    assert diagnostics.ok


def test_evaluate_cve_policy_allowlist_expiring_today_still_suppresses(tmp_path: Path, monkeypatch):
    """An entry expiring exactly today is still honored — '>=' not '>' (inclusive of its own last valid day)."""
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=1,
        critical=1,
        findings=[_finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl")],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})
    index = _index_with_configuration(
        [CveAllowedEntryModel(id="CVE-2024-1234", reason="expires today", expires=date.today().isoformat())]
    )

    diagnostics = evaluate_cve_policy(tmp_path, workspace, index)

    assert diagnostics.ok


def test_evaluate_cve_policy_without_a_configuration_document_has_no_suppression(tmp_path: Path, monkeypatch):
    """Pure regression test for the 'no-op when no Configuration is declared' default."""
    (tmp_path / "sbom.json").write_text("{}")
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="1.0",
        sbom_path=str(tmp_path / "sbom.json"),
        total_findings=1,
        critical=1,
        findings=[_finding("CVE-2024-1234", severity="CRITICAL", package_name="openssl")],
    )
    _patch_scanner(monkeypatch, _FakeScanner(available=True, result=result))
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH", "max_count": 0})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index())  # no Configuration document at all

    assert not diagnostics.ok
