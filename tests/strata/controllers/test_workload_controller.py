#!/usr/bin/env python3
"""Tests for `workload_controller` (ADR-0022 D5-D7)."""

from pathlib import Path

import pytest
import yaml

from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.controllers.workload_controller import build_workload_modules, resolve_module
from strata.integrations.resolved_context import ValueResolution
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
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
            _index(), tmp_path, remotes={}, namespace=namespace,
            resolved=ValueResolution(deployment="app"), build_path=tmp_path / "build",
        )


def test_build_workload_modules_raises_when_module_type_is_unset(tmp_path: Path):
    module = _module("authentik", type=None)
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="auth", module="authentik"))

    with pytest.raises(UsageError, match="spec.type is required"):
        build_workload_modules(
            index, tmp_path, remotes={}, namespace=namespace,
            resolved=ValueResolution(deployment="app"), build_path=tmp_path / "build",
        )


def test_build_workload_modules_renders_a_merged_compose_namespace(tmp_path: Path):
    """Compose merges every same-type module in the namespace into one
    docker-compose.yml (D6) - unlike Helm's one-pair-per-module."""
    root = tmp_path / "sln"
    _write(root / "services" / "caddy" / "docker-compose.yml", "# stand-in\n")
    _write(root / "services" / "portainer" / "docker-compose.yml", "# stand-in\n")
    caddy = _module(
        "caddy", type="compose", source=SourceModel(source_path="services/caddy"),
    )
    portainer = _module(
        "portainer", type="compose", source=SourceModel(source_path="services/portainer"),
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
            index, tmp_path, remotes={}, namespace=namespace,
            resolved=ValueResolution(deployment="app"), build_path=tmp_path / "build",
        )


# NOTE: "namespace with no modules" is not separately testable through a
# real NamespaceModel - NamespaceSpecModel.validate_namespace_spec() already
# requires lifecycle and/or modules (empty namespaces are rejected at
# construction), so `namespace.spec.modules or []` never actually sees an
# empty list for a real, validated namespace. build_workload_modules()'s own
# `for reference in namespace.spec.modules or []` is a defensive fallback
# for that reason, not a reachable path via a valid solution.
