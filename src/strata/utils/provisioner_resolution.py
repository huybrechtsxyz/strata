"""Shared stage → provisioner name resolution (ADR-0051 revision, 2026-09-16).

Single source of truth for "which workspace provisioner does this deployment
stage target?" — previously duplicated (and subtly inconsistent) across
``BaseDeployer._resolve_iac_model()`` and
``TerraformBuilder._stages_for_provisioner()``. Both now call
``resolve_stage_provisioner_name()``; ``CheckovPolicy``'s ``scope: staged``
filter uses ``stage_reachable_provisioner_names()`` built on top of it.

Resolution priority (strict — the first applicable condition wins outright,
never falls through to a later one):

1. ``stage.provisioner`` — explicit provisioner name reference.
2. ``stage.topology`` — topology name → ``topology.provisioner`` name reference.
3. Sole workspace provisioner — used only when exactly one is declared and
   neither of the above is set.

An explicit-but-unresolvable reference (a typo'd ``stage.provisioner`` or
``stage.topology``) is a hard resolution failure (returns ``None``) — it never
silently falls back to a different provisioner. Callers that need to warn on a
dangling reference (e.g. ``BaseDeployer``) do so themselves using the returned
``None``; this module has no logging side effects of its own.
"""

from typing import TYPE_CHECKING, List, Optional, Set

if TYPE_CHECKING:
    from strata.models.deployment_model import DeploymentStageModel
    from strata.models.workspace_model import WorkspaceModel


def resolve_stage_provisioner_name(
    stage: "DeploymentStageModel",
    workspace_model: "WorkspaceModel",
) -> Optional[str]:
    """Resolve the provisioner name a single stage targets, or ``None``.

    Does not validate that a ``stage.provisioner`` name actually exists among
    ``workspace_model.spec.provisioners`` — callers that need the resolved
    ``WorkspaceIacModel`` (and want to warn on a dangling reference) perform
    that lookup themselves; callers that only need the name for set-membership
    or equality checks (secret scoping, ``scope: staged`` filtering) can use
    the returned name directly.
    """
    spec = workspace_model.spec
    provisioners = spec.provisioners or []
    if not provisioners:
        return None

    if stage.provisioner:
        return stage.provisioner

    if stage.topology:
        topologies = spec.topology or []
        topo = next((t for t in topologies if str(t.name) == stage.topology), None)
        if topo is not None:
            return str(topo.provisioner)
        return None

    if len(provisioners) == 1:
        return provisioners[0].name

    return None


def stage_reachable_provisioner_names(
    workspace_model: "WorkspaceModel",
    stages: List["DeploymentStageModel"],
) -> Set[str]:
    """Return the set of provisioner names reachable from at least one stage."""
    names: Set[str] = set()
    for stage in stages:
        name = resolve_stage_provisioner_name(stage, workspace_model)
        if name is not None:
            names.add(name)
    return names
