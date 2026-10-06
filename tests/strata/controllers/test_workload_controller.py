#!/usr/bin/env python3
"""Tests for `workload_controller` (ADR-0022 D5-D7)."""

from pathlib import Path

import pytest
import yaml

from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry, SolutionController
from strata.controllers.workload_controller import (
    apply_version_pins,
    build_workload_modules,
    resolve_artifact_services,
    resolve_module,
)
from strata.integrations.resolved_context import ValueResolution
from strata.models.artifact_model import ArtifactMetaModel, ArtifactModel, ArtifactSpecModel
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.deployment_model import DeploymentMetaModel, DeploymentModel, DeploymentSpecModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.version_model import VersionMetaModel, VersionModel, VersionSpecModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _module(name: str, *, type: str | None = "helm", source: SourceModel | None = None) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=source or SourceModel(source_path=f"charts/{name}"),
            type=type,
            services=[ModuleServiceModel(name=name)],
            default_labels={},
        ),
    )


def _index(*modules: ModuleModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in modules:
        ref = DocumentRef(kind=PlatformKind.MODULE, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


def _artifact(name: str, *, image_name: str, image_tag: str | None = None) -> ArtifactModel:
    return ArtifactModel(
        meta=ArtifactMetaModel(name=name), spec=ArtifactSpecModel(image_name=image_name, image_tag=image_tag)
    )


def _deployment(*, version: str | None = None) -> DeploymentModel:
    return DeploymentModel(
        meta=DeploymentMetaModel(name="app"), spec=DeploymentSpecModel(partial=True, version=version)
    )


def _context(root: Path, index: DocumentIndex) -> SolutionContext:
    controller = SolutionController(root)
    controller.index = index
    return SolutionContext(controller=controller, diagnostics=Diagnostics())


def _namespace(*refs: ModuleReferenceModel) -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name="apps"),
        spec=NamespaceSpecModel(default_labels={}, modules=list(refs) or None, lifecycle=None),
    )


# ---------------------------------------------------------------------------
# resolve_module()
# ---------------------------------------------------------------------------


def test_resolve_module_returns_the_named_module():
    module = _module("authentik")
    index = _index(module)
    reference = ModuleReferenceModel(name="auth", module="authentik")

    assert resolve_module(index, reference) is module


def test_resolve_module_raises_for_an_unknown_name():
    reference = ModuleReferenceModel(name="auth", module="ghost")

    with pytest.raises(UsageError, match="ghost"):
        resolve_module(_index(), reference)


# ---------------------------------------------------------------------------
# _apply_version_pins() (docs/design/version-pin-overlay.md Phase 4)
# ---------------------------------------------------------------------------


def _version(
    name: str = "prd", *, chart_pins: dict[str, str] | None = None, image_pins: dict[str, str] | None = None
) -> VersionModel:
    pins: dict[str, dict[str, str]] = {}
    if chart_pins:
        pins["charts"] = chart_pins
    if image_pins:
        pins["images"] = image_pins
    return VersionModel(meta=VersionMetaModel(name=name), spec=VersionSpecModel(pins=pins))


def test_apply_version_pins_returns_same_module_when_version_is_none():
    module = _module("authentik")
    assert apply_version_pins(module, None) is module


def test_apply_version_pins_returns_same_module_when_no_pin_matches():
    module = _module("authentik")
    version = _version(chart_pins={"other-module": "9.9.9"}, image_pins={"other-service": "9.9.9"})
    assert apply_version_pins(module, version) is module


def test_apply_version_pins_overrides_chart_version_when_chart_based():
    module = _module(
        "authentik", source=SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.1.0")
    )
    version = _version(chart_pins={"authentik": "2024.2.0"})

    overlaid = apply_version_pins(module, version)

    assert overlaid is not module
    assert overlaid.spec.source.chart_version == "2024.2.0"
    assert module.spec.source.chart_version == "2024.1.0"  # original untouched


def test_apply_version_pins_ignores_chart_pin_when_module_is_not_chart_based():
    """A `charts` pin on a non-chart (git/local-path) module has nothing to
    apply to — `check_version_pins()` already warns about this separately."""
    module = _module("authentik")  # default source has no chart_name
    version = _version(chart_pins={"authentik": "2024.2.0"})

    assert apply_version_pins(module, version) is module


def test_apply_version_pins_overrides_matching_service_image():
    module = ModuleModel(
        meta=ModuleMetaModel(name="redis"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/redis"),
            type="compose",
            services=[ModuleServiceModel(name="redis", image="redis:7"), ModuleServiceModel(name="sidecar")],
            default_labels={},
        ),
    )
    version = _version(image_pins={"redis": "redis:7.2"})

    overlaid = apply_version_pins(module, version)

    assert overlaid is not module
    assert overlaid.spec.services[0].image == "redis:7.2"
    assert overlaid.spec.services[1] is module.spec.services[1]  # untouched sibling, same object
    assert module.spec.services[0].image == "redis:7"  # original untouched


def test_apply_version_pins_ignores_image_pin_when_service_has_no_image_of_its_own():
    """A service using `.artifact` instead of `.image` has nothing to
    overlay \u2014 a pin changes a version, never which field is in use."""
    module = ModuleModel(
        meta=ModuleMetaModel(name="app"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/app"),
            type="compose",
            services=[ModuleServiceModel(name="app", artifact="dspapi_container")],
            default_labels={},
        ),
    )
    version = _version(image_pins={"app": "ignored:1.0"})

    assert apply_version_pins(module, version) is module


def test_apply_version_pins_logs_each_application():
    import io
    import json

    from strata.logging.config import configure_logging, shutdown_logging

    module = ModuleModel(
        meta=ModuleMetaModel(name="redis"),
        spec=ModuleSpecModel(
            source=SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.1.0"),
            type="helm",
            services=[ModuleServiceModel(name="redis", image="redis:7")],
            default_labels={},
        ),
    )
    version = _version(chart_pins={"redis": "2024.2.0"}, image_pins={"redis": "redis:7.2"})

    stream = io.StringIO()
    try:
        configure_logging(level="INFO", json_output=True, stream=stream)
        apply_version_pins(module, version)
    finally:
        shutdown_logging()

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    categories = {entry["category"] for entry in lines}
    assert categories == {"charts", "images"}


# ---------------------------------------------------------------------------
# resolve_artifact_services() (docs/work/artifact-references.md Path 1)
# ---------------------------------------------------------------------------


def _module_with_artifact(name: str, artifact_name: str) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"services/{name}"),
            type="compose",
            services=[ModuleServiceModel(name=name, artifact=artifact_name)],
            default_labels={},
        ),
    )


def test_resolve_artifact_services_is_a_no_op_when_context_is_none():
    module = _module_with_artifact("app", "dspapi_container")
    assert resolve_artifact_services(module, None, _deployment()) is module


def test_resolve_artifact_services_is_a_no_op_when_deployment_is_none(tmp_path: Path):
    module = _module_with_artifact("app", "dspapi_container")
    context = _context(tmp_path, _index())
    assert resolve_artifact_services(module, context, None) is module


def test_resolve_artifact_services_is_a_no_op_when_no_service_has_artifact(tmp_path: Path):
    module = _module("app")  # plain .image service, no .artifact
    context = _context(tmp_path, _index())
    assert resolve_artifact_services(module, context, _deployment()) is module


def test_resolve_artifact_services_overrides_image_and_clears_artifact(tmp_path: Path):
    module = _module_with_artifact("dispatcher", "dspapi_container")
    index = DocumentIndex()
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.MODULE, name="dispatcher"), model=module, source=Path("dispatcher.yaml")
        )
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="dspapi_container"),
            model=_artifact(
                "dspapi_container", image_name="int-docker-test/src/acme.dispatcher.api", image_tag="1.2.3"
            ),
            source=Path("dspapi_container.yaml"),
        )
    )
    context = _context(tmp_path, index)

    resolved = resolve_artifact_services(module, context, _deployment())

    service = resolved.spec.services[0]
    assert service.image == "int-docker-test/src/acme.dispatcher.api:1.2.3"
    assert service.artifact is None


def test_resolve_artifact_services_leaves_other_services_untouched(tmp_path: Path):
    """A module with more than one service only overlays the one using
    `.artifact` — mirrors `apply_version_pins()`'s own multi-service
    treatment (`overrides.get(s.name, s)` fallback)."""
    module = ModuleModel(
        meta=ModuleMetaModel(name="app"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/app"),
            type="compose",
            services=[
                ModuleServiceModel(name="web", artifact="dspapi_container"),
                ModuleServiceModel(name="sidecar", image="redis:7"),
            ],
            default_labels={},
        ),
    )
    index = DocumentIndex()
    index.add(IndexEntry(ref=DocumentRef(kind=PlatformKind.MODULE, name="app"), model=module, source=Path("app.yaml")))
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="dspapi_container"),
            model=_artifact(
                "dspapi_container", image_name="int-docker-test/src/acme.dispatcher.api", image_tag="1.2.3"
            ),
            source=Path("dspapi_container.yaml"),
        )
    )
    context = _context(tmp_path, index)

    resolved = resolve_artifact_services(module, context, _deployment())

    services_by_name = {s.name: s for s in resolved.spec.services}
    assert services_by_name["web"].image == "int-docker-test/src/acme.dispatcher.api:1.2.3"
    assert services_by_name["web"].artifact is None
    assert services_by_name["sidecar"].image == "redis:7"


def test_resolve_artifact_services_raises_when_artifact_reference_does_not_resolve(tmp_path: Path):
    """Unreachable in a `require_valid()`-passed solution — exercised here by
    bypassing `validate_references()` on purpose, to prove the defensive
    backstop fires rather than silently skipping."""
    module = _module_with_artifact("dispatcher", "ghost")
    context = _context(tmp_path, _index())  # empty index — "ghost" never resolves

    with pytest.raises(UsageError, match="ghost"):
        resolve_artifact_services(module, context, _deployment())


# ---------------------------------------------------------------------------
# build_workload_modules() — end to end
# ---------------------------------------------------------------------------


def test_build_workload_modules_materialises_source_and_writes_helm_output(tmp_path: Path):
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    module_dir = build_path / "apps" / "auth"
    assert (module_dir / "Chart.yaml").read_text() == "name: authentik"
    assert (module_dir / "values.yaml").exists()
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    # releaseName defaults to the reference name ("auth"), not module.meta.name ("authentik").
    assert meta["releaseName"] == "auth"


def test_build_workload_modules_dry_run_writes_nothing(tmp_path: Path):
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    build_path = tmp_path / "build"

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=build_path,
        dry_run=True,
    )

    assert not build_path.exists()


def test_build_workload_modules_dry_run_still_validates_module_resolution(tmp_path: Path):
    namespace = _namespace(ModuleReferenceModel(name="auth", module="ghost"))

    with pytest.raises(UsageError, match="ghost"):
        build_workload_modules(
            _index(),
            tmp_path,
            remotes={},
            namespace=namespace,
            resolved=ValueResolution(deployment="app"),
            build_path=tmp_path / "build",
            dry_run=True,
        )


def test_build_workload_modules_dry_run_reports_planned_steps(tmp_path: Path):
    root = tmp_path / "sln"
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    steps: list[str] = []

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        dry_run=True,
        on_step=steps.append,
    )

    assert any("would materialise module 'auth'" in s for s in steps)
    assert any("would render helm workload for namespace 'apps' (auth)" in s for s in steps)
    assert not any(s.startswith("materialised") or s.startswith("rendered") for s in steps)


def test_build_workload_modules_real_run_reports_steps_too(tmp_path: Path):
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    steps: list[str] = []

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        on_step=steps.append,
    )

    assert any(s.startswith("materialised module 'auth' for namespace 'apps' at") for s in steps)
    assert any(s == "rendered helm workload for namespace 'apps'" for s in steps)


def test_build_workload_modules_writes_helm_output_for_a_registry_chart_module(tmp_path: Path):
    """Regression: a chart-based (registry) source has nothing to copy, so
    module_dir was never created before sync_module_source()'s fix - this
    previously crashed with a bare FileNotFoundError writing values.yaml/
    meta.yaml into a non-existent directory."""
    root = tmp_path / "sln"
    module = _module("authentik", source=SourceModel(remote="goauthentik", chart_name="authentik"))
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    module_dir = build_path / "apps" / "auth"
    assert (module_dir / "values.yaml").exists()
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta["chartName"] == "authentik"


def test_build_workload_modules_applies_chart_version_pin_end_to_end(tmp_path: Path):
    """The pinned chart version reaches the real rendered `meta.yaml` —
    deploy-time Helm reads it back from this file, never re-derives it
    (docs/design/version-pin-overlay.md Phase 4)."""
    root = tmp_path / "sln"
    module = _module(
        "authentik", source=SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.1.0")
    )
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))
    build_path = tmp_path / "build"
    version = _version(chart_pins={"authentik": "2024.2.0"})

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=build_path,
        version=version,
    )

    meta = yaml.safe_load((build_path / "apps" / "auth" / "meta.yaml").read_text())
    assert meta["chartVersion"] == "2024.2.0"


def test_build_workload_modules_applies_image_pin_end_to_end(tmp_path: Path):
    """The pinned image reaches the real rendered `docker-compose.yml`."""
    root = tmp_path / "sln"
    _write(root / "services" / "redis" / "docker-compose.yml", "# stand-in\n")
    module = ModuleModel(
        meta=ModuleMetaModel(name="redis"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/redis"),
            type="compose",
            services=[ModuleServiceModel(name="redis", image="redis:7")],
            default_labels={},
        ),
    )
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="redis", module="redis"))
    build_path = tmp_path / "build"
    version = _version(image_pins={"redis": "redis:7.2"})

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=build_path,
        version=version,
    )

    merged = yaml.safe_load((build_path / "apps" / "docker-compose.yml").read_text())
    assert merged["services"]["redis"]["image"] == "redis:7.2"


def test_build_workload_modules_resolves_artifact_reference_end_to_end(tmp_path: Path):
    """A service's `.artifact` reference reaches the real rendered
    `docker-compose.yml` as a resolved `.image` string (docs/work/
    artifact-references.md Path 1)."""
    root = tmp_path / "sln"
    _write(root / "services" / "dispatcher" / "docker-compose.yml", "# stand-in\n")
    module = ModuleModel(
        meta=ModuleMetaModel(name="dispatcher"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/dispatcher"),
            type="compose",
            services=[ModuleServiceModel(name="dispatcher", artifact="dspapi_container")],
            default_labels={},
        ),
    )
    index = _index(module)
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="dspapi_container"),
            model=_artifact(
                "dspapi_container", image_name="int-docker-test/src/acme.dispatcher.api", image_tag="1.2.3"
            ),
            source=Path("dspapi_container.yaml"),
        )
    )
    namespace = _namespace(ModuleReferenceModel(name="dispatcher", module="dispatcher"))
    build_path = tmp_path / "build"
    context = _context(root, index)

    build_workload_modules(
        index,
        root,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=build_path,
        context=context,
        deployment=_deployment(),
    )

    merged = yaml.safe_load((build_path / "apps" / "docker-compose.yml").read_text())
    assert merged["services"]["dispatcher"]["image"] == "int-docker-test/src/acme.dispatcher.api:1.2.3"


def test_build_workload_modules_keys_directories_by_reference_name_not_module_name(tmp_path: Path):
    """The same Module document attached twice under different reference
    names must not collide (ModuleReferenceModel's own docstring)."""
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(
        ModuleReferenceModel(name="auth-primary", module="authentik"),
        ModuleReferenceModel(name="auth-secondary", module="authentik"),
    )
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    assert (build_path / "apps" / "auth-primary" / "Chart.yaml").exists()
    assert (build_path / "apps" / "auth-secondary" / "Chart.yaml").exists()


def test_build_workload_modules_raises_for_an_unresolvable_reference(tmp_path: Path):
    namespace = _namespace(ModuleReferenceModel(name="auth", module="ghost"))

    with pytest.raises(UsageError, match="ghost"):
        build_workload_modules(
            _index(),
            tmp_path,
            remotes={},
            namespace=namespace,
            resolved=ValueResolution(deployment="app"),
            build_path=tmp_path / "build",
        )


def test_build_workload_modules_raises_when_module_type_is_unset(tmp_path: Path):
    module = _module("authentik", type=None)
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))

    with pytest.raises(UsageError, match="spec.type is required"):
        build_workload_modules(
            index,
            tmp_path,
            remotes={},
            namespace=namespace,
            resolved=ValueResolution(deployment="app"),
            build_path=tmp_path / "build",
        )


def test_build_workload_modules_renders_a_merged_compose_namespace(tmp_path: Path):
    """Compose merges every same-type module in the namespace into one
    docker-compose.yml (D6) - unlike Helm's one-pair-per-module."""
    root = tmp_path / "sln"
    _write(root / "services" / "caddy" / "docker-compose.yml", "# stand-in\n")
    _write(root / "services" / "portainer" / "docker-compose.yml", "# stand-in\n")
    caddy = _module(
        "caddy",
        type="compose",
        source=SourceModel(source_path="services/caddy"),
    )
    portainer = _module(
        "portainer",
        type="compose",
        source=SourceModel(source_path="services/portainer"),
    )
    index = _index(caddy, portainer)
    namespace = _namespace(
        ModuleReferenceModel(name="caddy", module="caddy"),
        ModuleReferenceModel(name="portainer", module="portainer"),
    )
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    # Each module's own source is still materialised into its own directory...
    assert (build_path / "apps" / "caddy" / "docker-compose.yml").read_text() == "# stand-in\n"
    assert (build_path / "apps" / "portainer" / "docker-compose.yml").read_text() == "# stand-in\n"
    # ...but the RENDERED compose file is the one shared, merged namespace file.
    merged = yaml.safe_load((build_path / "apps" / "docker-compose.yml").read_text())
    assert set(merged["services"]) == {"caddy", "portainer"}


def test_build_workload_modules_raises_usage_error_for_an_unregistered_type(tmp_path: Path):
    """A workload module type with no registered integration at all (built-in
    or plugin) must surface as a clean UsageError, not the bare
    IntegrationNotFoundError `command_run()` cannot catch."""
    module = _module("legacy-app", type="argocd", source=SourceModel(remote="reg", chart_name="legacy-app"))
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="legacy-app", module="legacy-app"))

    with pytest.raises(UsageError, match="argocd"):
        build_workload_modules(
            index,
            tmp_path,
            remotes={},
            namespace=namespace,
            resolved=ValueResolution(deployment="app"),
            build_path=tmp_path / "build",
        )


def test_build_workload_modules_skips_a_disabled_reference_entirely(tmp_path: Path):
    """v1 parity: a disabled module reference must not be resolved,
    materialised, or rendered - matches
    terraform_projection._build_resources_payload()'s identical treatment
    of WorkspaceResourceModel.enabled=False (the same shared `enabled`
    field, on the resource-attachment side)."""
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module = _module("authentik")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik", enabled=False))
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    assert not (build_path / "apps" / "auth").exists()


def test_build_workload_modules_renders_enabled_references_alongside_a_disabled_one(tmp_path: Path):
    root = tmp_path / "sln"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    _write(root / "charts" / "caddy" / "Chart.yaml", "name: caddy")
    authentik = _module("authentik")
    caddy = _module("caddy")
    index = _index(authentik, caddy)
    namespace = _namespace(
        ModuleReferenceModel(name="auth", module="authentik", enabled=False),
        ModuleReferenceModel(name="proxy", module="caddy"),
    )
    build_path = tmp_path / "build"

    build_workload_modules(
        index, root, remotes={}, namespace=namespace, resolved=ValueResolution(deployment="app"), build_path=build_path
    )

    assert not (build_path / "apps" / "auth").exists()
    assert (build_path / "apps" / "proxy" / "values.yaml").exists()


def test_build_workload_modules_disabled_reference_does_not_need_to_resolve(tmp_path: Path):
    """A disabled reference is skipped before resolve_module() is ever
    called - it can name a module that does not even exist in the index."""
    namespace = _namespace(ModuleReferenceModel(name="ghost", module="does-not-exist", enabled=False))

    build_workload_modules(
        _index(),
        tmp_path,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
    )


def test_build_workload_modules_namespace_with_no_enabled_modules_is_a_graceful_no_op(tmp_path: Path):
    """Every reference disabled (even across different module types) - the
    by_type dict never gains an entry, so the second loop never runs at
    all: no resolve_module_integration() call, no prepare_namespace() call,
    nothing written, no error. Same outcome as a namespace with zero
    modules attached, just reached a different way."""
    authentik = _module("authentik", type="helm")
    portainer = _module("portainer", type="compose", source=SourceModel(source_path="services/portainer"))
    index = _index(authentik, portainer)
    namespace = _namespace(
        ModuleReferenceModel(name="auth", module="authentik", enabled=False),
        ModuleReferenceModel(name="proxy", module="portainer", enabled=False),
    )
    build_path = tmp_path / "build"

    build_workload_modules(
        index,
        tmp_path,
        remotes={},
        namespace=namespace,
        resolved=ValueResolution(deployment="app"),
        build_path=build_path,
    )

    assert not build_path.exists()


# NOTE: "namespace with no modules" is not separately testable through a
# real NamespaceModel - NamespaceSpecModel.validate_namespace_spec() already
# requires lifecycle and/or modules (empty namespaces are rejected at
# construction), so `namespace.spec.modules or []` never actually sees an
# empty list for a real, validated namespace. build_workload_modules()'s own
# `for reference in namespace.spec.modules or []` is a defensive fallback
# for that reason, not a reachable path via a valid solution.
