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
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: storage-account
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
# Deployment -> Tenant/Workspace/Provider: static geography cross-check
# (docs/design/tenant-zone-policy.md's "static tier")
# ---------------------------------------------------------------------------


def _add_us_region_to_azure_config(root: Path) -> None:
    path = root / "registries/azure.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  regions:\n    - name: westeurope\n      geography: europe\n",
            "  regions:\n    - name: westeurope\n      geography: europe\n    - name: eastus2\n      geography: us\n",
        ),
        encoding="utf-8",
    )


def test_deployment_provider_geography_mismatch_is_caught(tmp_path):
    """The base solution's tenant only allows 'europe' — pointing its one
    provider at a region tagged 'us' instead must be caught."""
    root = _base_solution(tmp_path)
    _add_us_region_to_azure_config(root)
    path = root / "provider.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("region: westeurope", "region: eastus2"), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    messages = context.diagnostics.messages()
    assert any("eastus2" in m and "geography 'us'" in m and "c0062" in m for m in messages)


def test_deployment_with_no_tenant_skips_geography_check(tmp_path):
    root = _base_solution(tmp_path)
    _add_us_region_to_azure_config(root)
    provider_path = root / "provider.yaml"
    provider_path.write_text(
        provider_path.read_text(encoding="utf-8").replace("region: westeurope", "region: eastus2"), encoding="utf-8"
    )
    deployment_path = root / "deployment.yaml"
    deployment_path.write_text(
        deployment_path.read_text(encoding="utf-8").replace("  tenant: c0062\n", ""), encoding="utf-8"
    )

    # No tenant on the deployment — the mismatch above has nothing to check against.
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_tenant_with_no_geographies_skips_geography_check(tmp_path):
    """An empty (or omitted) `geographies` list means 'no data-residency
    constraint at all' — not a violation waiting to happen. Proven the same
    way `test_deployment_with_no_tenant_skips_geography_check` proves its own
    skip: set up a real mismatch (a provider region tagged 'us') that WOULD
    be caught if the tenant had any constraint, then confirm it isn't."""
    root = _base_solution(tmp_path)
    _add_us_region_to_azure_config(root)
    provider_path = root / "provider.yaml"
    provider_path.write_text(
        provider_path.read_text(encoding="utf-8").replace("region: westeurope", "region: eastus2"), encoding="utf-8"
    )
    tenant_path = root / "tenant.yaml"
    tenant_path.write_text(
        tenant_path.read_text(encoding="utf-8").replace("geographies: [europe]", "geographies: []"),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_provider_region_without_geography_tag_skips_geography_check(tmp_path):
    """A region that exists but declares no 'geography' tag at all is
    structurally unknowable, not a violation."""
    root = _base_solution(tmp_path)
    path = root / "registries/azure.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  regions:\n    - name: westeurope\n      geography: europe\n",
            "  regions:\n    - name: westeurope\n",
        ),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_deployment_with_multiple_providers_reports_each_mismatch_independently(tmp_path):
    """Two providers on one workspace, one matching and one not — only the
    mismatched one is reported, and it does not short-circuit the other."""
    root = _base_solution(tmp_path)
    _add_us_region_to_azure_config(root)
    _write(
        root,
        "provider2.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: azure-secondary\nspec:\n"
        "  properties:\n    type: azure\n    region: eastus2\n",
    )
    workspace_path = root / "workspace.yaml"
    workspace_path.write_text(
        workspace_path.read_text(encoding="utf-8").replace(
            "  providers: [azure-main]\n", "  providers: [azure-main, azure-secondary]\n"
        ),
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    messages = context.diagnostics.messages()
    assert any("azure-secondary" in m and "eastus2" in m for m in messages)
    assert not any("azure-main" in m and "westeurope" in m and "geography" in m for m in messages)


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
#
# Component/namespace resource-existence checks moved to Phase 1 model
# validators on WorkspaceSpecModel now that topology is inline (ADR-0028) —
# see test_models_workspace.py for those. Only the TopologyConfig registry
# cross-check (ADR-0013/ADR-0014) remains a Phase 2 concern, tested below.


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
# Workspace -> Network: resource subnet references name a real subnet
# (docs/work/solution-loading-and-phase2-validation.md)
# ---------------------------------------------------------------------------


def _solution_with_subnet(tmp_path: Path, subnet_name: str = "app") -> Path:
    """Base solution + a network.yaml and a workspace resource referencing
    it via `subnet: {network, subnet}`."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "network.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: network
meta:
  name: main-network
spec:
  networks:
    - name: vnet-main
      address_space: ["10.0.0.0/16"]
      subnets:
        - name: app
          cidr: "10.0.1.0/24"
""",
    )
    workspace_yaml = """apiVersion: strata.huybrechts.xyz/v2
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
  networks: [main-network]
  resources:
    - name: storage-account
      resource: storage-account
      role: control-plane
      subnet:
        network: main-network
        subnet: SUBNET_NAME
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: storage-account
  namespaces: [apps]
  dns_zones: [example]
"""
    _write(root, "workspace.yaml", workspace_yaml.replace("SUBNET_NAME", subnet_name))
    return root


def test_workspace_resource_subnet_reference_is_accepted(tmp_path):
    context = _resolve(_solution_with_subnet(tmp_path))
    assert context.ok, context.diagnostics.messages()


def test_workspace_resource_subnet_not_found_in_network_is_caught(tmp_path):
    root = _solution_with_subnet(tmp_path, subnet_name="ghost-subnet")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost-subnet" in m for m in context.diagnostics.messages())


def test_workspace_resource_subnet_check_is_skipped_without_a_network_document(tmp_path):
    """An unresolved `subnet.network` reference is caught by `validate_references`,
    not duplicated here — this only proves the subnet check itself does not
    crash when the referenced document is missing."""
    root = _solution_with_subnet(tmp_path)
    (root / "network.yaml").unlink()

    context = _resolve(root)
    assert not context.ok
    # validate_references already reports the dangling 'main-network' reference.
    assert any("main-network" in m for m in context.diagnostics.messages())


def test_workspace_resource_subnet_check_runs_without_a_topology_block(tmp_path):
    """Regression guard: the subnet check is wired unconditionally in
    `_check_workspaces()`, not gated on `workspace.spec.topology` like its
    sibling topology-components check — a workspace with no `topology:` at
    all must still have its subnet reference checked."""
    root = _solution_with_subnet(tmp_path, subnet_name="ghost-subnet")
    path = root / "workspace.yaml"
    content = path.read_text(encoding="utf-8")
    topology_block = """  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: storage-account
"""
    assert topology_block in content
    path.write_text(content.replace(topology_block, ""), encoding="utf-8")

    context = _resolve(root)
    assert not context.ok
    assert any("ghost-subnet" in m for m in context.diagnostics.messages())


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
# docs/design/gap_fit_v1.md gap #12, generalized (docs/design/deploy-command.md's
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
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: storage-account
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
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: storage-account
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


def test_topology_is_reachable_as_part_of_the_workspace_document(tmp_path):
    """Topology is inline on `WorkspaceModel.spec.topology` now (ADR-0028) —
    there is no separate Topology document to add to the reachable set;
    its fields (and any tokens in them) are already covered by the
    `(workspace, False)` entry `_documents_reachable_from_workspace()`
    always includes first."""
    from strata.controllers.semantic_checks import _documents_reachable_from_workspace

    context = _resolve(_base_solution(tmp_path))
    documents = _documents_reachable_from_workspace(context.controller.index, "main")
    kinds = {type(document).__name__ for document, _claimed in documents}
    assert "WorkspaceModel" in kinds
    assert "ProviderModel" in kinds
    assert "ResourceModel" in kinds


# ---------------------------------------------------------------------------
# Environment -> Configuration.spec.security (docs/design/gap_fit_v1.md gap #7 —
# real usage: config-deploy's config/stores.yaml)
# ---------------------------------------------------------------------------


def test_environment_store_allowed_by_security_passes(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_variable_stores: [constant]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_environment_store_not_allowed_by_security_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_variable_stores: [azure-appconfig]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("PUBLIC_IP" in m for m in context.diagnostics.messages())
    assert any(d.code == "disallowed_store" for d in context.diagnostics.errors)


def test_security_check_is_skipped_without_a_configuration_document(tmp_path):
    """No Configuration doc means no declared policy — skip, don't guess.
    Mirrors `test_topology_component_check_is_skipped_without_a_configuration_document`'s
    own reasoning for spec.topologies."""
    root = _base_solution(tmp_path)
    (root / "configuration.yaml").unlink()

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_security_with_no_fields_set_restricts_nothing(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "configuration.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "  security: {}\n", encoding="utf-8")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# Configuration.spec.paths (docs/design/path-conventions.md, docs/design/gap_fit_v1.md
# gap #7 — real usage: config-deploy's config/paths.yaml)
# ---------------------------------------------------------------------------

TENANT_PATH_CONVENTION = """  paths:
    - name: tenant-path
      scope: "customers/**"
      pattern: "customers/{code}"
      filename_pattern: "tenant.yaml"
      resolves: tenant
"""


def _relocate_tenant_under_customers(root: Path, *, dir_code: str = "c0062", tenant_name: str | None = None) -> None:
    """Move _base_solution()'s root-level tenant.yaml into
    customers/{dir_code}/tenant.yaml, optionally overriding its meta.name
    to set up a deliberate code-vs-content mismatch."""
    tenant_path = root / "tenant.yaml"
    content = tenant_path.read_text(encoding="utf-8")
    if tenant_name is not None:
        content = content.replace("name: c0062", f"name: {tenant_name}")
    tenant_path.unlink()
    _write(root, f"customers/{dir_code}/tenant.yaml", content)


def _repoint_deployment_tenant(root: Path, tenant_name: str) -> None:
    """Keep deployment.yaml's 'tenant: c0062' reference valid after
    _relocate_tenant_under_customers() renames the tenant."""
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("tenant: c0062", f"tenant: {tenant_name}"), encoding="utf-8"
    )


def _append_paths_config(root: Path, paths_yaml: str) -> None:
    path = root / "configuration.yaml"
    path.write_text(path.read_text(encoding="utf-8") + paths_yaml, encoding="utf-8")


def test_tenant_matching_path_convention_passes_cleanly(tmp_path):
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root)
    _append_paths_config(root, TENANT_PATH_CONVENTION)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


def test_tenant_mismatched_code_warns_by_default_and_does_not_fail_validate(tmp_path):
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root, tenant_name="c0099")
    _repoint_deployment_tenant(root, "c0099")
    _append_paths_config(root, TENANT_PATH_CONVENTION)

    context = _resolve(root)
    assert context.ok  # enforcement: warn (default) — reported, but does not fail validate
    messages = [d.message for d in context.diagnostics.warnings]
    assert any("c0099" in m and "c0062" in m for m in messages)
    assert any(d.code == "path_convention_tenant_mismatch" for d in context.diagnostics.warnings)


def test_tenant_mismatched_code_fails_when_enforcement_is_deny(tmp_path):
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root, tenant_name="c0099")
    _repoint_deployment_tenant(root, "c0099")
    _append_paths_config(
        root,
        """  paths:
    - name: tenant-path
      scope: "customers/**"
      pattern: "customers/{code}"
      filename_pattern: "tenant.yaml"
      resolves: tenant
      enforcement: deny
""",
    )

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "path_convention_tenant_mismatch" for d in context.diagnostics.errors)


def test_structural_shape_mismatch_is_caught(tmp_path):
    """A tenant filed one directory too deep for its convention (the real,
    empirically-found v1 bug class) — caught even with no 'resolves' logic
    involved, since the pattern itself simply doesn't match."""
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root, dir_code="c0062/nested")
    _append_paths_config(root, TENANT_PATH_CONVENTION)

    context = _resolve(root)
    assert context.ok  # warn by default
    assert any(d.code == "path_convention_mismatch" for d in context.diagnostics.warnings)


def test_paths_check_is_skipped_without_a_configuration_document(tmp_path):
    """No Configuration doc means no declared convention — skip, don't guess."""
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root)
    _append_paths_config(root, TENANT_PATH_CONVENTION)
    (root / "configuration.yaml").unlink()

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_document_outside_every_scope_is_unchecked(tmp_path):
    """A file that matches no declared convention's scope is simply
    unchecked — no 'closed universe' concept for spec.paths."""
    root = _base_solution(tmp_path)
    _append_paths_config(root, TENANT_PATH_CONVENTION)
    # provider.yaml lives at the solution root, outside 'customers/**'.

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


def test_filename_only_capture_with_no_directory_capture_is_checked(tmp_path):
    """provider-path's real shape: the capture lives entirely in the
    filename, the directory pattern is a pure literal with no capture at
    all — moving provider.yaml to match confirms the split still works
    end to end, not just at the utility level."""
    root = _base_solution(tmp_path)
    provider_path = root / "provider.yaml"
    provider_path.rename(root / "providers-tmp.yaml")
    _write(root, "providers/westeurope.yaml", (root / "providers-tmp.yaml").read_text(encoding="utf-8"))
    (root / "providers-tmp.yaml").unlink()
    _append_paths_config(
        root,
        """  paths:
    - name: provider-path
      scope: "providers/**"
      pattern: "providers"
      filename_pattern: "{region}.yaml"
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


def test_filename_only_capture_mismatch_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    provider_path = root / "provider.yaml"
    provider_path.rename(root / "providers-tmp.yaml")
    _write(root, "providers/azure/westeurope.yaml", (root / "providers-tmp.yaml").read_text(encoding="utf-8"))
    (root / "providers-tmp.yaml").unlink()
    _append_paths_config(
        root,
        """  paths:
    - name: provider-path
      scope: "providers/**"
      pattern: "providers"
      filename_pattern: "{region}.yaml"
""",
    )

    context = _resolve(root)
    assert context.ok  # warn by default
    assert any(d.code == "path_convention_mismatch" for d in context.diagnostics.warnings)


def test_overlapping_scope_prefers_matching_depth_over_declaration_order(tmp_path):
    """Multiple conventions can share one `scope` prefix to describe
    different directory depths of the same hierarchy (the real
    `deploy/hubs/**` bug this guards against — see
    docs/design/path-conventions.md). The deeper convention is declared
    FIRST here: under the old 'first scope match wins' behavior this would
    have permanently shadowed the shallower, equally valid convention and
    forced the tenant file to be checked against a pattern that can never
    match it."""
    root = _base_solution(tmp_path)
    _relocate_tenant_under_customers(root)
    _append_paths_config(
        root,
        """  paths:
    - name: nested-path
      scope: "customers/**"
      pattern: "customers/{code}/{sub}"
      filename_pattern: "extra.yaml"
    - name: tenant-path
      scope: "customers/**"
      pattern: "customers/{code}"
      filename_pattern: "tenant.yaml"
      resolves: tenant
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


# ---------------------------------------------------------------------------
# DeploymentLayersModel cross-check (docs/design/path-conventions.md Phase 3)
# ---------------------------------------------------------------------------

CONTROL_PATH_CONVENTION = """  paths:
    - name: control-path
      scope: "deploy/control/**"
      pattern: "deploy/control/{control}"
      resolves: layers
      segments:
        - name: control
          pattern: "^[a-z][a-z0-9-]*$"
"""


def _partial_layers_deployment(name: str, *, follows: str, segments: dict) -> str:
    segments_yaml = "\n".join(f"      {k}: {v}" for k, v in segments.items())
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: {name}
spec:
  partial: true
  layers:
    follows: {follows}
    segments:
{segments_yaml}
"""


def test_layers_segment_pattern_violation_is_caught(tmp_path):
    """The real, empirically-found v1 case: a segment value that doesn't
    match its own declared pattern (here: uppercase, fails '^[a-z][a-z0-9-]*$')."""
    root = _base_solution(tmp_path)
    _append_paths_config(root, CONTROL_PATH_CONVENTION)
    _write(
        root,
        "deploy/control/DEV/deployment.yaml",
        _partial_layers_deployment("control-dev", follows="control-path", segments={"control": "DEV"}),
    )

    context = _resolve(root)
    assert context.ok  # warn by default
    assert any(d.code == "path_convention_segment_mismatch" for d in context.diagnostics.warnings)


def test_layers_segments_agreeing_with_real_path_passes_cleanly(tmp_path):
    root = _base_solution(tmp_path)
    _append_paths_config(root, CONTROL_PATH_CONVENTION)
    _write(
        root,
        "deploy/control/dev/deployment.yaml",
        _partial_layers_deployment("control-dev", follows="control-path", segments={"control": "dev"}),
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


def test_layers_segments_drift_from_real_path_is_caught(tmp_path):
    """Both values are individually schema-valid ('dev'/'prod' both match
    '^[a-z][a-z0-9-]*$'), so only the path-vs-declared-segments cross-check
    (Phase 3) — not the structural pattern check — catches this."""
    root = _base_solution(tmp_path)
    _append_paths_config(root, CONTROL_PATH_CONVENTION)
    _write(
        root,
        "deploy/control/dev/deployment.yaml",
        _partial_layers_deployment("control-dev", follows="control-path", segments={"control": "prod"}),
    )

    context = _resolve(root)
    assert context.ok  # warn by default
    codes = [d.code for d in context.diagnostics.warnings]
    assert "path_convention_layers_drift" in codes
    assert "path_convention_segment_mismatch" not in codes


def test_layers_follows_naming_an_unknown_convention_is_always_an_error(tmp_path):
    """Resolved Open Question #2: unknown when a Configuration document
    exists is always an error, regardless of any convention's own
    'enforcement' (there is no convention to read one from)."""
    root = _base_solution(tmp_path)
    _append_paths_config(root, CONTROL_PATH_CONVENTION)
    _write(
        root,
        "deploy/control/dev/deployment.yaml",
        _partial_layers_deployment("control-dev", follows="bogus-path", segments={"control": "dev"}),
    )

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "unknown_path_convention" for d in context.diagnostics.errors)


def test_layers_cross_check_is_skipped_without_a_configuration_document(tmp_path):
    root = _base_solution(tmp_path)
    (root / "configuration.yaml").unlink()
    _write(
        root,
        "deploy/control/dev/deployment.yaml",
        _partial_layers_deployment("control-dev", follows="bogus-path", segments={"control": "dev"}),
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_layers_with_no_segments_declared_skips_the_cross_check_entirely(tmp_path):
    """No consumer of `layers` exists yet to auto-populate segments — a
    Deployment naming `follows` with no `segments` at all has nothing to
    cross-check (docs/design/path-conventions.md's 'Deliberately out of
    scope'). Structural path matching (_check_paths()) still applies
    independently and passes here, since the file's own real location
    matches control-path's pattern."""
    root = _base_solution(tmp_path)
    _append_paths_config(root, CONTROL_PATH_CONVENTION)
    _write(
        root,
        "deploy/control/dev/deployment.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: control-dev
spec:
  partial: true
  layers:
    follows: control-path
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not context.diagnostics.warnings


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
  image_name: int-docker-test/src/acme.dispatcher.api
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
# it (docs/design/gap_fit_v1.md gap #3).
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


# ---------------------------------------------------------------------------
# Deployment <-> Version: workspace exclusivity (docs/work/version-lifecycle.md
# Phase 1)
# ---------------------------------------------------------------------------


def _write_version(root: Path, *, name: str = "prd", workspace: str | None = None) -> None:
    workspace_line = f"  workspace: {workspace}\n" if workspace is not None else ""
    _write(
        root,
        "version.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: {name}
spec:
{workspace_line}  pins: {{}}
""",
    )


def _point_deployment_at_version(root: Path, version_name: str = "prd") -> None:
    path = root / "deployment.yaml"
    path.write_text(path.read_text(encoding="utf-8") + f"  version: {version_name}\n", encoding="utf-8")


def test_version_with_declared_workspace_matching_deployment_passes(tmp_path):
    root = _base_solution(tmp_path)
    _write_version(root, workspace="main")
    _point_deployment_at_version(root)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_version_with_declared_workspace_mismatching_deployment_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    _write_version(root, workspace="other")
    _point_deployment_at_version(root)

    context = _resolve(root)
    assert not context.ok
    messages = context.diagnostics.messages()
    assert any(d.code == "version_workspace_mismatch" for d in context.diagnostics.errors)
    assert any("'main'" in m and "'other'" in m for m in messages)


def test_version_with_no_declared_workspace_is_established_by_first_referencing_deployment(tmp_path):
    """Unset `spec.workspace` + one referencing deployment: nothing to
    conflict with yet, so this passes cleanly — the deployment's workspace
    becomes the expectation, not a declared value needing a match."""
    root = _base_solution(tmp_path)
    _write_version(root)
    _point_deployment_at_version(root)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_version_with_no_declared_workspace_catches_a_second_deployment_disagreeing(tmp_path):
    """Two deployments referencing the same unset-workspace Version document,
    naming two different workspaces — the second one disagrees with
    whichever workspace the first one established."""
    root = _base_solution(tmp_path)
    _write_version(root)
    _point_deployment_at_version(root)
    _write(
        root,
        "workspace2.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: secondary
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/secondary}
""",
    )
    _write(
        root,
        "deployment2.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: secondary-deployment
spec:
  workspace: secondary
  environments: [prd]
  version: prd
""",
    )

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "version_workspace_mismatch" for d in context.diagnostics.errors)


def test_version_with_no_referencing_deployment_is_a_no_op(tmp_path):
    """A Version document nothing references has nothing to check against."""
    root = _base_solution(tmp_path)
    _write_version(root, workspace="main")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_deployment_with_no_workspace_skips_the_version_check(tmp_path):
    """A partial (reusable-base) deployment referencing a Version document
    but naming no workspace of its own has nothing to compare — skip,
    don't guess (same 'partial: true' shape `_partial_layers_deployment()`
    already uses for an analogous reason)."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "workspace2.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: secondary
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/secondary}
""",
    )
    _write_version(root, workspace="secondary")
    _write(
        root,
        "deployment-base.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: reusable-base
spec:
  partial: true
  version: prd
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
