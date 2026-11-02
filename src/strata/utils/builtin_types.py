#!/usr/bin/env python3
"""Reference sets of strata's known built-in tool/deployer types.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003) rather than in a model file: `ProvisionerType` and its derived
subsets are pure code-classification facts — they only mean anything because
specific Python validation/deployer logic exists that treats these exact
values specially (e.g. "does terraform-specific `backend` validation apply").
They are not schema/model concerns themselves, even though model files
(`module_model.py`, `provisioning_model.py`) consume them — reused *by*
multiple models rather than belonging to any one of them.

Not used as any schema field's type directly. v1's real `DeployerFactory`
supports user-registered provisioner plugins beyond these built-ins
(`.strata/provisioners/*.py`), so fields like `Module.spec.type`/
`Provisioner.tool` are open `PlatformName` strings, checked against
`ProvisionerType` only to classify *recognized* values (an unrecognized
value is a possible custom plugin, not a hard error) — see ADR-0009/ADR-0011.

A new member is only added here when strata ships an actual new built-in
deployer class — the same "code change required" rule already documented for
custom-vs-built-in leniency (ADR-0011) — not something a user can register
via `Integration`/YAML configuration.
"""

from enum import Enum


class ProvisionerType(str, Enum):
    """Reference enumeration of known built-in provisioner/deployer tools.

    Not used as a schema field's type — see module docstring above.
    """

    TERRAFORM = "terraform"
    OPENTOFU = "opentofu"
    ANSIBLE = "ansible"
    BICEP = "bicep"
    SCRIPT = "script"
    HELM = "helm"
    COMPOSE = "compose"
    ARGOCD = "argocd"
    FLUX = "flux"


# Sync/GitOps tools: render from the platform artifact and commit to a git
# remote at deploy time — no IaC source directory needed, unlike the other
# ProvisionerType members.
SYNC_PROVISIONER_TYPES = frozenset({ProvisionerType.ARGOCD, ProvisionerType.FLUX})

# OpenTofu is a drop-in, backend-compatible fork of Terraform (same IaC
# semantics, same state-backend config shape) — treated identically to
# TERRAFORM wherever terraform-specific validation applies (e.g.
# `validate_backend_only_for_terraform` in provisioning_model.py).
TERRAFORM_COMPATIBLE_TYPES = frozenset({ProvisionerType.TERRAFORM, ProvisionerType.OPENTOFU})

# Tools that can deploy a Module's services (containers/sub-charts). Used to
# classify a *recognized* `ModuleSpecModel.type` value — terraform/ansible/bicep
# manage infrastructure state, not container workloads, so a known value in
# this category is rejected there; an unrecognized value (custom plugin)
# isn't checked against this at all.
WORKLOAD_DEPLOYER_TYPES = frozenset(
    {ProvisionerType.HELM, ProvisionerType.COMPOSE, ProvisionerType.ARGOCD, ProvisionerType.SCRIPT}
)
