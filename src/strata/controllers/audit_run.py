#!/usr/bin/env python3
"""Finalize and distribute a deploy run's audit trail — the end-of-run
sequencing (docs/design/audit-trail.md's "Write once locally, then
distribute").

Glues the audit trail into `deploy run`'s command flow. It wraps `deploy_run()`'s *result*: the run's findings,
and the `stages` its `StageLog` recorded (`deploy_stage_log.py`), which also decide `partial`.

`artifacts.platform` references `build_path/resolved.yaml`
(`build_controller.write_resolved_manifest()`) — v2's closest real
equivalent to v1's `platform.json`; v2 does not produce that exact file
today, so this is an honest adaptation, not a literal port.

`sbom` references `build_path/sbom.json` (`sbom_controller.write_sbom()`,
gap #18 Phase 1) the same way — re-hashed fresh here rather than threaded
through from the earlier `build run` process, since the two are separate
CLI invocations with no in-memory value to carry between them. Optional,
unlike `artifacts.platform`: a missing SBOM raises no diagnostic. Unlike `resolved.yaml`, the SBOM itself is
also kept: copied into the run's audit folder and pushed with the manifest to every git sink.

`action="destroy"` (docs/design/deploy-destroy.md) records a `deploy destroy` run the same way, with three
differences: the manifest and metrics say `destroy`; the event admitted is `deployment.destroyed` instead of
`deployment.completed`, so a sink has to list it to receive it; and the manifest carries no `policy_results`,
because a destroy evaluates no policy and the file in the build directory is left over from the earlier deploy.

Both sink arms now really dispatch: `git` pushes the written files
(Phase 4), and `integration` renders a CloudEvents+ECS envelope per
admitted event type and sends it through an `AuditSinkIntegration`
(docs/design/audit-sink-dispatch.md). Every integration-arm failure — a
missing/incapable Integration document as much as a failed send — is
reported through the sink's own `required` flag rather than raised, so a
misconfigured audit sink never takes down a deploy that already succeeded.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from strata import __version__
from strata.controllers.audit_error_category import error_category
from strata.controllers.audit_event_rendering import render_manifest_event, render_metrics_event
from strata.controllers.audit_path_resolution import resolve_audit_relative_path
from strata.controllers.audit_push import push_audit_files
from strata.controllers.build_controller import apply_remote_version_pins
from strata.controllers.checkout_state import checkout_state
from strata.controllers.deployment_document import render_document
from strata.controllers.deployment_report_store import DeploymentReportStore
from strata.controllers.deployment_report_view import deployment_report
from strata.controllers.deployment_resolution import deployment_workspace
from strata.controllers.effective_images import effective_images
from strata.controllers.integration_resolution import resolve_output_store_integration
from strata.controllers.policy_results import read_policy_results
from strata.controllers.sbom_controller import SBOM_FORMAT
from strata.controllers.security_gate_controller import built_commit_sha
from strata.controllers.solution_context import SolutionContext
from strata.controllers.source_record import read_build_record
from strata.controllers.value_controller import (
    reachable_environments,
    resolve_deployment,
    resolve_values,
    resolve_version,
)
from strata.controllers.workload_controller import apply_version_pins, resolve_module
from strata.integrations import registry
from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.audit_manifest_model import (
    ChangeReferenceModel,
    DeploymentManifestModel,
    ManifestArtifactsModel,
    ManifestChartModel,
    ManifestCheckModel,
    ManifestImageModel,
    ManifestPipelineModel,
    ManifestPlatformReferenceModel,
    ManifestProviderModel,
    ManifestRepositoryModel,
    ManifestSbomReferenceModel,
    ManifestStageModel,
)
from strata.models.audit_metrics_model import (
    DeploymentMetricsModel,
    MetricsDimensionsModel,
    MetricsMeasuresModel,
    verification_state,
)
from strata.models.audit_model import EVENT_DEFAULTS, AuditGitSinkTargetModel, AuditSinkModel
from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel, VersionPins
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.services.environment_service import merge_environment_models
from strata.utils import layout
from strata.utils.actor import resolve_actor
from strata.utils.ci_run import resolve_pipeline_run
from strata.utils.diagnostics import Diagnostics, Severity
from strata.utils.errors import StrataError
from strata.utils.sbom_file import SBOM_FILENAME, parse_sbom

#: Public — `controllers/audit_read.py` (docs/design/audit-commands.md) reads
#: this back and must glob for the exact same filename this module writes.
MANIFEST_FILENAME = "_manifest.json"
_METRICS_FILENAME = "_metrics.json"
#: The handover document `spec.documentation` writes next to the manifest (docs/design/deployment-documentation.md).
HANDOVER_FILENAME = "_handover.md"


def finalize_and_distribute_deploy_audit(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    execution_id: str,
    started_at: datetime,
    run_diagnostics: Diagnostics,
    dry_run: bool = False,
    action: Literal["deploy", "destroy"] = "deploy",
    pin: VersionPins = None,
    change_system: str | None = None,
    change_id: str | None = None,
    change_reason: str | None = None,
    change_classification: str | None = None,
    change_title: str | None = None,
    change_url: str | None = None,
    change_approved_by: str | None = None,
    change_approved_at: str | None = None,
    providers: Sequence[ManifestProviderModel] = (),
    stages: Sequence[ManifestStageModel] = (),
    checks: Sequence[ManifestCheckModel] = (),
    verified: bool | None = None,
) -> Diagnostics:
    """Finalize the manifest + metrics for a `deploy run` invocation, write
    them locally, then distribute to every configured sink.

    A no-op (empty `Diagnostics`) for a dry run — nothing was actually
    changed, so a manifest's fields would be hollow, the same reason "A
    third file" excludes dry runs from metrics, applied here to the
    manifest too.

    Args:
        context: The same already-`require_valid()`-ed context `deploy_run()` used.
        deployment_name: The deployment that was just run.
        build_path: Where `build run` rendered artifacts — same path
            `deploy_run()` executed against.
        execution_id: Fixed once at the top of the run, before `deploy_run()`
            was called.
        started_at: The run's start time, fixed once at the top of the run —
            never `datetime.now()` called from inside this function.
        run_diagnostics: What `deploy_run()` itself returned.
        dry_run: Same flag passed to `deploy_run()`.
        action: `"destroy"` for a `deploy destroy` run; see the module docstring for what changes.
        pin: The same `--pin` passed to `deploy_run()` (docs/work/
            version-lifecycle.md Phase 5) — forwarded to `resolve_deployment()`
            here too, so the manifest's `artifacts.repositories`/`.images`/
            `.charts` record what was *actually* deployed (the pinned
            version), not whatever `spec.version` happens to say on disk.
        change_system: `deploy run --change-system`, or `None` when the
            caller supplied no change reference at all. Supplied together
            with `change_id`/`change_reason` or not at all — enforced by
            `deploy_command.py` as a `UsageError` before `deploy_run()` ever
            executes, not re-validated here.
        change_id: `deploy run --change-id`.
        change_reason: `deploy run --change-reason`.
        change_classification: `deploy run --change-classification`, optional.
        change_title: `deploy run --change-title`, optional.
        change_url: `deploy run --change-url`, optional.
        change_approved_by: `deploy run --change-approved-by` — who approved
            this change in the tracker, distinct from `supplied_by` (who
            typed this reference) and `deployed_by` (who ran the deploy).
            Supplied together with `change_approved_at` or not at all —
            enforced by `ChangeReferenceModel`'s own validator, not
            re-checked here. Operator-supplied, not independently verified.
        change_approved_at: `deploy run --change-approved-at` — ISO-8601
            timestamp of the approval itself (in the tracker), never
            auto-derived from this run's own clock.
        providers: One entry per provisioner the run used, with the tool version and path its preflight found
            (`deploy_run(on_tool=...)`, docs/design/helm-chart-deployment.md); `artifacts.providers` is omitted when empty.
        stages: What each step did (`deploy_run(stage_log=...)`); `stages` is omitted when empty. A failed run with
            a stage that succeeded is `partial`: something changed before it stopped.
        checks: The answers of the deployment's `spec.verify` checks (`StageLog.deployment_checks()`); omitted
            when empty (docs/design/post-deploy-verification.md, D9).
        verified: `StageLog.verified()`: whether every check that ran answered pass or warn, `None` when none ran.

    Returns:
        Findings from finalizing/writing/distributing only — never
        `run_diagnostics` itself (the caller already has and reports that
        separately); merge the two if a single combined view is needed.
    """
    diagnostics = Diagnostics()
    if dry_run:
        return diagnostics

    deployment = resolve_deployment(context, deployment_name, version_pin=pin)
    if deployment.spec.workspace is None:
        return diagnostics  # deploy_run() already failed and reported this; nothing to finalize

    platform_ref, platform_diagnostics = _platform_reference(build_path)
    diagnostics.extend(platform_diagnostics)
    if platform_ref is None:
        return diagnostics  # can't build a manifest without its one required artifact reference

    configuration = _single_configuration(context)
    completed_at = datetime.now(timezone.utc)
    duration_seconds = (completed_at - started_at).total_seconds()
    status = _status(run_diagnostics, stages)
    actor = resolve_actor()
    environment = _single_environment(deployment)
    change_reference = _build_change_reference(
        system=change_system,
        change_id=change_id,
        reason=change_reason,
        classification=change_classification,
        title=change_title,
        url=change_url,
        approved_by=change_approved_by,
        approved_at=change_approved_at,
        actor=actor,
        supplied_at=completed_at.isoformat(),
    )
    # Never raises; `_platform_reference()` above already reported a missing file.
    record = read_build_record(build_path)
    repositories, images, charts = _collect_artifacts_bom(
        context, deployment, diagnostics, recorded=None if record is None else record.remotes
    )
    solution_commit, solution_dirty = (
        (built_commit_sha(context.root), None) if record is None else (record.solution_commit, record.solution_dirty)
    )

    policy_results = None if action == "destroy" else read_policy_results(build_path)
    sbom_reference, sbom_bytes = _sbom_reference(build_path, diagnostics)

    manifest = DeploymentManifestModel(
        execution_id=execution_id,
        action=action,
        version=str(__version__),
        deployment=deployment_name,
        workspace=deployment.spec.workspace,
        environment=environment,
        file=_document_file(context, deployment_name),
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_seconds=duration_seconds,
        status=status,
        dry_run=False,
        deployed_by=actor,
        pipeline=_pipeline(),
        commit_sha=solution_commit,
        commit_dirty=solution_dirty,
        artifacts=ManifestArtifactsModel(
            platform=platform_ref,
            repositories=repositories,
            images=images,
            charts=charts,
            providers=list(providers) or None,
        ),
        sbom=sbom_reference,
        change_reference=change_reference,
        stages=list(stages) or None,
        checks=list(checks) or None,
        verified=verified,
        policy_results=policy_results,
        errors=run_diagnostics.messages(Severity.ERROR) or None,
        warnings=run_diagnostics.messages(Severity.WARNING) or None,
        messages=run_diagnostics.messages(Severity.INFO) or None,
    )
    all_checks = [*(check for stage in stages for check in stage.checks or []), *checks]
    metrics = DeploymentMetricsModel(
        execution_id=execution_id,
        deployment=deployment_name,
        workspace=deployment.spec.workspace,
        timestamp=started_at.isoformat(),
        # `outcome` stays success or failed, so a dashboard's failure rate counts a partial run as failed.
        dimensions=MetricsDimensionsModel(
            outcome="success" if status == "success" else "failed",
            action=action,
            environment=environment,
            tenant=deployment.spec.tenant,
            actor=actor,
            commit_sha=solution_commit,
            verification=verification_state(verified),
        ),
        measures=MetricsMeasuresModel(
            duration_seconds=duration_seconds,
            stages_total=sum(stage.status != "skipped" for stage in stages),
            stages_failed=sum(stage.status == "failed" for stage in stages),
            # Left out, not zero, when no policy was judged (a destroy, or a build with none configured).
            policy_checks_total=None if policy_results is None else len(policy_results),
            policy_checks_failed=None if policy_results is None else sum(not r.passed for r in policy_results),
            # Left out, not zero, when no verify check ran; "failed" as `verified` reads it: fail or unknown.
            checks_total=len(all_checks) or None,
            checks_failed=sum(check.answer not in ("pass", "warn") for check in all_checks) if all_checks else None,
        ),
        error_category=error_category(run_diagnostics, policy_results),
    )

    relative_path = resolve_audit_relative_path(deployment, configuration, started_at.isoformat())
    local_dir = layout.audit_dir(context.root) / relative_path
    local_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = local_dir / MANIFEST_FILENAME
    metrics_path = local_dir / _METRICS_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    metrics_path.write_text(metrics.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    # The very bytes `manifest.sbom` hashed, under the name its `path` gives, so the reference resolves in the
    # audit folder too.
    sbom_path = None
    if sbom_reference is not None:
        sbom_path = local_dir / SBOM_FILENAME
        sbom_path.write_bytes(sbom_bytes)
    if action == "deploy" and status == "success":
        _record_last_deploy(context, deployment, manifest, diagnostics)
    handover_path = (
        _write_handover(context, deployment, manifest, local_dir / HANDOVER_FILENAME, diagnostics, pin=pin)
        if action == "deploy" and status == "success" and deployment.spec.documentation is not None
        else None
    )

    audit_config = configuration.spec.audit if configuration is not None else None
    if audit_config is None or not audit_config.sinks:
        return diagnostics

    event_enabled = {**EVENT_DEFAULTS, **(audit_config.event_overrides or {})}
    completion_event = "deployment.destroyed" if action == "destroy" else "deployment.completed"
    admitted_types = [t for t in (completion_event, "deployment.measured") if event_enabled.get(t, True)]

    # Deploy-time values a `git` sink's `remote.integration` credentials may
    # reference (docs/design/gitops-integration.md's credential pattern,
    # backported here 2026-10-08) — resolved once, same "all declared keys"
    # shape `deploy_controller.py` resolves for `TF_VAR_`/Helm delivery, reused
    # for every git sink rather than recomputed per-sink. Skipped entirely
    # unless at least one git sink exists, since `resolve_values()` is not
    # free (it runs every `${value:...}` cross-document reference and store
    # resolution in the whole solution).
    git_sink_values: Mapping[str, str] | None = None
    if any(sink.enabled and sink.git is not None for sink in audit_config.sinks):
        environments = reachable_environments(context, deployment)
        variables, secrets, features = merge_environment_models(environments)
        all_keys = sorted({**variables, **secrets, **features})
        git_sink_values = resolve_values(context, deployment_name, all_keys, version_pin=pin).values

    for sink in audit_config.sinks:
        _dispatch_sink(
            sink,
            admitted_types=admitted_types,
            context=context,
            manifest=manifest,
            metrics=metrics,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            relative_path=relative_path,
            actor=actor,
            api_version=deployment.apiVersion,
            diagnostics=diagnostics,
            git_sink_values=git_sink_values,
            handover_path=handover_path,
            sbom_path=sbom_path,
        )

    return diagnostics


def audit_summary(manifest: DeploymentManifestModel) -> dict[str, str | None]:
    """What the handover document says about a run (its `audit` data): never a value, only who, when, which
    commit, which audit record, and whether its verify checks said the deployment works."""
    return {
        "deployed_by": manifest.deployed_by,
        "completed_at": manifest.completed_at,
        "status": manifest.status,
        "action": manifest.action,
        "version": manifest.version,
        "execution_id": manifest.execution_id,
        "commit_sha": manifest.commit_sha,
        "verification": verification_state(manifest.verified),
    }


def _record_last_deploy(
    context: SolutionContext, deployment: DeploymentModel, manifest: DeploymentManifestModel, diagnostics: Diagnostics
) -> None:
    """Keep this successful deploy's audit summary in the workspace's `output_store`, so `strata deploy report`
    can say who deployed it, when and from which commit without reading the git audit sink. A no-op without an
    `output_store`; a failed write is a warning."""
    workspace = deployment_workspace(context.controller.index, deployment)
    if workspace is None:
        return
    try:
        backend = resolve_output_store_integration(context.controller.index, workspace)
        if backend is not None:
            DeploymentReportStore(backend, deployment.meta.name).record_last_deploy(audit_summary(manifest))
    except (StrataError, IntegrationError) as exc:
        diagnostics.warning(
            f"Deployment '{deployment.meta.name}': could not record this deploy's audit summary in the output "
            f"store - {exc}. 'deploy report' will show the previous one.",
            code="report_store_record",
        )


def _write_handover(
    context: SolutionContext,
    deployment: DeploymentModel,
    manifest: DeploymentManifestModel,
    path: Path,
    diagnostics: Diagnostics,
    *,
    pin: VersionPins = None,
) -> Path | None:
    """Render `spec.documentation`'s handover document into `path`, with this run's own audit entry, and return
    `path`; `None` with a warning when it cannot be rendered. Never fails the run: the deploy has succeeded, and
    failing now would invite a redeploy that changes nothing. `strata deploy report` can render it again.
    """
    documentation = deployment.spec.documentation
    assert documentation is not None
    audit = audit_summary(manifest)
    try:
        template_source = (
            (context.root / documentation.template).read_text(encoding="utf-8") if documentation.template else None
        )
        report = deployment_report(context, deployment.meta.name, version_pin=pin)
        document = render_document(report, template_source=template_source, audit=audit)
        path.write_text(document, encoding="utf-8")
    except Exception as exc:  # the deploy succeeded: nothing here may fail it
        diagnostics.warning(
            f"Deployment '{deployment.meta.name}': the handover document (spec.documentation) was not written - "
            f"{exc}. The deploy succeeded; run 'strata deploy report --format markdown' to render it.",
            location="spec.documentation",
            code="documentation_render_failed",
        )
        return None
    return path


def _dispatch_sink(
    sink: AuditSinkModel,
    *,
    admitted_types: list[str],
    context: SolutionContext,
    manifest: DeploymentManifestModel,
    metrics: DeploymentMetricsModel,
    manifest_path: Path,
    metrics_path: Path,
    relative_path: Path,
    actor: str,
    api_version: PlatformVersion,
    diagnostics: Diagnostics,
    git_sink_values: Mapping[str, str] | None,
    handover_path: Path | None = None,
    sbom_path: Path | None = None,
) -> None:
    """Admission + dispatch for one sink — appends findings to `diagnostics` in place. A git sink also pushes
    the handover document, when `spec.documentation` wrote one, and the SBOM the manifest references, when it
    references one."""
    if not sink.enabled:
        return
    sink_events = [t for t in admitted_types if sink.events is None or t in sink.events]
    if not sink_events:
        return

    if sink.integration is not None:
        _dispatch_integration_sink(
            sink,
            sink_events=sink_events,
            context=context,
            manifest=manifest,
            metrics=metrics,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            relative_path=relative_path,
            api_version=api_version,
            diagnostics=diagnostics,
        )
        return

    assert sink.git is not None  # AuditSinkModel's own exactly-one-arm validator guarantees this
    files = {MANIFEST_FILENAME: manifest_path}
    if "deployment.measured" in sink_events:
        files[_METRICS_FILENAME] = metrics_path
    if handover_path is not None:
        files[HANDOVER_FILENAME] = handover_path
    if sbom_path is not None:
        files[SBOM_FILENAME] = sbom_path

    auth = _resolve_git_sink_auth(context, sink.git)
    result = push_audit_files(
        context.root,
        sink.git,
        context.controller.solution,
        files,
        relative_path,
        actor,
        auth=auth,
        resolved_values=git_sink_values,
    )
    if result.success:
        if sink.git.action != "push":
            # A path remote's `commit` or `none`: a later pipeline step pushes (docs/design/remote-commit-provenance.md,
            # "Writing to a path remote").
            diagnostics.info(f"sink '{sink.name}': {result.detail}", code="audit_sink_not_pushed")
        return
    _record_sink_failure(
        sink, f"sink '{sink.name}': audit push failed — {result.detail}", "audit_sink_push_failed", diagnostics
    )


def _resolve_git_sink_auth(context: SolutionContext, sink_git: AuditGitSinkTargetModel) -> AuthenticationModel | None:
    """`sink_git.remote`'s declared `integration` -> its `AuthenticationModel`,
    or `None` — same lookup `deploy_controller.py` already does for a GitOps
    remote's `remote.integration` (`IntegrationModel.spec.authentication`),
    applied here so an audit git sink stops being the one git-push path in
    this codebase that ignores strata's single credential mechanism.

    A missing/typo'd remote or integration name resolves to `None` (today's
    ambient-only behaviour), never raises — `remote.integration` is
    schema-declared but not existence-validated, matching `deploy_controller.
    py`'s own documented reasoning for the identical GitOps lookup.
    """
    remotes_by_name = (
        {remote.name: remote for remote in (solution.spec.remotes or [])}
        if (solution := context.controller.solution)
        else {}
    )
    remote = remotes_by_name.get(sink_git.remote)
    if remote is None or not remote.integration:
        return None
    integration_entry = context.controller.index.get(PlatformKind.INTEGRATION, remote.integration)
    if integration_entry is None:
        return None
    return cast(IntegrationModel, integration_entry.model).spec.authentication


def _dispatch_integration_sink(
    sink: AuditSinkModel,
    *,
    sink_events: list[str],
    context: SolutionContext,
    manifest: DeploymentManifestModel,
    metrics: DeploymentMetricsModel,
    manifest_path: Path,
    metrics_path: Path,
    relative_path: Path,
    api_version: PlatformVersion,
    diagnostics: Diagnostics,
) -> None:
    """Render and send each admitted event through one `integration` sink
    (docs/design/audit-sink-dispatch.md).

    Every failure mode here is reported through the sink's own `required`
    flag, never raised — a misconfigured audit sink must not take down a
    deploy that already succeeded, unless the solution explicitly said it
    should.
    """
    entry = context.controller.index.get(PlatformKind.INTEGRATION, sink.integration or "")
    if entry is None:
        _record_sink_failure(
            sink,
            f"sink '{sink.name}': references Integration '{sink.integration}', which does not exist.",
            "audit_sink_integration_missing",
            diagnostics,
        )
        return

    integration_model = cast(IntegrationModel, entry.model)
    if not integration_model.spec.enabled:
        return  # an explicitly disabled integration is a deliberate off-switch, not a failure

    try:
        integration = registry.get(integration_model.spec.type, integration_model)
    except IntegrationError as exc:
        _record_sink_failure(sink, f"sink '{sink.name}': {exc}", "audit_sink_integration_unavailable", diagnostics)
        return

    if not isinstance(integration, AuditSinkIntegration):
        _record_sink_failure(
            sink,
            f"sink '{sink.name}': Integration '{integration_model.meta.name}' (type "
            f"'{integration_model.spec.type}') does not provide the '{Capability.AUDIT}' capability.",
            "audit_sink_integration_not_capable",
            diagnostics,
        )
        return

    for event_type in sink_events:
        if event_type == "deployment.measured":
            event = render_metrics_event(
                metrics,
                relative_path=(relative_path / _METRICS_FILENAME).as_posix(),
                file_sha256=_sha256_of(metrics_path),
                api_version=api_version,
            )
        else:
            event = render_manifest_event(
                manifest,
                relative_path=(relative_path / MANIFEST_FILENAME).as_posix(),
                file_sha256=_sha256_of(manifest_path),
                api_version=api_version,
            )
        try:
            integration.send(event)
        except IntegrationError as exc:
            _record_sink_failure(sink, f"sink '{sink.name}': {exc}", "audit_sink_dispatch_failed", diagnostics)


def _record_sink_failure(sink: AuditSinkModel, message: str, code: str, diagnostics: Diagnostics) -> None:
    """One place deciding warn-vs-fail, so both sink arms can never drift apart.

    `required` is the whole decision (docs/design/audit-trail.md's "When
    delivery fails").
    """
    if sink.required:
        diagnostics.error(message, code=code)
    else:
        diagnostics.warning(message, code=code)


def _sha256_of(path: Path) -> str:
    """`sha256:`-prefixed digest of `path`, matching `_platform_reference()`'s own format."""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _single_configuration(context: SolutionContext) -> ConfigurationModel | None:
    """Same "one declared source of truth, or nothing to check against" rule
    `semantic_checks.py::_single_configuration()` already applies."""
    entries = context.controller.index.all_of(PlatformKind.CONFIGURATION)
    if len(entries) != 1:
        return None
    return cast(ConfigurationModel, entries[0].model)


def _single_environment(deployment: DeploymentModel) -> str | None:
    """The manifest's `environment` field needs one label; a deployment may
    reference several. Unambiguous only when exactly one is declared."""
    environments = deployment.spec.environments
    if environments and len(environments) == 1:
        return environments[0]
    return None


def _document_file(context: SolutionContext, deployment_name: str) -> str | None:
    """The deployment document's file, relative to the solution root; absolute for one that lives elsewhere (a
    document remote outside the solution)."""
    entry = context.controller.index.get(PlatformKind.DEPLOYMENT, deployment_name)
    if entry is None:
        return None
    try:
        return entry.source.resolve().relative_to(context.root.resolve()).as_posix()
    except ValueError:
        return entry.source.as_posix()


def _status(
    run_diagnostics: Diagnostics, stages: Sequence[ManifestStageModel]
) -> Literal["success", "partial", "failed"]:
    """`success` without errors; `partial` when the run failed after a stage succeeded, so something changed;
    `failed` otherwise (docs/design/audit-trail.md, "Stages")."""
    if run_diagnostics.ok:
        return "success"
    return "partial" if any(stage.status == "success" for stage in stages) else "failed"


def _pipeline() -> ManifestPipelineModel | None:
    """The CI run doing this deploy (docs/design/audit-trail.md, "The audit files"); `None` outside CI."""
    run = resolve_pipeline_run()
    return None if run is None else ManifestPipelineModel(**run._asdict())


def _collect_artifacts_bom(
    context: SolutionContext,
    deployment: DeploymentModel,
    diagnostics: Diagnostics,
    *,
    recorded: Mapping[str, ManifestRepositoryModel] | None = None,
) -> tuple[dict[str, ManifestRepositoryModel] | None, list[ManifestImageModel] | None, list[ManifestChartModel] | None]:
    """The effective (pin-resolved) repositories/images/charts this
    deployment's workspace actually reaches — the fix for ADR-0019's own
    flagged gap (docs/work/version-lifecycle.md's "The audit gap").

    Images come from `effective_images()` (module service images and artifacts, pins
    applied). Charts and repositories reuse the exact same overlay functions
    `build_run()` itself calls — `workload_controller.apply_version_pins()` and
    `build_controller.apply_remote_version_pins()` — one resolution path,
    not a second, independently-maintained copy of the same logic. Deliberately
    not read back from `sbom.json`, which is a build-time file that may be stale
    or missing at deploy.

    Scoped to what this deployment's own workspace reaches (its
    provisioners' and workload modules' `source.remote`, its namespaces'
    modules), never the whole solution's — a manifest records what this
    deployment actually used, not everything merely declared somewhere.

    Repositories: with the build's record (`recorded`, from `resolved.yaml`'s `remotes`), each remote it holds is
    copied as recorded, so the manifest names the tree that went into the build output, not the checkout as it is
    now (docs/design/remote-commit-provenance.md, "The audit manifest copies the record"). That adds what the record
    alone holds (an asset's
    `spec.location`, a Checkov config's remote). A remote this workspace names that the build took no file from
    (a chart-based module's) is listed as today, with no commit: there is no tree to record. Without a record (a
    build made before it existed) a path remote's commit is read from its checkout, as before.

    Returns:
        `(repositories, images, charts)`, each `None` when empty rather
        than an empty collection — `DeploymentManifestModel`'s own
        `exclude_none` serialisation then omits the key entirely, matching
        `ManifestArtifactsModel`'s existing optional fields.
    """
    index = context.controller.index
    solution = context.controller.solution
    version = resolve_version(context, deployment)

    workspace = deployment_workspace(index, deployment)
    if workspace is None:
        return None, None, None

    modules: list[ModuleModel] = []
    for namespace_name in workspace.spec.namespaces or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, namespace_name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            # Re-derived for the record: the build logged each pin when it applied it.
            modules.append(apply_version_pins(resolve_module(index, reference), version, log=False))

    images: list[ManifestImageModel] = []
    rows = effective_images(context, deployment)
    # Delivered when any use delivers it (docs/design/helm-image-delivery.md, "The SBOM: the declared-only marker"); only `false` is written.
    delivered = {row.image_ref for row in rows if row.delivered}
    seen_images: set[str] = set()
    for row in rows:
        if row.image_ref not in seen_images:
            seen_images.add(row.image_ref)
            images.append(
                ManifestImageModel(
                    name=row.name, image=row.image_ref, delivered=None if row.image_ref in delivered else False
                )
            )
    charts: list[ManifestChartModel] = []
    for module in modules:
        if module.spec.source.chart_name is not None:
            charts.append(
                ManifestChartModel(
                    name=module.meta.name,
                    chart=module.spec.source.chart_name,
                    version=module.spec.source.chart_version,
                )
            )

    remote_names: set[str] = set()
    for provisioner in workspace.spec.provisioners:
        if provisioner.source is not None and provisioner.source.remote is not None:
            remote_names.add(provisioner.source.remote)
    for module in modules:
        if module.spec.source.remote is not None:
            remote_names.add(module.spec.source.remote)

    repositories: dict[str, ManifestRepositoryModel] = {}
    if remote_names and solution is not None:
        all_remotes = {remote.name: remote for remote in (solution.spec.remotes or [])}
        scoped_remotes = {name: all_remotes[name] for name in remote_names if name in all_remotes}
        for name, remote in apply_remote_version_pins(scoped_remotes, version).items():
            if recorded is not None:
                repositories[name] = recorded.get(name) or ManifestRepositoryModel(url=remote.url, ref=remote.reference)
                continue
            commit = (
                _checkout_commit(name, layout.path_remote_location(context.root, remote.path), diagnostics)
                if remote.path
                else None
            )
            repositories[name] = ManifestRepositoryModel(url=remote.url, ref=remote.reference, commit=commit)
    for key, entry in (recorded or {}).items():
        repositories.setdefault(key, entry)

    return (dict(sorted(repositories.items())) or None, images or None, charts or None)


def _checkout_commit(name: str, checkout: Path, diagnostics: Diagnostics) -> str | None:
    """The commit a path remote's checkout is at, or `None` (with a warning) when it cannot be read.

    A path remote has no `reference`, so this is the only record of which tree a deploy used
    (docs/design/remotes.md). Requires `.git` in the directory itself: otherwise `git
    rev-parse` would walk up and report the solution repository's own commit for a plain folder.
    It never fails a deploy. The rule and the `git` call are `checkout_state()`'s
    (docs/design/remote-commit-provenance.md, "Reading a checkout").
    """
    state = checkout_state(checkout)
    if state.commit is None:
        diagnostics.warning(
            f"Remote '{name}': {state.problem}, so the audit manifest records no commit for it.",
            code="audit_remote_commit_unavailable",
        )
    return state.commit


def _platform_reference(build_path: Path) -> tuple[ManifestPlatformReferenceModel | None, Diagnostics]:
    """Hash + reference `build_path/resolved.yaml` — v2's closest real
    equivalent to v1's `platform.json` (see module docstring)."""
    diagnostics = Diagnostics()
    resolved_path = build_path / "resolved.yaml"
    if not resolved_path.exists():
        diagnostics.warning(
            f"No 'resolved.yaml' found at '{resolved_path}' — cannot build the audit manifest's required "
            "artifacts.platform reference; skipping audit finalize for this run.",
            code="audit_manifest_missing_platform_artifact",
        )
        return None, diagnostics
    file_hash = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
    return ManifestPlatformReferenceModel(hash=f"sha256:{file_hash}", path="resolved.yaml"), diagnostics


def _sbom_reference(build_path: Path, diagnostics: Diagnostics) -> tuple[ManifestSbomReferenceModel | None, bytes]:
    """Hash + reference `build_path/sbom.json`, re-derived fresh at deploy
    time — same reasoning as `_platform_reference()` (build and deploy are
    separate CLI invocations; there is no in-memory value to carry between
    them, only what `write_sbom()` already wrote to disk earlier).

    Also returns the bytes it hashed (empty when there is no reference), so the run's audit folder keeps a copy
    of exactly the file the manifest vouches for: the build directory is thrown away on CI, and a git sink pushes
    the copy next to the manifest (docs/design/audit-trail.md, "The git sink").

    Unlike `_platform_reference()`, a missing SBOM is not an error and
    raises no diagnostic — SBOM generation is itself optional (a workspace
    with nothing SBOM-relevant produces none at all), and `deploy run`
    without a preceding `build run` is a legitimate, if unusual, sequence.

    A file that is there but unusable is a warning, never a failure: the deploy has already run, so failing here
    would only lose its audit record. A corrupt file is still referenced and hashed, without a component count, so
    the record shows what was on disk. One that cannot be read at all is left out. Blocking a deploy on a bad SBOM
    belongs before it, in the gates that read it (`image_gate_policy.on_missing_data: block`).
    """
    sbom_path = build_path / SBOM_FILENAME
    if not sbom_path.exists():
        return None, b""
    try:
        sbom_bytes = sbom_path.read_bytes()
    except OSError as exc:
        diagnostics.warning(
            f"'{sbom_path}' cannot be read ({exc}), so the audit manifest records no SBOM for this run.",
            code="audit_sbom_unreadable",
        )
        return None, b""
    try:
        component_count: int | None = len(parse_sbom(sbom_bytes, sbom_path)["components"])
    except ValueError as exc:
        diagnostics.warning(
            f"The audit manifest references the SBOM without a component count: {exc}.",
            code="audit_sbom_unreadable",
        )
        component_count = None
    reference = ManifestSbomReferenceModel(
        path=SBOM_FILENAME,
        format=SBOM_FORMAT,
        sha256=f"sha256:{hashlib.sha256(sbom_bytes).hexdigest()}",
        component_count=component_count,
    )
    return reference, sbom_bytes


def _build_change_reference(
    *,
    system: str | None,
    change_id: str | None,
    reason: str | None,
    classification: str | None,
    title: str | None,
    url: str | None,
    approved_by: str | None,
    approved_at: str | None,
    actor: str,
    supplied_at: str,
) -> ChangeReferenceModel | None:
    """Build a `ChangeReferenceModel` from `deploy run --change-*` values, or
    `None` when none were supplied — fully optional, matching every other
    `spec.audit`-adjacent surface in this design.

    `system`/`id`/`reason` are `ChangeReferenceModel`'s own required fields;
    `deploy_command.py` enforces they're supplied together (a `UsageError`
    before `deploy_run()` executes otherwise) — trusted here, not
    re-validated, the same way a sink's own exactly-one-arm validator is
    trusted by `_dispatch_sink()` rather than re-checked. `approved_by`/
    `approved_at` are similarly enforced supplied-together by
    `deploy_command.py`, with `ChangeReferenceModel`'s own validator as a
    second line of defense (same belt-and-suspenders pattern).
    """
    if not system and not change_id and not reason:
        return None
    assert system and change_id and reason, "deploy_command.py must supply system/id/reason together or not at all"
    return ChangeReferenceModel(
        system=system,
        id=change_id,
        reason=reason,
        classification=classification,
        title=title,
        url=url,
        supplied_by=actor,
        supplied_at=supplied_at,
        approved_by=approved_by,
        approved_at=approved_at,
    )
