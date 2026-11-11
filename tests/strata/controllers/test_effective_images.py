#!/usr/bin/env python3
"""Tests for `effective_images()` (docs/work/effective-images.md)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from strata.controllers.effective_images import _received_variable_keys, effective_images, unknown_image_modules
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment
from strata.models.workspace_model import WorkspaceModel

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"

_TERRAFORM_STEP = (
    "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
    "  execution:\n    - name: infra\n      provisioner: tf\n      namespaces:\n        - apps\n"
)


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(
    tmp_path: Path,
    *,
    artifact_spec: str = "  image_name: example/catalog-api\n  image_tag: '1.0.0'\n",
    variable_artifacts: tuple[str, ...] = ("catalog",),
    services: str = "    - name: redis\n      image: redis:7\n",
    pins: str = "",
    module_enabled: bool = True,
    deployment_version: bool = True,
    steps: str = _TERRAFORM_STEP,
) -> Path:
    """One workspace reaching one compose module, one artifact `catalog`, one unreferenced artifact `orphan`.
    `steps` are the workspace's provisioners and execution steps: by default one Terraform step, which receives
    every variable."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n  namespaces:\n    - apps\n" + steps,
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
    _write(root, "artifact.yaml", "artifact", "catalog", artifact_spec)
    _write(root, "orphan.yaml", "artifact", "orphan", "  image_name: example/orphan\n")
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
    row = _images(_solution(tmp_path))["catalog"]
    assert (row.origin, row.artifact, row.module) == ("artifact", "catalog", None)
    assert (row.image_name, row.image_tag, row.image_ref) == (
        "example/catalog-api",
        "1.0.0",
        "example/catalog-api:1.0.0",
    )
    assert (row.tag_source, row.pin_version, row.pin_flag) == ("declared", None, False)
    assert row.via == ("variable:CATALOG_TAG",)


def test_a_version_pin_overrides_the_declared_tag_and_names_its_document(tmp_path):
    row = _images(_solution(tmp_path, pins="    artifacts:\n      catalog: '2.0.0'\n"))["catalog"]
    assert (row.image_tag, row.tag_source, row.pin_version, row.pin_flag) == ("2.0.0", "pin", "prd", False)


def test_a_pin_flag_is_reported_for_a_pin_given_with_dash_dash_pin(tmp_path):
    root = _solution(tmp_path, pins="    artifacts:\n      catalog: '2.0.0'\n", deployment_version=False)
    row = _images(root, pin="prd")["catalog"]
    assert (row.image_tag, row.tag_source, row.pin_flag) == ("2.0.0", "pin", True)


def test_an_artifact_with_no_tag_and_no_pin_has_none_and_a_bare_ref(tmp_path):
    row = _images(_solution(tmp_path, artifact_spec="  image_name: example/catalog-api\n"))["catalog"]
    assert (row.image_tag, row.tag_source) == (None, "none")
    assert row.image_ref == "example/catalog-api"


_DIGEST = "sha256:" + "9f86d081" * 8


def test_a_digest_tag_makes_an_at_reference(tmp_path):
    spec = f"  image_name: int/api\n  image_tag: '{_DIGEST}'\n"
    row = _images(_solution(tmp_path, artifact_spec=spec))["catalog"]
    assert (row.image_ref, row.image_tag, row.image_digest) == (f"int/api@{_DIGEST}", None, _DIGEST)
    assert row.tag_source == "declared"


def test_a_tag_and_a_digest_make_a_tag_at_digest_reference(tmp_path):
    spec = f"  image_name: int/api\n  image_tag: '1.4.2@{_DIGEST}'\n"
    row = _images(_solution(tmp_path, artifact_spec=spec))["catalog"]
    assert (row.image_ref, row.image_tag, row.image_digest) == (f"int/api:1.4.2@{_DIGEST}", "1.4.2", _DIGEST)


def test_a_pinned_tag_at_digest_replaces_both_parts(tmp_path):
    pins = f"    artifacts:\n      catalog: '2.0.0@{_DIGEST}'\n"
    row = _images(_solution(tmp_path, pins=pins))["catalog"]
    assert (row.image_tag, row.image_digest, row.tag_source) == ("2.0.0", _DIGEST, "pin")


def test_a_module_service_image_with_a_tag_and_a_digest_keeps_them_apart(tmp_path):
    services = f"    - name: redis\n      image: redis:7@{_DIGEST}\n"
    row = _images(_solution(tmp_path, services=services))["redis"]
    assert (row.image_name, row.image_tag, row.image_digest) == ("redis", "7", _DIGEST)


def test_an_image_tag_that_is_not_a_tag_or_a_digest_fails_at_load(tmp_path):
    root = _solution(tmp_path, artifact_spec="  image_name: int/api\n  image_tag: '1.4.2@sha256:short'\n")
    context = open_solution(root)
    assert not context.ok
    assert any("the digest 'sha256:short'" in message for message in context.diagnostics.messages())


def test_registry_and_configuration_are_carried_on_the_row(tmp_path):
    spec = "  image_name: int/api\n  registry: cdn.example.com\n  configuration:\n    acr_image_name: catalog/api\n"
    row = _images(_solution(tmp_path, artifact_spec=spec))["catalog"]
    assert row.registry == "cdn.example.com"
    assert row.configuration == {"acr_image_name": "catalog/api"}


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
    services = "    - name: api\n      artifact: catalog\n"
    rows = _images(_solution(tmp_path, services=services))
    assert "api" not in rows
    assert rows["catalog"].via == ("variable:CATALOG_TAG", "service:stack/api")


def test_a_module_service_artifact_is_listed_without_any_variable(tmp_path):
    services = "    - name: api\n      artifact: catalog\n"
    rows = _images(_solution(tmp_path, services=services, variable_artifacts=()))
    assert rows["catalog"].via == ("service:stack/api",)


def test_a_disabled_module_reference_contributes_nothing(tmp_path):
    rows = _images(_solution(tmp_path, module_enabled=False))
    assert "redis" not in rows


def test_artifact_rows_come_before_module_service_rows(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root).require_valid()
    rows = effective_images(context, resolve_deployment(context, "app"))
    assert [row.origin for row in rows] == ["artifact", "module-service"]


# `${artifact:NAME.FIELD}` uses (docs/work/artifact-gap-analysis.md, design 3): an image delivered only
# through the token is still an image the deployment runs.

_TOKEN_SERVICE = (
    "    - name: redis\n      image: redis:7\n"
    "      environment:\n        - key: CATALOG_IMAGE\n          value: ${artifact:catalog.image_ref}\n"
)


def _append(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_a_token_in_a_module_lists_the_artifact_with_the_module_as_its_use(tmp_path):
    row = _images(_solution(tmp_path, variable_artifacts=(), services=_TOKEN_SERVICE))["catalog"]
    assert (row.origin, row.artifact, row.image_ref) == ("artifact", "catalog", "example/catalog-api:1.0.0")
    assert row.via == ("token:module/stack",)


def test_a_token_in_the_workspace_or_an_environment_is_a_use(tmp_path):
    root = _solution(tmp_path, variable_artifacts=())
    _append(root, "workspace.yaml", "  custom:\n    api_image: ${artifact:catalog.image_name}\n")
    environment = root / "environment.yaml"
    environment.write_text(
        _HEADER.format(kind="environment", name="prd") + "  properties:\n    tag: ${artifact:catalog.image_tag}\n",
        encoding="utf-8",
    )
    assert _images(root)["catalog"].via == ("token:workspace/main", "token:environment/prd")


def test_a_token_in_a_disabled_module_is_not_a_use(tmp_path):
    images = _images(_solution(tmp_path, variable_artifacts=(), services=_TOKEN_SERVICE, module_enabled=False))
    assert "catalog" not in images


def test_a_token_only_artifact_gets_the_deployments_pin(tmp_path):
    root = _solution(
        tmp_path, variable_artifacts=(), services=_TOKEN_SERVICE, pins="    artifacts:\n      catalog: '2.0.0'\n"
    )
    row = _images(root)["catalog"]
    assert (row.image_tag, row.tag_source, row.pin_version) == ("2.0.0", "pin", "prd")


def test_every_use_is_listed_once_variables_first(tmp_path):
    services = _TOKEN_SERVICE + "      command: [run, '${artifact:catalog.image_tag}']\n"
    row = _images(_solution(tmp_path, services=services))["catalog"]
    assert row.via == ("variable:CATALOG_TAG", "token:module/stack")


def test_an_escaped_token_is_not_a_use(tmp_path):
    services = "    - name: redis\n      image: redis:7\n      command: [echo, '$" + "${artifact:catalog.image_tag}']\n"
    assert "catalog" not in _images(_solution(tmp_path, variable_artifacts=(), services=services))


def test_image_tag_policy_judges_an_image_delivered_only_through_the_token(tmp_path):
    from strata.controllers.image_tag_policy import check_image_tag_policy
    from strata.controllers.value_controller import resolve_deployment_workspace

    root = _solution(
        tmp_path,
        artifact_spec="  image_name: example/catalog-api\n",
        variable_artifacts=(),
        services="    - name: redis\n      image: redis:7\n      command: [run, '${artifact:catalog.image_ref}']\n",
    )
    _append(root, "workspace.yaml", "  image_tag_policy: {}\n")
    context = open_solution(root).require_valid()
    deployment, workspace = resolve_deployment_workspace(context, "app")

    diagnostics = check_image_tag_policy(context, deployment, workspace)

    assert any("catalog" in message for message in diagnostics.messages())


# A `store: artifact` variable counts only where the deployment can receive it
# (docs/work/artifact-gap-analysis.md, design 7).

_HELM_STEP = (
    "  provisioners:\n    - name: charts\n      tool: helm\n      source:\n        source_path: infra\n"
    "  execution:\n    - name: apps\n      provisioner: charts\n      namespaces:\n        - apps\n"
)


def test_a_variable_no_step_receives_does_not_list_its_artifact(tmp_path):
    """A Helm step takes no variables except through a token, and nothing reads `${var:CATALOG_TAG}`."""
    assert "catalog" not in _images(_solution(tmp_path, steps=_HELM_STEP))


def test_a_workspace_without_execution_steps_delivers_no_variable(tmp_path):
    steps = "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
    assert "catalog" not in _images(_solution(tmp_path, steps=steps))


def test_a_var_token_in_a_reached_document_is_what_delivers_the_variable(tmp_path):
    services = (
        "    - name: redis\n      image: redis:7\n"
        "      environment:\n        - key: TAG\n          value: ${var:CATALOG_TAG}\n"
    )
    row = _images(_solution(tmp_path, steps=_HELM_STEP, services=services))["catalog"]
    assert row.via == ("variable:CATALOG_TAG",)


def _workspace(provisioner: str) -> WorkspaceModel:
    return WorkspaceModel.model_validate(
        yaml.safe_load(
            "meta:\n  name: main\nspec:\n  providers: [p1]\n  namespaces: [apps]\n"
            f"  provisioners:\n    - name: step\n{provisioner}"
            "  execution:\n    - name: run\n      provisioner: step\n      namespaces: [apps]\n"
        )
    )


@pytest.mark.parametrize(
    ("provisioner", "expected"),
    [
        ("      tool: terraform\n      source: {source_path: infra}\n", {"A", "B"}),
        ("      tool: opentofu\n      source: {source_path: infra}\n", {"A", "B"}),
        ("      tool: script\n      source: {source_path: s}\n      script: {file: run.py}\n", {"A", "B"}),
        ("      tool: script\n      source: {source_path: s}\n      script: {file: run.py, values: [A]}\n", {"A"}),
        ("      tool: helm\n      source: {source_path: c}\n", set()),
    ],
    ids=["terraform", "unknown-tool", "script-all-values", "script-listed-values", "helm"],
)
def test_which_steps_receive_variables_without_a_token(tmp_path, provisioner, expected):
    context = SimpleNamespace(root=tmp_path)
    assert _received_variable_keys(context, _workspace(provisioner), {"A", "B"}) == expected


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{{ variables.A }}", {"A"}),
        ("{{ variables['B'] }}", {"B"}),
        ("{{ secrets.A }}", set()),
        ("{% for k, v in variables.items() %}{{ v }}{% endfor %}", {"A", "B"}),
        ("{{ variables[name] }}", {"A", "B"}),
    ],
)
def test_an_output_template_receives_the_variables_it_reads(tmp_path, template, expected):
    (tmp_path / "out.j2").write_text(template, encoding="utf-8")
    provisioner = "      tool: helm\n      source: {source_path: c}\n      output: {template: out.j2}\n"
    context = SimpleNamespace(root=tmp_path)
    assert _received_variable_keys(context, _workspace(provisioner), {"A", "B"}) == expected


def test_an_output_template_that_cannot_be_read_receives_every_variable(tmp_path):
    provisioner = "      tool: helm\n      source: {source_path: c}\n      output: {template: missing.j2}\n"
    context = SimpleNamespace(root=tmp_path)
    assert _received_variable_keys(context, _workspace(provisioner), {"A", "B"}) == {"A", "B"}


# Delivered (docs/work/helm-image-delivery.md, option B): strata delivers an image only when it writes it into
# what runs. A Helm module's service image is a declaration; the chart decides.

_CHART_SOURCE = (
    "  source:\n    chart_repository: https://charts.example.com\n    chart_name: immich\n    chart_version: 0.13.1\n"
)


def _with_module(root: Path, *, kind: str = "helm", services: str = "", extra: str = "", enabled: bool = True) -> None:
    """Add a second module `chart` of type `kind` to namespace `apps`, next to the compose module `stack`."""
    source = _CHART_SOURCE if kind == "helm" else "  source:\n    source_path: services/chart\n"
    spec = f"{source}  type: {kind}\n" + (f"  services:\n{services}" if services else "") + extra
    _write(root, "chart.yaml", "module", "chart", spec)
    flag = "" if enabled else "      enabled: false\n"
    _append(root, "namespace.yaml", f"    - name: chart\n      module: chart\n{flag}")


def _unknown(root: Path):
    context = open_solution(root).require_valid()
    return unknown_image_modules(context, resolve_deployment(context, "app"))


def test_a_compose_service_image_and_a_variable_artifact_are_delivered(tmp_path):
    rows = _images(_solution(tmp_path))
    assert (rows["redis"].delivered, rows["redis"].delivery) == (True, None)
    assert (rows["catalog"].delivered, rows["catalog"].delivery) == (True, None)


def test_a_helm_service_image_is_declared_only(tmp_path):
    root = _solution(tmp_path)
    _with_module(root, services="    - name: server\n      image: ghcr.io/immich/server:v1.2.0\n")
    row = _images(root)["server"]
    assert (row.delivered, row.delivery) == (False, "helm module: the chart decides")


def test_a_script_module_service_image_is_not_delivered(tmp_path):
    root = _solution(tmp_path)
    _with_module(root, kind="script", services="    - name: tool\n      image: busybox:1.36\n")
    row = _images(root)["tool"]
    assert (row.delivered, row.delivery) == (False, "script module: strata writes no image")


def test_an_artifact_only_a_helm_service_names_is_not_delivered(tmp_path):
    root = _solution(tmp_path, variable_artifacts=())
    _with_module(root, services="    - name: server\n      artifact: catalog\n")
    row = _images(root)["catalog"]
    assert (row.delivered, row.delivery) == (False, "helm module: no ${artifact:} token")


def test_an_artifact_a_helm_module_writes_with_a_token_is_delivered(tmp_path):
    root = _solution(tmp_path, variable_artifacts=())
    _with_module(
        root,
        services="    - name: server\n      artifact: catalog\n",
        extra="  configuration:\n    image:\n      repository: ${artifact:catalog.image_name}\n",
    )
    row = _images(root)["catalog"]
    assert row.via == ("service:chart/server", "token:module/chart")
    assert (row.delivered, row.delivery) == (True, None)


def test_an_artifact_a_variable_delivers_stays_delivered_when_a_helm_service_names_it(tmp_path):
    root = _solution(tmp_path)
    _with_module(root, services="    - name: server\n      artifact: catalog\n")
    assert _images(root)["catalog"].delivered


def test_a_helm_module_that_declares_no_image_is_unknown(tmp_path):
    root = _solution(tmp_path)
    _with_module(root)
    [module] = _unknown(root)
    assert (module.module, module.namespace, module.chart, module.chart_version) == (
        "chart",
        "apps",
        "immich",
        "0.13.1",
    )


def test_an_unknown_module_carries_its_pinned_chart_version(tmp_path):
    root = _solution(tmp_path, pins="    charts:\n      chart: 0.14.0\n")
    _with_module(root)
    assert [module.chart_version for module in _unknown(root)] == ["0.14.0"]


@pytest.mark.parametrize(
    ("kind", "services", "enabled"),
    [
        ("helm", "    - name: server\n      image: ghcr.io/immich/server:v1.2.0\n", True),
        ("helm", "    - name: server\n      artifact: catalog\n", True),
        ("helm", "", False),
        ("script", "", True),
    ],
    ids=["declares-an-image", "declares-an-artifact", "disabled", "not-helm"],
)
def test_a_module_whose_images_strata_knows_or_that_is_not_helm_is_not_unknown(tmp_path, kind, services, enabled):
    root = _solution(tmp_path)
    _with_module(root, kind=kind, services=services, enabled=enabled)
    assert _unknown(root) == []


def test_a_helm_service_without_an_image_does_not_make_the_module_known(tmp_path):
    root = _solution(tmp_path)
    _with_module(root, services="    - name: server\n      environment:\n        - key: A\n          value: b\n")
    assert [module.module for module in _unknown(root)] == ["chart"]
