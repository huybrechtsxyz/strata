#!/usr/bin/env python3
"""Integration tests for cross-document semantic checks (`semantic_checks.py`).

Each test builds a minimal, real solution via `open_solution(...).resolve()`
— the same path `strata validate` runs — rather than calling the private
`_check_*` functions directly, so these prove the wiring works end to end,
not just that a service method works in isolation (already covered by its
own unit tests).
"""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.utils.diagnostics import Severity

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
      resources: [storage-account]
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


def _us_tenant_with_us_provider(root: Path) -> None:
    """A us-only tenant, plus a second provider document in eastus2 the deployment can bind."""
    _add_us_region_to_azure_config(root)
    tenant_path = root / "tenant.yaml"
    tenant_path.write_text(tenant_path.read_text(encoding="utf-8").replace("[europe]", "[us]"), encoding="utf-8")
    _write(
        root,
        "provider-us.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-us
spec:
  type: azure
  region: eastus2
""",
    )


def test_geography_check_uses_workspace_default_without_binding(tmp_path):
    """Unbound, the workspace's westeurope default is checked — and fails a us-only tenant."""
    root = _base_solution(tmp_path)
    _us_tenant_with_us_provider(root)

    context = _resolve(root)
    assert any("westeurope" in m and "c0062" in m for m in context.diagnostics.messages())


def test_geography_check_uses_deployment_provider_binding(tmp_path):
    """The same workspace deploys for a us-only tenant once its slot is rebound —
    the binding inherited from a partial base via `extends`."""
    root = _base_solution(tmp_path)
    _us_tenant_with_us_provider(root)
    _write(
        root,
        "deployment-us-base.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: us-base
spec:
  partial: true
  providers:
    - slot: azure-main
      provider: azure-us
""",
    )
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("spec:\n", "spec:\n  extends: us-base\n", 1), encoding="utf-8"
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# Deployment -> Workspace provider slots (docs/design/provider-slots.md)
# ---------------------------------------------------------------------------


def _provider_doc(root: Path, name: str, provider_type: str = "azure", region: str = "westeurope") -> None:
    _write(
        root,
        f"{name}.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: {name}
spec:
  type: {provider_type}
  region: {region}
""",
    )


def _bind_in_deployment(root: Path, *bindings: tuple[str, str]) -> None:
    """Add `spec.providers` bindings to the base solution's deployment."""
    lines = "".join(f"    - slot: {slot}\n      provider: {provider}\n" for slot, provider in bindings)
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("  tenant: c0062\n", f"  tenant: c0062\n  providers:\n{lines}", 1),
        encoding="utf-8",
    )


def _extend_deployment(root: Path, base: str) -> None:
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("spec:\n", f"spec:\n  extends: {base}\n", 1), encoding="utf-8"
    )


def _partial_base(root: Path, name: str, body: str) -> None:
    _write(
        root,
        f"{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: {name}\nspec:\n  partial: true\n{body}",
    )


def _findings(context) -> dict[str, str]:
    """`{code: severity}` for every provider-binding finding."""
    found = {d.code: "error" for d in context.diagnostics.errors if d.code and d.code.startswith("provider_")}
    found.update({d.code: "warning" for d in context.diagnostics.warnings if d.code and d.code.startswith("provider_")})
    return found


def test_provider_binding_to_same_type_is_clean(tmp_path):
    root = _base_solution(tmp_path)
    _provider_doc(root, "azure-other")
    _bind_in_deployment(root, ("azure-main", "azure-other"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _findings(context) == {}


def test_provider_binding_unknown_slot_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _provider_doc(root, "azure-other")
    _bind_in_deployment(root, ("ghost", "azure-other"))

    context = _resolve(root)
    assert not context.ok
    assert _findings(context) == {"provider_slot_unknown": "error"}
    assert any("ghost" in m and "azure-main" in m for m in context.diagnostics.messages())


def test_provider_binding_type_mismatch_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _provider_doc(root, "aws-main", provider_type="aws", region="us-east-1")
    _bind_in_deployment(root, ("azure-main", "aws-main"))

    context = _resolve(root)
    assert not context.ok
    assert _findings(context) == {"provider_type_mismatch": "error"}


def test_provider_binding_to_the_default_is_a_warning(tmp_path):
    root = _base_solution(tmp_path)
    _bind_in_deployment(root, ("azure-main", "azure-main"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()  # a warning, not a failure
    assert _findings(context) == {"provider_binding_redundant": "warning"}


def test_provider_binding_reset_to_default_via_extends_is_not_flagged(tmp_path):
    """A leaf may legitimately reset a base's binding back to the workspace default."""
    root = _base_solution(tmp_path)
    _provider_doc(root, "azure-other")
    _partial_base(root, "other-base", "  providers:\n    - slot: azure-main\n      provider: azure-other\n")
    _extend_deployment(root, "other-base")
    _bind_in_deployment(root, ("azure-main", "azure-main"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _findings(context) == {}


def test_unknown_slot_inherited_through_extends_is_reported(tmp_path):
    root = _base_solution(tmp_path)
    _provider_doc(root, "azure-other")
    _partial_base(root, "ghost-base", "  providers:\n    - slot: ghost\n      provider: azure-other\n")
    _extend_deployment(root, "ghost-base")

    context = _resolve(root)
    assert _findings(context) == {"provider_slot_unknown": "error"}
    assert any("inherited through 'extends'" in m for m in context.diagnostics.messages())


def test_partial_base_provider_bindings_are_not_checked_on_their_own(tmp_path):
    """A partial base's workspace may be overridden by a leaf, so it is only judged merged."""
    root = _base_solution(tmp_path)
    _provider_doc(root, "azure-other")
    _partial_base(
        root, "lonely-base", "  workspace: main\n  providers:\n    - slot: ghost\n      provider: azure-other\n"
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _findings(context) == {}


# ---------------------------------------------------------------------------
# Deployment -> Workspace asset slots (docs/design/asset-management.md)
# ---------------------------------------------------------------------------


def _asset_doc(root: Path, name: str) -> None:
    _write(
        root,
        f"asset-{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: {name}\nspec:\n  source_path: api\n",
    )


def _asset_slot_solution(tmp_path: Path) -> Path:
    """The base solution whose workspace declares the asset slot `api` (default `api-default`)."""
    root = _base_solution(tmp_path)
    _asset_doc(root, "api-default")
    _asset_doc(root, "api-other")
    path = root / "workspace.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "providers: [azure-main]",
            "providers: [azure-main]\n  assets:\n    - {slot: api, asset: api-default}",
        ),
        encoding="utf-8",
    )
    return root


def _bind_assets(root: Path, *bindings: tuple[str, str]) -> None:
    lines = "".join(f"    - slot: {slot}\n      asset: {asset}\n" for slot, asset in bindings)
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("  tenant: c0062\n", f"  tenant: c0062\n  assets:\n{lines}", 1),
        encoding="utf-8",
    )


def _asset_findings(context) -> dict[str, str]:
    """`{code: severity}` for every asset-binding finding."""
    found = {d.code: "error" for d in context.diagnostics.errors if d.code and d.code.startswith("asset_")}
    found.update({d.code: "warning" for d in context.diagnostics.warnings if d.code and d.code.startswith("asset_")})
    return found


def test_asset_binding_to_another_document_is_clean(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _bind_assets(root, ("api", "api-other"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _asset_findings(context) == {}


def test_asset_binding_unknown_slot_is_an_error(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _bind_assets(root, ("ghost", "api-other"))

    context = _resolve(root)
    assert not context.ok
    assert _asset_findings(context) == {"asset_slot_unknown": "error"}
    assert any("ghost" in m and "api" in m for m in context.diagnostics.messages())


def test_asset_binding_to_a_workspace_without_asset_slots_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _asset_doc(root, "api-other")
    _bind_assets(root, ("api", "api-other"))

    context = _resolve(root)
    assert _asset_findings(context) == {"asset_slot_unknown": "error"}


def test_asset_binding_to_the_default_is_a_warning(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _bind_assets(root, ("api", "api-default"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()  # a warning, not a failure
    assert _asset_findings(context) == {"asset_binding_redundant": "warning"}


def test_asset_binding_reset_to_default_via_extends_is_not_flagged(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _partial_base(root, "other-base", "  assets:\n    - slot: api\n      asset: api-other\n")
    _extend_deployment(root, "other-base")
    _bind_assets(root, ("api", "api-default"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _asset_findings(context) == {}


def test_unknown_asset_slot_inherited_through_extends_is_reported(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _partial_base(root, "ghost-base", "  assets:\n    - slot: ghost\n      asset: api-other\n")
    _extend_deployment(root, "ghost-base")

    context = _resolve(root)
    assert _asset_findings(context) == {"asset_slot_unknown": "error"}
    assert any("inherited through 'extends'" in m for m in context.diagnostics.messages())


def test_partial_base_asset_bindings_are_not_checked_on_their_own(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _partial_base(root, "lonely-base", "  workspace: main\n  assets:\n    - slot: ghost\n      asset: api-other\n")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _asset_findings(context) == {}


def test_asset_binding_to_a_missing_document_is_a_reference_error_not_a_binding_finding(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _bind_assets(root, ("api", "no-such-asset"))

    context = _resolve(root)
    assert not context.ok
    assert _asset_findings(context) == {}
    assert any(d.code == "unknown_reference" for d in context.diagnostics.errors)


def test_provider_and_asset_findings_stay_separate(tmp_path):
    root = _asset_slot_solution(tmp_path)
    _bind_assets(root, ("ghost", "api-other"))
    _bind_in_deployment(root, ("azure-main", "azure-main"))

    context = _resolve(root)
    assert _asset_findings(context) == {"asset_slot_unknown": "error"}
    assert _findings(context) == {"provider_binding_redundant": "warning"}


# ---------------------------------------------------------------------------
# Manifest remote -> Integration (docs/work/remotes-at-scale.md C4)
# ---------------------------------------------------------------------------


def _remote_with_integration(root: Path, integration: str | None) -> None:
    integration_line = f"      integration: {integration}\n" if integration else ""
    _write(
        root,
        "strata.yaml",
        MANIFEST.replace(
            "spec: {}\n",
            "spec:\n  remotes:\n    - name: infra\n      type: git\n      url: https://example.com/infra.git\n"
            f"      reference: main\n{integration_line}",
        ),
    )


def _remote_integration_findings(context) -> dict[str, str]:
    codes = {"remote_integration_unknown", "remote_integration_not_sources"}
    return {d.code: "error" for d in context.diagnostics.errors if d.code in codes}


def test_remote_integration_with_the_sources_capability_is_clean(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "corp-git", "[sources]")
    _remote_with_integration(root, "corp-git")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _remote_integration_findings(context) == {}


def test_remote_without_an_integration_has_no_finding(tmp_path):
    root = _base_solution(tmp_path)
    _remote_with_integration(root, None)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _remote_integration_findings(context) == {}


def test_remote_integration_that_does_not_exist_is_an_error_with_a_hint(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "corp-git", "[sources]")
    _remote_with_integration(root, "corp-gti")

    context = _resolve(root)
    assert not context.ok
    assert _remote_integration_findings(context) == {"remote_integration_unknown": "error"}
    finding = next(d for d in context.diagnostics.errors if d.code == "remote_integration_unknown")
    assert "did you mean 'corp-git'" in finding.message
    assert finding.location == "spec.remotes.0.integration"
    assert finding.source is not None and finding.source.endswith("strata.yaml")


def test_remote_integration_without_the_sources_capability_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "corp-git", "[secrets]")
    _remote_with_integration(root, "corp-git")

    context = _resolve(root)
    assert not context.ok
    assert _remote_integration_findings(context) == {"remote_integration_not_sources": "error"}
    assert any("declares: secrets" in m for m in context.diagnostics.messages())


# ---------------------------------------------------------------------------
# Asset location -> Integration (docs/work/remotes-at-scale.md C3)
# ---------------------------------------------------------------------------


def _git_integration(root: Path, name: str, capabilities: str) -> None:
    _write(
        root,
        f"integration-{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: {name}\nspec:\n"
        f"  type: git\n  capabilities: {capabilities}\n",
    )


def _location_asset(root: Path, *, integration: str | None, fetch: str = "strata") -> None:
    integration_line = f"    integration: {integration}\n" if integration else ""
    _write(
        root,
        "asset-c0062-api.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: c0062-api\nspec:\n  source_path: api\n"
        f"  location:\n    url: https://example.com/c0062.git\n    reference: v1\n    fetch: {fetch}\n{integration_line}",
    )


def _location_findings(context) -> dict[str, str]:
    codes = {"asset_integration_not_sources", "remote_integration_ignored"}
    found = {d.code: "error" for d in context.diagnostics.errors if d.code in codes}
    found.update({d.code: "warning" for d in context.diagnostics.warnings if d.code in codes})
    return found


def test_asset_location_integration_with_the_sources_capability_is_clean(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "customer-git", "[sources]")
    _location_asset(root, integration="customer-git")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _location_findings(context) == {}


def test_asset_location_integration_without_the_sources_capability_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "customer-git", "[secrets]")
    _location_asset(root, integration="customer-git")

    context = _resolve(root)
    assert not context.ok
    assert _location_findings(context) == {"asset_integration_not_sources": "error"}
    assert any("declares: secrets" in m for m in context.diagnostics.messages())


def test_asset_location_integration_that_does_not_exist_is_a_reference_error_only(tmp_path):
    root = _base_solution(tmp_path)
    _location_asset(root, integration="ghost-git")

    context = _resolve(root)
    assert not context.ok
    assert _location_findings(context) == {}
    assert any(d.code == "unknown_reference" and "ghost-git" in d.message for d in context.diagnostics.errors)


def test_asset_location_integration_with_fetch_external_is_a_warning(tmp_path):
    root = _base_solution(tmp_path)
    _git_integration(root, "customer-git", "[sources]")
    _location_asset(root, integration="customer-git", fetch="external")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _location_findings(context) == {"remote_integration_ignored": "warning"}


def test_asset_location_url_with_a_password_fails_loading_without_printing_it(tmp_path):
    root = _base_solution(tmp_path)
    _location_asset(root, integration=None)
    path = root / "asset-c0062-api.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("https://example.com", "https://bot:s3cr3tpw@example.com"),
        encoding="utf-8",
    )

    context = open_solution(root)

    assert not context.ok
    assert any("must not embed a password" in m for m in context.diagnostics.messages())
    assert "s3cr3tpw" not in repr(context.diagnostics.messages())


_TOKEN_AUTH = "  authentication:\n    method: token\n    token:\n      token: CUSTOMER_GIT_TOKEN\n"
_OAUTH_AUTH = "  authentication:\n    method: oauth2\n    oauth2:\n      client_id: A\n      client_secret: B\n"


def _fetch_integration(root: Path, *, auth: str = _TOKEN_AUTH, enabled: bool = True) -> None:
    _write(
        root,
        "integration-customer-git.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: customer-git\nspec:\n"
        f"  type: git\n  capabilities: [sources]\n  enabled: {str(enabled).lower()}\n{auth}",
    )


def _select_asset_in_step(root: Path) -> None:
    """The workspace declares slot `api` (default `c0062-api`) and its execution step selects it."""
    path = root / "workspace.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("providers: [azure-main]", "providers: [azure-main]\n  assets:\n    - {slot: api, asset: c0062-api}")
        .replace(
            "      provisioner: tf\n      resources: [storage-account]\n",
            "      provisioner: tf\n      resources: [storage-account]\n      assets: [api]\n",
        ),
        encoding="utf-8",
    )


def _declare_git_token(root: Path) -> None:
    path = root / "environment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "  secrets:\n    - key: CUSTOMER_GIT_TOKEN\n      store: environment\n      value: CUSTOMER_REPOS_PAT\n",
        encoding="utf-8",
    )


def _credential_findings(context) -> dict[str, str]:
    codes = {
        "fetch_credential_key_undeclared",
        "asset_integration_disabled",
        "asset_integration_auth_unsupported",
        "remote_integration_disabled",
        "remote_integration_auth_unsupported",
    }
    found = {d.code: "error" for d in context.diagnostics.errors if d.code in codes}
    found.update({d.code: "warning" for d in context.diagnostics.warnings if d.code in codes})
    return found


def test_a_cloned_assets_credential_key_declared_in_the_environment_is_clean(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root)
    _location_asset(root, integration="customer-git")
    _select_asset_in_step(root)
    _declare_git_token(root)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _credential_findings(context) == {}


def test_a_cloned_assets_undeclared_credential_key_is_reported_per_deployment(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root)
    _location_asset(root, integration="customer-git")
    _select_asset_in_step(root)

    context = _resolve(root)
    assert not context.ok
    assert _credential_findings(context) == {"fetch_credential_key_undeclared": "error"}
    finding = next(d for d in context.diagnostics.errors if d.code == "fetch_credential_key_undeclared")
    assert "CUSTOMER_GIT_TOKEN" in finding.message
    assert "'prd-deployment'" in finding.message and "'c0062-api'" in finding.message
    assert finding.location == "spec.environments"


def test_an_undeclared_credential_key_is_reported_for_an_asset_cloned_through_a_manifest_remote(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root)
    _remote_with_integration(root, "customer-git")
    _write(
        root,
        "asset-c0062-api.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: c0062-api\nspec:\n"
        "  remote: infra\n  source_path: api\n",
    )
    _select_asset_in_step(root)

    context = _resolve(root)
    assert _credential_findings(context) == {"fetch_credential_key_undeclared": "error"}


def test_credential_keys_are_not_checked_when_nothing_is_cloned(tmp_path):
    """`fetch: external` clones nothing, and an asset no execution step selects is never synced."""
    root = _base_solution(tmp_path)
    _fetch_integration(root)
    _location_asset(root, integration="customer-git", fetch="external")
    _select_asset_in_step(root)
    external = _resolve(root)
    assert _credential_findings(external) == {}

    unselected_root = _base_solution(tmp_path / "other")
    _fetch_integration(unselected_root)
    _location_asset(unselected_root, integration="customer-git")
    unselected = _resolve(unselected_root)
    assert _credential_findings(unselected) == {}


def test_a_partial_deployments_credential_keys_are_not_checked_on_their_own(tmp_path):
    """A partial base may get its environments from the leaf that extends it, so only the merged result is judged."""
    root = _base_solution(tmp_path)
    _fetch_integration(root)
    _location_asset(root, integration="customer-git")
    _select_asset_in_step(root)
    path = root / "deployment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("spec:\n", "spec:\n  partial: true\n", 1), encoding="utf-8"
    )

    context = _resolve(root)
    assert _credential_findings(context) == {}


def test_a_disabled_integration_on_an_asset_location_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root, enabled=False)
    _location_asset(root, integration="customer-git")

    context = _resolve(root)
    assert not context.ok
    assert _credential_findings(context) == {"asset_integration_disabled": "error"}


def test_an_unsupported_authentication_method_on_an_asset_location_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_OAUTH_AUTH)
    _location_asset(root, integration="customer-git")

    context = _resolve(root)
    assert not context.ok
    assert _credential_findings(context) == {"asset_integration_auth_unsupported": "error"}
    assert any("supported: token, ssh_key, cli" in m for m in context.diagnostics.messages())


def test_a_disabled_or_unsupported_integration_is_not_flagged_when_nothing_is_cloned(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_OAUTH_AUTH, enabled=False)
    _location_asset(root, integration="customer-git", fetch="external")

    context = _resolve(root)
    assert _credential_findings(context) == {}


def test_a_disabled_or_unsupported_integration_on_a_manifest_remote_is_only_a_warning(tmp_path):
    """GitOps and audit pushes also use a remote's integration and ignore `enabled`."""
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_OAUTH_AUTH, enabled=False)
    _remote_with_integration(root, "customer-git")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _credential_findings(context) == {
        "remote_integration_disabled": "warning",
        "remote_integration_auth_unsupported": "warning",
    }


_TOKEN_WITH_USERNAME = _TOKEN_AUTH + "      username: api\n"
_SSH_AUTH = "  authentication:\n    method: ssh_key\n    ssh_key:\n      private_key: K\n"
_CLI_AUTH = "  authentication:\n    method: cli\n    cli:\n      use_cli: true\n"


def _chart_remote_with_integration(root: Path, remote_type: str = "helm") -> None:
    url = "oci://proget.example.com/feeds/charts" if remote_type == "oci" else "https://proget.example.com/helm/charts"
    _write(
        root,
        "strata.yaml",
        MANIFEST.replace(
            "spec: {}\n",
            f"spec:\n  remotes:\n    - name: charts\n      type: {remote_type}\n      url: {url}\n"
            "      integration: customer-git\n",
        ),
    )


def _chart_registry_findings(context) -> dict[str, str]:
    codes = {
        "remote_integration_username_required",
        "remote_integration_auth_unsupported",
        "remote_integration_disabled",
    }
    found = {d.code: "error" for d in context.diagnostics.errors if d.code in codes}
    found.update({d.code: "warning" for d in context.diagnostics.warnings if d.code in codes})
    return found


@pytest.mark.parametrize("remote_type", ["helm", "oci"])
@pytest.mark.parametrize("auth", [_TOKEN_WITH_USERNAME, _CLI_AUTH], ids=["token-with-username", "cli"])
def test_a_chart_registry_remote_with_usable_credentials_is_clean(tmp_path, remote_type, auth):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=auth)
    _chart_remote_with_integration(root, remote_type)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _chart_registry_findings(context) == {}


@pytest.mark.parametrize("remote_type", ["helm", "oci"])
def test_a_chart_registry_token_without_a_username_is_an_error(tmp_path, remote_type):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_TOKEN_AUTH)
    _chart_remote_with_integration(root, remote_type)

    context = _resolve(root)
    assert not context.ok
    assert _chart_registry_findings(context) == {"remote_integration_username_required": "error"}
    message = next(m for m in context.diagnostics.messages() if "without 'username'" in m)
    assert "Remote 'charts'" in message and "chart registry" in message and "customer-git" in message


@pytest.mark.parametrize("auth", [_SSH_AUTH, _OAUTH_AUTH], ids=["ssh_key", "oauth2"])
def test_a_chart_registry_cannot_be_authenticated_with_an_unsupported_method(tmp_path, auth):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=auth)
    _chart_remote_with_integration(root)

    context = _resolve(root)
    assert not context.ok
    assert _chart_registry_findings(context) == {"remote_integration_auth_unsupported": "error"}
    assert any(
        "cannot authenticate a chart registry (supported: token, cli)" in m for m in context.diagnostics.messages()
    )


def test_a_disabled_integration_on_a_chart_registry_remote_is_an_error(tmp_path):
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_TOKEN_WITH_USERNAME, enabled=False)
    _chart_remote_with_integration(root)

    context = _resolve(root)
    assert not context.ok
    assert _chart_registry_findings(context) == {"remote_integration_disabled": "error"}


def test_a_git_remote_token_without_a_username_is_still_fine(tmp_path):
    """Only a chart registry needs the literal username; a git token answers git's password prompt alone."""
    root = _base_solution(tmp_path)
    _fetch_integration(root, auth=_TOKEN_AUTH)
    _remote_with_integration(root, "customer-git")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _chart_registry_findings(context) == {}


def test_asset_location_without_an_integration_has_no_finding(tmp_path):
    root = _base_solution(tmp_path)
    _location_asset(root, integration=None, fetch="external")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert _location_findings(context) == {}


def _two_slots_on_one_provider(root: Path) -> None:
    """Workspace slots `a` and `b` both default to the `azure-main` document."""
    path = root / "workspace.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "providers: [azure-main]",
            "providers:\n    - {slot: a, provider: azure-main}\n    - {slot: b, provider: azure-main}",
        ),
        encoding="utf-8",
    )


def test_two_slots_sharing_a_provider_report_a_geography_mismatch_once(tmp_path):
    root = _base_solution(tmp_path)
    _add_us_region_to_azure_config(root)
    path = root / "provider.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("region: westeurope", "region: eastus2"), encoding="utf-8")
    _two_slots_on_one_provider(root)

    context = _resolve(root)
    assert sum("eastus2" in m for m in context.diagnostics.messages()) == 1


def test_two_slots_sharing_a_provider_report_a_token_once(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "provider.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  configuration:\n    tenant_id: '${var:GHOST_SHARED_VAR}'\n",
        encoding="utf-8",
    )
    _two_slots_on_one_provider(root)

    context = _resolve(root)
    assert sum("GHOST_SHARED_VAR" in m for m in context.diagnostics.messages()) == 1


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
        "  type: azure\n  region: eastus2\n",
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
      resources: [storage-account]
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
      resources: [storage-account]
      dns_zones: [example]
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
      resources: [storage-account]
      namespaces: [apps]
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


def test_value_token_diagnostic_source_is_the_real_offending_document_not_the_deployment(tmp_path):
    """docs/work/gap_fit_v1.md gap #20 — a token error found in a document
    reached *through* the deployment's workspace (here, resource.yaml) must
    attribute `source` to that document, not to deployment.yaml."""
    root = _base_solution(tmp_path)
    path = root / "resource.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  configuration:\n    admin_password: '${secret:GHOST_RESOURCE_SECRET}'\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    matches = [item for item in context.diagnostics.items if "GHOST_RESOURCE_SECRET" in item.message]
    assert matches
    for item in matches:
        assert item.source is not None
        assert item.source.endswith("resource.yaml")
        assert "deployment.yaml" not in item.source


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
    `(workspace, False, source)` entry `_documents_reachable_from_workspace()`
    always includes first."""
    from strata.controllers.semantic_checks import _documents_reachable_from_workspace

    context = _resolve(_base_solution(tmp_path))
    documents = _documents_reachable_from_workspace(context.controller.index, "main")
    kinds = {type(document).__name__ for document, _claimed, _source in documents}
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


def test_a_variable_token_in_a_store_value_is_caught_by_validate(tmp_path):
    """`validate` used to accept this; it then failed at deploy time with
    `value_resolution_failed`, because a store's own value is only resolved
    against `${value:}` references."""
    root = _base_solution(tmp_path)
    path = root / "environment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '    - key: ALIAS_IP\n      store: constant\n      value: "${var:PUBLIC_IP}"\n',
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "store_value_token_unsupported" for d in context.diagnostics.errors)
    assert any("ALIAS_IP" in m for m in context.diagnostics.messages())


def test_a_value_reference_in_a_store_value_passes_validate(tmp_path):
    root = _base_solution(tmp_path)
    path = root / "environment.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '    - key: ENV_NAME\n      store: constant\n      value: "${value:environment.prd.meta.name}"\n',
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


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
# Workspace -> Configuration.spec.security.allowed_key_value_stores
# (docs/work/cross-run-step-output-delivery.md's Implementation Plan step 1,
# 'Governance') — same mechanism as allowed_variable_stores above, but
# checking WorkspaceModel.spec.output_store/.output_store_integration
# instead of an Environment's per-key store declarations.
# ---------------------------------------------------------------------------


def _append_to_workspace(root: Path, spec_yaml: str) -> None:
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8") + spec_yaml, encoding="utf-8")


def test_workspace_output_store_allowed_by_security_passes(tmp_path):
    root = _base_solution(tmp_path)
    _append_to_workspace(root, "  output_store: azure-appconfig\n")
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_key_value_stores: [azure-appconfig]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_workspace_output_store_not_allowed_by_security_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    _append_to_workspace(root, "  output_store: consul\n")
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_key_value_stores: [azure-appconfig]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("consul" in m and "allowed_key_value_stores" in m for m in context.diagnostics.messages())
    assert any(d.code == "disallowed_store" for d in context.diagnostics.errors)


def test_workspace_output_store_integration_resolves_to_its_own_type(tmp_path):
    """output_store_integration names a document — the allow-list is
    checked against *that document's* spec.type, not the reference name
    itself."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "integration.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: shared-consul
spec:
  type: consul
""",
    )
    _append_to_workspace(root, "  output_store_integration: shared-consul\n")
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_key_value_stores: [azure-appconfig]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("consul" in m and "allowed_key_value_stores" in m for m in context.diagnostics.messages())


def test_workspace_output_store_integration_wins_over_a_mismatched_output_store(tmp_path):
    """Both fields set, disagreeing types: `output_store_integration`'s own
    document type is what actually runs at deploy time (`resolve_
    output_store_integration()`'s precedence) and must be what gets
    checked — not the separately-declared `output_store`, even though
    that one alone is allow-listed. A regression test for a real bug:
    checking `output_store` first let a disallowed `output_store_
    integration` backend silently pass validation."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "integration.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: shared-consul
spec:
  type: consul
""",
    )
    _append_to_workspace(root, "  output_store: azure-appconfig\n  output_store_integration: shared-consul\n")
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_key_value_stores: [azure-appconfig]\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert not context.ok
    assert any("consul" in m and "allowed_key_value_stores" in m for m in context.diagnostics.messages())


def test_workspace_without_output_store_is_unaffected_by_restrictive_security(tmp_path):
    """Neither output_store nor output_store_integration set means the
    feature is disabled for this workspace — nothing to check, even under
    a restrictive allow-list that would reject every real backend type."""
    root = _base_solution(tmp_path)
    path = root / "configuration.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "  security:\n    allowed_key_value_stores: []\n",
        encoding="utf-8",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# Workspace -> output-store backend must support compare-and-swap
# (docs/work/cross-run-step-output-delivery.md — DeploymentOutputStore's
# read-modify-write cannot work with a backend lacking real CAS, e.g.
# azure-keyvault; caught here at validate time, independent of any
# Configuration document/allow-list).
# ---------------------------------------------------------------------------


def test_workspace_output_store_lacking_cas_support_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    _append_to_workspace(root, "  output_store: azure-keyvault\n")

    context = _resolve(root)
    assert not context.ok
    assert any("azure-keyvault" in m and "compare-and-swap" in m for m in context.diagnostics.messages())
    assert any(d.code == "output_store_lacks_cas" for d in context.diagnostics.errors)


def test_workspace_output_store_integration_lacking_cas_support_is_caught(tmp_path):
    """Same check, resolved through `output_store_integration` instead of
    the bare `output_store` type — same precedence as the allow-list
    check above."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "integration.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: shared-vault
spec:
  type: azure-keyvault
""",
    )
    _append_to_workspace(root, "  output_store_integration: shared-vault\n")

    context = _resolve(root)
    assert not context.ok
    assert any("azure-keyvault" in m and "compare-and-swap" in m for m in context.diagnostics.messages())


def test_workspace_output_store_with_cas_support_passes(tmp_path):
    root = _base_solution(tmp_path)
    _append_to_workspace(root, "  output_store: azure-appconfig\n")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_workspace_without_output_store_is_unaffected_by_the_cas_check(tmp_path):
    root = _base_solution(tmp_path)

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


# ---------------------------------------------------------------------------
# DnsZoneModel.name -> same domain declared by more than one document
# (docs/config/dns.md's cross-tenant zone-collision validation)
# ---------------------------------------------------------------------------


def test_dns_zone_declared_twice_across_documents_is_caught(tmp_path):
    root = _base_solution(tmp_path)
    _write(
        root,
        "dns-other-tenant.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: example-again
spec:
  zones:
    - name: example.com
      records:
        - name: www
          type: A
          value: "5.6.7.8"
""",
    )

    context = _resolve(root)
    assert context.ok  # warning, not an error — does not fail validate
    assert any(d.code == "dns_zone_declared_twice" for d in context.diagnostics.warnings)
    assert any("example.com" in m for m in context.diagnostics.messages(Severity.WARNING))


def test_dns_zone_declared_once_is_not_flagged(tmp_path):
    context = _resolve(_base_solution(tmp_path))
    assert not any(d.code == "dns_zone_declared_twice" for d in context.diagnostics.warnings)


# ---------------------------------------------------------------------------
# attributeconfig -> target kind: JSON Schema validation of properties/
# custom/configuration blocks (docs/design/dynamic-configuration-validation.md)
# Phase 3)
# ---------------------------------------------------------------------------

_OWNER_SCHEMA = """  schema:
    type: object
    required: [owner]
    additionalProperties: false
    properties:
      owner:
        type: string
        pattern: "^[a-z-]+$"
      retries:
        type: integer
"""


def _solution_with_resource_and_attributeconfig(
    tmp_path: Path, *, resource_spec: str = "", schema_yaml: str = _OWNER_SCHEMA, selector: str = ""
) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "resource.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: storage-account
spec:
  provider_type: azure
  resource_type: storage_account
{resource_spec}""",
    )
    _write(
        root,
        "attributeconfig.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: resource-custom-owner
spec:
  target:
    kind: resource
    block: custom
{selector}{schema_yaml}""",
    )
    return root


def test_attributeconfig_passing_case(tmp_path):
    root = _solution_with_resource_and_attributeconfig(tmp_path, resource_spec="  custom:\n    owner: platform-team\n")
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_pattern_mismatch_is_caught(tmp_path):
    root = _solution_with_resource_and_attributeconfig(tmp_path, resource_spec="  custom:\n    owner: Platform_Team\n")
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_pattern_mismatch" for d in context.diagnostics.errors)


def test_attributeconfig_field_missing_is_caught_when_block_is_entirely_unset(tmp_path):
    """A document that omits `custom:` entirely is checked as `{}`, not
    skipped — the common omission case `required` exists to catch."""
    root = _solution_with_resource_and_attributeconfig(tmp_path, resource_spec="")
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_missing" for d in context.diagnostics.errors)


def test_attributeconfig_field_not_allowed_is_caught(tmp_path):
    root = _solution_with_resource_and_attributeconfig(
        tmp_path, resource_spec="  custom:\n    owner: platform-team\n    extra: nope\n"
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_not_allowed" for d in context.diagnostics.errors)


def test_attributeconfig_type_mismatch_is_caught(tmp_path):
    root = _solution_with_resource_and_attributeconfig(
        tmp_path, resource_spec='  custom:\n    owner: platform-team\n    retries: "nope"\n'
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_type_mismatch" for d in context.diagnostics.errors)


def test_attributeconfig_falls_back_for_an_unmapped_keyword(tmp_path):
    schema = """  schema:
    type: object
    properties:
      tier:
        type: string
        enum: [standard, premium]
"""
    root = _solution_with_resource_and_attributeconfig(
        tmp_path, resource_spec="  custom:\n    tier: gold\n", schema_yaml=schema
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_validation_error" for d in context.diagnostics.errors)


def test_attributeconfig_invalid_schema_is_caught(tmp_path):
    schema = """  schema:
    type: not-a-real-type
"""
    root = _solution_with_resource_and_attributeconfig(tmp_path, schema_yaml=schema)
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_invalid" for d in context.diagnostics.errors)
    # Attributed to the schema document itself, not the (never-checked) resource.
    assert any("resource-custom-owner" in m for m in context.diagnostics.messages())


def test_attributeconfig_unused_warns_when_selector_matches_nothing(tmp_path):
    selector = "    selector:\n      spec.provider_type: ghost\n"
    root = _solution_with_resource_and_attributeconfig(tmp_path, selector=selector)
    context = _resolve(root)
    assert context.ok  # warning, not an error — does not fail validate
    assert any(d.code == "attributeconfig_unused" for d in context.diagnostics.warnings)


def test_attributeconfig_selector_scopes_to_matching_documents_only(tmp_path):
    """Two resources, only one matching the selector — the other is
    untouched even though it would fail the same schema."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "resource.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: storage-account
spec:
  provider_type: azure
  resource_type: storage_account
  custom:
    owner: platform-team
""",
    )
    _write(
        root,
        "resource2.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: other-account
spec:
  provider_type: hetzner
  resource_type: storage_account
""",
    )
    _write(
        root,
        "attributeconfig.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: resource-custom-owner
spec:
  target:
    kind: resource
    block: custom
    selector:
      spec.provider_type: azure
{_OWNER_SCHEMA}""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_cross_field_if_then_fails_when_condition_applies(tmp_path):
    schema = """  schema:
    type: object
    properties:
      tier:
        type: string
      sla:
        type: string
    if:
      properties:
        tier:
          const: premium
    then:
      required: [sla]
"""
    root = _solution_with_resource_and_attributeconfig(
        tmp_path, resource_spec="  custom:\n    tier: premium\n", schema_yaml=schema
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_missing" for d in context.diagnostics.errors)


def test_attributeconfig_cross_field_if_then_passes_when_condition_does_not_apply(tmp_path):
    schema = """  schema:
    type: object
    properties:
      tier:
        type: string
      sla:
        type: string
    if:
      properties:
        tier:
          const: premium
    then:
      required: [sla]
"""
    root = _solution_with_resource_and_attributeconfig(
        tmp_path, resource_spec="  custom:\n    tier: standard\n", schema_yaml=schema
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_no_merge_both_independent_schemas_are_applied(tmp_path):
    """D7: two schemas targeting the same (kind, block) are both evaluated
    independently — neither short-circuits the other."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "resource.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: storage-account
spec:
  provider_type: azure
  resource_type: storage_account
  custom:
    owner: Platform_Team
""",
    )
    _write(
        root,
        "attributeconfig-owner.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: resource-custom-owner
spec:
  target:
    kind: resource
    block: custom
{_OWNER_SCHEMA}""",
    )
    _write(
        root,
        "attributeconfig-extra.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: resource-custom-no-extra-keys
spec:
  target:
    kind: resource
    block: custom
  schema:
    type: object
    maxProperties: 0
""",
    )

    context = _resolve(root)
    assert not context.ok
    codes = [d.code for d in context.diagnostics.errors]
    assert "attributeconfig_pattern_mismatch" in codes  # from resource-custom-owner
    assert "attributeconfig_validation_error" in codes  # maxProperties, from resource-custom-no-extra-keys


# ---------------------------------------------------------------------------
# attributeconfig -> deployment: target.view: effective (merged
# workspace -> environments -> deployment blob; docs/work/
# environment-value-metadata.md Phase 4)
# ---------------------------------------------------------------------------


def _write_deployment_properties_attributeconfig(root: Path, *, view: str, selector: str = "") -> None:
    _write(
        root,
        "attributeconfig.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-properties-owner
spec:
  target:
    kind: deployment
    block: properties
    view: {view}
{selector}  schema:
    type: object
    required: [owner]
""",
    )


def _write_tenant_with_properties_owner(root: Path) -> None:
    """Overwrites `_base_solution()`'s tenant.yaml, adding the base-layer
    `properties.owner` ADR-0024 folds into every deployment it resolves."""
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
  properties:
    owner: platform-team
""",
    )


def test_attributeconfig_document_view_misses_a_value_only_the_tenant_sets(tmp_path):
    """`view: document` (the default) checks the deployment's own raw
    `properties` fragment only — a tenant default (ADR-0024) is folded in by
    `resolve_deployment_chains()`, which this view never consults, so
    `required: [owner]` misfires on a deployment that resolves to a
    perfectly valid, fully-merged `owner`."""
    root = _base_solution(tmp_path)
    _write_tenant_with_properties_owner(root)
    _write_deployment_properties_attributeconfig(root, view="document")

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_missing" for d in context.diagnostics.errors)


def test_attributeconfig_effective_view_sees_the_tenant_default(tmp_path):
    """The identical schema, `view: effective`, checks the merged
    workspace -> environments -> (tenant-resolved) deployment blob instead —
    the tenant's `properties.owner` reaches it, so the same deployment passes."""
    root = _base_solution(tmp_path)
    _write_tenant_with_properties_owner(root)
    _write_deployment_properties_attributeconfig(root, view="effective")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_effective_view_merges_workspace_and_environment_too(tmp_path):
    """Not just the tenant layer — `properties` set on the workspace AND on
    an environment both reach the merged blob, same as `build run`/
    `deploy run` already see. Uses a schema requiring one key from each layer
    so neither assertion could pass from only one of them merging."""
    root = _base_solution(tmp_path)
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
      resources: [storage-account]
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
  properties:
    owner: platform-team
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
  properties:
    region: westeurope
""",
    )
    _write(
        root,
        "attributeconfig.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-properties-owner-and-region
spec:
  target:
    kind: deployment
    block: properties
    view: effective
  schema:
    type: object
    required: [owner, region]
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_effective_view_skips_a_partial_deployment(tmp_path):
    """A `partial: true` base is not deployable and may lack required fields
    by design — skipped, not checked against `{}`."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "deployment.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: prd-deployment
spec:
  partial: true
  tenant: c0062
  environments: [prd]
""",
    )
    _write_deployment_properties_attributeconfig(root, view="effective")

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert any(d.code == "attributeconfig_unused" for d in context.diagnostics.warnings)


def test_attributeconfig_effective_view_unused_warns_when_selector_matches_no_deployment(tmp_path):
    selector = "    selector:\n      spec.workspace: ghost\n"
    root = _base_solution(tmp_path)
    _write_deployment_properties_attributeconfig(root, view="effective", selector=selector)

    context = _resolve(root)
    assert context.ok  # warning, not an error — does not fail validate
    assert any(d.code == "attributeconfig_unused" for d in context.diagnostics.warnings)


def test_attributeconfig_effective_view_finding_names_the_deployment(tmp_path):
    """An `effective`-view finding is attributed to the deployment and its
    message names it — distinct from a `document`-view finding on the same
    document (both attribute to `entry.source`, so the message is what
    disambiguates which blob failed)."""
    root = _base_solution(tmp_path)
    _write_deployment_properties_attributeconfig(root, view="effective")

    context = _resolve(root)
    assert not context.ok
    assert any(
        d.code == "attributeconfig_field_missing" and "prd-deployment" in d.message for d in context.diagnostics.errors
    )


_VNET_SAME_BLOCK_IF_THEN_SCHEMA = """  schema:
    type: object
    if:
      required: [VNET_NAME]
      properties:
        VNET_NAME:
          properties:
            value: { minLength: 1 }
    then:
      required: [VNET_PRIVATE_ENDPOINTS_SUBNET_NAME]
      properties:
        VNET_PRIVATE_ENDPOINTS_SUBNET_NAME:
          properties:
            value: { minLength: 1 }
"""


def _write_deployment_variables_effective_attributeconfig(root: Path) -> None:
    """The real recorded defect's shape (docs/work/environment-value-metadata.md's Evidence section):
    `vnet_private_endpoints_subnet_name` must be set whenever `vnet_name` is — both are **variables**, a
    same-block cross-key rule."""
    _write(
        root,
        "attributeconfig.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-vnet-subnet-required
spec:
  target:
    kind: deployment
    block: variables
    view: effective
{_VNET_SAME_BLOCK_IF_THEN_SCHEMA}""",
    )


def test_attributeconfig_effective_view_variables_fails_the_recorded_real_defect_shape(tmp_path):
    """`vnet_name` set, `vnet_private_endpoints_subnet_name` left blank — the exact real defect this whole
    doc started from, now caught by `view: effective` on `variables`."""
    root = _base_solution(tmp_path)
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
    - key: VNET_NAME
      store: constant
      value: "vnet-prd"
    - key: VNET_PRIVATE_ENDPOINTS_SUBNET_NAME
      store: constant
      value: ""
""",
    )
    _write_deployment_variables_effective_attributeconfig(root)

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_validation_error" for d in context.diagnostics.errors)


def test_attributeconfig_effective_view_variables_passes_when_condition_does_not_apply(tmp_path):
    """`vnet_name` unset — the `if` condition never applies, so the blank subnet name is fine."""
    root = _base_solution(tmp_path)
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
    - key: VNET_NAME
      store: constant
      value: ""
    - key: VNET_PRIVATE_ENDPOINTS_SUBNET_NAME
      store: constant
      value: ""
""",
    )
    _write_deployment_variables_effective_attributeconfig(root)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_effective_view_variables_merges_multiple_environments(tmp_path):
    """Two environments, each setting one half of the pair — the merge (not either environment alone) is
    what makes this pass, same multi-environment convention `merge_environment_models()`'s own tests use."""
    root = _base_solution(tmp_path)
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
    - key: VNET_PRIVATE_ENDPOINTS_SUBNET_NAME
      store: constant
      value: "subnet-prd"
""",
    )
    _write(
        root,
        "environment-base.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: base
spec:
  variables:
    - key: VNET_NAME
      store: constant
      value: "vnet-prd"
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
  environments: [base, prd]
  stages:
    - step: provision-infra
""",
    )
    _write_deployment_variables_effective_attributeconfig(root)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_effective_view_features_block_evaluates_the_merged_environment(tmp_path):
    """Same mechanism, `features` block — proves the branch isn't hardcoded to `variables`."""
    root = _base_solution(tmp_path)
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
  features:
    - key: ENABLE_AKS
      store: constant
      value: "true"
""",
    )
    _write(
        root,
        "attributeconfig.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-feature-required
spec:
  target:
    kind: deployment
    block: features
    view: effective
  schema:
    type: object
    required: [ENABLE_AKS]
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# attributeconfig -> deployment: block: composite (cross-block conditional
# rules; docs/work/environment-value-metadata.md's composite design, Phase 1)
# ---------------------------------------------------------------------------

_ENABLE_AKS_REQUIRES_CLUSTER_ID = """  schema:
    type: object
    if:
      properties:
        features:
          required: [enable_aks]
          properties:
            enable_aks:
              properties:
                value: { enum: [true, "true"] }
    then:
      properties:
        variables:
          required: [aks_cluster_id]
          properties:
            aks_cluster_id:
              properties:
                value: { type: string, minLength: 1 }
"""

_ENABLE_SCI_REQUIRES_SCI_VARIABLES = """  schema:
    type: object
    if:
      properties:
        features:
          required: [enable_sci]
          properties:
            enable_sci:
              properties:
                value: { enum: [true, "true"] }
    then:
      properties:
        variables:
          required: [aci_subnet_name, sci_function_app_url, sci_storage_account_name, sci_storage_container_name]
          properties:
            aci_subnet_name: { properties: { value: { type: string, minLength: 1 } } }
            sci_function_app_url: { properties: { value: { type: string, minLength: 1 } } }
            sci_storage_account_name: { properties: { value: { type: string, minLength: 1 } } }
            sci_storage_container_name: { properties: { value: { type: string, minLength: 1 } } }
"""


def _write_composite_attributeconfig(root: Path, name: str, schema_yaml: str) -> None:
    _write(
        root,
        f"attributeconfig-{name}.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: {name}
spec:
  target:
    kind: deployment
    block: composite
    view: effective
{schema_yaml}""",
    )


def _write_ring_chain(
    root: Path, *, base_features: str, base_variables: str, leaf_features: str = "", leaf_variables: str = ""
) -> None:
    """A base `ring` environment plus the `prd` leaf, merged in that order (leaf wins) — the
    `stacks/ring` base + `c0224/dev` leaf shape the recorded cfg-int cases come from."""
    leaf_features_block = f"  features:\n{leaf_features}" if leaf_features else ""
    _write(
        root,
        "environment-ring.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: ring
spec:
  variables:
{base_variables}  features:
{base_features}""",
    )
    _write(
        root,
        "environment.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: PUBLIC_IP
      store: constant
      value: "1.2.3.4"
{leaf_variables}{leaf_features_block}""",
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
  environments: [ring, prd]
  stages:
    - step: provision-infra
""",
    )


_AKS_ON_BOOLEAN = "    - key: enable_aks\n      store: constant\n      value: true\n"
_AKS_OFF = "    - key: enable_aks\n      store: constant\n      value: false\n"
_CLUSTER_ID_BLANK = '    - key: aks_cluster_id\n      store: constant\n      value: ""\n'


def test_attributeconfig_composite_fails_when_inherited_feature_on_and_variable_blank(tmp_path):
    """The real `enable_aks` case: the base turns the feature on (as a YAML boolean), the cluster id is blank."""
    root = _base_solution(tmp_path)
    _write_ring_chain(root, base_features=_AKS_ON_BOOLEAN, base_variables=_CLUSTER_ID_BLANK)
    _write_composite_attributeconfig(root, "aks-cluster-id-required", _ENABLE_AKS_REQUIRES_CLUSTER_ID)

    context = _resolve(root)
    assert not context.ok
    findings = [d for d in context.diagnostics.errors if "prd-deployment" in d.message]
    assert findings, context.diagnostics.messages()
    assert findings[0].location == "spec.variables.aks_cluster_id.value"


def test_attributeconfig_composite_passes_when_leaf_overrides_feature_off(tmp_path):
    """Same base, but the leaf turns `enable_aks` off — only the merged view knows the condition no longer applies."""
    root = _base_solution(tmp_path)
    _write_ring_chain(root, base_features=_AKS_ON_BOOLEAN, base_variables=_CLUSTER_ID_BLANK, leaf_features=_AKS_OFF)
    _write_composite_attributeconfig(root, "aks-cluster-id-required", _ENABLE_AKS_REQUIRES_CLUSTER_ID)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_composite_passes_when_feature_on_and_variable_set(tmp_path):
    root = _base_solution(tmp_path)
    _write_ring_chain(
        root,
        base_features=_AKS_ON_BOOLEAN,
        base_variables='    - key: aks_cluster_id\n      store: constant\n      value: "/subscriptions/x/aks"\n',
    )
    _write_composite_attributeconfig(root, "aks-cluster-id-required", _ENABLE_AKS_REQUIRES_CLUSTER_ID)

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


@pytest.mark.parametrize(
    "blank", ["aci_subnet_name", "sci_function_app_url", "sci_storage_account_name", "sci_storage_container_name"]
)
def test_attributeconfig_composite_one_feature_gates_several_variables(tmp_path, blank):
    """The real `enable_sci` case: one feature, four dependent variables; any one blank fails."""
    names = ["aci_subnet_name", "sci_function_app_url", "sci_storage_account_name", "sci_storage_container_name"]
    variables = "".join(
        f'    - key: {name}\n      store: constant\n      value: "{"" if name == blank else "set"}"\n' for name in names
    )
    root = _base_solution(tmp_path)
    _write_ring_chain(
        root,
        base_features='    - key: enable_sci\n      store: constant\n      value: "true"\n',
        base_variables=variables,
    )
    _write_composite_attributeconfig(root, "sci-variables-required", _ENABLE_SCI_REQUIRES_SCI_VARIABLES)

    context = _resolve(root)
    assert not context.ok
    assert any(d.location == f"spec.variables.{blank}.value" for d in context.diagnostics.errors), (
        context.diagnostics.messages()
    )


def test_attributeconfig_composite_exposes_properties_and_custom_too(tmp_path):
    """All four blocks are present — a feature can gate a property, not only a variable."""
    root = _base_solution(tmp_path)
    _write_ring_chain(root, base_features=_AKS_ON_BOOLEAN, base_variables=_CLUSTER_ID_BLANK)
    _write_composite_attributeconfig(
        root,
        "aks-needs-owner-property",
        """  schema:
    type: object
    required: [properties, custom, variables, features]
    if:
      properties:
        features:
          required: [enable_aks]
    then:
      properties:
        properties:
          required: [owner]
""",
    )

    context = _resolve(root)
    assert not context.ok
    assert any(
        d.location == "spec.properties" and d.code == "attributeconfig_field_missing"
        for d in context.diagnostics.errors
    ), context.diagnostics.messages()


# ---------------------------------------------------------------------------
# attributeconfig -> environment: document view on variables/features
# (docs/work/environment-value-metadata.md's `variables`/`features` design, Phase 2)
# ---------------------------------------------------------------------------


def _write_environment_variable_attributeconfig(root: Path, *, block: str, schema_yaml: str) -> None:
    _write(
        root,
        "attributeconfig.yaml",
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: environment-{block}-check
spec:
  target:
    kind: environment
    block: {block}
  schema:
{schema_yaml}""",
    )


def test_attributeconfig_document_view_variables_passes_when_key_declared(tmp_path):
    """`_base_solution()`'s `prd` environment declares `PUBLIC_IP` — `required: [PUBLIC_IP]` passes."""
    root = _base_solution(tmp_path)
    _write_environment_variable_attributeconfig(
        root, block="variables", schema_yaml="    type: object\n    required: [PUBLIC_IP]\n"
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


def test_attributeconfig_document_view_variables_fails_when_key_missing(tmp_path):
    """The same schema, requiring a key `prd` never declares, fails — proving the check actually inspects
    the reshaped `{key: ...}` object rather than vacuously passing on a non-dict value."""
    root = _base_solution(tmp_path)
    _write_environment_variable_attributeconfig(
        root, block="variables", schema_yaml="    type: object\n    required: [MISSING_VAR]\n"
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_missing" for d in context.diagnostics.errors)


def test_attributeconfig_document_view_variables_checks_the_full_dumped_store_entry(tmp_path):
    """A rule against `store` (not just `value`) proves the whole entry is dumped, not only `.value` —
    `_base_solution()`'s `PUBLIC_IP` variable is `store: constant`."""
    root = _base_solution(tmp_path)
    _write_environment_variable_attributeconfig(
        root,
        block="variables",
        schema_yaml=(
            "    type: object\n"
            "    properties:\n"
            "      PUBLIC_IP:\n"
            "        type: object\n"
            "        properties:\n"
            '          store: { const: "environment" }\n'
        ),
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_validation_error" for d in context.diagnostics.errors)


def test_attributeconfig_document_view_features_pass_and_fail(tmp_path):
    """Same mechanism, `features` block — `_base_solution()` declares none, so `required` on any key fails,
    and a schema with no `required` passes against the resulting `{}`."""
    root = _base_solution(tmp_path)
    _write_environment_variable_attributeconfig(
        root, block="features", schema_yaml="    type: object\n    required: [ENABLE_AKS]\n"
    )
    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_field_missing" for d in context.diagnostics.errors)

    _write_environment_variable_attributeconfig(root, block="features", schema_yaml="    type: object\n")
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()


# ---------------------------------------------------------------------------
# attributeconfig -> attributeconfig: duplicate-attribute load-time check
# (docs/design/dynamic-configuration-validation.md Phase 4, Q12/Q16)
# ---------------------------------------------------------------------------


def _write_attributeconfig(
    root: Path, filename: str, name: str, *, selector_yaml: str = "", properties_yaml: str
) -> None:
    _write(
        root,
        filename,
        f"""apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: {name}
spec:
  target:
    kind: resource
    block: custom
{selector_yaml}  schema:
    type: object
    properties:
{properties_yaml}
""",
    )


def test_attributeconfig_conflict_fires_for_same_attribute_and_compatible_selectors(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write_attributeconfig(root, "ac1.yaml", "ac-owner-a", properties_yaml="      owner:\n        type: string\n")
    _write_attributeconfig(root, "ac2.yaml", "ac-owner-b", properties_yaml="      owner:\n        type: string\n")

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)
    messages = context.diagnostics.messages()
    assert any("ac-owner-a" in m and "ac-owner-b" in m and "resource.spec.custom.owner" in m for m in messages)


def test_attributeconfig_conflict_silent_when_selectors_disagree_on_a_shared_path(tmp_path):
    """Mutually exclusive selectors: no document could ever satisfy both,
    so declaring the same attribute under both is safe, not a conflict."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write_attributeconfig(
        root,
        "ac1.yaml",
        "ac-owner-a",
        selector_yaml="    selector:\n      spec.provider_type: azure\n",
        properties_yaml="      owner:\n        type: string\n",
    )
    _write_attributeconfig(
        root,
        "ac2.yaml",
        "ac-owner-b",
        selector_yaml="    selector:\n      spec.provider_type: hetzner\n",
        properties_yaml="      owner:\n        type: string\n",
    )

    context = _resolve(root)
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def test_attributeconfig_conflict_fires_for_a_selector_less_schema_vs_a_selectored_one(tmp_path):
    """Vacant compatibility (Q16): a selector-less selector has no shared
    keys with anything, so it is compatible with any other selector."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write_attributeconfig(root, "ac1.yaml", "ac-owner-a", properties_yaml="      owner:\n        type: string\n")
    _write_attributeconfig(
        root,
        "ac2.yaml",
        "ac-owner-b",
        selector_yaml="    selector:\n      spec.provider_type: azure\n",
        properties_yaml="      owner:\n        type: string\n",
    )

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def test_attributeconfig_conflict_silent_for_disjoint_top_level_attributes(tmp_path):
    """Control case: same (kind, block), compatible (unset) selectors, but
    no top-level `properties` key in common — nothing to conflict over."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write_attributeconfig(root, "ac1.yaml", "ac-owner", properties_yaml="      owner:\n        type: string\n")
    _write_attributeconfig(root, "ac2.yaml", "ac-region", properties_yaml="      region:\n        type: string\n")

    context = _resolve(root)
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def test_attributeconfig_conflict_silent_between_document_and_effective_view(tmp_path):
    """Same `(kind, block)`, same top-level attribute, but different `view`
    — `document` and `effective` check different data (one document's own
    fragment vs. a deployment's merged blob) and can never conflict
    (Phase 4: `view` joined the grouping key)."""
    root = _base_solution(tmp_path)
    _write(
        root,
        "ac1.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-properties-owner-document
spec:
  target:
    kind: deployment
    block: properties
    view: document
  schema:
    type: object
    properties:
      owner:
        type: string
""",
    )
    _write(
        root,
        "ac2.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: deployment-properties-owner-effective
spec:
  target:
    kind: deployment
    block: properties
    view: effective
  schema:
    type: object
    properties:
      owner:
        type: string
""",
    )

    context = _resolve(root)
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def test_attributeconfig_conflict_check_does_not_crash_on_a_malformed_properties_shape(tmp_path):
    """Regression: `schema.properties` is an unvalidated `dict[str, Any]` —
    a document declaring it as something other than a mapping (e.g. a typo)
    must not crash the conflict check; `_check_attributeconfigs()`'s own
    schema-compile check reports it as `attributeconfig_invalid` instead."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "ac1.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: ac-malformed
spec:
  target:
    kind: resource
    block: custom
  schema:
    type: object
    properties: not-a-mapping
""",
    )
    _write_attributeconfig(root, "ac2.yaml", "ac-owner", properties_yaml="      owner:\n        type: string\n")

    context = _resolve(root)  # must not raise
    assert not context.ok
    assert any(d.code == "attributeconfig_invalid" for d in context.diagnostics.errors)
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def _composite_variables_schema(*keys: str) -> str:
    properties = "".join(f"          {key}:\n            type: object\n" for key in keys)
    return f"""  schema:
    type: object
    properties:
      variables:
        type: object
        properties:
{properties}"""


def test_attributeconfig_conflict_composite_silent_for_different_keys_in_the_same_sub_block(tmp_path):
    """Both schemas declare `properties.variables` at the top level; comparing top-level keys would flag them.
    They constrain different variables, so they don't conflict."""
    root = _base_solution(tmp_path)
    _write_composite_attributeconfig(root, "composite-a", _composite_variables_schema("aks_cluster_id"))
    _write_composite_attributeconfig(root, "composite-b", _composite_variables_schema("worker_pools"))

    context = _resolve(root)
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)


def test_attributeconfig_conflict_composite_fires_for_the_same_sub_block_key(tmp_path):
    root = _base_solution(tmp_path)
    _write_composite_attributeconfig(root, "composite-a", _composite_variables_schema("aks_cluster_id"))
    _write_composite_attributeconfig(root, "composite-b", _composite_variables_schema("aks_cluster_id", "worker_pools"))

    context = _resolve(root)
    conflicts = [d for d in context.diagnostics.errors if d.code == "attributeconfig_conflict"]
    assert len(conflicts) == 1, context.diagnostics.messages()
    assert "'deployment.spec.variables.aks_cluster_id'" in conflicts[0].message
    assert conflicts[0].location == "spec.schema.properties.variables.properties.aks_cluster_id"


def test_attributeconfig_conflict_composite_ignores_a_malformed_sub_block(tmp_path):
    """A sub-block declared as something other than a mapping must not crash the check."""
    root = _base_solution(tmp_path)
    _write_composite_attributeconfig(
        root, "composite-malformed", "  schema:\n    type: object\n    properties:\n      variables: not-a-mapping\n"
    )
    _write_composite_attributeconfig(root, "composite-b", _composite_variables_schema("aks_cluster_id"))

    context = _resolve(root)  # must not raise
    assert not any(d.code == "attributeconfig_conflict" for d in context.diagnostics.errors)
