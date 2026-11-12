#!/usr/bin/env python3
"""Collect the container images local Helm charts deploy, by rendering them (docs/work/helm-image-delivery.md, the
SBOM side, part 2).

Strata writes no image into a chart's values, so a Helm module's declared service images are only a statement
about it, and the images its chart deploys by itself (the app, sidecars, init containers, subcharts) are in no
SBOM. With the workspace's `sbom.render_charts`, this collector renders every local chart in the build path with
`helm template`, offline, and lists the images in the manifests. They are what runs, so they carry no
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

from strata.integrations.helm import VALUES_FILE, HelmIntegration
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import (
    FOUND_IN_PROPERTY,
    FOUND_IN_RENDERED_CHART,
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
    """Renders each local chart the build placed (a folder with `Chart.yaml` and a `meta.yaml` without
    `chartName`, the folder `deploy run` installs from) and lists the images it deploys. Off unless the workspace
    sets `sbom.render_charts: true`."""

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        settings = graph.workspace.spec.sbom
        if settings is None or not settings.render_charts:
            return CollectorResult()
        charts = list(_local_charts(build_path))
        if not charts:
            return CollectorResult()

        result = CollectorResult()
        integration = helm()
        if not integration.is_available():
            for folder, _ in charts:
                result.diagnostics.append(
                    _info(
                        build_path, folder, "chart images not listed: helm is not installed", "sbom_chart_not_rendered"
                    )
                )
            return result

        for folder, meta in charts:
            values = folder / VALUES_FILE
            rendered = integration.template(
                folder,
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
                result.components.append(_component(name, image, use, result.diagnostics))
        return result


def _component(name: str, image: str, use: str, diagnostics: list[Diagnostic]) -> SbomComponentModel:
    _, tag, digest = parse_image_ref(image)
    properties = {FOUND_IN_PROPERTY: FOUND_IN_RENDERED_CHART, USED_BY_PROPERTY: use}
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


def _local_charts(build_path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """`(folder, meta)` for each local chart folder below `build_path`, sorted, skipping vendored folders (and a
    chart's own `charts/`) below the root only: the build path itself is routinely named `build`."""
    for meta_path in sorted(build_path.rglob("meta.yaml")):
        folder = meta_path.parent
        if not _SKIP_DIRS.isdisjoint(folder.relative_to(build_path).parts):
            continue
        if not (folder / "Chart.yaml").is_file():
            continue
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(meta, dict) and "chartName" not in meta and meta.get("releaseName") and meta.get("namespace"):
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
