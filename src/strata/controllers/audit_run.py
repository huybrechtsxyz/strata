#!/usr/bin/env python3
"""Finalize and distribute a deploy run's audit trail — the end-of-run
sequencing (docs/design/audit-trail.md's "Write once locally, then
distribute").

Phase 5 of that doc's Layer 2 Implementation Plan — the final phase,
gluing Phases 1-4 into `deploy run`'s actual command flow. Deliberately a
NEW module, not a change to `deploy_controller.py::deploy_run()` itself:
that function's existing fail-fast, single-return-diagnostics shape is
complex, heavily tested, and load-bearing — this wraps its *result*
rather than restructuring its internals to expose finer-grained stage/step
data it doesn't have today. `stages`/per-step timing on the manifest are
therefore coarser than the full design in this pass: no `stages[]` at all
— revisited once `deploy_run()` itself is refactored to expose that data
(separate, larger work, not bundled into this phase).

`artifacts.platform` references `build_path/resolved.yaml`
(`build_controller.write_resolved_manifest()`) — v2's closest real
equivalent to v1's `platform.json`; v2 does not produce that exact file
today, so this is an honest adaptation, not a literal port.

`sbom` references `build_path/sbom.json` (`sbom_controller.write_sbom()`,
gap #18 Phase 1) the same way — re-hashed fresh here rather than threaded
through from the earlier `build run` process, since the two are separate
CLI invocations with no in-memory value to carry between them. Optional,
unlike `artifacts.platform`: a missing SBOM raises no diagnostic.

Only wired for `deploy` in this pass — no `destroy` command exists in v2
yet (the design doc's own Phase 5 checklist said "and the equivalent
destroy path" before this was checked against real code; there is no such
path to wire yet). Extend when `deploy destroy` is built.

Both sink arms now really dispatch: `git` pushes the written files
(Phase 4), and `integration` renders a CloudEvents+ECS envelope per
admitted event type and sends it through an `AuditSinkIntegration`
(docs/design/audit-sink-dispatch.md). Every integration-arm failure — a
missing/incapable Integration document as much as a failed send — is
reported through the sink's own `required` flag rather than raised, so a
misconfigured audit sink never takes down a deploy that already succeeded.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from strata import __version__
from strata.controllers.audit_event_rendering import render_manifest_event, render_metrics_event
from strata.controllers.audit_path_resolution import resolve_audit_relative_path
from strata.controllers.audit_push import push_audit_files
from strata.controllers.sbom_controller import SBOM_FORMAT
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment
from strata.integrations import registry
from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.audit_manifest_model import (
    ChangeReferenceModel,
    DeploymentManifestModel,
    ManifestArtifactsModel,
    ManifestPlatformReferenceModel,
    ManifestSbomReferenceModel,
)
from strata.models.audit_metrics_model import DeploymentMetricsModel, MetricsDimensionsModel, MetricsMeasuresModel
from strata.models.audit_model import EVENT_DEFAULTS, AuditSinkModel
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils import layout
from strata.utils.actor import resolve_actor
from strata.utils.diagnostics import Diagnostics, Severity

#: Public — `controllers/audit_read.py` (docs/design/audit-commands.md) reads
#: this back and must glob for the exact same filename this module writes.
MANIFEST_FILENAME = "_manifest.json"
_METRICS_FILENAME = "_metrics.json"


def finalize_and_distribute_deploy_audit(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    execution_id: str,
    started_at: datetime,
    run_diagnostics: Diagnostics,
    dry_run: bool = False,
    change_system: str | None = None,
    change_id: str | None = None,
    change_reason: str | None = None,
    change_classification: str | None = None,
    change_title: str | None = None,
    change_url: str | None = None,
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

    Returns:
        Findings from finalizing/writing/distributing only — never
        `run_diagnostics` itself (the caller already has and reports that
        separately); merge the two if a single combined view is needed.
    """
    diagnostics = Diagnostics()
    if dry_run:
        return diagnostics

    deployment = resolve_deployment(context, deployment_name)
    if deployment.spec.workspace is None:
        return diagnostics  # deploy_run() already failed and reported this; nothing to finalize

    platform_ref, platform_diagnostics = _platform_reference(build_path)
    diagnostics.extend(platform_diagnostics)
    if platform_ref is None:
        return diagnostics  # can't build a manifest without its one required artifact reference

    configuration = _single_configuration(context)
    completed_at = datetime.now(timezone.utc)
    duration_seconds = (completed_at - started_at).total_seconds()
    status: Literal["success", "failed"] = "success" if run_diagnostics.ok else "failed"
    actor = resolve_actor()
    environment = _single_environment(deployment)
    change_reference = _build_change_reference(
        system=change_system,
        change_id=change_id,
        reason=change_reason,
        classification=change_classification,
        title=change_title,
        url=change_url,
        actor=actor,
        supplied_at=completed_at.isoformat(),
    )

    manifest = DeploymentManifestModel(
        execution_id=execution_id,
        action="deploy",
        version=str(__version__),
        deployment=deployment_name,
        workspace=deployment.spec.workspace,
        environment=environment,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_seconds=duration_seconds,
        status=status,
        dry_run=False,
        deployed_by=actor,
        artifacts=ManifestArtifactsModel(platform=platform_ref),
        sbom=_sbom_reference(build_path),
        change_reference=change_reference,
        errors=run_diagnostics.messages(Severity.ERROR) or None,
    )
    metrics = DeploymentMetricsModel(
        execution_id=execution_id,
        deployment=deployment_name,
        workspace=deployment.spec.workspace,
        timestamp=started_at.isoformat(),
        dimensions=MetricsDimensionsModel(outcome=status, action="deploy", environment=environment, actor=actor),
        measures=MetricsMeasuresModel(
            duration_seconds=duration_seconds, stages_total=1, stages_failed=0 if status == "success" else 1
        ),
    )

    relative_path = resolve_audit_relative_path(deployment, configuration, started_at.isoformat())
    local_dir = layout.audit_dir(context.root) / relative_path
    local_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = local_dir / MANIFEST_FILENAME
    metrics_path = local_dir / _METRICS_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    metrics_path.write_text(metrics.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    audit_config = configuration.spec.audit if configuration is not None else None
    if audit_config is None or not audit_config.sinks:
        return diagnostics

    event_enabled = {**EVENT_DEFAULTS, **(audit_config.event_overrides or {})}
    admitted_types = [t for t in ("deployment.completed", "deployment.measured") if event_enabled.get(t, True)]

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
        )

    return diagnostics


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
) -> None:
    """Admission + dispatch for one sink — appends findings to `diagnostics` in place."""
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

    result = push_audit_files(context.root, sink.git, context.controller.solution, files, relative_path, actor)
    if result.success:
        return
    _record_sink_failure(
        sink, f"sink '{sink.name}': audit push failed — {result.detail}", "audit_sink_push_failed", diagnostics
    )


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

    `required` is the whole decision (docs/design/audit-trail.md's "Dispatch
    failure — a `required` flag, not a binary silent-vs-fail choice").
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


def _sbom_reference(build_path: Path) -> ManifestSbomReferenceModel | None:
    """Hash + reference `build_path/sbom.json`, re-derived fresh at deploy
    time — same reasoning as `_platform_reference()` (build and deploy are
    separate CLI invocations; there is no in-memory value to carry between
    them, only what `write_sbom()` already wrote to disk earlier).

    Unlike `_platform_reference()`, a missing SBOM is not an error and
    raises no diagnostic — SBOM generation is itself optional (a workspace
    with nothing SBOM-relevant produces none at all), and `deploy run`
    without a preceding `build run` is a legitimate, if unusual, sequence.
    """
    sbom_path = build_path / "sbom.json"
    if not sbom_path.exists():
        return None
    sbom_bytes = sbom_path.read_bytes()
    file_hash = hashlib.sha256(sbom_bytes).hexdigest()
    component_count = len(json.loads(sbom_bytes).get("components", []))
    return ManifestSbomReferenceModel(
        path="sbom.json",
        format=SBOM_FORMAT,
        sha256=f"sha256:{file_hash}",
        component_count=component_count,
    )


def _build_change_reference(
    *,
    system: str | None,
    change_id: str | None,
    reason: str | None,
    classification: str | None,
    title: str | None,
    url: str | None,
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
    trusted by `_dispatch_sink()` rather than re-checked.
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
    )
