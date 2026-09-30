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

Only wired for `deploy` in this pass — no `destroy` command exists in v2
yet (the design doc's own Phase 5 checklist said "and the equivalent
destroy path" before this was checked against real code; there is no such
path to wire yet). Extend when `deploy destroy` is built.

Never dispatches through the `integration` sink arm — produces one
`Severity.INFO` finding per configured integration sink instead, naming it
explicitly so a config author sees the sink is inert rather than silently
assuming it forwards (see "Capability gating" and this module's own
`_admit_sinks`).
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from strata import __version__
from strata.controllers.audit_path_resolution import resolve_audit_relative_path
from strata.controllers.audit_push import push_audit_files
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment
from strata.models.audit_manifest_model import (
    DeploymentManifestModel,
    ManifestArtifactsModel,
    ManifestPlatformReferenceModel,
)
from strata.models.audit_metrics_model import DeploymentMetricsModel, MetricsDimensionsModel, MetricsMeasuresModel
from strata.models.audit_model import EVENT_DEFAULTS, AuditSinkModel
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.utils import layout
from strata.utils.actor import resolve_actor
from strata.utils.diagnostics import Diagnostics, Severity

_MANIFEST_FILENAME = "_manifest.json"
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
    manifest_path = local_dir / _MANIFEST_FILENAME
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
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            relative_path=relative_path,
            actor=actor,
            diagnostics=diagnostics,
        )

    return diagnostics


def _dispatch_sink(
    sink: AuditSinkModel,
    *,
    admitted_types: list[str],
    context: SolutionContext,
    manifest_path: Path,
    metrics_path: Path,
    relative_path: Path,
    actor: str,
    diagnostics: Diagnostics,
) -> None:
    """Admission + dispatch for one sink — appends findings to `diagnostics` in place."""
    if not sink.enabled:
        return
    sink_events = [t for t in admitted_types if sink.events is None or t in sink.events]
    if not sink_events:
        return

    if sink.integration is not None:
        diagnostics.info(
            f"sink '{sink.name}': integration dispatch is not implemented yet "
            "(docs/design/audit-trail.md's Layer 2 Implementation Plan) — this sink is configured but inert.",
            code="audit_integration_sink_not_dispatched",
        )
        return

    assert sink.git is not None  # AuditSinkModel's own exactly-one-arm validator guarantees this
    files = {_MANIFEST_FILENAME: manifest_path}
    if "deployment.measured" in sink_events:
        files[_METRICS_FILENAME] = metrics_path

    result = push_audit_files(context.root, sink.git, context.controller.solution, files, relative_path, actor)
    if result.success:
        return
    message = f"sink '{sink.name}': audit push failed — {result.detail}"
    if sink.required:
        diagnostics.error(message, code="audit_sink_push_failed")
    else:
        diagnostics.warning(message, code="audit_sink_push_failed")


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
