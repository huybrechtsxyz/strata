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
    path.write_text(path.read_text(encoding="utf-8").replace("environments: [prd]", "environments: [ghost-env]"), encoding="utf-8")

    # The dangling 'ghost-env' reference is caught by validate_references;
    # here we only assert this does not crash the token check.
    context = _resolve(root)
    assert not context.ok
