#!/usr/bin/env python3
"""`strata deploy run` — executes plan/apply (Terraform)/upgrade (Compose/
Helm) against a workspace's provisioners, using fully-resolved values
(secrets, integration-backed variables/features included) — unlike
`build run`'s deliberately-partial build-time resolution
(docs/design/deploy-command.md, ADR-0022 D4: "build run renders; it does
not execute... deploy run runs plan/apply").

Reuses `build_controller.py`'s `ordered_by_depends_on()`/`find_provisioner()`
unchanged (same execution graph, same lookup) and `value_controller.py`'s
`resolve_deployment()`/`reachable_environments()`/`resolve_values()`
(the last one, unlike `build_value_references()`, already resolves every
store kind including secrets/integration-backed ones — ADR-0021 Phase 4).

`init()`/`validate()` are Terraform-specific extras, not part of the
`InfraIntegration` ABC (confirmed directly: `init()`'s own docstring says
so) — called via `getattr(..., None)` so the loop stays tool-agnostic
(ADR-0022's own "zero tool-specific branching" principle) without forcing
Compose/Helm to implement methods they have no use for.
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import jinja2

from strata.controllers.build_controller import build_resolved_workspace_graph, find_provisioner, ordered_by_depends_on
from strata.controllers.integration_resolution import resolve_integration
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import (
    build_value_references,
    merge_workspace_environment_deployment_properties,
    reachable_environments,
    resolve_deployment,
    resolve_tenant,
    resolve_values,
)
from strata.controllers.workload_controller import resolve_namespace_modules
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.resolved_context import ValueResolution
from strata.integrations.terraform_projection import (
    FLAT_CATEGORIES,
    build_configuration_payloads,
    build_dns_networks_firewalls_payloads,
    real_variable_name,
)
from strata.models.common_models import PlatformKind
from strata.models.integration_model import Capability
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.solution_model import SolutionRemoteModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import merge_environment_models
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.value_tokens import extract_value_tokens, resolve_value_tokens_in_mapping


def _contains_output_token(node: object) -> bool:
    """True if `node` (a raw dns/networks/firewalls payload, or any nested
    piece of one) contains a well-formed `${output:...}` token anywhere.

    Used to flag a DNS/network/firewall document that needs an owning step
    (docs/design/gap_fit_v1.md gap #12) but has none — `${var:}`/`${secret:}` tokens
    don't need this check, they resolve identically for every step
    regardless of ownership.
    """
    if isinstance(node, str):
        return any(kind == "output" for kind, _ in extract_value_tokens(node))
    if isinstance(node, dict):
        return any(_contains_output_token(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_output_token(v) for v in node)
    return False


def tf_var_env(resolved: ValueResolution, prefix: str | None) -> dict[str, str]:
    """Map every resolved value to its `<prefix><key>` env-var form —
    haven's own confirmed convention for Terraform specifically: "the
    terraform deployer auto-injects each resolved secret as `TF_VAR_<key>`
    ... no manual `TF_VAR_*` wiring needed" (docs/design/deploy-command.md's
    "Real evidence").

    `prefix` is the resolved step's own `integration.ENV_VAR_PREFIX`, never
    a hardcoded literal here — this function has no opinion on which tool
    it's for, only whether that tool declares an env-var-prefix delivery
    mechanism at all (see `Integration.ENV_VAR_PREFIX`'s own docstring for
    why Helm/Compose can't just use a different, empty prefix). Returns
    `{}` when `prefix` is `None` — this whole mechanism has nothing to
    deliver for such a tool; a container-capable step's `deploy_namespace()`
    gets its own tokens/secrets through entirely separate kwargs instead.
    """
    if prefix is None:
        return {}
    return {f"{prefix}{key}": value for key, value in resolved.values.items()}


def collect_step_outputs(integration: InfraIntegration, path: Path, env: Mapping[str, str]) -> dict[str, str]:
    """Collect a just-deployed step's outputs (`terraform output -json`) for
    later, dependent steps to reference via `${output:step.key}` tokens
    (docs/design/deploy-command.md's "Cross-step output context").

    `output()` is a Terraform-specific extra, like `init`/`validate` —
    confirmed not part of the `InfraIntegration` ABC — so Compose/Helm
    integrations (no native "outputs" concept in either real reference repo)
    simply contribute nothing, via the same `getattr(..., None)` pattern
    already used for `init`/`validate`. Returns `{}` (rather than raising) on
    any collection failure — a step already deployed successfully; losing
    its outputs should not fail the run, only leave later `${output:}`
    references for this step unresolved.
    """
    output = getattr(integration, "output", None)
    if output is None:
        return {}
    try:
        result = output(path, json_format=True, env=env)
    except TypeError:
        # Some integrations expose an `output()` with an incompatible shape
        # (e.g. `ComposeIntegration.output()` is `docker stack services`,
        # v1's own `output` step for Compose — a status listing, not a
        # JSON key/value map, and requires a `namespace` kwarg this doesn't
        # pass). Only Terraform's `output(json_format=...)` shape is
        # understood here; anything else contributes nothing.
        return {}
    if not result.is_successful:
        return {}
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    values: dict[str, str] = {}
    for key, entry in parsed.items():
        if isinstance(entry, dict) and "value" in entry:
            value = entry["value"]
            values[key] = value if isinstance(value, str) else json.dumps(value)
    return values


def _upstream_step_names(step_name: str, steps: list[ProvisioningStepModel]) -> set[str]:
    """Every step `step_name` (transitively) `depends_on` — the set a step may
    reference via `${output:X.key}` (docs/design/deploy-command.md's
    "Cross-step output context": dependency-scoped, not v1's global pool).
    Mirrors `provisioning_model.py`'s own (private) `reachable_from()`
    computed there for a different check (shared-target ambiguity) — not
    reused directly since that one is nested inside a validator, not
    exported.
    """
    graph = {s.name: set(s.depends_on or []) for s in steps}
    seen: set[str] = set()
    stack = list(graph.get(step_name, ()))
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return seen


def deploy_run(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    stage: str | None = None,
    scope: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> Diagnostics:
    """Execute `deployment_name`'s workspace provisioners against `build_path`
    (already rendered by a prior `build run` — this never re-renders it).

    Per filtered step: `init` (backend config, tokens resolved) ->
    `validate` -> `plan` (saved to `<step>.tfplan`) -> `deploy` (applies
    that saved plan file) — v1's real, confirmed `setup -> check -> plan
    -> apply` sequence (docs/design/deploy-command.md's "Orchestrator
    shape"). Applying a *saved* plan file needs no `-auto-approve` —
    Terraform never prompts for one regardless of any flag (confirmed
    directly in v1's own `TerraformDeployer.apply()`, which never reads
    `force` at all) — so `force` plays no role in the normal apply path.

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to deploy.
        build_path: Where `build run` already rendered artifacts — never
            re-rendered here.
        force: Currently inert (docs/design/deploy-command.md's
            Remaining Work item 7) — v1 uses it to bypass *advisory*
            approval gates (AI plan review, promotion-override guards),
            neither of which v2 has built yet. Kept in the signature so
            a future gate can consume it without changing the call shape.
        dry_run: Report which steps would run without calling
            `init`/`validate`/`plan`/`deploy` at all — matches
            `build_run()`'s own `--dry-run` treatment (report, no
            mutation), not a Terraform "plan only" mode.
        stage: Restrict to the one step named `stage`.
        scope: Restrict to steps whose `ProvisioningStepModel.scope`
            matches (docs/design/deploy-command.md's resolved `scope`
            placement — workspace-owned, not deployment-owned).
        on_step: Called with a one-line progress message per step.

    Returns:
        Diagnostics accumulated while resolving values, preflight-
        checking, and executing every step — not raised, matching
        `build_run()`'s own "still useful to inspect a partial result"
        precedent.

    Raises:
        UsageError: `deployment_name` does not exist, its `workspace` is
            unset or does not resolve, or a provisioning step names an
            integration/provisioner that cannot be resolved.
    """

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    index = context.controller.index

    deployment = resolve_deployment(context, deployment_name)
    if deployment.spec.workspace is None:
        raise UsageError(f"Deployment '{deployment_name}' has no workspace to deploy.")
    workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
    if workspace_entry is None:
        raise UsageError(
            f"Deployment '{deployment_name}' names workspace '{deployment.spec.workspace}', which is not in the index."
        )
    workspace = cast(WorkspaceModel, workspace_entry.model)

    diagnostics = Diagnostics()

    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)
    all_keys = sorted({**variables, **secrets, **features})
    resolved = resolve_values(context, deployment_name, all_keys)
    diagnostics.extend(resolved.diagnostics)
    if not resolved.diagnostics.ok:
        return diagnostics

    # For `provisioner.output.template` rendering only (phase 8) — same
    # assembly `build_run()` uses, so a template validated at build time
    # gets the identical context shape at deploy time. `variable_refs`/
    # `feature_refs`/`secret_refs`'s own `.value` stays build-time-safe-only
    # (`build_value_references()`'s deliberate restriction) but is never read
    # here — only `.key` membership matters; the real value always comes
    # from `resolved.values` (full resolution, secrets included).
    variable_refs, feature_refs, secret_refs = build_value_references(
        environments, context=context, deployment=deployment
    )
    properties = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "properties")
    custom = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "custom")
    tenant = resolve_tenant(context, deployment)
    graph = build_resolved_workspace_graph(
        index,
        workspace,
        variable_refs=variable_refs,
        feature_refs=feature_refs,
        secret_refs=secret_refs,
        properties=properties,
        custom=custom,
        tenant=tenant,
        deployment=deployment,
    )

    # Raw (unresolved) snapshot, built once per run — workspace-wide, not
    # per-step data (matches `build_platform_projection()`'s own dns/
    # networks/firewalls categories exactly). Re-resolved per step below
    # using that step's own `tokens` (mirrors `backend.configuration`'s
    # existing per-step pattern). A `${output:...}` token needs an owning
    # step to scope which step's outputs apply — see the ownership/claiming
    # logic right below (docs/design/gap_fit_v1.md gap #12) — `${var:}`/`${secret:}`
    # tokens need no such owner and resolve identically for every step.
    #
    # Why built once here but `backend_config` is built per step inside the
    # loop below: `backend_config` genuinely is per-step data (each step's
    # `provisioner.backend.configuration` is a different field on a
    # different provisioner). `dns`/`networks`/`firewalls` come from
    # `workspace.spec.dns_zones`/`.networks`/`.firewalls` instead — the same
    # three payloads for every step, no per-step variant exists — so only
    # the *resolution* (which needs the per-step `tokens`) happens in the
    # loop; building the raw payload does not, since recomputing identical
    # data every iteration would be pure waste.
    #
    # Why three separate dict entries instead of one merged payload: this
    # mirrors Terraform's own variable convention, not an arbitrary choice.
    # `terraform_projection.py`'s `planned_files()` already writes one
    # `*.auto.tfvars.json` file per category at build time, because each is
    # Terraform's own natural `variable "dns" {}`/`variable "networks" {}`/
    # `variable "firewalls" {}` unit — `TF_VAR_<name>` has to match that
    # per-variable convention 1:1 (`TF_VAR_dns`/`TF_VAR_networks`/
    # `TF_VAR_firewalls`), so a merged blob under one name wouldn't be
    # readable by any of them.
    dns_networks_firewalls = build_dns_networks_firewalls_payloads(graph)

    # Ten more Value-token-bearing categories (docs/design/gap_fit_v1.md gap #8's
    # Terraform-side refinement + gap #17's universal-resolution-reach
    # fix, docs/design/value-token-resolution.md's "Full Solution" Phase 6
    # and "Decision (2026-09-29)") — broadcast only, unlike the three
    # above: `workspace`/`providers`/`resx_<type>`/`topologies`/
    # `namespaces`/`flags`/`variables`/`properties`/`custom`/`tenant` are
    # either workspace/deployment-wide singletons (no name to claim by) or
    # grouped by type/topology/namespace name (addressable, but nothing
    # evidences a real need for per-name ownership yet, gap #12-style) —
    # so `${output:...}` is rejected outright for all ten (checked once,
    # right below) rather than given a claiming mechanism. Gap #17 closed
    # the confirmed real hole here: `providers` used to be entirely absent
    # from this delivery, so a `${var:}`/`${secret:}` token in a Provider's
    # `configuration`/`custom` passed `strata validate` but was silently
    # never resolved or delivered anywhere.
    configuration_payloads = build_configuration_payloads(graph)

    # Same lookup `build_workload_modules()` uses at build time (docs/design/gap_fit_v1.md
    # gap #13) — `HelmIntegration.deploy_namespace()` needs it to resolve a
    # chart-based module's `chartRemote` name into a real registry URL.
    solution = context.controller.solution
    remotes: dict[str, SolutionRemoteModel] = (
        {remote.name: remote for remote in (solution.spec.remotes or [])} if solution is not None else {}
    )

    all_steps = workspace.spec.execution or []

    # Ownership (docs/design/gap_fit_v1.md gap #12): a document is "claimed" once any
    # step's `targets` names it (`workspace_model.py`'s `validate_execution()`
    # accepts dns/network/firewall names there since 2026-09-28, reusing
    # `validate_provisioning_steps()`'s existing shared-target ordering rule
    # for two steps that both claim one). An unclaimed document still
    # broadcasts to every step for `${var:}`/`${secret:}` (unchanged, Phase 2
    # behaviour) — only `${output:...}` needs an owner, since that's the
    # only per-step-varying data a dns/network/firewall document could ever
    # reference.
    claimed_by_category: dict[str, set[str]] = {category: set() for category in dns_networks_firewalls}
    for step in all_steps:
        for category, docs in dns_networks_firewalls.items():
            claimed_by_category[category] |= set(step.targets) & docs.keys()

    for category, docs in dns_networks_firewalls.items():
        for name, payload in docs.items():
            if name not in claimed_by_category[category] and _contains_output_token(payload):
                diagnostics.error(
                    f"'{name}' uses '${{output:...}}' but no execution step's 'targets' names "
                    f"this document — add it to the step that should apply it (docs/design/gap_fit_v1.md gap #12).",
                    location=name,
                )

    # None of build_configuration_payloads()'s ten broadcast categories may
    # ever contain `${output:...}` — there is no per-step-varying data they
    # could meaningfully reference (no claiming mechanism exists for a
    # workspace-wide singleton or a type-grouped category, unlike
    # dns/networks/firewalls above).
    for name, payload in configuration_payloads.items():
        if _contains_output_token(payload):
            diagnostics.error(
                f"'{name}' uses '${{output:...}}', which is not supported outside dns/networks/firewalls "
                "(docs/design/gap_fit_v1.md gap #8) — remove it or move the value into a dns/network/firewall document instead.",
                location=name,
            )

    # TF_VAR delivery below needs two extra, step-invariant things worked
    # out once, up front (docs/design/terraform-tfvars-parity.md): (1) a
    # `FLAT_CATEGORIES` category delivers one env var per top-level key,
    # not one per category — two categories that happen to declare the
    # same key would otherwise silently collide as the same `env[...]`
    # entry with no ordering guarantee at all (unlike separate on-disk
    # `*.auto.tfvars.json` files, at least loaded in a deterministic
    # order); caught here as a hard error instead. (2) every `resx_<type>`
    # category is really the same "resources" Terraform variable
    # regardless of type (confirmed against v1's real
    # `_build_resources_by_category()`) — merged into one dict so
    # delivering it as a single `TF_VAR_resources` doesn't have one type
    # clobber another.
    flat_key_owner: dict[str, str] = {}
    merged_resources: dict[str, Any] = {}
    for name, payload in configuration_payloads.items():
        if name.startswith("resx_"):
            # Every resx_<type> merges into the same "resources" variable
            # by design (multiple types are expected to combine, not
            # collide) — registered under a single synthetic owner label
            # so a FLAT_CATEGORIES key that happens to also be named
            # "resources" is still caught below.
            flat_key_owner.setdefault("resources", "resx_<type>")
            merged_resources.update(payload.get("resources", {}))
            continue
        if name not in FLAT_CATEGORIES:
            continue
        for key in payload:
            owner = flat_key_owner.get(key)
            if owner is not None and owner != name:
                diagnostics.error(
                    f"'{key}' is declared by both '{owner}' and '{name}' — they would collide as "
                    f"the same 'TF_VAR_{key}' env var (docs/design/terraform-tfvars-parity.md).",
                    location=key,
                )
                continue
            flat_key_owner[key] = name
    if not diagnostics.ok:
        return diagnostics

    steps = ordered_by_depends_on(all_steps)
    if stage is not None:
        steps = [s for s in steps if s.name == stage]
    if scope is not None:
        steps = [s for s in steps if s.scope == scope]

    if not steps:
        _step("no steps match the given --stage/--scope filter")
        return diagnostics

    # Preflight — validate every filtered step's tool is available BEFORE
    # running anything (docs/design/deploy-command.md's "Structural
    # gotchas from v1": fail fast, before a later step's missing tool is
    # discovered only after an earlier one already made real changes).
    resolved_steps: list[tuple[ProvisioningStepModel, ProvisionerModel, InfraIntegration]] = []
    for step in steps:
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(index, provisioner)
        if not integration.is_available():
            diagnostics.error(
                f"Provisioner '{provisioner.name}' (step '{step.name}'): "
                f"'{integration.command}' is not available on PATH.",
                location=step.name,
            )
            continue
        resolved_steps.append((step, provisioner, integration))
    if not diagnostics.ok:
        return diagnostics

    upstream_by_step = {s.name: _upstream_step_names(s.name, all_steps) for s in all_steps}
    step_outputs: dict[str, dict[str, str]] = {}

    for step, provisioner, integration in resolved_steps:
        # Matches `build_run()`'s own `source_path` resolution exactly
        # (`source_sync.sync_source()`'s `destination = build_path /
        # (source.target_path or source.source_path)`) — a provisioner with
        # a `source` is materialised once, keyed by its *source* location,
        # not by whichever step happens to reference it (two steps can
        # share one provisioner/source, matching a real Terraform root
        # module used by more than one execution step). Only a source-less
        # (sync/GitOps) provisioner uses the step's own name, mirroring
        # build_run()'s identical branch.
        if provisioner.source is None:
            path = build_path / step.name
        else:
            # `source_path` is guaranteed set for any git-based source that
            # `sync_source()` successfully materialised (chart-based sources
            # fail there instead, before `deploy run` ever gets called) —
            # the fallback below only matters to satisfy the type checker.
            relative = provisioner.source.target_path or provisioner.source.source_path or step.name
            path = build_path / relative
        integration_type = type(integration).__name__

        if dry_run:
            _step(f"would deploy step '{step.name}' via {integration_type}")
            continue

        env = tf_var_env(resolved, integration.ENV_VAR_PREFIX)

        upstream = upstream_by_step.get(step.name, set())
        visible_outputs = {
            f"{upstream_step}.{key}": value
            for upstream_step, outputs in step_outputs.items()
            if upstream_step in upstream
            for key, value in outputs.items()
        }
        tokens = {**resolved.values, **visible_outputs}

        # Helm/Compose dispatch (docs/design/gap_fit_v1.md gap #13): a
        # container-capable step never goes through Terraform's
        # init/validate/plan/deploy sequence at all — it deploys every
        # namespace its own `targets` names instead, one `deploy_namespace()`
        # call per namespace, filtered to this integration's own module
        # `TYPE` (mirrors v1's real `DeployerFactory.resolve_type()`: the
        # step's own provisioner picks the tool, never inferred from
        # namespace linkage — see docs/design/deploy-command.md's
        # "Superseded" section for the two earlier, wrong drafts).
        if Capability.CONTAINER in integration.CAPABILITIES:
            namespaces_targeted = [graph.namespaces[name] for name in step.targets if name in graph.namespaces]
            if not namespaces_targeted:
                diagnostics.error(
                    f"Step '{step.name}': provisioner '{provisioner.name}' ({integration_type}) is "
                    "container-capable but 'targets' names no namespace.",
                    location=step.name,
                )
                return diagnostics
            for namespace in namespaces_targeted:
                by_type = resolve_namespace_modules(index, namespace, build_path)
                modules = by_type.get(integration.TYPE, [])
                if not modules:
                    continue
                diagnostics.extend(
                    integration.deploy_namespace(
                        namespace, modules, tokens=tokens, dry_run=dry_run, remotes=remotes, env=env
                    )
                )
            if not diagnostics.ok:
                return diagnostics
            _step(f"deployed step '{step.name}' via {integration_type}")
            continue

        backend_config: dict[str, str] = {}
        if provisioner.backend is not None:
            resolved_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)
            backend_config = {k: str(v) for k, v in resolved_config.items()}

        # Terraform delivery for dns/networks/firewalls (docs/design/gap_fit_v1.md gap
        # #9/#12, docs/design/value-token-resolution.md's "Full Solution"
        # Phase 2) and build_configuration_payloads()'s ten broadcast
        # categories (gap #8 + gap #17): the
        # whole resolved payload as one JSON-encoded env var per category,
        # same never-touches-disk pattern `backend.configuration` already
        # has (`terraform_projection.py`'s own `*.auto.tfvars.json` written
        # by `build run` is never rewritten). Named via `integration.
        # ENV_VAR_PREFIX` + `real_variable_name(category)` (docs/design/
        # terraform-tfvars-parity.md) — never a hardcoded `"TF_VAR_"` literal
        # or the bare category name, so a real Terraform root's own
        # `TF_VAR_<declared_variable_name>` override actually matches what
        # `planned_files()` wrapped the on-disk file's content under
        # (`providers` → `TF_VAR_platform_providers`, `dns` →
        # `TF_VAR_dns_zones`, `tenant` → `TF_VAR_strata_tenant`). Two
        # categories don't reduce to a single rename at all, handled
        # separately below instead of through `real_variable_name()`:
        # `FLAT_CATEGORIES` (`workspace`/`flags`/`variables`/`properties`/
        # `custom`) delivers one env var per top-level key, since each key
        # is its own independent real Terraform variable; every
        # `resx_<type>` category merges into one `TF_VAR_resources` (every
        # real file is actually named `resources` regardless of type —
        # `flat_key_owner`/`merged_resources` above already worked out
        # there's no collision to worry about). `None` for a tool with no
        # such mechanism (see that attribute's own docstring); this whole
        # block is then correctly a no-op for it, rather than injecting a
        # meaningless `"NoneD ns"`-shaped env var.
        # A document claimed by some step's `targets` (gap #12) is
        # delivered only to its owning step(s) — resolved using that step's
        # own `tokens`, so `${output:}` sees exactly that step's
        # dependency-scoped outputs, never another step's. An unclaimed
        # document still broadcasts to every step (Phase 2's original,
        # unchanged behaviour — the pre-flight check above already
        # guarantees it has no `${output:}` token to be wrong about).
        # Empty categories are skipped, matching `planned_files()`'s own
        # convention.
        if integration.ENV_VAR_PREFIX is not None:
            for category, docs in dns_networks_firewalls.items():
                docs_for_step = {
                    name: payload
                    for name, payload in docs.items()
                    if name in step.targets or name not in claimed_by_category[category]
                }
                if not docs_for_step:
                    continue
                resolved_payload = resolve_value_tokens_in_mapping(docs_for_step, tokens)
                env[f"{integration.ENV_VAR_PREFIX}{real_variable_name(category)}"] = json.dumps(resolved_payload)

            # build_configuration_payloads()'s ten broadcast categories
            # (gap #8 + gap #17) — identical for
            # every step (no ownership to vary by, and no `${output:...}`
            # can be present, guaranteed by the pre-flight check above), so
            # this is pure repetition of the same resolution per step, same
            # as an unclaimed dns/network/firewall document already does
            # above. `FLAT_CATEGORIES`/`resx_<type>` are delivered
            # separately below, once per step — skipped here so neither
            # gets a second, wrongly-shaped `TF_VAR_<category>` entry too.
            for name, payload in configuration_payloads.items():
                if name in FLAT_CATEGORIES or name.startswith("resx_"):
                    continue
                resolved_payload = resolve_value_tokens_in_mapping(payload, tokens)
                env[f"{integration.ENV_VAR_PREFIX}{real_variable_name(name)}"] = json.dumps(resolved_payload)

            # FLAT_CATEGORIES: one env var per top-level key, not one per
            # category — each key is its own independent real Terraform
            # variable (`workspace`'s six fixed keys; every user-declared
            # flag/variable/property/custom key). `flat_key_owner` above
            # already proved no two categories declare the same key.
            for name in FLAT_CATEGORIES:
                payload = configuration_payloads.get(name, {})
                if not payload:
                    continue
                resolved_payload = resolve_value_tokens_in_mapping(payload, tokens)
                for key, value in resolved_payload.items():
                    env[f"{integration.ENV_VAR_PREFIX}{key}"] = json.dumps(value)

            # resx_<type>: every type merges into the one real "resources"
            # variable (`merged_resources` above) — delivered once, not
            # once per type, so multiple active resource types don't
            # clobber each other under the same env var name.
            if merged_resources:
                resolved_resources = resolve_value_tokens_in_mapping(merged_resources, tokens)
                env[f"{integration.ENV_VAR_PREFIX}resources"] = json.dumps(resolved_resources)

        if provisioner.output and provisioner.output.template:
            template_path = context.root / provisioner.output.template
            try:
                integration.render_output_template(
                    path, resolved=resolved, provisioner=provisioner, graph=graph, template_path=template_path
                )
            except jinja2.TemplateError as exc:
                diagnostics.error(
                    f"Step '{step.name}': output.template '{provisioner.output.template}' failed to render — {exc}",
                    location=step.name,
                )
                return diagnostics

        init = getattr(integration, "init", None)
        if init is not None:
            init(path, backend_config=backend_config, env=env)
        validate = getattr(integration, "validate", None)
        if validate is not None:
            validate(path, env=env)

        plan_result = integration.plan(path, out_file=f"{step.name}.tfplan", env=env)
        if not plan_result.is_successful:
            diagnostics.error(f"Step '{step.name}': plan failed — {plan_result.stderr}", location=step.name)
            return diagnostics

        deploy_result = integration.deploy(path, plan_file=f"{step.name}.tfplan", env=env)
        if not deploy_result.is_successful:
            diagnostics.error(f"Step '{step.name}': deploy failed — {deploy_result.stderr}", location=step.name)
            return diagnostics

        step_outputs[step.name] = collect_step_outputs(integration, path, env)

        _step(f"deployed step '{step.name}' via {integration_type}")

    return diagnostics
