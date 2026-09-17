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

from typing import TYPE_CHECKING, Any, List, Optional, Set

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


def allowed_secret_keys_for_stages(stages: List[Any], all_secret_keys: Set[str]) -> Set[str]:
    """Scope declared secret keys to what ``ResolvedValues.for_stage()`` would inject.

    Single source of truth for a widespread pattern (originally
    ``TerraformBuilder._allowed_secret_keys_for_stages()``, now also used for Helm —
    ADR-0051 follow-up, 2026-09-17):

    - No matching stages at all (no ``stages:`` defined, or nothing resolves to this
      provisioner/namespace) — no per-stage scoping signal available, so fall back to
      the legacy unscoped behavior rather than silently skipping validation.
    - Any matching stage with ``secrets: ['*']`` — all secrets (escape hatch, same as
      ``ResolvedValues.for_stage()``).
    - Otherwise — the union of every matching stage's ``secrets:`` allowlist.
    """
    if not stages:
        return set(all_secret_keys)

    allowed: Set[str] = set()
    for stage in stages:
        stage_secrets = getattr(stage, "secrets", None)
        if not stage_secrets:
            continue
        if stage_secrets == ["*"]:
            return set(all_secret_keys)
        allowed.update(stage_secrets)
    return allowed


def helm_namespaces_for_stage(stage: "DeploymentStageModel", workspace_model: "WorkspaceModel") -> Set[str]:
    """Return the namespace names *stage* deploys, for the helm provisioner.

    Mirrors ``HelmDeployer``'s own runtime filtering: ``stage.helm_namespaces`` is a
    default-deny allowlist when set; when unset, the stage deploys every namespace
    declared in the workspace (today's default, non-breaking behavior) — see
    ``DeploymentStageModel.helm_namespaces``'s docstring. No topology traversal is
    needed; this is a direct, single-hop mapping.
    """
    all_names = {ns.name for ns in (workspace_model.spec.namespaces or [])}
    helm_namespaces = getattr(stage, "helm_namespaces", None)
    if helm_namespaces:
        return set(helm_namespaces) & all_names
    return all_names


def stages_for_helm_namespace(
    namespace_name: str,
    stages: List["DeploymentStageModel"],
    workspace_model: "WorkspaceModel",
) -> List["DeploymentStageModel"]:
    """Return the deployment stages that deploy *namespace_name* via the helm provisioner.

    Mirrors ``TerraformBuilder._stages_for_provisioner()``'s pattern: a stage matches
    when its resolved provisioner (``resolve_stage_provisioner_name()``) is of type
    ``helm`` AND ``namespace_name`` is in that stage's ``helm_namespaces_for_stage()``
    set. Used to scope Helm's build-time secret-reference check to the stage(s) that
    actually deploy a given namespace (ADR-0051 follow-up, 2026-09-17) — previously
    this checked against every secret declared anywhere in the environment,
    unconditionally, because no stage-to-namespace resolver existed.
    """
    from strata.models.common_models import ProvisionerType

    provisioners = workspace_model.spec.provisioners or []
    matched: List["DeploymentStageModel"] = []
    for stage in stages:
        resolved_name = resolve_stage_provisioner_name(stage, workspace_model)
        prov = next((p for p in provisioners if p.name == resolved_name), None)
        if prov is None or prov.provisioner != ProvisionerType.HELM:
            continue
        if namespace_name in helm_namespaces_for_stage(stage, workspace_model):
            matched.append(stage)
    return matched
