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
from strata.models.integration_model import IntegrationModel
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

    monkeypatch.setattr(sbom_controller_module, "_build_cyclonedx_json", lambda *args, **kwargs: "{}")

    with pytest.raises(RuntimeError, match="failed CycloneDX schema validation"):
        write_sbom(tmp_path, _graph(), _index())


# ---------------------------------------------------------------------------
# What the SBOM describes, stable component references, one entry per component
# ---------------------------------------------------------------------------


def _deployment_graph(*namespaces: NamespaceModel) -> ResolvedWorkspaceGraph:
    from dataclasses import replace

    from strata.models.deployment_model import DeploymentModel

    deployment = DeploymentModel.model_validate(
        {"meta": {"name": "prd-app"}, "spec": {"workspace": "ws", "environments": ["prd"]}}
    )
    return replace(_graph(*namespaces), deployment=deployment)


def _written(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))


def _redis_build(tmp_path: Path) -> dict:
    module = _module("redis", services=[ModuleServiceModel(name="redis", image="ghcr.io/acme/redis:7.2")])
    write_sbom(
        tmp_path, _deployment_graph(_namespace(ModuleReferenceModel(name="redis", module="redis"))), _index(module)
    )
    return _written(tmp_path)


def test_the_sbom_says_which_deployment_it_describes(tmp_path: Path):
    component = _redis_build(tmp_path)["metadata"]["component"]

    assert component["type"] == "application"
    assert component["name"] == "prd-app"
    assert {p["name"]: p["value"] for p in component["properties"]}["strata:workspace"] == "ws"


def test_the_sbom_names_the_strata_version_that_wrote_it(tmp_path: Path):
    from strata import __version__

    tools = _redis_build(tmp_path)["metadata"]["tools"]["components"]

    assert [(tool["name"], tool["version"]) for tool in tools] == [("strata", str(__version__))]


def test_without_a_deployment_the_workspace_is_the_subject(tmp_path: Path):
    write_sbom(tmp_path, _graph(), _index())
    assert _written(tmp_path)["metadata"]["component"]["name"] == "ws"


def test_the_sbom_names_the_commit_that_was_built(tmp_path: Path):
    write_sbom(tmp_path / "a", _graph(), _index(), commit="a" * 40)
    write_sbom(tmp_path / "b", _graph(), _index())

    def properties(path: Path) -> dict[str, str]:
        return {p["name"]: p["value"] for p in _written(path)["metadata"]["component"]["properties"]}

    assert properties(tmp_path / "a")["strata:commit"] == "a" * 40
    assert "strata:commit" not in properties(tmp_path / "b")


def test_a_component_reference_is_its_purl_so_two_builds_agree(tmp_path: Path):
    first = _redis_build(tmp_path / "a")
    second = _redis_build(tmp_path / "b")

    [component] = first["components"]
    assert component["bom-ref"] == component["purl"] == "pkg:docker/acme/redis@7.2?repository_url=ghcr.io"
    assert [c["bom-ref"] for c in first["components"]] == [c["bom-ref"] for c in second["components"]]


def test_the_deployment_depends_on_every_component(tmp_path: Path):
    bom = _redis_build(tmp_path)

    root = bom["metadata"]["component"]["bom-ref"]
    dependencies = {entry["ref"]: entry.get("dependsOn", []) for entry in bom["dependencies"]}
    assert dependencies[root] == [bom["components"][0]["bom-ref"]]


def test_an_image_found_by_two_collectors_is_listed_once(tmp_path: Path):
    """The image collector reads the module, the compose collector the rendered file (where the service may be
    named differently): one component, with the properties of both."""
    module = _module("app", services=[ModuleServiceModel(name="app", image="acme/app:latest")])
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: acme/app:latest\n", encoding="utf-8"
    )

    write_sbom(tmp_path, _graph(_namespace(ModuleReferenceModel(name="app", module="app"))), _index(module))

    components = _written(tmp_path)["components"]
    assert [c["purl"] for c in components] == ["pkg:docker/acme/app@latest"]
    assert {p["name"] for p in components[0]["properties"]} >= {"strata:tag-stability"}


def test_duplicates_keep_the_first_component_and_gain_the_others_properties():
    from strata.controllers.sbom_controller import _merge_by_purl
    from strata.models.sbom_model import SbomComponentModel

    def component(name: str, **properties: str) -> SbomComponentModel:
        return SbomComponentModel(
            component_type="container",
            name=name,
            version="1",
            purl="pkg:docker/a@1",
            properties=properties,
            source_collector=name,
        )

    [merged] = _merge_by_purl([component("image", a="1"), component("compose", a="2", b="3")])

    assert (merged.name, merged.properties) == ("image", {"a": "1", "b": "3"})


def test_two_spellings_of_one_purl_are_one_component(tmp_path: Path, monkeypatch):
    """The bom-ref is the normalised purl, so duplicates must be found on it too, or the BOM is invalid."""
    from strata.integrations.sbom_collectors import registry
    from strata.integrations.sbom_collectors.base import CollectorResult
    from strata.models.sbom_model import SbomComponentModel

    def chart(purl: str, **properties: str) -> SbomComponentModel:
        return SbomComponentModel(
            component_type="library", name="a", version="1", purl=purl, properties=properties, source_collector="test"
        )

    class TwoSpellings:
        def collect(self, graph, modules, build_path):
            return CollectorResult(
                components=[
                    chart("pkg:helm/a@1?repository_url=https%3A//x.io"),
                    chart("pkg:helm/a@1?repository_url=https://x.io", note="second"),
                ]
            )

    monkeypatch.setattr(registry, "list_collectors", lambda: ["test"])
    monkeypatch.setattr(registry, "get", lambda name: TwoSpellings())

    write_sbom(tmp_path, _graph(), _index())

    [component] = _written(tmp_path)["components"]
    assert component["bom-ref"] == "pkg:helm/a@1?repository_url=https://x.io"
    assert component["properties"] == [{"name": "note", "value": "second"}]


_DIGEST = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def _image_build(tmp_path: Path, *images: str, compose: str | None = None) -> list[dict]:
    services = [ModuleServiceModel(name=f"svc{n}", image=image) for n, image in enumerate(images)]
    module = _module("app", services=services)
    if compose is not None:
        (tmp_path / "apps").mkdir(parents=True, exist_ok=True)
        (tmp_path / "apps" / "docker-compose.yml").write_text(
            f"services:\n  web:\n    image: {compose}\n", encoding="utf-8"
        )
    write_sbom(tmp_path, _graph(_namespace(ModuleReferenceModel(name="app", module="app"))), _index(module))
    return _written(tmp_path)["components"]


def test_an_image_digest_is_written_as_a_cyclonedx_hash(tmp_path: Path):
    """The file passes write_sbom's own schema validation, so the hash shape is the schema's."""
    by_name = {c["name"]: c for c in _image_build(tmp_path, f"ghcr.io/acme/api:1.4.2@sha256:{_DIGEST}", "redis:7.2")}

    assert by_name["svc0"]["hashes"] == [{"alg": "SHA-256", "content": _DIGEST}]
    assert "hashes" not in by_name["svc1"]


def test_an_image_found_by_two_collectors_has_one_hash(tmp_path: Path):
    image = f"postgres@sha256:{_DIGEST}"
    [component] = _image_build(tmp_path, image, compose=image)
    assert component["hashes"] == [{"alg": "SHA-256", "content": _DIGEST}]


def test_duplicates_keep_the_first_hash_and_gain_the_others_algorithms():
    from strata.controllers.sbom_controller import _merge_by_purl
    from strata.models.sbom_model import SbomComponentModel

    def component(**hashes: str) -> SbomComponentModel:
        return SbomComponentModel(
            component_type="container", name="a", purl="pkg:docker/a@1", hashes=hashes, source_collector="test"
        )

    [merged] = _merge_by_purl([component(SHA_256="first"), component(SHA_256="second", SHA_512="other")])

    assert merged.hashes == {"SHA_256": "first", "SHA_512": "other"}


def test_a_hash_with_an_algorithm_cyclonedx_does_not_know_is_left_out(tmp_path: Path, monkeypatch):
    from strata.integrations.sbom_collectors import registry
    from strata.integrations.sbom_collectors.base import CollectorResult
    from strata.models.sbom_model import SbomComponentModel

    class Plugin:
        def collect(self, graph, modules, build_path):
            component = SbomComponentModel(
                component_type="library",
                name="a",
                purl="pkg:generic/a@1",
                hashes={"SHA-256": _DIGEST, "CRC32": "deadbeef"},
                source_collector="plugin",
            )
            return CollectorResult(components=[component])

    monkeypatch.setattr(registry, "list_collectors", lambda: ["plugin"])
    monkeypatch.setattr(registry, "get", lambda name: Plugin())

    write_sbom(tmp_path, _graph(), _index())

    [component] = _written(tmp_path)["components"]
    assert component["hashes"] == [{"alg": "SHA-256", "content": _DIGEST}]


def test_a_chart_from_a_solution_remote_carries_the_repository(tmp_path: Path):
    from strata.models.solution_model import SolutionRemoteModel

    chart = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(
            source=SourceModel(remote="charts", chart_name="authentik", chart_version="2026.5.2"),
            type="helm",
            default_labels={},
        ),
    )
    remotes = {"charts": SolutionRemoteModel(name="charts", type="helm", url="https://charts.goauthentik.io")}

    write_sbom(
        tmp_path,
        _graph(_namespace(ModuleReferenceModel(name="authentik", module="authentik"))),
        _index(chart),
        remotes=remotes,
    )

    [component] = _written(tmp_path)["components"]
    assert component["purl"] == "pkg:helm/authentik@2026.5.2?repository_url=https://charts.goauthentik.io"


def test_a_chart_whose_remote_is_unknown_keeps_a_purl_without_repository(tmp_path: Path):
    chart = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(
            source=SourceModel(remote="charts", chart_name="authentik", chart_version="2026.5.2"),
            type="helm",
            default_labels={},
        ),
    )
    write_sbom(tmp_path, _graph(_namespace(ModuleReferenceModel(name="authentik", module="authentik"))), _index(chart))

    [component] = _written(tmp_path)["components"]
    assert component["purl"] == "pkg:helm/authentik@2026.5.2"


# ---------------------------------------------------------------------------
# evaluate_cve_policy (docs/archive/cve-scanner-integration.md)
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


def _index_with_scanner(*types: str) -> DocumentIndex:
    index = DocumentIndex()
    for integration_type in types:
        document = IntegrationModel.model_validate(
            {
                "meta": {"name": f"{integration_type}-doc"},
                "spec": {"type": integration_type, "capabilities": ["scanner"]},
            }
        )
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.INTEGRATION, name=document.meta.name),
                model=document,
                source=Path(f"{integration_type}.yaml"),
            )
        )
    return index


def test_a_declared_trivy_is_the_scanner_the_cve_gate_uses(tmp_path: Path, monkeypatch):
    import strata.integrations.cve_scanner as cve_scanner_module

    monkeypatch.setattr(
        cve_scanner_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "grype" else None
    )
    (tmp_path / "sbom.json").write_text("{}", encoding="utf-8")
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH"})

    diagnostics = evaluate_cve_policy(tmp_path, workspace, _index_with_scanner("trivy"))

    [error] = diagnostics.errors
    assert "trivy 'trivy-doc': 'trivy' is not available on PATH" in error.message


def test_a_declared_grype_is_used_and_a_declared_trivy_wins_over_it(tmp_path: Path, monkeypatch):
    import strata.integrations.cve_scanner as cve_scanner_module

    monkeypatch.setattr(cve_scanner_module.shutil, "which", lambda name: None)
    (tmp_path / "sbom.json").write_text("{}", encoding="utf-8")
    workspace = _workspace_with_cve_policy({"max_severity": "HIGH"})

    grype_only = evaluate_cve_policy(tmp_path, workspace, _index_with_scanner("grype"))
    both = evaluate_cve_policy(tmp_path, workspace, _index_with_scanner("grype", "trivy"))

    assert "grype 'grype-doc'" in grype_only.errors[0].message
    assert "trivy 'trivy-doc'" in both.errors[0].message


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
# allowlist (docs/archive/cve-scanner-integration.md Phase 3)
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


def _container(collector: str, **properties: str):
    from strata.models.sbom_model import SbomComponentModel

    return SbomComponentModel(
        component_type="container",
        name=collector,
        version="1",
        purl="pkg:docker/a@1",
        properties=properties,
        source_collector=collector,
    )


def test_duplicates_join_their_uses_in_first_use_order():
    from strata.controllers.sbom_controller import _merge_by_purl

    [merged] = _merge_by_purl(
        [
            _container("image", **{"strata:used-by": "service:a/x, variable:K"}),
            _container("chart-image", **{"strata:used-by": "chart:apps/a, service:a/x"}),
        ]
    )

    assert merged.properties["strata:used-by"] == "service:a/x, variable:K, chart:apps/a"


def test_a_rendered_chart_image_drops_the_declared_only_marker():
    from strata.controllers.sbom_controller import _merge_by_purl

    declared = _container(
        "image", **{"strata:image-delivered": "false", "strata:image-delivery": "helm module: the chart decides"}
    )
    [merged] = _merge_by_purl([declared, _container("chart-image", **{"strata:found-in": "rendered-chart"})])

    assert "strata:image-delivered" not in merged.properties
    assert "strata:image-delivery" not in merged.properties
    assert merged.properties["strata:found-in"] == "rendered-chart"


def test_a_declared_only_image_no_chart_renders_keeps_its_marker():
    from strata.controllers.sbom_controller import _merge_by_purl

    [merged] = _merge_by_purl([_container("image", **{"strata:image-delivered": "false"})])

    assert merged.properties["strata:image-delivered"] == "false"


# A declared Helm image its module's rendered chart does not run (docs/design/helm-image-delivery.md, "The SBOM: declared but not rendered").


def _declared(purl: str, *uses: str):
    from strata.models.sbom_model import SbomComponentModel

    return SbomComponentModel(
        component_type="container",
        name="server",
        version="2026.5.2",
        purl=purl,
        properties={
            "strata:image-delivered": "false",
            "strata:image-delivery": "helm module: the chart decides",
            "strata:used-by": ", ".join(uses),
        },
        source_collector="image",
    )


def _rendered(purl: str, use: str):
    from strata.models.sbom_model import SbomComponentModel

    return SbomComponentModel(
        component_type="container",
        name="app",
        version="1",
        purl=purl,
        properties={"strata:found-in": "rendered-chart", "strata:used-by": use},
        source_collector="chart-image",
    )


def _helm_graph(*images):
    """Namespace `apps` references module `authentik` as `auth` and module `other` as `other`."""
    graph = _graph(
        _namespace(
            ModuleReferenceModel(name="auth", module="authentik"), ModuleReferenceModel(name="other", module="other")
        )
    )
    return graph.__class__(workspace=graph.workspace, namespaces=graph.namespaces, images=list(images))


def _image(ref: str, *via: str):
    from strata.integrations.resolved_context import EffectiveImage
    from strata.utils.sbom_utils import parse_image_ref

    name, tag, _ = parse_image_ref(ref)
    return EffectiveImage(
        origin="module-service",
        name="server",
        image_name=name,
        image_tag=tag,
        image_ref=ref,
        tag_source="declared",
        via=via,
        delivered=False,
        delivery="helm module: the chart decides",
    )


def _not_rendered(components, graph):
    from strata.controllers.sbom_controller import _declared_not_rendered, _merge_by_purl
    from strata.utils.diagnostics import Diagnostics

    diagnostics = Diagnostics()
    return _declared_not_rendered(_merge_by_purl(components), graph, diagnostics), diagnostics


_SERVER = "pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io"


def test_a_declared_image_the_rendered_chart_does_not_run_gets_an_info_line():
    graph = _helm_graph(_image("ghcr.io/goauthentik/server:2026.5.2", "service:authentik/server"))
    components, diagnostics = _not_rendered(
        [_declared(_SERVER, "service:authentik/server"), _rendered("pkg:docker/other@1", "chart:apps/auth/app")],
        graph,
    )

    [finding] = diagnostics.infos
    assert (finding.code, finding.location) == ("sbom_declared_image_not_rendered", "authentik")
    assert finding.message == (
        "service 'server' of module 'authentik' declares ghcr.io/goauthentik/server:2026.5.2, but the rendered chart "
        "does not run it. The declaration is kept, marked not delivered."
    )
    server = next(c for c in components if c.purl == _SERVER)
    assert server.properties["strata:image-delivered"] == "false"
    assert server.properties["strata:image-delivery"] == "helm module: not in the rendered chart"


def test_a_declared_image_the_rendered_chart_runs_gets_nothing():
    graph = _helm_graph(_image("ghcr.io/goauthentik/server:2026.5.2", "service:authentik/server"))
    [server], diagnostics = _not_rendered(
        [_declared(_SERVER, "service:authentik/server"), _rendered(_SERVER, "chart:apps/auth/server")], graph
    )

    assert diagnostics.infos == []
    assert "strata:image-delivered" not in server.properties


def test_a_declared_image_of_a_module_whose_chart_was_not_rendered_gets_nothing():
    graph = _helm_graph(_image("ghcr.io/goauthentik/server:2026.5.2", "service:authentik/server"))
    components, diagnostics = _not_rendered(
        [_declared(_SERVER, "service:authentik/server"), _rendered("pkg:docker/other@1", "chart:apps/other/app")],
        graph,
    )

    assert diagnostics.infos == []
    server = next(c for c in components if c.purl == _SERVER)
    assert server.properties["strata:image-delivery"] == "helm module: the chart decides"


def test_an_image_two_modules_declare_keeps_its_reason_while_one_chart_is_not_rendered():
    uses = ("service:authentik/server", "service:legacy/server")
    graph = _helm_graph(_image("ghcr.io/goauthentik/server:2026.5.2", *uses))
    components, diagnostics = _not_rendered(
        [_declared(_SERVER, *uses), _rendered("pkg:docker/other@1", "chart:apps/auth/app")], graph
    )

    assert [d.location for d in diagnostics.infos] == ["authentik"]
    server = next(c for c in components if c.purl == _SERVER)
    assert server.properties["strata:image-delivery"] == "helm module: the chart decides"


def test_write_sbom_reports_a_declared_image_the_rendered_chart_does_not_run(tmp_path: Path, monkeypatch):
    from strata.integrations.sbom_collectors import registry
    from strata.integrations.sbom_collectors.base import CollectorResult

    class _Both:
        def collect(self, graph, modules, build_path):
            components = [
                _declared(_SERVER, "service:authentik/server"),
                _rendered("pkg:docker/x@1", "chart:apps/auth/a"),
            ]
            return CollectorResult(components=components)

    monkeypatch.setattr(registry, "list_collectors", lambda: ["both"])
    monkeypatch.setattr(registry, "get", lambda name: _Both())
    graph = _helm_graph(_image("ghcr.io/goauthentik/server:2026.5.2", "service:authentik/server"))

    diagnostics = write_sbom(tmp_path, graph, _index(_module("authentik"), _module("other")))

    assert [d.code for d in diagnostics.infos] == ["sbom_declared_image_not_rendered"]
    data = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    server = next(c for c in data["components"] if c["name"] == "server")
    assert {"name": "strata:image-delivery", "value": "helm module: not in the rendered chart"} in server["properties"]
