#!/usr/bin/env python3
"""Collect the container images Helm charts deploy, by rendering them (docs/work/helm-image-delivery.md, the SBOM
side, part 2, and plan item 9b for registry charts).

Strata writes no image into a chart's values, so a Helm module's declared service images are only a statement
about it, and the images its chart deploys by itself (the app, sidecars, init containers, subcharts) are in no
SBOM. Unless the workspace sets `sbom.render_charts: false`, this collector renders every local chart in the build path, and
every pinned registry chart from the `.strata/helm/` cache, with `helm template`, offline, and lists the images in
the manifests. They are what runs, so they carry no
declared-only marker, and `sbom_controller._merge_by_purl()` drops the marker from a declared image the chart also
deploys.

The render uses `strata-values.yaml`, which holds `${...}` tokens resolved only at deploy, never a secret value.
The rendered text stays in memory. A chart that cannot be rendered gets one `info` finding and is skipped: the
build never fails here.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.helm import VALUES_FILE, HelmIntegration, chart_cache_source
from strata.integrations.helm_cache import cached_pin
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import (
    FOUND_IN_PROPERTY,
    FOUND_IN_RENDERED_CHART,
    IMAGE_REF_PROPERTY,
    USED_BY_PROPERTY,
    image_digest_hash,
    image_to_purl,
    is_floating_tag,
    parse_image_ref,
)

_SOURCE = "sbom:chart-image"
_FLOATING_PROPERTY = "strata:tag-stability"
#: The pod spec lists whose entries carry an `image`, wherever a manifest embeds a pod spec.
_CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")
_SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__", "charts"})


def rendered_images(text: str) -> list[tuple[str, str]]:
    """`(container name, image)` for every container in rendered Kubernetes manifests, in order, each pair once.

    Walks every document instead of matching kinds, so Deployment, StatefulSet, DaemonSet, Job, CronJob, Pod and a
    custom resource that embeds a pod spec are all covered. A document that is not YAML is skipped.
    """
    found: list[tuple[str, str]] = []
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return found
    for document in documents:
        for name, image in _containers(document):
            if (name, image) not in found:
                found.append((name, image))
    return found


def _containers(node: Any) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _CONTAINER_LISTS and isinstance(value, list):
                for container in value:
                    if isinstance(container, dict) and isinstance(container.get("image"), str):
                        image = container["image"].strip()
                        if image:
                            yield str(container.get("name") or image), image
            else:
                yield from _containers(value)
    elif isinstance(node, list):
        for item in node:
            yield from _containers(item)


def helm() -> HelmIntegration:
    """The Helm integration that renders; its own function so tests can replace it."""
    return HelmIntegration()


class ChartImageCollector(SbomCollector):
    """Renders each chart the build placed and lists the images it deploys. On unless the workspace sets
    `sbom.render_charts: false` (the default since docs/work/helm-image-delivery.md, plan item 10b).

    - A local chart is a module folder with `Chart.yaml` and a `meta.yaml` without `chartName`, the folder
      `deploy run` installs from.
    - A registry chart is a module folder whose `meta.yaml` has `chartName`. Its archive is rendered from the
      `.strata/helm/` cache at `helm_cache`, only when its digest is the `chartDigest` the build pinned, the archive
      `deploy run` installs (docs/work/helm-image-delivery.md, plan item 9b). Without `helm_cache`, registry charts
      are not rendered.
    """

    def __init__(self, helm_cache: Path | None = None) -> None:
        self.helm_cache = helm_cache

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        settings = graph.workspace.spec.sbom
        if settings is not None and not settings.render_charts:
            return CollectorResult()
        result = CollectorResult()
        charts: list[tuple[Path, dict[str, Any], Path]] = []
        for folder, meta in _chart_folders(build_path):
            chart, skipped = self._chart(folder, meta)
            if skipped is not None:
                result.diagnostics.append(
                    _info(build_path, folder, f"chart images not listed: {skipped}", "sbom_chart_not_rendered")
                )
            elif chart is not None:
                charts.append((folder, meta, chart))
        if not charts:
            return result

        integration = helm()
        if not integration.is_available():
            for folder, _, _ in charts:
                result.diagnostics.append(
                    _info(
                        build_path, folder, "chart images not listed: helm is not installed", "sbom_chart_not_rendered"
                    )
                )
            return result

        for folder, meta, chart in charts:
            values = folder / VALUES_FILE
            rendered = integration.template(
                chart,
                release=str(meta["releaseName"]),
                namespace=str(meta["namespace"]),
                values=values if values.is_file() else None,
            )
            if not rendered.is_successful:
                reason = "timed out" if rendered.timed_out else _first_error(rendered.stderr)
                result.diagnostics.append(
                    _info(
                        build_path,
                        folder,
                        f"chart images not listed: helm template failed: {reason}",
                        "sbom_chart_not_rendered",
                    )
                )
                continue
            use = f"chart:{_where(build_path, folder)}"
            for name, image in rendered_images(rendered.stdout):
                if "${" in image:
                    result.diagnostics.append(
                        _info(
                            build_path,
                            folder,
                            f"image '{image}' is resolved only at deploy, so it is not listed; an artifact delivered "
                            "by an ${artifact:} token is listed through the artifact",
                            "sbom_chart_image_unresolved",
                        )
                    )
                    continue
                result.components.append(_component(name, image, f"{use}/{name}", result.diagnostics))
        return result

    def _chart(self, folder: Path, meta: dict[str, Any]) -> tuple[Path | None, str | None]:
        """What to render for one module folder, or why it is skipped. `(None, None)`: nothing to say."""
        if "chartName" not in meta:
            return (folder, None) if (folder / "Chart.yaml").is_file() else (None, None)
        if self.helm_cache is None:
            return None, None
        name, version, digest = str(meta["chartName"]), meta.get("chartVersion"), meta.get("chartDigest")
        wanted = f"chart '{name}'" + (f" {version}" if version else "")
        if version is None or digest is None:
            return None, f"{wanted} was not pinned by the build"
        pin = cached_pin(self.helm_cache, chart_cache_source(meta), name, str(version))
        if pin is None:
            return None, f"{wanted} is not in .strata/helm/"
        if pin.digest != digest:
            return None, f"{wanted} in .strata/helm/ is {pin.digest}, but the build pinned {digest}"
        return pin.archive, None


def _component(name: str, image: str, use: str, diagnostics: list[Diagnostic]) -> SbomComponentModel:
    """The use names the container as well as the chart, so it survives `_merge_by_purl()` keeping one component
    (and one name) for an image two containers or two charts run. `strata:image` keeps the reference as written."""
    _, tag, digest = parse_image_ref(image)
    properties = {FOUND_IN_PROPERTY: FOUND_IN_RENDERED_CHART, USED_BY_PROPERTY: use, IMAGE_REF_PROPERTY: image}
    if digest is None and is_floating_tag(tag):
        properties[_FLOATING_PROPERTY] = "floating"
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                message=f"floating image tag detected for container '{name}' ({use}): {image}",
                source=_SOURCE,
            )
        )
    digest_hash = image_digest_hash(image)
    return SbomComponentModel(
        component_type="container",
        name=name,
        version=tag,
        purl=image_to_purl(image),
        properties=properties,
        hashes=dict([digest_hash]) if digest_hash else {},
        source_collector="chart-image",
    )


def _chart_folders(build_path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """`(folder, meta)` for each Helm module folder below `build_path` (a `meta.yaml` with a release name and a
    namespace), sorted, skipping vendored folders (and a chart's own `charts/`) below the root only: the build path
    itself is routinely named `build`."""
    for meta_path in sorted(build_path.rglob("meta.yaml")):
        folder = meta_path.parent
        if not _SKIP_DIRS.isdisjoint(folder.relative_to(build_path).parts):
            continue
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(meta, dict) and meta.get("releaseName") and meta.get("namespace"):
            yield folder, meta


def _where(build_path: Path, folder: Path) -> str:
    return folder.relative_to(build_path).as_posix()


def _first_error(stderr: str) -> str:
    for line in stderr.splitlines():
        if line.strip():
            return line.strip()
    return "no error output"


def _info(build_path: Path, folder: Path, message: str, code: str) -> Diagnostic:
    return Diagnostic(
        severity=Severity.INFO, message=f"{_where(build_path, folder)}: {message}", source=_SOURCE, code=code
    )
