#!/usr/bin/env python3
"""Tests for the `chart-image` SBOM collector and `rendered_images()` (docs/work/helm-image-delivery.md, the SBOM
side, part 2)."""

import shutil
from pathlib import Path

import pytest
import yaml

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors import chart_image_collector
from strata.integrations.sbom_collectors.chart_image_collector import ChartImageCollector, rendered_images
from strata.models.common_models import SourceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.sbom_model import WorkspaceSbomModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.diagnostics import Severity
from strata.utils.transport import CommandResult

_DEPLOYMENT = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: web}
spec:
  template:
    spec:
      initContainers:
        - {name: init, image: "busybox:1.36"}
      containers:
        - {name: web, image: "traefik/whoami:v1.10"}
        - {name: proxy, image: "ghcr.io/acme/proxy:2.0"}
"""

_CRONJOB = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: backup}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - {name: backup, image: "restic/restic:0.16.4"}
"""

_CUSTOM = """
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata: {name: api}
spec:
  template:
    spec:
      containers:
        - {name: api, image: "ghcr.io/acme/api:1.4.2"}
"""


def test_rendered_images_reads_containers_and_init_containers_in_order():
    assert rendered_images(_DEPLOYMENT) == [
        ("init", "busybox:1.36"),
        ("web", "traefik/whoami:v1.10"),
        ("proxy", "ghcr.io/acme/proxy:2.0"),
    ]


def test_rendered_images_walks_every_document_and_any_kind():
    text = "---\n# Source: chart/templates/a.yaml\n" + _CRONJOB + "---\n" + _CUSTOM + "---\n\n"
    assert rendered_images(text) == [("backup", "restic/restic:0.16.4"), ("api", "ghcr.io/acme/api:1.4.2")]


def test_rendered_images_lists_an_image_once_per_container_name():
    assert rendered_images(_CRONJOB + "---\n" + _CRONJOB) == [("backup", "restic/restic:0.16.4")]


def test_rendered_images_ignores_what_is_not_a_container_image():
    text = "kind: ConfigMap\ndata:\n  image: not/a-container:1\n---\nkind: Pod\nspec:\n  containers:\n    - name: x\n"
    assert rendered_images(text) == []


def test_rendered_images_survives_text_that_is_not_yaml():
    assert rendered_images("key: [unclosed") == []


# The collector, with Helm replaced.


class _FakeHelm:
    def __init__(self, stdout: str = _DEPLOYMENT, *, available: bool = True, returncode: int = 0, stderr: str = ""):
        self.stdout, self.available, self.returncode, self.stderr = stdout, available, returncode, stderr
        self.calls: list[tuple[Path, str, str, Path | None]] = []

    def is_available(self) -> bool:
        return self.available

    def template(self, chart_dir: Path, *, release: str, namespace: str, values: Path | None = None, timeout=120):
        self.calls.append((chart_dir, release, namespace, values))
        return CommandResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def _graph(render_charts: bool | None = True) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
            sbom=None if render_charts is None else WorkspaceSbomModel(render_charts=render_charts),
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def _chart(build_path: Path, where: str, *, registry: bool = False, values: bool = True) -> Path:
    folder = build_path / where
    folder.mkdir(parents=True)
    meta = {"releaseName": "whoami", "namespace": "apps-k8s"}
    if registry:
        meta["chartName"] = "immich"
    else:
        (folder / "Chart.yaml").write_text("apiVersion: v2\nname: whoami\nversion: 0.1.0\n", encoding="utf-8")
    (folder / "meta.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    if values:
        (folder / "strata-values.yaml").write_text("{}\n", encoding="utf-8")
    return folder


@pytest.fixture
def fake_helm(monkeypatch):
    fake = _FakeHelm()
    monkeypatch.setattr(chart_image_collector, "helm", lambda: fake)
    return fake


@pytest.mark.parametrize("setting", [None, False])
def test_it_renders_nothing_unless_the_workspace_asks(tmp_path, fake_helm, setting):
    _chart(tmp_path, "apps/whoami")
    result = ChartImageCollector().collect(_graph(setting), [], tmp_path)
    assert (result.components, result.diagnostics, fake_helm.calls) == ([], [], [])


def test_a_local_chart_is_rendered_with_its_release_namespace_and_values(tmp_path, fake_helm):
    folder = _chart(tmp_path, "apps/whoami")
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert fake_helm.calls == [(folder, "whoami", "apps-k8s", folder / "strata-values.yaml")]
    components = {c.name: c for c in result.components}
    assert set(components) == {"init", "web", "proxy"}
    web = components["web"]
    assert (web.purl, web.version, web.source_collector) == ("pkg:docker/traefik/whoami@v1.10", "v1.10", "chart-image")
    assert web.properties == {"strata:found-in": "rendered-chart", "strata:used-by": "chart:apps/whoami"}
    assert result.diagnostics == []


def test_a_chart_without_strata_values_is_rendered_with_its_defaults(tmp_path, fake_helm):
    _chart(tmp_path, "apps/whoami", values=False)
    ChartImageCollector().collect(_graph(), [], tmp_path)
    assert fake_helm.calls[0][3] is None


def test_a_registry_chart_and_a_vendored_folder_are_not_rendered(tmp_path, fake_helm):
    _chart(tmp_path, "apps/immich", registry=True)
    _chart(tmp_path, "apps/whoami/charts/sub")
    assert ChartImageCollector().collect(_graph(), [], tmp_path).components == []
    assert fake_helm.calls == []


def test_without_helm_each_chart_gets_an_info_line(tmp_path, monkeypatch):
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm(available=False))
    _chart(tmp_path, "apps/whoami")
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert result.components == []
    [finding] = result.diagnostics
    assert (finding.severity, finding.code) == (Severity.INFO, "sbom_chart_not_rendered")
    assert finding.message == "apps/whoami: chart images not listed: helm is not installed"


def test_a_failed_render_gets_an_info_line_with_helms_first_error(tmp_path, monkeypatch):
    error = "\nError: found in Chart.yaml, but missing in charts/ directory: postgresql\nmore\n"
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm("", returncode=1, stderr=error))
    _chart(tmp_path, "apps/whoami")
    [finding] = ChartImageCollector().collect(_graph(), [], tmp_path).diagnostics
    assert (finding.severity, finding.code) == (Severity.INFO, "sbom_chart_not_rendered")
    assert finding.message.endswith(
        "helm template failed: Error: found in Chart.yaml, but missing in charts/ directory: postgresql"
    )


def test_an_image_holding_a_token_is_left_out_with_an_info_line(tmp_path, monkeypatch):
    stdout = "kind: Pod\nspec:\n  containers:\n    - {name: api, image: 'acme/api:${artifact:api.image_tag}'}\n"
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm(stdout))
    _chart(tmp_path, "apps/whoami")
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert result.components == []
    assert [d.code for d in result.diagnostics] == ["sbom_chart_image_unresolved"]


def test_a_floating_rendered_image_is_flagged(tmp_path, monkeypatch):
    stdout = "kind: Pod\nspec:\n  containers:\n    - {name: app, image: 'acme/app:latest'}\n"
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm(stdout))
    _chart(tmp_path, "apps/whoami")
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert result.components[0].properties["strata:tag-stability"] == "floating"
    assert [d.severity for d in result.diagnostics] == [Severity.WARNING]


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")
def test_a_real_local_chart_is_rendered_by_helm(tmp_path):
    folder = _chart(tmp_path, "apps/whoami")
    (folder / "values.yaml").write_text("tag: v1.10\n", encoding="utf-8")
    (folder / "strata-values.yaml").write_text("tag: v1.11\n", encoding="utf-8")
    (folder / "templates").mkdir()
    (folder / "templates" / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\nspec:\n  selector:\n"
        "    matchLabels: {app: whoami}\n  template:\n    metadata:\n      labels: {app: whoami}\n    spec:\n"
        "      containers:\n        - name: whoami\n          image: traefik/whoami:{{ .Values.tag }}\n",
        encoding="utf-8",
    )
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert [(c.name, c.purl) for c in result.components] == [("whoami", "pkg:docker/traefik/whoami@v1.11")]
    assert result.diagnostics == []
