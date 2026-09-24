#!/usr/bin/env python3
"""`extends` chain resolution, plus tenant defaults (ADR-0024) — folding a
deployment's ancestry, and its tenant's base layer, into one document.

`merge_deployment_specs()` (`deployment_service.py`) already merges one base
into one child, on raw dicts, and is fully tested. Nothing called it: this
module is that caller, generalised to chains of any length.

**Why no new raw-dict plumbing is needed.** A `partial: true` base already
validates standalone (that is exactly what the flag is for), so it already
sits in the index as an ordinary, already-validated `DeploymentModel`.
`model.spec.model_dump(exclude_none=True)` on that gives back precisely the
dict shape `merge_deployment_specs` expects — the loader does not need to be
touched.

**Why completeness is checked against the document's *own* declared
`partial`, not the merged result's.** `merge_deployment_specs` strips
`partial`/`extends` out of every merged result. That is correct for a leaf,
but wrong applied to an *intermediate* link — a base that is itself
`partial: true` AND is further extended by something else. Once merged with
its own ancestor, an intermediate link can look complete (it inherits
`workspace` from its parent) even though its author never intended it to be
deployable on its own; re-validating that merge would demand fields
(`environments`) only the eventual leaf is meant to supply. So completeness
is only checked for documents whose *own, unmerged* `spec.partial` is falsy —
an intermediate base is still resolved (its descendants need the dict) but is
never itself required to be complete.

**Cycles** (`A extends B extends C extends A`) are the one thing the model
genuinely cannot catch by itself — `DeploymentModel.validate_not_self_extending`
only rejects the trivial `A extends A`. Detection here is unconditional
(runs before the partial/completeness check), because a cycle is an
authoring error regardless of whether anything currently deployable extends
into it — the same reason a circular import is a bug even in dead code.
Detected via a `visiting` list threaded through the recursive resolve, and
reported against the deployment whose `extends` field closes the loop,
naming the full cycle; every other node on the same cycle gets a shorter
"reported elsewhere" pointer rather than a second full trace.

**Tenant defaults (ADR-0024) are a second, deliberately separate merge
axis, applied last.** `extends` folds deployment-to-deployment ancestry;
`_merge_tenant_defaults()` folds tenant-to-deployment defaults
(`properties`/`custom`/`environments`) — conceptually different relations,
reusing the same tested `merge_deployment_specs()` mechanics rather than
inventing a second one. Kept in this module rather than given its own
call site per consumer: every current and planned reader of this
function's output wants the same fully-resolved deployment, and a second
call site is one more place to forget to call it.
"""

from typing import Any, cast

from pydantic import ValidationError

from strata.controllers.solution_controller import DocumentIndex
from strata.logging.config import get_logger
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.tenant_model import TenantModel
from strata.services.base_service import diagnostics_from_validation_error
from strata.services.deployment_service import merge_deployment_specs
from strata.utils.diagnostics import Diagnostics

log = get_logger(__name__)


class _CircularExtendsError(Exception):
    """Internal signal only — carries the cycle so the top-level caller can
    report it against the deployment whose `extends` closes the loop.
    """

    def __init__(self, cycle: list[str]) -> None:
        super().__init__(f"circular extends chain: {' -> '.join(cycle)}")
        self.cycle = cycle


def resolve_deployment_chains(index: DocumentIndex) -> tuple[dict[str, DeploymentModel], Diagnostics]:
    """Resolve every deployment's `extends` chain into a complete document.

    Args:
        index: The loaded `DocumentIndex`. Assumes Phase 1 already passed —
            same precondition as `run_semantic_checks`.

    Returns:
        `(resolved, diagnostics)`. `resolved` maps a deployment's own name to
        its fully-merged model — present for every deployment whose own
        document is not `partial: true`, whether or not it used `extends` at
        all (a plain deployment resolves to itself). A `partial: true`
        document is deliberately **not** in `resolved`, whether it is a root
        base or an intermediate link: it is not deployable, so the other
        checks have nothing to say about it — logged at debug, not reported
        as a finding.
    """
    diagnostics = Diagnostics()
    dict_cache: dict[str, dict[str, object] | None] = {}
    poisoned: set[str] = set()
    resolved: dict[str, DeploymentModel] = {}

    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        name = entry.ref.name
        deployment = entry.model
        assert isinstance(deployment, DeploymentModel)

        try:
            merged_dict = _resolve_dict(name, index, dict_cache, poisoned, visiting=[])
        except _CircularExtendsError as exc:
            diagnostics.error(str(exc), source=str(entry.source), location="spec.extends", code="circular_extends")
            continue

        if merged_dict is None:
            if name in poisoned:
                # Part of a cycle first detected while resolving a *different*
                # deployment's chain; that occurrence already reported the
                # cycle itself, with the full path. This one just also fails.
                diagnostics.error(
                    f"Deployment '{name}': extends chain could not be resolved — "
                    "part of a circular extends chain reported elsewhere",
                    source=str(entry.source),
                    location="spec.extends",
                    code="circular_extends",
                )
            # Otherwise: an ancestor is simply missing from the index —
            # validate_references already reports that against spec.extends.
            continue

        if deployment.spec.partial:
            log.debug("deployment base has no consumers", name=name, source=str(entry.source))
            continue

        merged_dict = _merge_tenant_defaults(merged_dict, index)

        try:
            merged_model = DeploymentModel.model_validate({"meta": deployment.meta.model_dump(), "spec": merged_dict})
        except ValidationError as exc:
            diagnostics.extend(diagnostics_from_validation_error(exc), source=str(entry.source))
            continue

        resolved[name] = merged_model

    return resolved, diagnostics


def _merge_tenant_defaults(spec_dict: dict[str, Any], index: DocumentIndex) -> dict[str, Any]:
    """Fold a tenant's `properties`/`custom`/`environments` in as a base
    layer under the deployment's own (ADR-0024) — deployment always wins on
    a key conflict, same `merge_deployment_specs()` semantics `extends`
    already uses. Applied *after* `extends` resolution, not before:
    `spec_dict["tenant"]` is already the deployment's own final value by
    this point, however many `extends` links deep it came from — tenant is
    one further outer layer, underneath the already-fully-resolved dict.

    Only `environments`/`properties`/`custom` travel from the tenant, not
    its whole spec — `display_name`/`geographies`/`onboarded` are
    tenant-identity fields with no `DeploymentSpecModel` equivalent to
    merge into.

    A missing/unresolvable `tenant` reference silently no-ops — the same
    treatment `_resolve_dict()` gives a missing `extends` ancestor;
    `validate_references` is the layer that reports a bad `spec.tenant`.
    """
    tenant_name = spec_dict.get("tenant")
    if not tenant_name:
        return spec_dict
    tenant_entry = index.get(PlatformKind.TENANT, tenant_name)
    if tenant_entry is None:
        return spec_dict
    tenant = cast(TenantModel, tenant_entry.model)
    tenant_base = {
        "environments": tenant.spec.environments or [],
        "properties": tenant.spec.properties or {},
        "custom": tenant.spec.custom or {},
    }
    return merge_deployment_specs(tenant_base, spec_dict)


def _resolve_dict(
    name: str,
    index: DocumentIndex,
    cache: dict[str, dict[str, object] | None],
    poisoned: set[str],
    visiting: list[str],
) -> dict[str, object] | None:
    """Return the fully-merged `spec` dict for deployment `name`, recursively.

    Memoised per name: a base shared by several leaves (a diamond) is folded
    once, not once per descendant.

    Returns:
        `None` for two distinct reasons, distinguished via `poisoned`: the
        chain is missing an ancestor entirely (not poisoned — already
        reported elsewhere), or an ancestor is part of a cycle (poisoned —
        the caller reports its own pointer).

    Raises:
        _CircularExtendsError: Only from the exact call that closes the loop, so
            its message names the real cycle.
    """
    if name in cache:
        return cache[name]

    if name in visiting:
        poisoned.add(name)
        raise _CircularExtendsError([*visiting, name])

    entry = index.get(PlatformKind.DEPLOYMENT, name)
    if entry is None:
        return None  # missing ancestor — not a cycle, not cached: nothing to memoise

    deployment = entry.model
    assert isinstance(deployment, DeploymentModel)
    own_dict = deployment.spec.model_dump(exclude_none=True)

    if not deployment.spec.extends:
        cache[name] = own_dict
        return own_dict

    try:
        base_dict = _resolve_dict(deployment.spec.extends, index, cache, poisoned, [*visiting, name])
    except _CircularExtendsError:
        poisoned.add(name)
        cache[name] = None
        raise

    if base_dict is None:
        if deployment.spec.extends in poisoned:
            poisoned.add(name)
        cache[name] = None
        return None

    merged = merge_deployment_specs(base_dict, own_dict)
    cache[name] = merged
    return merged
