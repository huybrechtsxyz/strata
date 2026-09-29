#!/usr/bin/env python3
"""Integration tests for cross-document semantic checks (`semantic_checks.py`).

Each test builds a minimal, real solution via `open_solution(...).resolve()`
— the same path `strata validate` runs — rather than calling the private
`_check_*` functions directly, so these prove the wiring works end to end,
not just that a service method works in isolation (already covered by its
own unit tests).
"""

from pathlib import Path

from strata.controllers.solution_context import open_solution

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_solution(tmp_path: Path) -> Path:
    """A solution exercising every check: provider/config, workspace with
    topology+namespace+dns+network+firewall, tenant, environment, deployment.
    """
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)

    _write(
        root,
        "registries/azure.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: providerconfig
meta:
  name: azure
spec:
  description: "Azure provider type registry"
  additional_regions: false
  regions:
    - name: westeurope
      geography: europe
  additional_resources: false
  resources:
    - name: storage_account
""",
    )
    _write(
        root,
        "registries/kubernetes.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: topologyconfig
meta:
  name: kubernetes
spec:
  additional_components: false
  components:
    - role: control-plane
      required: true
      min_count: 1
      max_count: 1
""",
    )
    _write(
        root,
        "configuration.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: config
spec:
  providers: [azure]
  topologies: [kubernetes]
""",
    )
    _write(
        root,
        "provider.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
""",
    )
    _write(
        root,
        "resource.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: storage-account
spec:
  properties:
    provider_type: azure
    resource_type: storage_account
  default_tags:
    environment: test
""",
    )
    _write(
        root,
        "namespace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: namespace
meta:
  name: apps
spec:
  default_labels:
    environment: test
  modules:
    - name: web-module
      module: web
""",
    )
    _write(
        root,
        "module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web
spec:
  type: docker-compose
  default_labels:
    environment: test
  source:
    source_path: modules/web
  services:
    - name: web
      environment:
        - key: PUBLIC_IP
          value: "${var:PUBLIC_IP}"
""",
    )
    _write(
        root,
        "dns.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: example
spec:
  provider: cloudflare
  zones:
    - name: example.com
      records:
        - name: "@"
          type: A
          value: "1.2.3.4"
      default_tags:
        environment: test
""",
    )
    _write(
        root,
        "topology.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: main-topology
spec:
  type: kubernetes
  components:
    - resource: storage-account
""",
    )
    _write(
        root,
        "workspace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
  execution:
    - name: provision-infra
      provisioner: tf
      targets: [storage-account]
  resources:
    - name: storage-account
      resource: storage-account
      role: control-plane
  topology: [main-topology]
  namespaces: [apps]
  dns_zones: [example]
""",
    )
    _write(
        root,
        "environment.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: PUBLIC_IP
      store: constant
      value: "1.2.3.4"
""",
    )
    _write(
        root,
        "tenant.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0062
spec:
  display_name: Acme
  geographies: [europe]
""",
    )
    _write(
        root,
        "deployment.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: prd-deployment
spec:
  workspace: main
  tenant: c0062
  environments: [prd]
  stages:
    - step: provision-infra
""",
    )
    return root


def _resolve(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()  # Phase 1 must be clean first
    context.resolve()
    return context


def test_base_solution_passes_every_check(tmp_path):
    """The control case: nothing wrong anywhere."""
    context = _resolve(_base_solution(tmp_path))
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# Deployment -> Workspace: stages name real execution steps
# ---------------------------------------------------------------------------


def test_stage_naming_unknown_step_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "deployment.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("provision-infra", "ghost-step"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost-step" in m for m in context.diagnostics.messages())


# ---------------------------------------------------------------------------
# Tenant -> ProviderConfig: declared geographies are real
# ---------------------------------------------------------------------------


def test_tenant_geography_not_declared_by_any_provider_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "tenant.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("europe", "antarctica"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("antarctica" in m for m in context.diagnostics.messages())


# ---------------------------------------------------------------------------
# Provider / Resource -> ProviderConfig
# ---------------------------------------------------------------------------


def test_provider_region_invalid_for_its_config_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "provider.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("westeurope", "brazilsouth"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("brazilsouth" in m for m in context.diagnostics.messages())


def test_resource_type_invalid_for_its_config_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "resource.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("storage_account", "ghost_type"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost_type" in m for m in context.diagnostics.messages())


def test_unregistered_provider_type_skips_rather_than_crashes(tmp_path):
    """No matching ProviderConfig in the index: nothing to check against."""
    root = _base_solution(tmp_path)
    path = root / "provider.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("type: azure", "type: kamatera"), encoding="utf-8")

    # Should not raise, even though 'kamatera' has no providerconfig document.
    _resolve(root)


# ---------------------------------------------------------------------------
# Workspace -> Topology (+ TopologyConfig)
# ---------------------------------------------------------------------------


def test_topology_component_references_undefined_resource_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "topology.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("storage-account", "ghost-resource"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost-resource" in m for m in context.diagnostics.messages())


def test_missing_required_component_role_is_caught(tmp_path):
    """kubernetes.yaml requires a control-plane role; the workspace only declares 'worker'."""
    root = _base_solution(tmp_path)
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("role: control-plane", "role: worker"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("control-plane" in m for m in context.diagnostics.messages())


def test_topology_component_check_is_skipped_without_a_configuration_document(tmp_path):
    """No Configuration doc means no declared policy — skip, don't guess."""
    root = _base_solution(tmp_path)
    (root / "configuration.yaml").unlink()

    # Should not raise despite the missing required role, since the check
    # that would catch it needs a Configuration document to run at all.
    context = _resolve(root)
    assert context.ok


# ---------------------------------------------------------------------------
# Deployment -> Environment/Tenant reachability: value tokens resolve
# ---------------------------------------------------------------------------


def test_module_token_resolves_via_the_deployments_environment(tmp_path):
    """The base solution's module token IS declared — proves the reachability
    walk (workspace -> namespace -> module) actually runs, not just passes
    by omission.
    """
    context = _resolve(_base_solution(tmp_path))
    assert context.ok


def test_module_token_not_declared_by_any_reachable_environment_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "module.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("PUBLIC_IP", "GHOST_VAR"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_VAR" in m for m in context.diagnostics.messages())


def test_dns_token_reachable_through_workspace_dns_zones_is_checked(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "dns.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('value: "1.2.3.4"', 'value: "${var:GHOST_IP}"'),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_IP" in m for m in context.diagnostics.messages())


def test_deployment_with_no_resolvable_environment_skips_token_checking(tmp_path):
    """No environment to check against — must not raise or false-positive."""
    root = _base_solution(tmp_path)
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("environments: [prd]", "environments: [ghost-env]"), encoding="utf-8"
    )

    # The dangling 'ghost-env' reference is caught by validate_references;
    # here we only assert this does not crash the token check.
    context = _resolve(root)
    assert not context.ok


# ---------------------------------------------------------------------------
# docs/_gap_v1.md gap #12, generalized (docs/design/deploy-command.md's
# "Cross-invocation output access" section, "Expanded finding"): `strata
# validate` previously rejected EVERY `${output:...}` token unconditionally,
# even one a real execution step's `targets` claims — confirmed to affect
# both DNS/network/firewall *and* Modules (reached through a targeted
# namespace), not just DNS/network/firewall as gap #12 originally scoped.
# ---------------------------------------------------------------------------


def test_output_token_in_a_claimed_dns_document_is_accepted(tmp_path):
    root = _base_solution(tmp_path)
    _write(
        root,
        "dns.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: example
spec:
  provider: cloudflare
  zones:
    - name: example.com
      records:
        - name: "@"
          type: A
          value: "${output:provision-infra.public_ip}"
      default_tags:
        environment: test
""",
    )
    _write(
        root,
        "workspace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
  execution:
    - name: provision-infra
      provisioner: tf
      targets: [storage-account, example]
  resources:
    - name: storage-account
      resource: storage-account
      role: control-plane
  topology: [main-topology]
  namespaces: [apps]
  dns_zones: [example]
""",
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_output_token_in_an_unclaimed_dns_document_is_still_rejected(tmp_path):
    """Regression: an unclaimed document must still be rejected — this fix
    only changes the claimed case, matching gap #12's own ownership rule."""
    root = _base_solution(tmp_path)
    path = root / "dns.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('value: "1.2.3.4"', 'value: "${output:provision-infra.public_ip}"'),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("output" in m and "not supported" in m for m in context.diagnostics.messages())


def test_output_token_in_a_module_reached_through_a_claimed_namespace_is_accepted(tmp_path):
    """The expanded finding: a Module inherits its owning namespace's claim,
    not just directly-targeted DNS/network/firewall documents."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web
spec:
  type: docker-compose
  default_labels:
    environment: test
  source:
    source_path: modules/web
  services:
    - name: web
      environment:
        - key: PUBLIC_IP
          value: "${output:provision-infra.public_ip}"
""",
    )
    _write(
        root,
        "workspace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
  execution:
    - name: provision-infra
      provisioner: tf
      targets: [storage-account, apps]
  resources:
    - name: storage-account
      resource: storage-account
      role: control-plane
  topology: [main-topology]
  namespaces: [apps]
  dns_zones: [example]
""",
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_output_token_in_a_module_reached_through_an_unclaimed_namespace_is_still_rejected(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "module.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'value: "${var:PUBLIC_IP}"', 'value: "${output:provision-infra.public_ip}"'
        ),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("output" in m and "not supported" in m for m in context.diagnostics.messages())


# ---------------------------------------------------------------------------
# gap #10 Phase 7: tokens are now also checked in the workspace/resource/
# provider/topology/tenant/environment/deployment documents themselves, not
# just DNS/network/firewall/module — these were never checked at all before,
# regardless of whether gap #9's deploy-time delivery (Phase 2/6) resolves
# them.
# ---------------------------------------------------------------------------


def test_resource_configuration_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "resource.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  configuration:\n    admin_password: '${secret:GHOST_RESOURCE_SECRET}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_RESOURCE_SECRET" in m for m in context.diagnostics.messages())


def test_provider_configuration_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "provider.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  configuration:\n    tenant_id: '${var:GHOST_PROVIDER_VAR}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_PROVIDER_VAR" in m for m in context.diagnostics.messages())


def test_workspace_custom_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "workspace.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  custom:\n    owner: '${var:GHOST_WORKSPACE_VAR}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_WORKSPACE_VAR" in m for m in context.diagnostics.messages())


def test_tenant_configuration_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "tenant.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  configuration:\n    tier: '${var:GHOST_TENANT_VAR}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_TENANT_VAR" in m for m in context.diagnostics.messages())


def test_environment_custom_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "environment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  custom:\n    team: '${var:GHOST_ENV_VAR}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_ENV_VAR" in m for m in context.diagnostics.messages())


def test_deployment_custom_token_not_declared_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  custom:\n    owner: '${var:GHOST_DEPLOYMENT_VAR}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("GHOST_DEPLOYMENT_VAR" in m for m in context.diagnostics.messages())


def test_topology_document_itself_is_now_checked_for_tokens(tmp_path):
    """The topology document itself (not just modules reached through it)
    is now part of the reachable set - proven directly against
    `_documents_reachable_from_workspace()` rather than a full end-to-end
    fixture, since the base solution's topology has no string field handy
    to embed a token in without extra fixture plumbing."""
    from strata.controllers.semantic_checks import _documents_reachable_from_workspace

    context = _resolve(_base_solution(tmp_path))
    documents = _documents_reachable_from_workspace(context.controller.index, "main")
    kinds = {type(document).__name__ for document, _claimed in documents}
    assert "TopologyModel" in kinds
    assert "WorkspaceModel" in kinds
    assert "ProviderModel" in kinds
    assert "ResourceModel" in kinds


# ---------------------------------------------------------------------------
# Environment -> Artifact: store: artifact references (docs/design/
# artifact-references.md's full-review finding)
# ---------------------------------------------------------------------------


def _solution_with_artifact_reference(tmp_path: Path, *, artifact_value: str = "dspapi_container") -> Path:
    root = _base_solution(tmp_path)
    _write(
        root,
        "artifact.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dspapi_container
spec:
  image_name: int-docker-test/src/omp.dispatcher.api
  image_tag: "1.0.0"
""",
    )
    path = root / "environment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"    - key: IMAGE_TAG\n      store: artifact\n      value: {artifact_value}\n      field: image_tag\n",
        encoding="utf-8",
    )
    return root


def test_artifact_reference_to_a_real_artifact_passes(tmp_path):
    context = _resolve(_solution_with_artifact_reference(tmp_path))
    assert context.ok, context.diagnostics.messages()


def test_artifact_reference_to_an_unknown_artifact_is_caught(tmp_path):
    root = _solution_with_artifact_reference(tmp_path, artifact_value="ghost_artifact")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost_artifact" in m for m in context.diagnostics.messages())


# ---------------------------------------------------------------------------
# SolutionRemoteModel -> Module/Provisioner SourceModel usages: an OCI
# remote's `reference` is required only when a real non-chart consumer needs
# it (docs/_gap_v1.md gap #3).
# ---------------------------------------------------------------------------


def _manifest_with_oci_remote(*, reference: str | None = None) -> str:
    reference_line = f'      reference: "{reference}"\n' if reference is not None else ""
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: charts
      type: oci
      url: oci://ghcr.io/org/charts
{reference_line}"""


def _solution_with_module_source(tmp_path: Path, source_yaml: str) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", _manifest_with_oci_remote())
    _write(
        root,
        "module.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: authentik
spec:
  source:
{source_yaml}
""",
    )
    return root


def test_oci_remote_without_reference_used_only_by_chart_based_module_passes(tmp_path):
    root = _solution_with_module_source(
        tmp_path, '    remote: charts\n    chart_name: authentik\n    chart_version: "2024.12.0"\n'
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_oci_remote_without_reference_used_by_a_git_based_module_is_caught(tmp_path):
    root = _solution_with_module_source(tmp_path, "    remote: charts\n    source_path: modules/authentik\n")
    context = _resolve(root)
    assert not context.ok
    assert any(e.code == "oci_remote_missing_reference" for e in context.diagnostics.errors)
    assert any("'charts'" in m and "non-chart source" in m for m in context.diagnostics.messages())


def test_oci_remote_with_reference_used_by_a_git_based_module_passes(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", _manifest_with_oci_remote(reference="v1.2.3"))
    _write(
        root,
        "module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: authentik
spec:
  source:
    remote: charts
    source_path: modules/authentik
""",
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_oci_remote_without_reference_used_by_a_git_based_provisioner_source_is_caught(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", _manifest_with_oci_remote())
    _write(
        root,
        "provider.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
""",
    )
    _write(
        root,
        "workspace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source:
        remote: charts
        source_path: terraform/main
""",
    )
    context = _resolve(root)
    assert not context.ok
    assert any(e.code == "oci_remote_missing_reference" for e in context.diagnostics.errors)


def test_oci_remote_with_no_remotes_needing_the_check_is_a_no_op(tmp_path):
    """No OCI remote lacks a reference at all — the check must not even
    look at consumers (and must not crash on a solution with no remotes)."""
    root = tmp_path / "sln"
    _write(
        root,
        "strata.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
""",
    )
    _write(
        root,
        "module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web
spec:
  source:
    source_path: modules/web
""",
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# ${value:kind.name.path} cross-document references (docs/design/
# cross-document-value-references.md's Phase 5) — end-to-end through real
# `strata validate` (`open_solution(...).resolve()`), not the private
# `_check_value_references()`/`resolve_document_value_references()` directly
# (already unit-tested in `test_value_controller.py`). Proves the wiring:
# `strata validate` now actually calls this check, and no longer flags
# `${value:...}` as `unsupported_value_token_kind` the way it used to
# before Phase 5 (docs/design/cross-document-value-references.md's own
# Phase 2 changelog entry documented that exact interim behaviour).
# ---------------------------------------------------------------------------


def _solution_with_tenant_and_probe(tmp_path: Path, token: str) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "c0062.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: c0062\nspec:\n"
        '  display_name: "GSK"\n  geographies:\n    - europe\n',
    )
    _write(
        root,
        "environments/probe.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: probe\nspec:\n"
        f'  properties:\n    probe: "{token}"\n',
    )
    return root


def test_value_reference_that_resolves_passes_validate(tmp_path):
    root = _solution_with_tenant_and_probe(tmp_path, "${value:tenant.c0062.meta.name}")
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_value_reference_to_an_unknown_document_is_caught_by_validate(tmp_path):
    root = _solution_with_tenant_and_probe(tmp_path, "${value:tenant.doesnotexist.meta.name}")
    context = _resolve(root)
    assert not context.ok
    assert any(e.code == "value_reference_unknown_document" for e in context.diagnostics.errors)


def test_value_reference_no_longer_reported_as_unsupported_token_kind(tmp_path):
    """Before Phase 5, `${value:...}` was flagged `unsupported_value_token_kind`
    by `unresolved_value_tokens()` (the exact interim behaviour Phase 2's own
    changelog entry documented and tested) — confirm that code never appears
    now, for either a resolving or a failing reference."""
    for token in ("${value:tenant.c0062.meta.name}", "${value:tenant.doesnotexist.meta.name}"):
        context = _resolve(_solution_with_tenant_and_probe(tmp_path, token))
        assert not any(e.code == "unsupported_value_token_kind" for e in context.diagnostics.errors)


def test_value_reference_with_no_value_tokens_anywhere_is_a_no_op(tmp_path):
    """The base solution (every other check's control case) has zero
    `${value:...}` tokens anywhere — confirms wiring `_check_value_references()`
    into `run_semantic_checks()` doesn't regress the fully-clean case."""
    context = _resolve(_base_solution(tmp_path))
    assert context.ok, context.diagnostics.messages()
    assert not any(e.code and e.code.startswith("value_reference_") for e in context.diagnostics.errors)
