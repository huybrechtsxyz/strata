#!/usr/bin/env python3
"""Regression test for docs/design/terraform-variable-precedence.md: a real
`terraform` binary is required (no stubbed `run_command` can ever observe
this) because the defect IS Terraform's own file-beats-env-var
variable-definition precedence (`*.auto.tfvars.json` outranks `TF_VAR_*`,
confirmed against https://developer.hashicorp.com/terraform/language/values/variables).

`build run` writes `properties.auto.tfvars.json` with the literal,
unresolved `${var:CUSTOMER_CODE}` token (by design — `build run` never
resolves `properties`/`custom`, see docs/design/build-time-value-categories.md
Q4/Q5). Without Phase 2's fix, `deploy run` would resolve the token
correctly and set `TF_VAR_customer_code`, but Terraform's own precedence
would mean the stale file silently wins regardless — `deploy_run()`'s own
`Diagnostics` stays green throughout either way, exactly like every
existing (stubbed) test in `test_deploy_controller.py`; only inspecting
the real `terraform show -json` plan output, bypassing strata entirely for
the final assertion, can tell the difference. Phase 2
(`resolve_deploy_time_files()`) closes this by rewriting the on-disk file
with the resolved value at deploy time, so the file legitimately wins —
this test was the original (now-fixed) Phase 1 reproduction and keeps
running as the permanent regression guard for it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _solution_with_properties_token(tmp_path: Path) -> Path:
    """One real Terraform step (a genuine `variable`/`output` pair, not the
    placeholder `# root module` comment `test_deploy_controller.py`'s own
    fixtures use — those rely on a stubbed `run_command`, so the file's
    actual HCL content never matters there) whose environment's
    `spec.properties` embeds a `${var:CUSTOMER_CODE}` token — the exact
    shape of the reported bug (a `properties`/`custom` field; any token
    kind reaches the same `FLAT_CATEGORIES` delivery path identically, see
    the work doc's "Scope" section)."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "infra/main.tf",
        'output "customer_code" {\n  value = var.strata.properties.customer_code\n}\n',
    )
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  properties:\n    customer_code: '${var:CUSTOMER_CODE}'\n"
        "  variables:\n    - key: CUSTOMER_CODE\n      store: constant\n      value: real-customer-42\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_deploy_run_properties_token_is_shadowed_by_stale_build_time_file(tmp_path: Path):
    """Regression test for docs/design/terraform-variable-precedence.md Phase
    1/2, ported to ADR-0035's unified single-file shape (Phase 7): a
    `${var:}` token inside `properties` must resolve to its real value in
    the one `strata.auto.tfvars.json` Terraform actually reads — proven
    against a real `terraform plan`, not just strata's own diagnostics."""
    root = _solution_with_properties_token(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    strata_file = step_dir / "strata.auto.tfvars.json"
    # build run writes the unresolved, literal token.
    assert json.loads(strata_file.read_text())["strata"]["properties"]["customer_code"] == "${var:CUSTOMER_CODE}"

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    # deploy run rewrites the same file with the resolved value.
    assert json.loads(strata_file.read_text())["strata"]["properties"]["customer_code"] == "real-customer-42"

    plan_file = step_dir / "apply_infra.tfplan"
    assert plan_file.exists(), "deploy run --dry-run should have saved a real plan file"

    shown = subprocess.run(
        ["terraform", "show", "-json", str(plan_file)],
        cwd=step_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(shown.stdout)
    effective_value = plan["planned_values"]["outputs"]["customer_code"]["value"]

    assert effective_value == "real-customer-42", (
        f"Terraform's real plan used {effective_value!r} instead of the resolved "
        "'real-customer-42' — the unified strata.auto.tfvars.json rewrite at deploy "
        "time did not take effect (ADR-0035 Phase 7)."
    )


def _solution_with_two_resource_types(tmp_path: Path) -> Path:
    """Two Resource documents of different `resource_type` (so `build run`
    writes two separate `resx_<type>.auto.tfvars.json` files, each
    independently declaring the real `resources` Terraform variable in
    full), each with a `${var:}` token in its own `configuration` — the
    shape Phase 3's always-blank rule exists for
    (docs/design/terraform-variable-precedence.md's "Why `resx_<type>`
    always writes `{}`" section)."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "infra/main.tf",
        'output "resources" {\n  value = var.strata.resources\n}\n',
    )
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  configuration:\n    partner_id: '${var:PARTNER_ID}'\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "resource2.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r2\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: storage\n    category: storage\n"
        "  configuration:\n    tier: '${var:TIER}'\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n        - r2\n"
        "  resources:\n    - name: r1\n      resource: r1\n    - name: r2\n      resource: r2\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: PARTNER_ID\n      store: constant\n      value: ACME123\n"
        "    - key: TIER\n      store: constant\n      value: gold\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_deploy_run_merges_two_resx_types_via_tf_var_resources_alone(tmp_path: Path):
    """Regression test for docs/design/terraform-variable-precedence.md Phase
    3, ported to ADR-0035's unified shape (Phase 7): `strata.resources` is
    one flat map (Phase 2) — two active resource types land as two entries
    in the SAME map inside the one `strata.auto.tfvars.json`, never under
    separate per-type keys/files, proven against a real `terraform plan`."""
    root = _solution_with_two_resource_types(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    strata_file = step_dir / "strata.auto.tfvars.json"

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    on_disk = json.loads(strata_file.read_text())["strata"]["resources"]
    assert set(on_disk) == {"r1", "r2"}
    assert on_disk["r1"]["configuration"]["partner_id"] == "ACME123"
    assert on_disk["r2"]["configuration"]["tier"] == "gold"

    plan_file = step_dir / "apply_infra.tfplan"
    assert plan_file.exists(), "deploy run --dry-run should have saved a real plan file"

    shown = subprocess.run(
        ["terraform", "show", "-json", str(plan_file)],
        cwd=step_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(shown.stdout)
    effective_resources = plan["planned_values"]["outputs"]["resources"]["value"]

    assert set(effective_resources) == {"r1", "r2"}, (
        f"Terraform's real plan only saw {set(effective_resources)!r} resource(s) — the unified "
        "strata.auto.tfvars.json rewrite at deploy time did not take effect (ADR-0035 Phase 7)."
    )
    assert effective_resources["r1"]["configuration"]["partner_id"] == "ACME123"
    assert effective_resources["r2"]["configuration"]["tier"] == "gold"


def _solution_with_secrets_in_every_shape(tmp_path: Path) -> Path:
    """One `${secret:}` token in each of the three shapes
    `resolve_deploy_time_files()` covers
    (docs/design/terraform-variable-precedence.md's "Proposed design"
    cases 1-3):

    - `custom.db_password` — a `FLAT_CATEGORIES` key.
    - `provider.custom.cost_center` — a single-variable broadcast category.
    - a DNS record's `value` — a claimed/broadcast document category.

    Each resolves to its own distinct secret value so a mix-up between the
    three shapes would be caught, not just a secret landing somewhere."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(
        root,
        "infra/main.tf",
        'output "db_password" {\n  value     = lookup(var.strata_secrets, "custom.db_password", "MISSING")\n'
        "  sensitive = true\n}\n\n"
        'output "cost_center" {\n'
        '  value     = lookup(var.strata_secrets, "providers.p1.custom.cost_center", "MISSING")\n'
        "  sensitive = true\n}\n\n"
        'output "dns_secret" {\n'
        '  value     = lookup(var.strata_secrets, "dns.public-dns.zones.example\\\\.com.records.0.value", "MISSING")\n'
        "  sensitive = true\n}\n\n"
        'output "strata_dns" {\n  value     = var.strata.dns\n  sensitive = true\n}\n',
    )
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n"
        "  custom:\n    cost_center: '${secret:COST_CENTER}'\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "dns.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: dns\nmeta:\n  name: public-dns\nspec:\n"
        "  zones:\n    - name: example.com\n      records:\n"
        "        - name: '@'\n          type: A\n          value: '${secret:DNS_SECRET}'\n"
        "      default_tags:\n        environment: prd\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  dns_zones:\n    - public-dns\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  custom:\n    db_password: '${secret:DB_PASSWORD}'\n"
        "  secrets:\n    - key: DB_PASSWORD\n      store: constant\n      value: hunter2\n"
        "    - key: COST_CENTER\n      store: constant\n      value: platform-secret\n"
        "    - key: DNS_SECRET\n      store: constant\n      value: dns-secret-xyz\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
def test_deploy_run_resolves_secrets_in_every_shape_without_writing_them_to_disk(tmp_path: Path):
    """Regression test for docs/design/terraform-variable-precedence.md
    Phase 4, ported to ADR-0035's unified shape (Phase 7): a `${secret:}`
    token in each of `custom`/`providers`/`dns` resolves to its real value
    in `TF_VAR_strata_secrets`, proven via a real Terraform plan reading
    it directly with `lookup()` (bypassing `build_strata_locals()`'s own
    generic merge machinery, which only handles dict-nested paths, not a
    path through a list index like a DNS record's — a separate, known
    limitation of Phase 6, not this phase's concern), AND no resolved
    secret value ever appears in the one on-disk `strata.auto.tfvars.json`
    — the "secrets never touch disk" guarantee, checked directly against
    the real file, not just inferred from the code."""
    root = _solution_with_secrets_in_every_shape(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    strata_file = step_dir / "strata.auto.tfvars.json"

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    # The "secrets never touch disk" guarantee, checked directly against
    # the one rewritten file: none of the three resolved secret values
    # appear anywhere in it — only their sentinels do.
    content = strata_file.read_text()
    for secret_value in ("hunter2", "platform-secret", "dns-secret-xyz"):
        assert secret_value not in content, f"resolved secret {secret_value!r} leaked into strata.auto.tfvars.json"
    strata = json.loads(content)["strata"]
    assert strata["custom"]["db_password"] == "__strata_secret__:custom.db_password"
    assert strata["providers"]["p1"]["custom"]["cost_center"] == ("__strata_secret__:providers.p1.custom.cost_center")

    plan_file = step_dir / "apply_infra.tfplan"
    assert plan_file.exists(), "deploy run --dry-run should have saved a real plan file"

    shown = subprocess.run(
        ["terraform", "show", "-json", str(plan_file)],
        cwd=step_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(shown.stdout)
    outputs = plan["planned_values"]["outputs"]

    # This is the regression this test guards: without the sentinel +
    # TF_VAR_strata_secrets delivery, a direct lookup() by dotted path
    # would find nothing (falling back to "MISSING") instead of the real
    # resolved secret value.
    assert outputs["db_password"]["value"] == "hunter2"
    assert outputs["cost_center"]["value"] == "platform-secret"
    assert outputs["dns_secret"]["value"] == "dns-secret-xyz"
    # var.strata itself (no secrets resolved) still carries the sentinel.
    assert outputs["strata_dns"]["value"]["public-dns"]["zones"]["example.com"]["records"][0]["value"] == (
        "__strata_secret__:dns.public-dns.zones.example\\.com.records.0.value"
    )
