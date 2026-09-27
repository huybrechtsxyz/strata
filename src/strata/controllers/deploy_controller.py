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
from typing import cast

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
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.resolved_context import ValueResolution
from strata.models.common_models import PlatformKind
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import merge_environment_models
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.value_tokens import resolve_value_tokens_in_mapping


def tf_var_env(resolved: ValueResolution) -> dict[str, str]:
    """Map every resolved value to its `TF_VAR_<key>` env-var form —
    haven's own confirmed convention: "the terraform deployer
    auto-injects each resolved secret as `TF_VAR_<key>`... no manual
    `TF_VAR_*` wiring needed" (docs/design/deploy-command.md's "Real
    evidence"). Applied uniformly to every step regardless of tool —
    Compose/Helm integrations simply never read these extra env vars,
    and `transport.run_command()`'s `env` kwarg already merges rather
    than replaces the process environment, so cloud-auth vars the
    workflow set (Azure OIDC, `TF_TOKEN_*`) survive untouched.
    """
    return {f"TF_VAR_{key}": value for key, value in resolved.values.items()}


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
            f"Deployment '{deployment_name}' names workspace '{deployment.spec.workspace}', "
            "which is not in the index."
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
    variable_refs, feature_refs, secret_refs = build_value_references(environments, context=context, deployment=deployment)
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
    )

    steps = ordered_by_depends_on(workspace.spec.execution or [])
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

    all_steps = workspace.spec.execution or []
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

        env = tf_var_env(resolved)

        upstream = upstream_by_step.get(step.name, set())
        visible_outputs = {
            f"{upstream_step}.{key}": value
            for upstream_step, outputs in step_outputs.items()
            if upstream_step in upstream
            for key, value in outputs.items()
        }

        backend_config: dict[str, str] = {}
        if provisioner.backend is not None:
            tokens = {**resolved.values, **visible_outputs}
            resolved_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)
            backend_config = {k: str(v) for k, v in resolved_config.items()}

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
