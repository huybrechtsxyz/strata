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
        'variable "customer_code" {\n  type = string\n}\n\noutput "customer_code" {\n  value = var.customer_code\n}\n',
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
    1/2. `deploy_run()` itself reports success throughout even when the bug
    is present — the bug only ever showed up in Terraform's own real plan,
    never in strata's own diagnostics, which is exactly why this test must
    run a real `terraform` binary rather than asserting on strata's own
    inputs."""
    root = _solution_with_properties_token(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    properties_file = step_dir / "properties.auto.tfvars.json"
    # `build run` never resolves properties/custom — confirmed by design,
    # not itself part of the bug (see this test module's own docstring).
    assert json.loads(properties_file.read_text()) == {"customer_code": "${var:CUSTOMER_CODE}"}

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    # deploy_run() reports success regardless of whether the on-disk file
    # actually ends up correct — strata's own diagnostics cannot see what
    # Terraform itself does with the result. This is exactly why every
    # existing (stubbed) assertion in test_deploy_controller.py could stay
    # green despite the real bug before Phase 2 — only the real plan below
    # can tell the difference.
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    # Phase 2: the on-disk file itself is now rewritten with the resolved
    # value (never left stale) — the actual fix mechanism, checked directly
    # before falling through to the real-Terraform-plan proof below.
    assert json.loads(properties_file.read_text()) == {"customer_code": "real-customer-42"}

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

    # This is the regression this test guards: without Phase 2's fix,
    # Terraform's own precedence rules (*.auto.tfvars.json beats TF_VAR_*)
    # would mean the stale, unresolved file strata wrote at build time wins
    # over the correctly-resolved env var strata set at deploy time — Terraform's
    # real plan would use the literal string "${var:CUSTOMER_CODE}" instead
    # of the resolved value.
    assert effective_value == "real-customer-42", (
        f"Terraform's real plan used {effective_value!r} instead of the resolved "
        "'real-customer-42' — the stale, unresolved build-time properties.auto.tfvars.json "
        "shadowed deploy_run()'s correctly-resolved TF_VAR_customer_code "
        "(docs/design/terraform-variable-precedence.md)."
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
        'variable "resources" {\n  type = any\n}\n\noutput "resources" {\n  value = var.resources\n}\n',
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
    3: with two resource types present, `build run` writes two separate
    `resx_<type>.auto.tfvars.json` files that each independently declare
    the SAME real `resources` Terraform variable — a second, narrower
    instance of the same precedence bug (whichever file sorts last
    lexically would otherwise silently clobber the other's declaration,
    on top of carrying a stale, unresolved value). Confirms both effects
    at once, against a real plan: every resource type's resolved value
    shows up (not a literal token), and BOTH types are present together
    (neither lost to a file-vs-file collision) — `TF_VAR_resources` alone
    is the effective source, exactly as designed."""
    root = _solution_with_two_resource_types(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    # Both files exist after build run, each independently unresolved —
    # by design, not itself the bug (see this test module's own docstring).
    server_file = step_dir / "resx_server.auto.tfvars.json"
    storage_file = step_dir / "resx_storage.auto.tfvars.json"
    assert json.loads(server_file.read_text())["resources"]["r1"]["configuration"]["partner_id"] == "${var:PARTNER_ID}"
    assert json.loads(storage_file.read_text())["resources"]["r2"]["configuration"]["tier"] == "${var:TIER}"

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    # Phase 3: both on-disk files are now unconditionally blanked, never
    # resolved per-file — TF_VAR_resources (merging both types) is the
    # sole, uncontested source for the real plan checked below.
    assert json.loads(server_file.read_text()) == {}
    assert json.loads(storage_file.read_text()) == {}

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

    # This is the regression this test guards: without Phase 3, whichever
    # of resx_server.auto.tfvars.json/resx_storage.auto.tfvars.json
    # Terraform's own lexical-order precedence picked would shadow
    # TF_VAR_resources — losing the other resource type entirely, not just
    # carrying a stale value for the one that survived.
    assert set(effective_resources) == {"r1", "r2"}, (
        f"Terraform's real plan only saw {set(effective_resources)!r} resource(s) — one of the two "
        "resx_<type>.auto.tfvars.json files shadowed TF_VAR_resources, or shadowed each other "
        "(docs/design/terraform-variable-precedence.md Phase 3)."
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
        'variable "db_password" {\n  type = string\n}\n\n'
        'variable "platform_providers" {\n  type = any\n}\n\n'
        'variable "dns_zones" {\n  type = any\n}\n\n'
        'output "db_password" {\n  value = var.db_password\n}\n\n'
        'output "platform_providers" {\n  value = var.platform_providers\n}\n\n'
        'output "dns_zones" {\n  value = var.dns_zones\n}\n',
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
    Phase 4: a `${secret:}` token in each of the three shapes Phase 2
    covers resolves correctly in a real Terraform plan, AND no resolved
    secret value ever appears in any on-disk `*.auto.tfvars.json` file —
    the "secrets never touch disk" guarantee, checked directly against
    real files, not just inferred from the code."""
    root = _solution_with_secrets_in_every_shape(tmp_path)
    build_path = tmp_path / "build"

    build_diagnostics = build_run(_context(root), "app", build_path)
    assert build_diagnostics.ok, build_diagnostics.messages()

    step_dir = build_path / "infra"
    custom_file = step_dir / "custom.auto.tfvars.json"
    providers_file = step_dir / "providers.auto.tfvars.json"
    dns_file = step_dir / "dns.auto.tfvars.json"
    # `build run` never resolves secrets — confirmed by design, not itself
    # part of the bug (see this test module's own module docstring).
    assert json.loads(custom_file.read_text()) == {"db_password": "${secret:DB_PASSWORD}"}
    assert json.loads(providers_file.read_text())["platform_providers"]["p1"]["custom"]["cost_center"] == (
        "${secret:COST_CENTER}"
    )
    dns_before = json.loads(dns_file.read_text())
    assert (
        dns_before["dns_zones"]["public-dns"]["zones"]["example.com"]["records"][0]["value"] == "${secret:DNS_SECRET}"
    )

    deploy_diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)
    assert deploy_diagnostics.ok, deploy_diagnostics.messages()

    # Phase 2/4: every one of the three files is blanked to {} — a
    # secret-shaped leaf taints the whole variable (FLAT_CATEGORIES' own
    # per-key rule still applies to `custom`, but `db_password` is its
    # only key here, so the whole file ends up empty either way).
    assert json.loads(custom_file.read_text()) == {}
    assert json.loads(providers_file.read_text()) == {}
    assert json.loads(dns_file.read_text()) == {}

    # The "secrets never touch disk" guarantee, checked directly: scan
    # every *.auto.tfvars.json file this step wrote and confirm none of
    # the three resolved secret values appear anywhere in any of them.
    secret_values = ["hunter2", "platform-secret", "dns-secret-xyz"]
    for tfvars_file in step_dir.glob("*.auto.tfvars.json"):
        content = tfvars_file.read_text()
        for secret_value in secret_values:
            assert secret_value not in content, f"resolved secret {secret_value!r} leaked into {tfvars_file.name}"

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

    # This is the regression this test guards: without Phase 2/3 (the file
    # rewrite), the stale, unresolved secret token would win in all three
    # shapes identically — the real plan would show the literal
    # "${secret:...}" string, not the resolved secret value, exactly like
    # the non-secret properties/resx cases Phases 1-3 already guard.
    assert outputs["db_password"]["value"] == "hunter2"
    assert outputs["platform_providers"]["value"]["p1"]["custom"]["cost_center"] == "platform-secret"
    dns_value = outputs["dns_zones"]["value"]["public-dns"]["zones"]["example.com"]["records"][0]["value"]
    assert dns_value == "dns-secret-xyz"
