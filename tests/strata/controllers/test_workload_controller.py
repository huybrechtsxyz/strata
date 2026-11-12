#!/usr/bin/env python3
"""Tests for `workload_controller` (ADR-0022 D5-D7)."""

import functools
import gzip
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import threading
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
from strata.integrations.helm_cache import archive_digest, entry_path
from strata.integrations.resolved_context import ValueResolution
from strata.models.artifact_model import ArtifactMetaModel, ArtifactModel, ArtifactSpecModel
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.deployment_model import DeploymentMetaModel, DeploymentModel, DeploymentSpecModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.version_model import VersionMetaModel, VersionModel, VersionSpecModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.transport import CommandResult


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
            services=[ModuleServiceModel(name="app", artifact="api_container")],
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


def test_apply_version_pins_without_logging_still_applies_every_pin(monkeypatch):
    """`log=False` is for callers that only re-derive what the build applied (`effective_images()`, the SBOM, the
    audit manifest): the overlay is the same, nothing is logged."""
    import strata.controllers.workload_controller as workload_controller

    logged: list[str] = []
    monkeypatch.setattr(workload_controller, "log_pin_applied", lambda category, *_args: logged.append(category))
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

    overlaid = apply_version_pins(module, version, log=False)

    assert (overlaid.spec.source.chart_version, overlaid.spec.services[0].image) == ("2024.2.0", "redis:7.2")
    assert logged == []
    apply_version_pins(module, version)
    assert logged == ["charts", "images"]


# ---------------------------------------------------------------------------
# resolve_artifact_services() (docs/archive/artifact-references.md Path 1)
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
    module = _module_with_artifact("app", "api_container")
    assert resolve_artifact_services(module, None, _deployment()) is module


def test_resolve_artifact_services_is_a_no_op_when_deployment_is_none(tmp_path: Path):
    module = _module_with_artifact("app", "api_container")
    context = _context(tmp_path, _index())
    assert resolve_artifact_services(module, context, None) is module


def test_resolve_artifact_services_is_a_no_op_when_no_service_has_artifact(tmp_path: Path):
    module = _module("app")  # plain .image service, no .artifact
    context = _context(tmp_path, _index())
    assert resolve_artifact_services(module, context, _deployment()) is module


def test_resolve_artifact_services_overrides_image_and_clears_artifact(tmp_path: Path):
    module = _module_with_artifact("scheduler", "api_container")
    index = DocumentIndex()
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.MODULE, name="scheduler"), model=module, source=Path("scheduler.yaml")
        )
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="api_container"),
            model=_artifact("api_container", image_name="example/scheduler-api", image_tag="1.2.3"),
            source=Path("api_container.yaml"),
        )
    )
    context = _context(tmp_path, index)

    resolved = resolve_artifact_services(module, context, _deployment())

    service = resolved.spec.services[0]
    assert service.image == "example/scheduler-api:1.2.3"
    assert service.artifact is None


@pytest.mark.parametrize(
    ("image_tag", "image"),
    [
        ("sha256:" + "ab" * 32, "example/scheduler-api@sha256:" + "ab" * 32),
        ("1.2.3@sha256:" + "ab" * 32, "example/scheduler-api:1.2.3@sha256:" + "ab" * 32),
    ],
)
def test_resolve_artifact_services_writes_a_digest_as_a_runtime_pulls_it(tmp_path: Path, image_tag, image):
    """`1.2.3@sha256:...` gives `name:1.2.3@sha256:...`, never `name@1.2.3@sha256:...`
    (docs/archive/artifact-gap-analysis.md, design 4)."""
    module = _module_with_artifact("scheduler", "api_container")
    index = DocumentIndex()
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.MODULE, name="scheduler"), model=module, source=Path("scheduler.yaml")
        )
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="api_container"),
            model=_artifact("api_container", image_name="example/scheduler-api", image_tag=image_tag),
            source=Path("api_container.yaml"),
        )
    )

    resolved = resolve_artifact_services(module, _context(tmp_path, index), _deployment())

    assert resolved.spec.services[0].image == image


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
                ModuleServiceModel(name="web", artifact="api_container"),
                ModuleServiceModel(name="sidecar", image="redis:7"),
            ],
            default_labels={},
        ),
    )
    index = DocumentIndex()
    index.add(IndexEntry(ref=DocumentRef(kind=PlatformKind.MODULE, name="app"), model=module, source=Path("app.yaml")))
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="api_container"),
            model=_artifact("api_container", image_name="example/scheduler-api", image_tag="1.2.3"),
            source=Path("api_container.yaml"),
        )
    )
    context = _context(tmp_path, index)

    resolved = resolve_artifact_services(module, context, _deployment())

    services_by_name = {s.name: s for s in resolved.spec.services}
    assert services_by_name["web"].image == "example/scheduler-api:1.2.3"
    assert services_by_name["web"].artifact is None
    assert services_by_name["sidecar"].image == "redis:7"


def test_resolve_artifact_services_raises_when_artifact_reference_does_not_resolve(tmp_path: Path):
    """Unreachable in a `require_valid()`-passed solution — exercised here by
    bypassing `validate_references()` on purpose, to prove the defensive
    backstop fires rather than silently skipping."""
    module = _module_with_artifact("scheduler", "ghost")
    context = _context(tmp_path, _index())  # empty index — "ghost" never resolves

    with pytest.raises(UsageError, match="ghost"):
        resolve_artifact_services(module, context, _deployment())


# ---------------------------------------------------------------------------
# build_workload_modules() — end to end
# ---------------------------------------------------------------------------


def test_build_workload_modules_clones_a_module_source_with_the_resolved_credentials(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.controllers.remote_credentials import FetchCredentials
    from strata.models.auth_models import AuthenticationModel, TokenAuthenticationModel
    from strata.models.solution_model import RemoteType, SolutionRemoteModel
    from strata.utils.transport import CommandResult

    token = "ghp_workloadtoken123"
    envs: list[dict[str, str]] = []

    def _fake_git(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(dict(env or {}))
        if args[:2] == ["git", "clone"]:
            _write(Path(args[-1]) / "charts" / "authentik" / "Chart.yaml", "name: authentik")
        return CommandResult(returncode=0, stdout="", stderr="")

    class _Resolver:
        calls: list[tuple[str, str]] = []

        def for_integration(self, name, *, owner):
            self.calls.append((name, owner))
            auth = AuthenticationModel(method="token", token=TokenAuthenticationModel(token="GIT_TOKEN"))
            return FetchCredentials(auth=auth, values={"GIT_TOKEN": token})

        def redact(self, text):
            return text

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_git)
    remote = SolutionRemoteModel(
        name="infra",
        type=RemoteType.GIT,
        url="https://example.com/org/charts.git",
        reference="main",
        integration="customer-git",
        description=None,
    )
    module = _module("authentik", source=SourceModel(remote="infra", source_path="charts/authentik"))
    resolver = _Resolver()
    root = tmp_path / "sln"
    root.mkdir()

    build_workload_modules(
        _index(module),
        root,
        remotes={"infra": remote},
        namespace=_namespace(ModuleReferenceModel(name="auth", module="authentik")),
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        credentials=resolver,
    )

    assert resolver.calls == [("customer-git", "remote 'infra'")]
    assert envs[0]["STRATA_GIT_ASKPASS_TOKEN"] == token
    assert (tmp_path / "build" / "apps" / "auth" / "Chart.yaml").is_file()


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
    assert (module_dir / "strata-values.yaml").exists()
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    # releaseName defaults to the reference name ("auth"), not module.meta.name ("authentik").
    assert meta["releaseName"] == "auth"


def _helm_module_with_configuration(configuration: dict, *, source: SourceModel | None = None) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name="app"),
        spec=ModuleSpecModel(
            source=source or SourceModel(source_path="charts/app"),
            type="helm",
            services=[ModuleServiceModel(name="app")],
            configuration=configuration,
            default_labels={},
        ),
    )


def _build_with_chart_defaults(tmp_path: Path, module: ModuleModel, chart_values: str | None):
    root = tmp_path / "sln"
    _write(root / "charts" / "app" / "Chart.yaml", "name: app")
    if chart_values is not None:
        _write(root / "charts" / "app" / "values.yaml", chart_values)
    return build_workload_modules(
        _index(module),
        root,
        remotes={},
        namespace=_namespace(ModuleReferenceModel(name="app", module="app")),
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
    )


def test_build_workload_modules_warns_on_configuration_keys_a_local_chart_does_not_declare(tmp_path: Path):
    module = _helm_module_with_configuration({"replicaCnt": 2, "image": {"tagg": "1.0"}})

    diagnostics = _build_with_chart_defaults(tmp_path, module, "replicaCount: 1\nimage:\n  tag: latest\n")

    assert diagnostics.ok
    messages = [item.message for item in diagnostics.items]
    assert len(messages) == 2
    assert "'replicaCnt'" in messages[0] and "did you mean 'replicaCount'" in messages[0]
    assert "'image.tagg'" in messages[1] and "did you mean 'image.tag'" in messages[1]
    assert {item.code for item in diagnostics.items} == {"helm_unknown_configuration_key"}
    # The module's values go beside the chart's own defaults, which stay as they were copied.
    module_dir = tmp_path / "build" / "apps" / "app"
    assert "replicaCnt" in (module_dir / "strata-values.yaml").read_text()
    assert (module_dir / "values.yaml").read_text() == "replicaCount: 1\nimage:\n  tag: latest\n"


def test_build_workload_modules_is_quiet_when_every_configuration_key_is_declared(tmp_path: Path):
    module = _helm_module_with_configuration({"replicaCount": 2, "image": {"tag": "1.0"}})

    diagnostics = _build_with_chart_defaults(tmp_path, module, "replicaCount: 1\nimage:\n  tag: latest\n")

    assert diagnostics.items == []


def test_build_workload_modules_skips_the_key_check_without_chart_defaults(tmp_path: Path):
    module = _helm_module_with_configuration({"anything": 1})

    assert _build_with_chart_defaults(tmp_path, module, None).items == []


def test_build_workload_modules_cannot_key_check_an_unpinned_registry_chart(tmp_path: Path):
    module = _helm_module_with_configuration(
        {"anything": 1}, source=SourceModel(chart_repository="https://charts.example.com", chart_name="app")
    )

    diagnostics = _build_with_chart_defaults(tmp_path, module, "replicaCount: 1\n")

    assert diagnostics.ok
    assert [item.code for item in diagnostics.items] == ["helm_chart_not_pinned"]


# ---------------------------------------------------------------------------
# Registry charts pinned at build (docs/design/helm-chart-deployment.md, "Registry charts")
# ---------------------------------------------------------------------------

_REPOSITORY = "https://charts.example.com"


def _packaged_chart(destination: Path, name: str, version: str, values: str) -> Path:
    def add(tar: tarfile.TarFile, member: str, text: str) -> None:
        data = text.encode()
        info = tarfile.TarInfo(member)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    archive = destination / f"{name}-{version}.tgz"
    # A fixed gzip timestamp: a registry serves the same bytes on every pull.
    with gzip.GzipFile(archive, "wb", mtime=0) as packed, tarfile.open(fileobj=packed, mode="w") as tar:
        add(tar, f"{name}/Chart.yaml", f"apiVersion: v2\nname: {name}\nversion: {version}\nappVersion: '9.1'\n")
        add(tar, f"{name}/values.yaml", values)
        add(tar, f"{name}/charts/redis/Chart.yaml", "apiVersion: v2\nname: redis\nversion: 19.0.1\n")
    return archive


def _fake_helm(monkeypatch, *, version: str = "1.4.2", values: str = "replicaCount: 1\n", fail: str | None = None):
    """`helm` on PATH whose `pull` writes a packaged chart; returns every call's argv."""
    import strata.integrations.base as base_module

    calls: list[list[str]] = []

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        if args[1] == "pull":
            if fail is not None:
                return CommandResult(returncode=1, stdout="", stderr=fail)
            _packaged_chart(Path(args[args.index("--destination") + 1]), "app", version, values)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def _registry_build(tmp_path: Path, *, version: str | None = "^1.4.0", configuration=None, **kwargs):
    module = _helm_module_with_configuration(
        configuration or {}, source=SourceModel(chart_repository=_REPOSITORY, chart_name="app", chart_version=version)
    )
    steps: list[str] = []
    diagnostics = build_workload_modules(
        _index(module),
        tmp_path / "sln",
        remotes={},
        namespace=_namespace(ModuleReferenceModel(name="app", module="app")),
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        on_step=steps.append,
        **kwargs,
    )
    meta_file = tmp_path / "build" / "apps" / "app" / "meta.yaml"
    meta = yaml.safe_load(meta_file.read_text()) if meta_file.exists() else None
    return diagnostics, meta, steps


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "sln" / ".strata" / "helm"


@pytest.mark.chart_pulls
def test_a_registry_chart_is_pulled_and_pinned_in_meta_yaml(tmp_path: Path, monkeypatch):
    calls = _fake_helm(monkeypatch)

    diagnostics, meta, steps = _registry_build(tmp_path)

    assert diagnostics.items == []
    [pull] = [call for call in calls if call[1] == "pull"]
    assert pull[-2:] == ["--version", "^1.4.0"]
    [source] = [path.name for path in _cache(tmp_path).iterdir()]
    archive = entry_path(_cache(tmp_path), source, "app", "1.4.2") / "app-1.4.2.tgz"
    assert meta == {
        "releaseName": "app",
        "namespace": "apps",
        "chartName": "app",
        "chartRepository": _REPOSITORY,
        "chartVersion": "1.4.2",
        "chartVersionConstraint": "^1.4.0",
        "chartDigest": archive_digest(archive),
        "chartAppVersion": "9.1",
        "chartSubcharts": [{"name": "redis", "version": "19.0.1"}],
    }
    assert (
        f"pinned chart 'app' (^1.4.0) at 1.4.2 ({archive_digest(archive)}) for Namespace 'apps', module 'app'" in steps
    )


@pytest.mark.chart_pulls
def test_a_pull_runs_in_an_isolated_helm_configuration(tmp_path: Path, monkeypatch):
    import strata.integrations.base as base_module

    envs: list[dict] = []
    _fake_helm(monkeypatch)
    real = base_module.run_command

    def _recording(args, *, env=None, **kwargs):
        envs.append(dict(env or {}))
        return real(args, env=env, **kwargs)

    monkeypatch.setattr(base_module, "run_command", _recording)

    _registry_build(tmp_path)

    assert envs and all("HELM_REPOSITORY_CONFIG" in env for env in envs)


@pytest.mark.chart_pulls
def test_an_exact_version_is_written_without_a_constraint(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch)

    _, meta, _ = _registry_build(tmp_path, version="1.4.2")

    assert meta["chartVersion"] == "1.4.2"
    assert "chartVersionConstraint" not in meta


@pytest.mark.chart_pulls
def test_a_cached_exact_version_needs_no_helm(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch)
    _registry_build(tmp_path, version="1.4.2")
    calls = _fake_helm(monkeypatch)
    monkeypatch.setattr("strata.controllers.workload_controller.can_pull_charts", lambda integration: False)

    diagnostics, meta, steps = _registry_build(tmp_path, version="1.4.2")

    assert calls == []
    assert diagnostics.items == []
    assert meta["chartDigest"].startswith("sha256:")
    assert "using cached chart 'app' (1.4.2) for Namespace 'apps', module 'app'" in steps


@pytest.mark.chart_pulls
def test_a_range_is_pulled_again_on_every_build(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch)
    _registry_build(tmp_path)
    calls = _fake_helm(monkeypatch)

    _registry_build(tmp_path)

    assert [call[1] for call in calls if call[1] == "pull"] == ["pull"]


@pytest.mark.chart_pulls
def test_a_pinned_registry_chart_gets_the_configuration_key_check(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch, values="replicaCount: 1\n")

    diagnostics, _, _ = _registry_build(tmp_path, configuration={"replicaCnt": 2})

    [finding] = diagnostics.items
    assert finding.code == "helm_unknown_configuration_key"
    assert "'replicaCnt'" in finding.message and "did you mean 'replicaCount'" in finding.message


def test_without_helm_a_registry_chart_is_left_unpinned(tmp_path: Path):
    diagnostics, meta, _ = _registry_build(tmp_path)

    assert diagnostics.ok
    [warning] = diagnostics.items
    assert warning.code == "helm_chart_not_pinned"
    assert warning.message == (
        "Namespace 'apps', module 'app': chart 'app' (^1.4.0) is not pinned: 'helm' is not installed. A deploy of "
        "this build stops at this chart; build again where the chart can be pulled."
    )
    assert meta["chartVersion"] == "^1.4.0"
    assert "chartDigest" not in meta


@pytest.mark.chart_pulls
def test_offline_pulls_nothing_and_leaves_an_uncached_chart_unpinned(tmp_path: Path, monkeypatch):
    calls = _fake_helm(monkeypatch)

    diagnostics, meta, _ = _registry_build(tmp_path, offline=True)

    assert calls == []
    [warning] = diagnostics.items
    assert "is not pinned: --offline was given and it is not in .strata/helm/" in warning.message
    assert "chartDigest" not in meta


@pytest.mark.chart_pulls
def test_offline_still_pins_a_cached_exact_version(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch)
    _registry_build(tmp_path, version="1.4.2")

    diagnostics, meta, _ = _registry_build(tmp_path, version="1.4.2", offline=True)

    assert diagnostics.items == []
    assert "chartDigest" in meta


@pytest.mark.chart_pulls
def test_a_failed_pull_is_an_error(tmp_path: Path, monkeypatch):
    _fake_helm(monkeypatch, fail='Error: chart "app" matching ^1.4.0 not found')

    diagnostics, _, _ = _registry_build(tmp_path)

    [error] = diagnostics.errors
    assert error.code == "helm_chart_pull_failed"
    assert error.message == (
        "Namespace 'apps', module 'app': could not pull chart 'app' (^1.4.0) — "
        'Error: chart "app" matching ^1.4.0 not found'
    )


@pytest.mark.chart_pulls
def test_a_dry_run_reports_the_pull_and_pulls_nothing(tmp_path: Path, monkeypatch):
    calls = _fake_helm(monkeypatch)

    diagnostics, meta, steps = _registry_build(tmp_path, dry_run=True)

    assert calls == [] and meta is None and diagnostics.items == []
    assert "would pull chart 'app' (^1.4.0) for Namespace 'apps', module 'app'" in steps


class _Credentials:
    """A `RemoteCredentialResolver` stand-in for the registry path."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def for_registry(self, name, *, owner):
        if self.error is not None:
            raise self.error
        return None


@pytest.mark.chart_pulls
@pytest.mark.parametrize(
    ("error", "severity_code"),
    [
        ("needs-resolve", ("warning", "helm_chart_not_pinned")),
        ("invalid", ("error", "helm_registry_credentials_invalid")),
    ],
)
def test_registry_credentials_that_need_resolve_unpin_and_broken_ones_fail(tmp_path, monkeypatch, error, severity_code):
    from strata.controllers.remote_credentials import CredentialsNeedResolveError, RemoteCredentialError
    from strata.models.solution_model import SolutionRemoteModel

    calls = _fake_helm(monkeypatch)
    raised = (
        CredentialsNeedResolveError(
            "remote 'charts' (integration 'reg'): 'PW' is stored in 'infisical'; pass --resolve"
        )
        if error == "needs-resolve"
        else RemoteCredentialError("remote 'charts' (integration 'reg'): integration 'reg' not found")
    )
    module = _helm_module_with_configuration(
        {}, source=SourceModel(remote="charts", chart_name="app", chart_version="1.4.2")
    )
    remote = SolutionRemoteModel(name="charts", type="helm", url=_REPOSITORY, integration="reg")

    diagnostics = build_workload_modules(
        _index(module),
        tmp_path / "sln",
        remotes={"charts": remote},
        namespace=_namespace(ModuleReferenceModel(name="app", module="app")),
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        credentials=_Credentials(raised),  # type: ignore[arg-type]
    )

    [finding] = diagnostics.items
    assert (finding.severity.value, finding.code) == severity_code
    assert calls == []


# ---------------------------------------------------------------------------
# Local chart dependencies placed at build (docs/design/helm-chart-deployment.md, "Chart dependencies")
# ---------------------------------------------------------------------------


def _fake_dependency_helm(monkeypatch, *, resolved: str = "19.0.4", dependency_list: str = ""):
    """`helm` on PATH whose `pull` writes the chart the ref names, at an exact `--version` or else `resolved`."""
    import strata.integrations.base as base_module

    calls: list[list[str]] = []

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        if args[1] == "pull":
            name = args[2].rsplit("/", 1)[-1]
            asked = args[args.index("--version") + 1] if "--version" in args else None
            version = asked if asked and asked[0].isdigit() else resolved
            _packaged_chart(Path(args[args.index("--destination") + 1]), name, version, "")
        if args[1:3] == ["dependency", "list"]:
            return CommandResult(returncode=0, stdout=dependency_list, stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def _dependent_build(tmp_path: Path, dependencies: str, *, lock: str | None = None, configuration=None, **kwargs):
    root = tmp_path / "sln"
    _write(
        root / "charts" / "app" / "Chart.yaml",
        f"apiVersion: v2\nname: app\nversion: 1.0.0\ndependencies:\n{dependencies}",
    )
    _write(root / "charts" / "app" / "values.yaml", "replicaCount: 1\n")
    if lock is not None:
        _write(root / "charts" / "app" / "Chart.lock", lock)
    module = _helm_module_with_configuration(configuration or {})
    steps: list[str] = []
    diagnostics = build_workload_modules(
        _index(module),
        root,
        remotes=kwargs.pop("remotes", {}),
        namespace=_namespace(ModuleReferenceModel(name="app", module="app")),
        resolved=ValueResolution(deployment="app"),
        build_path=tmp_path / "build",
        on_step=steps.append,
        **kwargs,
    )
    module_dir = tmp_path / "build" / "apps" / "app"
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    return diagnostics, meta, steps, module_dir


_SEPV = f"  - name: sepv\n    version: 99.99.343\n    repository: {_REPOSITORY}\n"


@pytest.mark.chart_pulls
def test_an_exact_dependency_is_pulled_into_charts_and_recorded(tmp_path: Path, monkeypatch):
    calls = _fake_dependency_helm(monkeypatch)

    diagnostics, meta, steps, module_dir = _dependent_build(tmp_path, _SEPV)

    assert diagnostics.items == []
    [pull] = [call for call in calls if call[1] == "pull"]
    assert pull[-2:] == ["--version", "99.99.343"]
    archive = module_dir / "charts" / "sepv-99.99.343.tgz"
    assert archive.is_file()
    assert meta["chartDependencies"] == [
        {
            "name": "sepv",
            "version": "99.99.343",
            "repository": _REPOSITORY,
            "digest": archive_digest(archive),
            "origin": "pulled",
        }
    ]
    assert "chartDependenciesUnpinned" not in meta
    # Strata never writes Helm's lock, in the source or in the build copy.
    assert not (tmp_path / "sln" / "charts" / "app" / "Chart.lock").exists()
    assert not (module_dir / "Chart.lock").exists()
    assert ["dependency", "list", str(module_dir)] == [call for call in calls if call[1] == "dependency"][0][1:]


@pytest.mark.chart_pulls
def test_a_cached_exact_dependency_needs_no_helm(tmp_path: Path, monkeypatch):
    _fake_dependency_helm(monkeypatch)
    _dependent_build(tmp_path, _SEPV)
    calls = _fake_dependency_helm(monkeypatch)
    monkeypatch.setattr("strata.controllers.workload_controller.can_pull_charts", lambda integration: False)

    diagnostics, meta, steps, module_dir = _dependent_build(tmp_path, _SEPV)

    assert calls == []
    assert diagnostics.items == []
    assert meta["chartDependencies"][0]["origin"] == "cache"
    assert (module_dir / "charts" / "sepv-99.99.343.tgz").is_file()
    assert "using cached dependency 'sepv' (99.99.343) for Namespace 'apps', module 'app'" in steps


@pytest.mark.chart_pulls
def test_a_range_dependency_is_resolved_and_its_constraint_recorded(tmp_path: Path, monkeypatch):
    _fake_dependency_helm(monkeypatch, resolved="19.0.4")

    diagnostics, meta, _, module_dir = _dependent_build(
        tmp_path, f"  - name: redis\n    version: ~19.0\n    repository: {_REPOSITORY}\n"
    )

    assert diagnostics.items == []
    [record] = meta["chartDependencies"]
    assert (record["version"], record["versionConstraint"]) == ("19.0.4", "~19.0")
    assert (module_dir / "charts" / "redis-19.0.4.tgz").is_file()


@pytest.mark.chart_pulls
def test_a_committed_lock_decides_the_pulled_version(tmp_path: Path, monkeypatch):
    calls = _fake_dependency_helm(monkeypatch)

    diagnostics, meta, _, _ = _dependent_build(
        tmp_path,
        f"  - name: redis\n    version: ~19.0\n    repository: {_REPOSITORY}\n",
        lock=f"dependencies:\n- name: redis\n  repository: {_REPOSITORY}\n  version: 19.0.2\n",
    )

    assert diagnostics.items == []
    [pull] = [call for call in calls if call[1] == "pull"]
    assert pull[-2:] == ["--version", "19.0.2"]
    assert meta["chartDependencies"][0]["versionConstraint"] == "~19.0"


def test_without_helm_a_dependency_is_left_unpinned(tmp_path: Path):
    diagnostics, meta, _, module_dir = _dependent_build(tmp_path, _SEPV)

    assert diagnostics.ok
    [warning] = diagnostics.items
    assert warning.code == "helm_chart_dependency_not_pinned"
    assert warning.message == (
        "Namespace 'apps', module 'app': dependency 'sepv' (99.99.343) is not pinned: 'helm' is not installed. A "
        "deploy of this build stops at this chart; build again where the dependency can be pulled."
    )
    assert meta["chartDependenciesUnpinned"] == ["sepv"]
    assert not (module_dir / "charts").exists()


def test_a_file_dependency_is_copied_without_helm(tmp_path: Path):
    root = tmp_path / "sln"
    _write(root / "charts" / "common" / "Chart.yaml", "apiVersion: v2\nname: common\nversion: 0.1.0\n")

    diagnostics, meta, _, module_dir = _dependent_build(
        tmp_path, "  - name: common\n    version: 0.1.0\n    repository: file://../common\n"
    )

    assert diagnostics.items == []
    assert (module_dir / "charts" / "common" / "Chart.yaml").is_file()
    assert meta["chartDependencies"] == [
        {"name": "common", "version": "0.1.0", "repository": "file://../common", "origin": "file"}
    ]


def test_a_file_dependency_may_not_leave_the_repository(tmp_path: Path):
    _write(tmp_path / "outside" / "Chart.yaml", "apiVersion: v2\nname: outside\nversion: 0.1.0\n")

    diagnostics, _, _, _ = _dependent_build(
        tmp_path, "  - name: outside\n    version: 0.1.0\n    repository: file://../../../outside\n"
    )

    [error] = diagnostics.errors
    assert error.code == "helm_chart_dependency_outside_source"


def test_a_committed_dependency_is_recorded_and_not_pulled(tmp_path: Path):
    _write(tmp_path / "sln" / "charts" / "app" / "charts" / "sepv" / "Chart.yaml", "name: sepv\nversion: 99.99.343\n")

    diagnostics, meta, _, _ = _dependent_build(tmp_path, _SEPV)

    assert diagnostics.items == []
    assert meta["chartDependencies"] == [
        {"name": "sepv", "version": "99.99.343", "repository": _REPOSITORY, "origin": "committed"}
    ]


@pytest.mark.chart_pulls
def test_a_dependency_on_a_declared_remotes_url_uses_its_credentials(tmp_path: Path, monkeypatch):
    from strata.models.solution_model import SolutionRemoteModel

    _fake_dependency_helm(monkeypatch)
    asked: list[str] = []

    class _Recording(_Credentials):
        def for_registry(self, name, *, owner):
            asked.append(owner)
            return None

    remote = SolutionRemoteModel(name="feed", type="helm", url=_REPOSITORY + "/", integration="reg")

    diagnostics, meta, _, _ = _dependent_build(tmp_path, _SEPV, remotes={"feed": remote}, credentials=_Recording())

    assert diagnostics.items == []
    assert asked == ["remote 'feed' (integration 'reg')"]
    assert meta["chartDependencies"][0]["repository"] == _REPOSITORY


@pytest.mark.chart_pulls
def test_a_dependency_helm_lists_as_wrong_version_is_an_error(tmp_path: Path, monkeypatch):
    _fake_dependency_helm(
        monkeypatch,
        dependency_list="NAME\tVERSION\tREPOSITORY\tSTATUS\nsepv\t99.99.343\thttps://x\twrong version\n",
    )

    diagnostics, _, _, _ = _dependent_build(tmp_path, _SEPV)

    [error] = diagnostics.errors
    assert error.code == "helm_chart_dependency_wrong_version"
    assert "dependency 'sepv' is 'wrong version'" in error.message


def test_an_out_of_sync_lock_is_an_error(tmp_path: Path):
    diagnostics, _, _, _ = _dependent_build(
        tmp_path, _SEPV, lock="dependencies:\n- name: other\n  repository: https://x\n  version: 1.0.0\n"
    )

    [error] = diagnostics.errors
    assert error.code == "helm_chart_dependency_lock_out_of_sync"


@pytest.mark.chart_pulls
@pytest.mark.skipif(not shutil.which("helm"), reason="helm is not installed")
def test_real_helm_installs_from_the_dependencies_the_build_placed(tmp_path: Path):
    """A remote dependency served over local HTTP and a `file://` one; `helm template` then renders the build copy."""
    root = tmp_path / "sln"
    repo = tmp_path / "repo"
    repo.mkdir()
    helm_home = tmp_path / "helm-home"
    env = {
        **os.environ,
        "HELM_REPOSITORY_CONFIG": str(helm_home / "repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(helm_home / "cache"),
        "HELM_CACHE_HOME": str(helm_home / "cache-home"),
    }

    def helm(*args: str) -> str:
        return subprocess.run(["helm", *args], check=True, capture_output=True, text=True, env=env, timeout=60).stdout

    _write(tmp_path / "src" / "sub" / "Chart.yaml", "apiVersion: v2\nname: sub\nversion: 1.2.0\n")
    _write(tmp_path / "src" / "sub" / "templates" / "cm.yaml", "kind: ConfigMap\nmetadata:\n  name: sub\n")
    helm("package", str(tmp_path / "src" / "sub"), "--destination", str(repo))
    _write(root / "charts" / "common" / "Chart.yaml", "apiVersion: v2\nname: common\nversion: 0.1.0\n")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        helm("repo", "index", str(repo), "--url", url)
        diagnostics, meta, _, module_dir = _dependent_build(
            tmp_path,
            f"  - name: sub\n    version: ~1.2.0\n    repository: {url}\n"
            "  - name: common\n    version: 0.1.0\n    repository: file://../common\n",
        )
    finally:
        server.shutdown()

    assert diagnostics.items == [], diagnostics.messages()
    assert [(d["name"], d["version"], d["origin"]) for d in meta["chartDependencies"]] == [
        ("sub", "1.2.0", "pulled"),
        ("common", "0.1.0", "file"),
    ]
    assert "name: sub" in helm("template", "x", str(module_dir))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's parameter name
        del format, args


def test_configuration_for_a_subchart_is_not_an_unknown_key(tmp_path: Path):
    diagnostics, _, _, _ = _dependent_build(
        tmp_path, _SEPV, configuration={"sepv": {"enabled": True}, "global": {"domain": "x"}, "replicaCnt": 2}
    )

    keys = [item for item in diagnostics.items if item.code == "helm_unknown_configuration_key"]
    assert len(keys) == 1 and "'replicaCnt'" in keys[0].message


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
    assert (module_dir / "strata-values.yaml").exists()
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
    `docker-compose.yml` as a resolved `.image` string (docs/archive/
    artifact-references.md Path 1)."""
    root = tmp_path / "sln"
    _write(root / "services" / "scheduler" / "docker-compose.yml", "# stand-in\n")
    module = ModuleModel(
        meta=ModuleMetaModel(name="scheduler"),
        spec=ModuleSpecModel(
            source=SourceModel(source_path="services/scheduler"),
            type="compose",
            services=[ModuleServiceModel(name="scheduler", artifact="api_container")],
            default_labels={},
        ),
    )
    index = _index(module)
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.ARTIFACT, name="api_container"),
            model=_artifact("api_container", image_name="example/scheduler-api", image_tag="1.2.3"),
            source=Path("api_container.yaml"),
        )
    )
    namespace = _namespace(ModuleReferenceModel(name="scheduler", module="scheduler"))
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
    assert merged["services"]["scheduler"]["image"] == "example/scheduler-api:1.2.3"


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
    assert (build_path / "apps" / "proxy" / "strata-values.yaml").exists()


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
