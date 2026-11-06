#!/usr/bin/env python3
"""Tests for `evaluate_provisioner_lock_policy()` (docs/design/provisioner-lock-policy.md, phase 4)."""

from datetime import date
from pathlib import Path

import pytest

from strata.controllers.provisioner_lock_policy import evaluate_provisioner_lock_policy
from strata.models.workspace_model import WorkspaceModel

_AZURERM = (
    'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
    '      version = "~> 4.0"\n    }\n  }\n}\n'
)
_RANDOM = 'terraform {\n  required_providers {\n    random = {\n      source = "hashicorp/random"\n    }\n  }\n}\n'


def _lock(*addresses: str) -> str:
    return "\n".join(f'provider "registry.terraform.io/{a}" {{\n  version = "1.0.0"\n}}\n' for a in addresses)


def _root(path: Path, *files: str, lock: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for n, content in enumerate(files):
        (path / f"f{n}.tf").write_text(content, encoding="utf-8")
    if lock is not None:
        (path / ".terraform.lock.hcl").write_text(lock, encoding="utf-8")
    return path


def _workspace(policy: dict | None = None, *, tools: dict[str, str] | None = None) -> WorkspaceModel:
    provisioners = [
        {"name": name, "tool": tool, "source": {"source_path": f"src/{name}"}}
        for name, tool in (tools or {"infra": "terraform"}).items()
    ]
    spec: dict = {"providers": ["p"], "provisioners": provisioners}
    if policy is not None:
        spec["provisioner_lock_policy"] = policy
    return WorkspaceModel.model_validate({"meta": {"name": "ws"}, "spec": spec})


def _evaluate(paths, policy: dict | None = None, *, ring=None, today=None, tools=None):
    workspace = _workspace({} if policy is None else policy, tools=tools)
    return evaluate_provisioner_lock_policy(paths, workspace, ring, today=today)


def _codes(diagnostics) -> list[str | None]:
    return [item.code for item in diagnostics]


def test_without_the_policy_nothing_is_reported(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}
    workspace = _workspace(None)
    assert evaluate_provisioner_lock_policy(paths, workspace, None).items == []


def test_a_complete_lock_file_passes(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM, lock=_lock("hashicorp/azurerm"))}
    assert _evaluate(paths).items == []


def test_a_missing_lock_file_fails_naming_the_provisioner_and_its_providers(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM, _RANDOM)}

    diagnostics = _evaluate(paths)

    assert _codes(diagnostics.errors) == ["provisioner_lock_missing"]
    assert diagnostics.errors[0].message == (
        "provisioner_lock_policy: provisioner 'infra' has no .terraform.lock.hcl (providers hashicorp/azurerm, "
        "hashicorp/random). Run terraform init and commit the lock file it writes next to the *.tf files."
    )


def test_an_incomplete_lock_file_is_the_same_finding(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM, _RANDOM, lock=_lock("hashicorp/azurerm"))}

    diagnostics = _evaluate(paths)

    assert _codes(diagnostics.errors) == ["provisioner_lock_missing"]
    assert diagnostics.errors[0].message == (
        "provisioner_lock_policy: provisioner 'infra': .terraform.lock.hcl does not lock hashicorp/random. "
        "Run terraform init and commit the updated lock file."
    )


def test_a_provider_from_another_registry_keeps_its_host(tmp_path: Path):
    tofu = 'terraform {\n  required_providers {\n    null = {\n      source = "registry.opentofu.org/hashicorp/null"\n    }\n  }\n}\n'
    paths = {"infra": _root(tmp_path / "infra", tofu)}
    assert "(provider registry.opentofu.org/hashicorp/null)" in _evaluate(paths).errors[0].message


def test_a_root_that_needs_no_providers_passes(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", 'output "x" {\n  value = 1\n}\n')}
    assert _evaluate(paths).items == []


def test_a_nested_root_locked_on_its_own_does_not_fail_the_provisioner(tmp_path: Path):
    infra = _root(tmp_path / "infra", _AZURERM, lock=_lock("hashicorp/azurerm"))
    _root(infra / "envs" / "prd", _RANDOM, lock=_lock("hashicorp/random"))
    assert _evaluate({"infra": infra}).items == []


def test_a_root_using_a_provider_implicitly_needs_a_lock_file(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", 'resource "null_resource" "x" {}\n')}
    diagnostics = _evaluate(paths)
    assert diagnostics.errors[0].message.startswith(
        "provisioner_lock_policy: provisioner 'infra' has no .terraform.lock.hcl. Run terraform init"
    )


def test_only_terraform_and_opentofu_provisioners_are_judged(tmp_path: Path):
    tools = {"infra": "terraform", "tofu": "opentofu", "config": "ansible"}
    paths = {name: _root(tmp_path / name, _AZURERM) for name in tools}

    diagnostics = _evaluate(paths, tools=tools)

    assert [d.message.split("'")[1] for d in diagnostics.errors] == ["infra", "tofu"]


def test_a_provisioner_this_build_did_not_materialise_is_skipped(tmp_path: Path):
    assert _evaluate({"infra": None}).items == []
    assert _evaluate({}).items == []


def test_warn_reports_a_warning_instead_of_an_error(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}
    diagnostics = _evaluate(paths, {"enforcement": "warn"})
    assert (_codes(diagnostics.errors), _codes(diagnostics.warnings)) == ([], ["provisioner_lock_missing"])


def test_an_allowed_provisioner_is_exempt_until_its_entry_expires(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}
    policy = {"allow": [{"provisioner": "infra", "reason": "vendored", "expires": "2026-12-31"}]}

    assert _evaluate(paths, policy, today=date(2026, 12, 31)).items == []
    diagnostics = _evaluate(paths, policy, today=date(2027, 1, 1))
    assert diagnostics.errors[0].message.endswith(" (its allow entry expired on 2026-12-31)")


def test_an_allow_entry_without_expiry_always_exempts(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}
    assert _evaluate(paths, {"allow": [{"provisioner": "infra", "reason": "vendored"}]}).items == []


def test_a_deployment_outside_the_rings_is_not_judged(tmp_path: Path):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}

    diagnostics = _evaluate(paths, {"rings": ["acc", "prd"]}, ring="dev")

    assert [(d.severity.value, d.code) for d in diagnostics] == [("info", "provisioner_lock_policy_not_applied")]
    assert diagnostics.infos[0].message == (
        "provisioner_lock_policy: not applied, the deployment's ring 'dev' is not one of acc, prd."
    )


@pytest.mark.parametrize("ring", ["prd", None])
def test_a_deployment_in_the_rings_or_without_a_ring_is_judged(tmp_path: Path, ring):
    paths = {"infra": _root(tmp_path / "infra", _AZURERM)}
    assert _codes(_evaluate(paths, {"rings": ["prd"]}, ring=ring).errors) == ["provisioner_lock_missing"]
