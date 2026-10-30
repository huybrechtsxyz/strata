#!/usr/bin/env python3
"""Tests for `effective_images()` (docs/work/effective-images.md)."""

from pathlib import Path

from strata.controllers.effective_images import effective_images
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(
    tmp_path: Path,
    *,
    artifact_spec: str = "  image_name: int-docker-test/src/datahub.api\n  image_tag: '1.0.0'\n",
    variable_artifacts: tuple[str, ...] = ("datahub",),
    services: str = "    - name: redis\n      image: redis:7\n",
    pins: str = "",
    module_enabled: bool = True,
    deployment_version: bool = True,
) -> Path:
    """One workspace reaching one compose module, one artifact `datahub`, one unreferenced artifact `orphan`."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(
        root,
        "module.yaml",
        "module",
        "stack",
        "  source:\n    source_path: services/stack\n  type: compose\n  default_labels:\n    app: stack\n"
        f"  services:\n{services}",
    )
    enabled = "" if module_enabled else "      enabled: false\n"
    _write(
        root,
        "namespace.yaml",
        "namespace",
        "apps",
        f"  default_labels:\n    app: apps\n  modules:\n    - name: stack\n      module: stack\n{enabled}",
    )
    _write(root, "artifact.yaml", "artifact", "datahub", artifact_spec)
    _write(root, "orphan.yaml", "artifact", "orphan", "  image_name: int-docker-test/src/orphan\n")
    variables = "".join(
        f"    - key: {name.upper()}_TAG\n      store: artifact\n      value: {name}\n      field: image_tag\n"
        for name in variable_artifacts
    )
    _write(root, "environment.yaml", "environment", "prd", f"  variables:\n{variables}" if variables else "  {}\n")
    if pins:
        _write(root, "version.yaml", "version", "prd", f"  pins:\n{pins}")
    version = "  version: prd\n" if pins and deployment_version else ""
    _write(root, "deployment.yaml", "deployment", "app", f"  workspace: main\n{version}  environments:\n    - prd\n")
    return root


def _images(root: Path, *, pin: str | None = None):
    context = open_solution(root).require_valid()
    deployment = resolve_deployment(context, "app", version_pin=pin)
    return {row.name: row for row in effective_images(context, deployment, pinned_by_flag=pin is not None)}


def test_a_store_artifact_variable_yields_an_artifact_row(tmp_path):
    row = _images(_solution(tmp_path))["datahub"]
    assert (row.origin, row.artifact, row.module) == ("artifact", "datahub", None)
    assert (row.image_name, row.image_tag, row.image_ref) == (
        "int-docker-test/src/datahub.api",
        "1.0.0",
        "int-docker-test/src/datahub.api:1.0.0",
    )
    assert (row.tag_source, row.pin_version, row.pin_flag) == ("declared", None, False)
    assert row.via == ("variable:DATAHUB_TAG",)


def test_a_version_pin_overrides_the_declared_tag_and_names_its_document(tmp_path):
    row = _images(_solution(tmp_path, pins="    artifacts:\n      datahub: '2.0.0'\n"))["datahub"]
    assert (row.image_tag, row.tag_source, row.pin_version, row.pin_flag) == ("2.0.0", "pin", "prd", False)


def test_a_pin_flag_is_reported_for_a_pin_given_with_dash_dash_pin(tmp_path):
    root = _solution(tmp_path, pins="    artifacts:\n      datahub: '2.0.0'\n", deployment_version=False)
    row = _images(root, pin="prd")["datahub"]
    assert (row.image_tag, row.tag_source, row.pin_flag) == ("2.0.0", "pin", True)


def test_an_artifact_with_no_tag_and_no_pin_has_none_and_a_bare_ref(tmp_path):
    row = _images(_solution(tmp_path, artifact_spec="  image_name: int-docker-test/src/datahub.api\n"))["datahub"]
    assert (row.image_tag, row.tag_source) == (None, "none")
    assert row.image_ref == "int-docker-test/src/datahub.api"


def test_a_digest_tag_makes_an_at_reference(tmp_path):
    spec = "  image_name: int/api\n  image_tag: 'sha256:abc123'\n"
    assert _images(_solution(tmp_path, artifact_spec=spec))["datahub"].image_ref == "int/api@sha256:abc123"


def test_registry_and_configuration_are_carried_on_the_row(tmp_path):
    spec = "  image_name: int/api\n  registry: cdn.example.com\n  configuration:\n    acr_image_name: datahub/api\n"
    row = _images(_solution(tmp_path, artifact_spec=spec))["datahub"]
    assert row.registry == "cdn.example.com"
    assert row.configuration == {"acr_image_name": "datahub/api"}


def test_an_artifact_nothing_references_is_not_listed(tmp_path):
    assert "orphan" not in _images(_solution(tmp_path))


def test_a_literal_module_service_image_is_a_module_service_row(tmp_path):
    row = _images(_solution(tmp_path))["redis"]
    assert (row.origin, row.module, row.artifact) == ("module-service", "stack", None)
    assert (row.image_name, row.image_tag, row.image_ref) == ("redis", "7", "redis:7")
    assert (row.tag_source, row.via) == ("declared", ("service:stack/redis",))


def test_an_images_pin_replaces_the_literal_image(tmp_path):
    row = _images(_solution(tmp_path, pins="    images:\n      redis: redis:7.2\n"))["redis"]
    assert (row.image_ref, row.image_tag, row.tag_source, row.pin_version) == ("redis:7.2", "7.2", "pin", "prd")


def test_a_literal_image_without_a_tag_has_source_none(tmp_path):
    row = _images(_solution(tmp_path, services="    - name: redis\n      image: redis\n"))["redis"]
    assert (row.image_tag, row.tag_source) == (None, "none")


def test_a_module_service_artifact_shares_the_row_with_a_variable_using_it(tmp_path):
    services = "    - name: api\n      artifact: datahub\n"
    rows = _images(_solution(tmp_path, services=services))
    assert "api" not in rows
    assert rows["datahub"].via == ("variable:DATAHUB_TAG", "service:stack/api")


def test_a_module_service_artifact_is_listed_without_any_variable(tmp_path):
    services = "    - name: api\n      artifact: datahub\n"
    rows = _images(_solution(tmp_path, services=services, variable_artifacts=()))
    assert rows["datahub"].via == ("service:stack/api",)


def test_a_disabled_module_reference_contributes_nothing(tmp_path):
    rows = _images(_solution(tmp_path, module_enabled=False))
    assert "redis" not in rows


def test_artifact_rows_come_before_module_service_rows(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root).require_valid()
    rows = effective_images(context, resolve_deployment(context, "app"))
    assert [row.origin for row in rows] == ["artifact", "module-service"]
