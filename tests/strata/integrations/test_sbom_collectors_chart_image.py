#!/usr/bin/env python3
"""Tests for the `chart-image` SBOM collector and `rendered_images()` (docs/work/helm-image-delivery.md, the SBOM
side, part 2)."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata.controllers.sbom_controller import _merge_by_purl, write_sbom
from strata.controllers.solution_controller import DocumentIndex
from strata.integrations.helm_cache import archive_digest
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


def _chart(
    build_path: Path, where: str, *, registry: bool = False, values: bool = True, pin: tuple[str, str] | None = None
) -> Path:
    """A module folder as the build writes it; `pin` is the registry chart's `(chartVersion, chartDigest)`."""
    folder = build_path / where
    folder.mkdir(parents=True)
    meta = {"releaseName": "whoami", "namespace": "apps-k8s"}
    if registry:
        meta["chartName"] = "immich"
        meta["chartRemote"] = "charts"
        if pin is not None:
            meta["chartVersion"], meta["chartDigest"] = pin
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
    assert web.properties == {
        "strata:found-in": "rendered-chart",
        "strata:used-by": "chart:apps/whoami/web",
        "strata:image": "traefik/whoami:v1.10",
    }
    assert result.diagnostics == []


def test_the_reference_keeps_the_tag_the_purl_drops_for_a_digest_pinned_image(tmp_path, monkeypatch):
    digest = "sha256:" + "a" * 64
    stdout = f"kind: Pod\nspec:\n  containers:\n    - {{name: app, image: 'traefik/whoami:v1.10.2@{digest}'}}\n"
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm(stdout))
    _chart(tmp_path, "apps/whoami")
    [component] = ChartImageCollector().collect(_graph(), [], tmp_path).components
    assert "v1.10.2" not in component.purl
    assert component.properties["strata:image"] == f"traefik/whoami:v1.10.2@{digest}"


def test_two_containers_of_one_image_keep_both_uses_after_the_merge(tmp_path, monkeypatch):
    stdout = (
        "kind: Pod\nspec:\n  initContainers:\n    - {name: wait, image: 'busybox:1.36'}\n"
        "  containers:\n    - {name: tail, image: 'busybox:1.36'}\n"
    )
    monkeypatch.setattr(chart_image_collector, "helm", lambda: _FakeHelm(stdout))
    _chart(tmp_path, "apps/whoami")
    components = ChartImageCollector().collect(_graph(), [], tmp_path).components
    [merged] = _merge_by_purl(components)
    assert merged.properties["strata:used-by"] == "chart:apps/whoami/wait, chart:apps/whoami/tail"


def test_a_chart_without_strata_values_is_rendered_with_its_defaults(tmp_path, fake_helm):
    _chart(tmp_path, "apps/whoami", values=False)
    ChartImageCollector().collect(_graph(), [], tmp_path)
    assert fake_helm.calls[0][3] is None


def test_without_the_cache_a_registry_chart_is_not_rendered_nor_a_vendored_folder(tmp_path, fake_helm):
    _chart(tmp_path, "apps/immich", registry=True, pin=("0.13.1", "sha256:x"))
    _chart(tmp_path, "apps/whoami/charts/sub")
    result = ChartImageCollector().collect(_graph(), [], tmp_path)
    assert (result.components, result.diagnostics, fake_helm.calls) == ([], [], [])


# Registry charts, rendered from the `.strata/helm/` cache (plan item 9b).


def _cache_entry(cache: Path, version: str = "0.13.1", content: bytes = b"archive") -> Path:
    """A cache entry as `pull_chart()` leaves it: one archive and its unpacked chart folder."""
    entry = cache / "charts" / "immich" / version
    (entry / "immich").mkdir(parents=True)
    (entry / "immich" / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: immich\nversion: {version}\n", encoding="utf-8"
    )
    archive = entry / f"immich-{version}.tgz"
    archive.write_bytes(content)
    return archive


def test_a_pinned_registry_chart_is_rendered_from_its_cached_archive(tmp_path, fake_helm):
    cache = tmp_path / "cache"
    archive = _cache_entry(cache)
    folder = _chart(tmp_path / "build", "apps/immich", registry=True, pin=("0.13.1", archive_digest(archive)))
    result = ChartImageCollector(helm_cache=cache).collect(_graph(), [], tmp_path / "build")
    assert fake_helm.calls == [(archive, "whoami", "apps-k8s", folder / "strata-values.yaml")]
    assert {c.properties["strata:used-by"] for c in result.components} == {
        "chart:apps/immich/init",
        "chart:apps/immich/web",
        "chart:apps/immich/proxy",
    }
    assert result.diagnostics == []


@pytest.mark.parametrize(
    ("pin", "in_cache", "reason"),
    [
        (None, True, "chart 'immich' was not pinned by the build"),
        (("0.13.1", "sha256:x"), False, "chart 'immich' 0.13.1 is not in .strata/helm/"),
        (("0.13.1", "sha256:other"), True, "chart 'immich' 0.13.1 in .strata/helm/ is sha256:"),
    ],
    ids=["unpinned", "not-cached", "other-digest"],
)
def test_a_registry_chart_that_does_not_match_its_pin_gets_an_info_line(tmp_path, fake_helm, pin, in_cache, reason):
    cache = tmp_path / "cache"
    if in_cache:
        _cache_entry(cache)
    _chart(tmp_path / "build", "apps/immich", registry=True, pin=pin)
    result = ChartImageCollector(helm_cache=cache).collect(_graph(), [], tmp_path / "build")
    assert (result.components, fake_helm.calls) == ([], [])
    [finding] = result.diagnostics
    assert (finding.severity, finding.code) == (Severity.INFO, "sbom_chart_not_rendered")
    assert finding.message.startswith(f"apps/immich: chart images not listed: {reason}")


def test_write_sbom_hands_the_cache_to_the_collector(tmp_path, fake_helm):
    cache = tmp_path / "cache"
    archive = _cache_entry(cache)
    build = tmp_path / "build"
    _chart(build, "apps/immich", registry=True, pin=("0.13.1", archive_digest(archive)))
    write_sbom(build, _graph(), DocumentIndex(), helm_cache=cache)
    assert [call[0] for call in fake_helm.calls] == [archive]


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


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")
def test_a_real_packaged_registry_chart_is_rendered_by_helm_with_strata_values(tmp_path):
    source = tmp_path / "src" / "immich"
    (source / "templates").mkdir(parents=True)
    (source / "Chart.yaml").write_text("apiVersion: v2\nname: immich\nversion: 0.13.1\n", encoding="utf-8")
    (source / "values.yaml").write_text("tag: v1.0\n", encoding="utf-8")
    (source / "templates" / "pod.yaml").write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: {{ .Release.Name }}\nspec:\n  containers:\n"
        "    - name: server\n      image: ghcr.io/immich-app/immich-server:{{ .Values.tag }}\n",
        encoding="utf-8",
    )
    entry = tmp_path / "cache" / "charts" / "immich" / "0.13.1"
    entry.mkdir(parents=True)
    packaged = subprocess.run(
        [shutil.which("helm") or "helm", "package", str(source), "--destination", str(entry)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert packaged.returncode == 0, packaged.stderr
    shutil.copytree(source, entry / "immich")
    [archive] = entry.glob("*.tgz")
    folder = _chart(tmp_path / "build", "apps/immich", registry=True, pin=("0.13.1", archive_digest(archive)))
    (folder / "strata-values.yaml").write_text("tag: v2.0\n", encoding="utf-8")

    result = ChartImageCollector(helm_cache=tmp_path / "cache").collect(_graph(), [], tmp_path / "build")
    assert [(c.name, c.properties["strata:image"]) for c in result.components] == [
        ("server", "ghcr.io/immich-app/immich-server:v2.0")
    ]
    assert result.diagnostics == []
