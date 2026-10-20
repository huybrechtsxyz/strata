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
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, cast

import jinja2

from strata.controllers.build_controller import build_resolved_workspace_graph, find_provisioner, ordered_by_depends_on
from strata.controllers.deployment_output_store import (
    DeploymentOutputStore,
    OutputRecordNotFoundError,
    OutputRecordStaleError,
)
from strata.controllers.integration_resolution import resolve_integration, resolve_output_store_integration
from strata.controllers.solution_context import SolutionContext
from strata.controllers.source_sync import SourceSyncError, resolved_asset_info
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
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ValueResolution
from strata.integrations.terraform_projection import (
    build_strata_assets,
    build_strata_payload,
    rewrite_strata_payload,
)
from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentStageModel
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.solution_model import SolutionRemoteModel
from strata.models.workspace_model import WorkspaceModel, claimed_names, effective_resources_and_namespaces
from strata.services.environment_service import merge_environment_models
from strata.services.workspace_service import effective_assets
from strata.utils.builtin_types import ProvisionerType
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import SystemError, UsageError
from strata.utils.value_tokens import extract_value_tokens, referenced_output_keys, resolve_value_tokens_in_mapping

#: Minimum length a resolved secret value must have before `_redact()`
#: will replace it out of streamed subprocess output — a short secret
#: would otherwise turn redaction into a scattergun replace that corrupts
#: unrelated, innocuous output. Same accepted tradeoff tools like GitHub
#: Actions' own log masking make.
_MIN_REDACT_LENGTH = 6


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


def _timeout_kwargs(step: ProvisioningStepModel, stage: DeploymentStageModel | None, phase: str) -> dict[str, int]:
    """`{"timeout": N}` for this step's `phase` (`setup`/`check`/`plan`/
    `apply`/`destroy` — `DeploymentStageTimeoutsModel`'s own field names),
    or `{}` to let the integration method's own hardcoded default apply —
    never `{"timeout": None}`, which would mean "wait forever" instead of
    "use the default" (docs/design/cli-timeout.md).

    `STRATA_TIMEOUT_<STEP>_<PHASE>` (step name and phase upper-cased,
    non-alphanumerics replaced with `_`) always wins when set, for a
    one-off override with no document edit — `stage.timeouts.<phase>` is
    the fallback. `stage` is this step's `DeploymentStageModel` (matched by
    `stage.step == step.name`), not a field on the step/workspace side:
    timeouts are a deploy-time runtime knob, same as this model's sibling
    `health_checks`/`secrets`/`namespace` fields — a given Workspace recipe
    is reused by many Deployments/Environments, so a timeout bump for one
    slow environment must not force every other Deployment of that same
    Workspace to carry it too.
    """
    env_name = "STRATA_TIMEOUT_" + re.sub(r"[^A-Za-z0-9]", "_", f"{step.name}_{phase}").upper()
    override = os.environ.get(env_name)
    if override is not None:
        try:
            return {"timeout": int(override)}
        except ValueError:
            pass
    timeouts = stage.timeouts if stage is not None else None
    configured = getattr(timeouts, phase, None) if timeouts is not None else None
    return {"timeout": configured} if configured is not None else {}


def collect_step_outputs(
    integration: InfraIntegration,
    path: Path,
    env: Mapping[str, str],
    *,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Collect a just-deployed step's outputs (`terraform output -json`) for
    later, dependent steps to reference via `${output:step.key}` tokens
    (docs/design/deploy-command.md's "Cross-step output context").

    `output()` is a Terraform-specific extra, like `init`/`validate` —
    confirmed not part of the `InfraIntegration` ABC — so Compose/Helm
    integrations (no native "outputs" concept in either real reference repo)
    simply contribute nothing, via the same `getattr(..., None)` pattern
    already used for `init`/`validate`. Returns `({}, set())` (rather than
    raising) on any collection failure — a step already deployed
    successfully; losing its outputs should not fail the run, only leave
    later `${output:}` references for this step unresolved.

    `auth`/`resolved_values` (docs/design/gitops-integration.md
    Implementation Plan Phase 5) are **only** added to the `output()` call
    when at least one is given — never unconditionally. Found on review:
    `TerraformIntegration.output()` is the one real, currently-reachable
    `output()` implementation this function calls in production (Helm has
    none; Compose's is never reached here — `Capability.CONTAINER` steps
    `continue` earlier in the caller's loop) and it has no `**kwargs: Any`
    catch-all of its own — unconditionally passing these two kwargs for
    every step, not just GitOps ones, would raise `TypeError` on every real
    Terraform deploy, silently caught by this function's own `except
    TypeError` below and degrading `${output:...}` resolution to
    permanently empty. Callers pass both only for an actual GitOps step;
    `None`/`None` for every other tool keeps this call textually identical
    to before Phase 5.

    Returns:
        `(values, sensitive_keys)` — `values` is every output's real value
        (never redacted; real Terraform returns it unredacted in `-json`
        output regardless of the output's own `sensitive` flag), keyed by
        output name; `sensitive_keys` is the subset of those names whose
        own `sensitive` flag was `true` — found as a real gap (2026-10-10):
        without this, an `${output:step.key}` reference to a Terraform
        output marked `sensitive = true` was never treated as secret-
        shaped at all, so its real value reached a plain on-disk file.
    """
    output = getattr(integration, "output", None)
    if output is None:
        return {}, set()
    extra: dict[str, Any] = {}
    if auth is not None or resolved_values is not None:
        extra["auth"] = auth
        extra["resolved_values"] = resolved_values
    try:
        result = output(path, json_format=True, env=env, **extra)
    except TypeError:
        # Some integrations expose an `output()` with an incompatible shape
        # (e.g. `ComposeIntegration.output()` is `docker stack services`,
        # v1's own `output` step for Compose — a status listing, not a
        # JSON key/value map, and requires a `namespace` kwarg this doesn't
        # pass). Only Terraform's `output(json_format=...)` shape is
        # understood here; anything else contributes nothing.
        return {}, set()
    if not result.is_successful:
        return {}, set()
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}, set()
    if not isinstance(parsed, dict):
        return {}, set()
    values: dict[str, str] = {}
    sensitive_keys: set[str] = set()
    for key, entry in parsed.items():
        if isinstance(entry, dict) and "value" in entry:
            value = entry["value"]
            values[key] = value if isinstance(value, str) else json.dumps(value)
            if entry.get("sensitive") is True:
                sensitive_keys.add(key)
    return values, sensitive_keys


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


def _merge_durable_outputs(
    output_store: DeploymentOutputStore | None,
    upstream: set[str],
    step_outputs: dict[str, dict[str, str]],
    visible_outputs: dict[str, str],
    *,
    step_name: str,
    diagnostics: Diagnostics,
) -> bool:
    """Fill in `visible_outputs` for any `upstream` step not already
    produced by this same invocation (`step_outputs`), reading from the
    durable `output_store` as a fallback (docs/work/
    cross-run-step-output-delivery.md's Implementation Plan step 4) —
    the actual cross-*run* half of `${output:...}` resolution; the
    in-memory half above is unchanged, same-run only.

    Deliberately asymmetric, not a single pass/fail: a durable record
    that was simply never written is forgiving (`OutputRecordNotFoundError`,
    silently skipped) — this step may not even reference that upstream
    step's outputs at all, and the existing per-token resolution still
    raises its own error later if it does. A durable record that DOES
    exist but is untrustworthy (stale, or the store itself is
    unreachable) is not forgiving — silently ignoring it risks exactly
    the "quiet wrong value" class of bug this feature exists to prevent,
    so those two cases fail loudly here, immediately, before this step's
    tool ever runs (the "all four stop the pipeline" decision already
    settled in that doc, for the two cases reachable at this stage —
    "sensitive" and "never recorded by any invocation" are the other two,
    both already handled elsewhere: sensitive outputs are excluded by
    `DeploymentOutputStore.record()` itself, and a reference to a key
    that was never produced anywhere still reaches the existing deep
    resolution error unchanged).

    Returns:
        `True` if no stop-the-pipeline condition was hit (the caller
        should continue); `False` once `diagnostics` already carries a
        pre-flight error for `step_name` (the caller must return
        immediately, matching every other pre-flight check in this
        function).
    """
    if output_store is None:
        return True
    for upstream_step in upstream - step_outputs.keys():
        try:
            durable_values = output_store.read(upstream_step)
        except OutputRecordNotFoundError:
            continue
        except (OutputRecordStaleError, IntegrationError) as exc:
            diagnostics.error(
                f"Step '{step_name}': durable output for upstream step '{upstream_step}' is unusable — {exc}",
                location=step_name,
            )
            return False
        visible_outputs.update({f"{upstream_step}.{key}": value for key, value in durable_values.items()})
    return True


def deploy_run(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    smoke_test: bool = False,
    stop_after: Literal["setup", "check"] | None = None,
    use_saved_plan: bool = False,
    stage: str | None = None,
    scope: str | None = None,
    on_step: Callable[[str], None] | None = None,
    on_line: Callable[[str, str, str], None] | None = None,
    pin: str | None = None,
    output_max_age: int | None = None,
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
        dry_run: Runs `init`/`validate`/`plan` for real (saved to
            `<step>.tfplan`, same as a normal run) and stops — `deploy()`
            is never called. Matches v1's own `setup -> check -> plan`
            dry-run sequence — **not** a zero-contact "would run" report;
            that's `smoke_test` now. A step whose tool doesn't
            meaningfully support a preview gets a warning instead of a
            hard failure (`InfraIntegration.plan_or_warn()`), and the
            run continues to the next step. A container-capable
            (Helm/Compose) step's `deploy_namespace()` gets the
            equivalent real-preview treatment (phase 3). Mutually
            exclusive with `smoke_test` (enforced by `deploy_command.py`,
            not here).
        smoke_test: Report which steps would run without calling
            `init`/`validate`/`plan`/`deploy` at all — zero tool contact,
            no auth needed. Mutually exclusive with `dry_run` (enforced by
            `deploy_command.py`, not here).
        stop_after: Stops every resolved step right after `init()`
            (`"setup"`) or right after `validate()` (`"check"`) succeeds —
            backs `deploy setup`/`deploy check`
            (docs/design/deploy-pipeline-stage-commands.md). `None`
            (default) does not stop early here; `dry_run`/`smoke_test`
            already cover the other two real stopping points ("plan" and
            zero-contact respectively) and are unaffected by this
            parameter. A container-capable (Helm/Compose) step has no
            init/validate phase to begin with and is reported as a
            graceful skip, never an error, for either value. Mutually
            exclusive with `dry_run`/`smoke_test` (enforced by
            `deploy_command.py`, not here).
        use_saved_plan: Skips `validate()`/`plan_or_warn()` entirely after
            `init()` and applies the `<step>.tfplan` already on disk at
            `build_path` — backs `deploy apply`
            (docs/design/deploy-pipeline-stage-commands.md). Hard-errors
            (a `Diagnostic`, not a crash) if that file does not exist —
            never silently falls back to computing a fresh plan, since
            the entire point is to apply *exactly* a previously reviewed
            plan. A container-capable (Helm/Compose) step has no
            saved-plan-file concept at all — `deploy_namespace()` just
            does a real (`dry_run=False`) deploy, with no guarantee it
            matches whatever an earlier `deploy plan` showed (an
            acknowledged tool limitation, not something this flag papers
            over). Mutually exclusive with `dry_run`/`smoke_test`/
            `stop_after` (enforced by `deploy_command.py`, not here,
            matching every other mutual-exclusivity rule above).
        stage: Restrict to the one step named `stage`.
        scope: Restrict to steps whose `ProvisioningStepModel.scope`
            matches (docs/design/deploy-command.md's resolved `scope`
            placement — workspace-owned, not deployment-owned).
        on_step: Called with a one-line progress message per step.
        on_line: Called as `(tool, stream, text)` for every subprocess
            output line as it arrives — `tool` is the resolved
            integration's own `TYPE`
            (`"terraform"`/`"helm"`/`"compose"`), `stream` is
            `"stdout"`/`"stderr"`, `text` the raw line. `None` (default)
            keeps today's buffered-until-exit behaviour. This function
            itself only enriches a lower-level `Callable[[str, str],
            None]` (what every `Integration.run()`/`InfraIntegration.
            plan()`/`.deploy()` call actually expects) with the current
            step's `tool` name before forwarding — the orchestrator is
            what knows which tool is running at any given point in the
            loop, not the caller.
        pin: `--pin` (docs/work/version-lifecycle.md Phase 5) — overrides
            `deployment.spec.version` for this invocation only, zero
            persisted mutation. Covers the `artifacts` pin category here
            (the one category that resolves live, at deploy time, via
            `resolve_values()`'s `store: artifact` handling) —
            `images`/`charts`/`remotes` only ever take effect at build
            time, so `build run --pin` is where those three actually need
            it; passing the same pin to both commands keeps one deploy
            consistent with the build it is executing.
        output_max_age: `--output-max-age` (docs/work/cross-run-step-
            output-delivery.md) — trust window in seconds for durable
            cross-run step outputs, overriding `WorkspaceSpecModel.
            output_store_max_age` for this invocation only (same
            override precedence as `pin` over `spec.version`). `None`
            (default) falls back to the workspace's own setting, itself
            `None` by default (no staleness enforcement).

    Returns:
        Diagnostics accumulated while resolving values, preflight-
        checking, and executing every step — not raised, matching
        `build_run()`'s own "still useful to inspect a partial result"
        precedent.

    Raises:
        UsageError: `deployment_name` does not exist, its `workspace` is
            unset or does not resolve, `pin` does not name an indexed
            Version document, or a provisioning step names an
            integration/provisioner that cannot be resolved.
    """

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    index = context.controller.index

    deployment = resolve_deployment(context, deployment_name, version_pin=pin)
    if deployment.spec.workspace is None:
        raise UsageError(f"Deployment '{deployment_name}' has no workspace to deploy.")
    workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
    if workspace_entry is None:
        raise UsageError(
            f"Deployment '{deployment_name}' names workspace '{deployment.spec.workspace}', which is not in the index."
        )
    workspace = cast(WorkspaceModel, workspace_entry.model)

    # Cross-run step-output persistence (docs/work/cross-run-step-output-
    # delivery.md) — `None` when the workspace configures no
    # `output_store`/`output_store_integration` at all, matching today's
    # in-memory-only behavior exactly (no new required step for existing
    # consumers). Trust window: `--output-max-age` wins when given, else
    # the workspace's own `output_store_max_age`, else `None` (no
    # staleness enforcement — today's behavior, unchanged by default).
    output_store_backend = resolve_output_store_integration(index, workspace)
    effective_max_age = output_max_age if output_max_age is not None else workspace.spec.output_store_max_age
    output_store = (
        DeploymentOutputStore(
            output_store_backend,
            deployment_name,
            max_age=timedelta(seconds=effective_max_age) if effective_max_age is not None else None,
        )
        if output_store_backend
        else None
    )

    diagnostics = Diagnostics()

    environments = reachable_environments(context, deployment)
    variables, secrets, features = merge_environment_models(environments)
    all_keys = sorted({**variables, **secrets, **features})
    resolved = resolve_values(context, deployment_name, all_keys, version_pin=pin)
    diagnostics.extend(resolved.diagnostics)
    if not resolved.diagnostics.ok:
        return diagnostics

    # Secret redaction for streamed output — `resolved.values` deliberately
    # drops which store a value came from (`ValueResolution`'s own
    # docstring), but `secrets` above still has every declared secret key
    # name, so the actual resolved secret *values* can be reconstructed
    # here, once, and redacted out of every streamed line before a caller
    # (console
    # `--follow`, a future NDJSON writer) ever sees it. This one set also
    # covers Helm/Compose's own per-module `--set-string` secrets for
    # free — `resolve_module_values()`/`resolve_compose_values()` derive
    # those from `${secret:KEY}` tokens resolved against this exact same
    # `resolved.values` dict, so no second mechanism is needed.
    secret_values = {v for k, v in resolved.values.items() if k in secrets and len(v) >= _MIN_REDACT_LENGTH}

    def _redact(text: str) -> str:
        for value in secret_values:
            text = text.replace(value, "***")
        return text

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
    # per-step data (ADR-0035, docs/design/unified-terraform-projection.md
    # Phase 7). Re-resolved per step below using that step's own `tokens`
    # (mirrors `backend.configuration`'s existing per-step pattern). A
    # `${output:...}` token needs an owning step to scope which step's
    # outputs apply — see the ownership/claiming logic right below
    # (docs/design/gap_fit_v1.md gap #12) — `${var:}`/`${secret:}` tokens
    # need no such owner and resolve identically for every step.
    #
    # One payload now, not two (`dns_networks_firewalls`/
    # `configuration_payloads`, the pre-ADR-0035 shape) — `payload["dns"]`/
    # `["networks"]`/`["firewalls"]` are the only three categories with a
    # per-name claiming concept (gap #12); every other key is broadcast,
    # identical for every step, exactly as before.
    payload = build_strata_payload(graph)

    # Same lookup `build_workload_modules()` uses at build time (docs/design/gap_fit_v1.md
    # gap #13) — `HelmIntegration.deploy_namespace()` needs it to resolve a
    # chart-based module's `chartRemote` name into a real registry URL.
    solution = context.controller.solution
    remotes: dict[str, SolutionRemoteModel] = (
        {remote.name: remote for remote in (solution.spec.remotes or [])} if solution is not None else {}
    )

    all_steps = workspace.spec.execution or []

    # Ownership (docs/design/gap_fit_v1.md gap #12): a document is "claimed" once any
    # step's effective dns/firewall/network set (`claimed_names()`,
    # docs/design/workspace-execution-model.md — direct fields plus
    # resource-derived claiming, topology-expanded) names it
    # (`workspace_model.py`'s `validate_execution()` accepts dns/network/
    # firewall names there since 2026-09-28, reusing
    # `validate_provisioning_steps()`'s existing shared-target ordering rule
    # for two steps that both claim one). An unclaimed document still
    # broadcasts to every step for `${var:}`/`${secret:}` (unchanged, Phase 2
    # behaviour) — only `${output:...}` needs an owner, since that's the
    # only per-step-varying data a dns/network/firewall document could ever
    # reference. Preserves ADR-0035's own resolved decision (b): still
    # only these three categories, not generalised to every category.
    resources_by_name = {r.name: r for r in (workspace.spec.resources or []) if r.enabled}
    topologies_by_name = {t.name: t for t in (workspace.spec.topology or [])}
    claimed_by_category: dict[str, set[str]] = {category: set() for category in ("dns", "networks", "firewalls")}
    for step in all_steps:
        step_claimed = claimed_names(step, resources_by_name, topologies_by_name)
        for category in claimed_by_category:
            claimed_by_category[category] |= step_claimed & payload[category].keys()

    for category in claimed_by_category:
        for name, doc_payload in payload[category].items():
            if name not in claimed_by_category[category] and _contains_output_token(doc_payload):
                diagnostics.error(
                    f"'{name}' uses '${{output:...}}' but no execution step claims this document "
                    f"(directly or via its resources/topologies) — add it to the step that should apply "
                    f"it (docs/design/gap_fit_v1.md gap #12).",
                    location=name,
                )

    # None of the other twelve categories may ever contain `${output:...}`
    # — there is no per-step-varying data they could meaningfully
    # reference (no claiming mechanism exists for a workspace-wide
    # singleton or a type-grouped category, unlike dns/networks/firewalls
    # above).
    for category, category_payload in payload.items():
        if category in claimed_by_category:
            continue
        if _contains_output_token(category_payload):
            diagnostics.error(
                f"'{category}' uses '${{output:...}}', which is not supported outside dns/networks/firewalls "
                "(docs/design/gap_fit_v1.md gap #8) — remove it or move the value into a dns/network/firewall document instead.",
                location=category,
            )
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
    step_sensitive_outputs: dict[str, set[str]] = {}
    stage_by_step = {s.step: s for s in (deployment.spec.stages or [])}

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
        deployment_stage = stage_by_step.get(step.name)

        # `_tool` is bound as a default argument, not read from the
        # enclosing scope at call time (ruff B023) — `integration` is a
        # loop variable, and this closure must capture *this* iteration's
        # value, not whatever `integration` happens to be by the time
        # `_line` is actually called (always within the same iteration in
        # practice, but the default-argument binding is correct regardless).
        def _line(stream: str, text: str, _tool: str = integration.TYPE) -> None:
            if on_line is not None:
                on_line(_tool, stream, _redact(text))

        # `smoke_test` is the only flag that short-circuits here — zero
        # tool contact at all. `dry_run` flows all the way through
        # init/validate/plan and only stops before `deploy()` below, after
        # a successful plan — it is intentionally absent from this
        # condition.
        if smoke_test:
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
        visible_sensitive_outputs = {
            f"{upstream_step}.{key}"
            for upstream_step, keys in step_sensitive_outputs.items()
            if upstream_step in upstream
            for key in keys
        }
        if not _merge_durable_outputs(
            output_store, upstream, step_outputs, visible_outputs, step_name=step.name, diagnostics=diagnostics
        ):
            return diagnostics
        tokens = {**resolved.values, **visible_outputs}

        # Helm/Compose dispatch (docs/design/gap_fit_v1.md gap #13): a
        # container-capable step never goes through Terraform's
        # init/validate/plan/deploy sequence at all — it deploys every
        # namespace its effective namespace set names instead (direct
        # `namespaces` plus topology-expanded, point 7), one
        # `deploy_namespace()` call per namespace, filtered to this
        # integration's own module `TYPE` (mirrors v1's real
        # `DeployerFactory.resolve_type()`: the step's own provisioner picks
        # the tool, never inferred from
        # namespace linkage — see docs/design/deploy-command.md's
        # "Superseded" section for the two earlier, wrong drafts).
        if Capability.CONTAINER in integration.CAPABILITIES:
            if stop_after is not None:
                _step(
                    f"step '{step.name}' via {integration_type} has no {stop_after} phase (container-capable) — skipped"
                )
                continue
            _, namespaces_eff = effective_resources_and_namespaces(step, resources_by_name, topologies_by_name)
            # sorted(): namespaces_eff is a set (hash order, not declaration order) — deploy order
            # must stay deterministic across runs, not vary with PYTHONHASHSEED.
            namespaces_targeted = [
                graph.namespaces[name] for name in sorted(namespaces_eff) if name in graph.namespaces
            ]
            if not namespaces_targeted:
                diagnostics.error(
                    f"Step '{step.name}': provisioner '{provisioner.name}' ({integration_type}) is "
                    "container-capable but names no namespace (directly or via topologies).",
                    location=step.name,
                )
                return diagnostics
            _step(f"running step '{step.name}' via {integration_type}")
            for namespace in namespaces_targeted:
                by_type = resolve_namespace_modules(index, namespace, build_path)
                modules = by_type.get(integration.TYPE, [])
                if not modules:
                    continue
                diagnostics.extend(
                    integration.deploy_namespace(
                        namespace,
                        modules,
                        tokens=tokens,
                        dry_run=dry_run,
                        remotes=remotes,
                        env=env,
                        line_callback=_line,
                    )
                )
            if not diagnostics.ok:
                return diagnostics
            _step(f"deployed step '{step.name}' via {integration_type}")
            continue

        # This step's own view: dns/networks/firewalls filtered to
        # what it claims plus whatever nobody claims (gap #12,
        # unchanged); every other category broadcasts as-is — no
        # per-category file-existence filtering needed any more
        # (Phase 2: `build_strata_payload()` always emits every key).
        # Computed unconditionally (not just when `integration.ENV_VAR_
        # PREFIX is not None`, below) so the output pre-flight check right
        # after it can scan this step's real payload regardless of which
        # delivery mechanism the tool uses.
        step_claimed = claimed_names(step, resources_by_name, topologies_by_name)
        step_payload = {
            category: (
                {
                    name: doc
                    for name, doc in docs.items()
                    if name in step_claimed or name not in claimed_by_category[category]
                }
                if category in claimed_by_category
                else docs
            )
            for category, docs in payload.items()
        }

        # Output pre-flight (docs/design/output-declarations.md) — every
        # '${output:STEP.KEY}' this step's own payload references must
        # already be in `tokens` (this invocation's outputs, merged with
        # any durable record above) before any resolution/file-write is
        # attempted below. Reports every missing key in one pass, not
        # just the first `resolve_value_tokens()` happens to hit.
        backend_configuration = provisioner.backend.configuration if provisioner.backend is not None else {}
        referenced = referenced_output_keys(backend_configuration) | referenced_output_keys(step_payload)
        missing = sorted(key for key in referenced if key not in tokens)
        if missing:
            for key in missing:
                upstream_step, _, output_key = key.partition(".")
                available = sorted(k.split(".", 1)[1] for k in tokens if k.startswith(f"{upstream_step}."))
                diagnostics.error(
                    f"Step '{step.name}': '${{output:{key}}}' references step '{upstream_step}', but "
                    f"'{output_key}' was not produced by it (or any durable record). "
                    f"Available from '{upstream_step}': {available}",
                    location=step.name,
                )
            return diagnostics

        backend_config: dict[str, str] = {}
        if provisioner.backend is not None:
            try:
                resolved_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)
            except ValueError as exc:
                diagnostics.error(f"Step '{step.name}': {exc}", location=step.name)
                return diagnostics
            backend_config = {k: str(v) for k, v in resolved_config.items()}

        # Terraform delivery (ADR-0035, docs/design/
        # unified-terraform-projection.md) — one `strata.auto.
        # tfvars.json` rewrite plus one `TF_VAR_strata_secrets` env var,
        # replacing the old per-category file rewrite + one-env-var-per-
        # category/per-flat-key delivery entirely. There's only one object
        # now, and `rewrite_strata_payload()` blanks exactly the
        # secret-shaped leaves, never a whole category — the old
        # `resx_<type>` collision workaround's reason for existing is gone
        # too (`resources` is one flat map, Phase 2).
        # `None` for a tool with no such mechanism (see that attribute's
        # own docstring); this whole block is then correctly a no-op for
        # it, rather than injecting a meaningless `"NoneD ns"`-shaped env
        # var.
        if integration.ENV_VAR_PREFIX is not None:
            # `graph` was built here without the build's `resolved_assets`, so `payload["assets"]`
            # is empty; rebuild it from the directories `build run` materialised. Lazy and
            # inside this block on purpose: only a step that writes the tfvars file needs them.
            asset_names = sorted({name for s in all_steps for name in (s.assets or [])})
            if asset_names:
                bound_assets = effective_assets(workspace, deployment)
                try:
                    resolved_assets = {
                        slot: resolved_asset_info(build_path, slot, bound_assets[slot]) for slot in asset_names
                    }
                except SourceSyncError as exc:
                    diagnostics.error(f"Step '{step.name}': {exc}", location=step.name)
                    return diagnostics
                step_graph = replace(
                    graph,
                    resolved_assets=resolved_assets,
                    step_root=path.relative_to(build_path).as_posix(),
                )
                step_payload = {**step_payload, "assets": build_strata_assets(step_graph)}
            try:
                resolved_payload, strata_secrets = rewrite_strata_payload(
                    step_payload, tokens, sensitive_output_keys=visible_sensitive_outputs
                )
            except ValueError as exc:
                diagnostics.error(f"Step '{step.name}': {exc}", location=step.name)
                return diagnostics
            (path / "strata.auto.tfvars.json").write_text(json.dumps({"strata": resolved_payload}))
            env[f"{integration.ENV_VAR_PREFIX}strata_secrets"] = json.dumps(strata_secrets)

        if provisioner.output and provisioner.output.template:
            template_path = context.root / provisioner.output.template
            try:
                rendered_output_path = integration.render_output_template(
                    path, resolved=resolved, provisioner=provisioner, graph=graph, template_path=template_path
                )
            except jinja2.TemplateError as exc:
                diagnostics.error(
                    f"Step '{step.name}': output.template '{provisioner.output.template}' failed to render — {exc}",
                    location=step.name,
                )
                return diagnostics
        else:
            rendered_output_path = None

        _step(f"running step '{step.name}' via {integration_type}")
        init = getattr(integration, "init", None)
        if init is not None:
            init_result = init(
                path,
                backend_config=backend_config,
                env=env,
                line_callback=_line,
                **_timeout_kwargs(step, deployment_stage, "setup"),
            )
            if not init_result.is_successful:
                diagnostics.error(f"Step '{step.name}': init failed — {init_result.stderr}", location=step.name)
                return diagnostics
        if stop_after == "setup":
            _step(f"setup complete for step '{step.name}' via {integration_type} (stop-after-setup)")
            continue
        if use_saved_plan:
            # Deliberately skips `validate()` AND `plan_or_warn()` below —
            # `deploy apply` must apply *exactly* the plan a human or
            # automated gate already reviewed, never a freshly recomputed
            # one (docs/design/deploy-pipeline-stage-commands.md).
            saved_plan_path = path / f"{step.name}.tfplan"
            if not saved_plan_path.exists():
                diagnostics.error(
                    f"Step '{step.name}': no saved plan found at '{saved_plan_path}' — run 'deploy plan' first.",
                    location=step.name,
                )
                return diagnostics
        else:
            validate = getattr(integration, "validate", None)
            if validate is not None:
                validate_result = validate(
                    path, env=env, line_callback=_line, **_timeout_kwargs(step, deployment_stage, "check")
                )
                if not validate_result.is_successful:
                    diagnostics.error(
                        f"Step '{step.name}': validate failed — {validate_result.stderr}", location=step.name
                    )
                    return diagnostics
            if stop_after == "check":
                _step(f"check complete for step '{step.name}' via {integration_type} (stop-after-check)")
                continue

        # Real credentials for a GitOps remote (docs/design/
        # gitops-integration.md Implementation Plan Phase 5) — resolved
        # fresh, here, at actual deploy time, never at `build run` (secrets
        # are deploy-time-only, ADR-0022 D4). `auth`/`step_resolved_values`
        # stay `None` for every non-GitOps step — not merely harmless but
        # required: `collect_step_outputs()`'s own docstring explains why
        # unconditionally passing them would silently break Terraform's
        # real output collection. `remote.integration` is schema-declared
        # but never existence-validated (unlike `provisioner.integration`,
        # which Phase 1 validation guarantees resolves) — a missing/typo'd
        # name here must resolve to `auth = None` (today's ambient
        # behaviour), never raise.
        auth: AuthenticationModel | None = None
        step_resolved_values: Mapping[str, str] | None = None
        if provisioner.gitops is not None:
            step_resolved_values = resolved.values
            gitops_remote = remotes.get(provisioner.gitops.remote)
            if gitops_remote is not None and gitops_remote.integration:
                integration_entry = index.get(PlatformKind.INTEGRATION, gitops_remote.integration)
                if integration_entry is not None:
                    auth = cast(IntegrationModel, integration_entry.model).spec.authentication
        elif provisioner.tool == ProvisionerType.SCRIPT.value:
            # Unlike GitOps above, script wants upstream step outputs too
            # (`tokens`, not bare `resolved.values`) — its `args`/`STRATA_
            # VALUES` delivery has no reason to withhold what's already
            # visible to this step via `${output:...}` (docs/design/
            # script-provisioner.md).
            step_resolved_values = tokens

        if not use_saved_plan:
            plan_result = integration.plan_or_warn(
                path,
                diagnostics=diagnostics,
                location=step.name,
                out_file=f"{step.name}.tfplan",
                env=env,
                auth=auth,
                resolved_values=step_resolved_values,
                output_path=rendered_output_path,
                line_callback=_line,
                **_timeout_kwargs(step, deployment_stage, "plan"),
            )
            if plan_result is None:
                # No real plan preview available for this integration. Under
                # `--dry-run` that's a graceful skip — a warning was already
                # recorded by `plan_or_warn()`; move on to the next step
                # rather than aborting the whole run (docs/work/
                # deploy-plan-preview.md §3: "if the IaC/provisioner supports
                # it, otherwise not"). On a real apply, `plan()` is a required
                # prerequisite, not an optional preview — silently skipping
                # `deploy()` here would report the run as successful having
                # never actually deployed this step, so this is a hard error
                # instead, matching Helm/Compose's `deploy_namespace()`, which
                # only ever calls `plan_or_warn()` inside its own `dry_run`
                # branch and goes straight to `deploy()` otherwise.
                if dry_run:
                    continue
                diagnostics.error(
                    f"Step '{step.name}': plan is required before apply, but no plan preview is available for "
                    f"'{integration_type}' — cannot proceed with apply.",
                    location=step.name,
                )
                return diagnostics
            if not plan_result.is_successful:
                diagnostics.error(f"Step '{step.name}': plan failed — {plan_result.stderr}", location=step.name)
                return diagnostics

            if dry_run:
                _step(f"planned step '{step.name}' via {integration_type} (dry-run, no apply)")
                continue

        deploy_result = integration.deploy(
            path,
            plan_file=f"{step.name}.tfplan",
            env=env,
            auth=auth,
            resolved_values=step_resolved_values,
            output_path=rendered_output_path,
            line_callback=_line,
            **_timeout_kwargs(step, deployment_stage, "apply"),
        )
        if not deploy_result.is_successful:
            diagnostics.error(f"Step '{step.name}': deploy failed — {deploy_result.stderr}", location=step.name)
            return diagnostics

        step_outputs[step.name], step_sensitive_outputs[step.name] = collect_step_outputs(
            integration, path, env, auth=auth, resolved_values=step_resolved_values
        )
        if output_store is not None:
            # Per step, immediately after collection — not batched to the
            # end of the run (docs/work/cross-run-step-output-delivery.md)
            # — so a mid-run crash still leaves every already-completed
            # step's output durably recorded. A write failure here never
            # fails the run: the step already deployed successfully,
            # matching `collect_step_outputs()`'s own stated philosophy
            # one paragraph up — losing the durable record only leaves a
            # later invocation's `${output:}` reference unresolved, same
            # as losing the in-memory collection would.
            try:
                output_store.record(step.name, step_outputs[step.name], step_sensitive_outputs[step.name])
            except (SystemError, IntegrationError) as exc:
                diagnostics.warning(
                    f"Step '{step.name}': could not durably record output for later invocations — {exc}",
                    location=step.name,
                )

        _step(f"deployed step '{step.name}' via {integration_type}")

    return diagnostics
