#!/usr/bin/env python3
"""Evaluate `spec.provisioner_lock_policy` over a build path (docs/design/provisioner-lock-policy.md).

Pure: it reads the policy, the folders the build materialised each provisioner into, and the deployment's ring,
so it needs no network or credential. `build run` and `strata policy check` decide when to run it.
"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path

from strata.controllers.image_tag_policy import deployment_ring
from strata.controllers.solution_context import SolutionContext
from strata.integrations.sbom_collectors.terraform_collector import read_provider_locks
from strata.models.deployment_model import DeploymentModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.builtin_types import TERRAFORM_COMPATIBLE_TYPES
from strata.utils.diagnostics import Diagnostics
from strata.utils.sbom_utils import TERRAFORM_DEFAULT_REGISTRY

_LOCK_FILENAME = ".terraform.lock.hcl"
_TOOLS = {tool.value for tool in TERRAFORM_COMPATIBLE_TYPES}


def check_provisioner_lock_policy(
    context: SolutionContext,
    deployment: DeploymentModel,
    workspace: WorkspaceModel,
    source_paths: Mapping[str, Path | None],
) -> Diagnostics:
    """Evaluate the workspace's `provisioner_lock_policy` for `deployment`; nothing to report when it is unset."""
    if workspace.spec.provisioner_lock_policy is None:
        return Diagnostics()
    return evaluate_provisioner_lock_policy(source_paths, workspace, deployment_ring(context, deployment))


def evaluate_provisioner_lock_policy(
    source_paths: Mapping[str, Path | None],
    workspace: WorkspaceModel,
    ring: str | None,
    *,
    today: date | None = None,
) -> Diagnostics:
    """One finding per Terraform/OpenTofu provisioner whose lock file is missing or incomplete.

    Args:
        source_paths: Provisioner name -> the folder this build materialised its source into (`build_run()`'s
            `materialised`, or `expected_source_paths()`); `None` or absent means not materialised, so not judged.
        workspace: The deployment's resolved workspace, owner of `spec.provisioner_lock_policy`.
        ring: The deployment's `spec.promotion.ring`, or `None`. A deployment whose ring is not in `rings` is not
            judged and gets one info finding; one with no ring is always judged.
        today: The day an `allow` entry's `expires` is compared with; defaults to the current date.
    """
    diagnostics = Diagnostics()
    policy = workspace.spec.provisioner_lock_policy
    if policy is None:
        return diagnostics
    if policy.rings is not None and ring is not None and ring not in policy.rings:
        diagnostics.info(
            f"provisioner_lock_policy: not applied, the deployment's ring '{ring}' is not one of "
            f"{', '.join(policy.rings)}.",
            code="provisioner_lock_policy_not_applied",
        )
        return diagnostics

    report = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning
    allowed = {entry.provisioner: entry for entry in policy.allow or []}
    day = today or date.today()
    for provisioner in workspace.spec.provisioners:
        path = source_paths.get(provisioner.name)
        if provisioner.tool not in _TOOLS or path is None:
            continue
        entry = allowed.get(provisioner.name)
        note = ""
        if entry is not None:
            if entry.expires is None or date.fromisoformat(entry.expires) >= day:
                continue
            note = f" (its allow entry expired on {entry.expires})"

        locks = read_provider_locks(path)
        if not locks.needs_providers:
            continue
        if locks.lock_file is None:
            providers = _named(sorted(locks.declared))
            listed = f" ({providers})" if providers else ""
            report(
                f"provisioner_lock_policy: provisioner '{provisioner.name}' has no {_LOCK_FILENAME}{listed}. "
                f"Run terraform init and commit the lock file it writes next to the *.tf files.{note}",
                code="provisioner_lock_missing",
            )
        elif locks.unlocked:
            report(
                f"provisioner_lock_policy: provisioner '{provisioner.name}': {_LOCK_FILENAME} does not lock "
                f"{', '.join(_short(a) for a in locks.unlocked)}. Run terraform init and commit the updated lock "
                f"file.{note}",
                code="provisioner_lock_missing",
            )
    return diagnostics


def _short(address: str) -> str:
    """`hashicorp/azurerm` for the default registry; another registry keeps its host."""
    return address.removeprefix(f"{TERRAFORM_DEFAULT_REGISTRY}/")


def _named(addresses: list[str]) -> str:
    if not addresses:
        return ""
    noun = "provider" if len(addresses) == 1 else "providers"
    return f"{noun} {', '.join(_short(a) for a in addresses)}"
