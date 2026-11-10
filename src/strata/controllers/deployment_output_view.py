#!/usr/bin/env python3
"""What `strata deploy output` shows: each step's recorded outputs, read from the workspace's `output_store`
(docs/design/deployment-documentation.md, section 1). The `terraform output` equivalent.

Read-only. Never calls a provisioner, never reads Terraform state, never reads a secret. Values come from the output
record (ADR-0057); sensitive output names and descriptions from the report record. A sensitive value is never
stored, so it is never shown.

Steps come from the workspace's current `execution` list, so a step removed from the configuration is not shown
even if the store still has a record for it. Old records are shown, not refused: `output_store_max_age` protects a
deploy from consuming a stale value, and here only adds a warning.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from strata.controllers.deployment_output_store import (
    DeploymentOutputStore,
    OutputRecordDestroyedError,
    OutputRecordNotFoundError,
    OutputRecordOmittedError,
)
from strata.controllers.deployment_report_store import DeploymentReportStore, StepReport
from strata.controllers.integration_resolution import resolve_output_store_integration
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.models.deployment_model import DeploymentModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError, ValidationError

#: Shown in place of a sensitive output's value. Matches `terraform output`.
SENSITIVE_PLACEHOLDER = "<sensitive>"


@dataclass(frozen=True)
class OutputRow:
    """One output. `value` is `None` for a sensitive output: it is never stored."""

    name: str
    value: str | None
    sensitive: bool
    description: str | None


@dataclass(frozen=True)
class StepOutputs:
    """One step's outputs.

    `status`: `recorded`, `not_recorded` (never deployed with an output store), `too_large` (its last deploy's
    outputs were larger than the output store accepts, so none were recorded; `omitted_at` says when) or
    `destroyed`.
    `report`: the report record, which holds sensitive names and descriptions: `recorded`, `not_recorded` (an
    older strata deployed the step, or its write failed), `mismatch` (from a different deploy than the values:
    an earlier one, or a later one whose output write failed) or `destroyed`.
    """

    step: str
    status: str
    collected_at: str | None = None
    destroyed_at: str | None = None
    omitted_at: str | None = None
    report: str = "not_recorded"
    truncated: bool = False
    outputs: list[OutputRow] = field(default_factory=list)


@dataclass(frozen=True)
class OutputStores:
    """A deployment, its workspace, and the two stores `deploy run` writes for it."""

    deployment: DeploymentModel
    workspace: WorkspaceModel
    outputs: DeploymentOutputStore
    reports: DeploymentReportStore


@dataclass
class DeploymentOutputs:
    deployment: str
    steps: list[StepOutputs]
    diagnostics: Diagnostics


def status_line(step: StepOutputs) -> str:
    """The one console line that opens a step in `deploy output` and `deploy report`."""
    if step.status == "destroyed":
        return f"{step.step}  destroyed {step.destroyed_at}"
    if step.status == "too_large":
        return f"{step.step}  no outputs recorded: too large for the output store (deploy at {step.omitted_at})"
    if step.status != "recorded":
        return f"{step.step}  no outputs recorded"
    return f"{step.step}  collected {step.collected_at}"


def parse_output_address(address: str | None, step_names: list[str]) -> tuple[str | None, str | None]:
    """Split `STEP` or `STEP.KEY` (the `${output:STEP.KEY}` address) into `(step, key)`.

    The first `.` separates the step from the key, as in an `${output:}` token. A whole address that names a step
    is that step.

    Raises:
        UsageError: the step is not in the workspace's `execution` list.
    """
    if not address:
        return None, None
    if address in step_names:
        return address, None
    step, dot, key = address.partition(".")
    if step not in step_names:
        known = ", ".join(step_names) or "none"
        raise UsageError(f"No execution step '{step}' in this deployment's workspace. Steps: {known}.")
    if not dot or not key:
        raise UsageError(f"'{address}' is not STEP or STEP.KEY.")
    return step, key


def deployment_outputs(context: SolutionContext, deployment_name: str, address: str | None = None) -> DeploymentOutputs:
    """Read the recorded outputs of `deployment_name`'s steps, all of them or the one `address` names.

    Raises:
        UsageError: the deployment does not exist; `address` names an unknown step; or `address` is a
            `STEP.KEY` whose step has no record or no such output.
        ValidationError: the workspace sets no `output_store`.
        strata.utils.errors.SystemError: a stored record is not valid JSON.
        strata.integrations.errors.IntegrationError: the output store is unreachable or unauthenticated.
    """
    stores = open_output_stores(context, deployment_name)
    step_names = [step.name for step in stores.workspace.spec.execution or []]
    step_filter, key_filter = parse_output_address(address, step_names)
    steps, _, diagnostics = read_steps(stores, [name for name in step_names if step_filter in (None, name)])
    if key_filter is not None:
        steps = [_only_key(step, key_filter) for step in steps]
    return DeploymentOutputs(deployment=stores.deployment.meta.name, steps=steps, diagnostics=diagnostics)


def open_output_stores(
    context: SolutionContext, deployment_name: str, *, version_pin: str | None = None
) -> OutputStores:
    """Resolve `deployment_name` (with `version_pin`, as `deploy run --pin` does) and open its output and report
    stores.

    Raises:
        UsageError: the deployment does not exist, or names no workspace.
        ValidationError: the workspace sets no `output_store`.
    """
    deployment, workspace = resolve_deployment_workspace(context, deployment_name, version_pin=version_pin)
    backend = resolve_output_store_integration(context.controller.index, workspace)
    if backend is None:
        missing = Diagnostics()
        missing.error(
            f"Workspace '{workspace.meta.name}' sets no output_store, so no step outputs are recorded. Set "
            "'output_store' (or 'output_store_integration') on the workspace and deploy again.",
            location=workspace.meta.name,
            code="output_store_missing",
        )
        raise ValidationError(missing, missing.errors[0].message)
    return OutputStores(
        deployment=deployment,
        workspace=workspace,
        outputs=DeploymentOutputStore(backend, deployment.meta.name),
        reports=DeploymentReportStore(backend, deployment.meta.name),
    )


def read_steps(
    stores: OutputStores, names: list[str]
) -> tuple[list[StepOutputs], dict[str, StepReport | None], Diagnostics]:
    """Each named step's outputs, and its report record as read: one read of each record per step.

    Raises:
        strata.utils.errors.SystemError: a stored record is not valid JSON.
        strata.integrations.errors.IntegrationError: the output store is unreachable or unauthenticated.
    """
    max_age = stores.workspace.spec.output_store_max_age
    diagnostics = Diagnostics()
    steps: list[StepOutputs] = []
    reports: dict[str, StepReport | None] = {}
    for name in names:
        step, reports[name] = _read_step(stores.outputs, stores.reports, name)
        _add_warnings(step, max_age, diagnostics)
        steps.append(step)
    return steps, reports, diagnostics


def _read_step(
    output_store: DeploymentOutputStore, report_store: DeploymentReportStore, name: str
) -> tuple[StepOutputs, StepReport | None]:
    report = report_store.read(name)
    try:
        record = output_store.read_record(name)
    except OutputRecordDestroyedError as exc:
        return StepOutputs(step=name, status="destroyed", destroyed_at=exc.destroyed_at, report="destroyed"), report
    except OutputRecordOmittedError as exc:
        return StepOutputs(step=name, status="too_large", omitted_at=exc.omitted_at), report
    except OutputRecordNotFoundError:
        return StepOutputs(step=name, status="not_recorded"), report

    report_state = _report_state(report, record.collected_at)
    current = report if report_state == "recorded" else None
    sensitive = set(current.sensitive_keys) if current else set()
    descriptions = current.descriptions if current else {}
    rows = [
        OutputRow(
            name=key,
            value=None if key in sensitive else record.values.get(key),
            sensitive=key in sensitive,
            description=descriptions.get(key),
        )
        for key in sorted(set(record.values) | sensitive)
    ]
    step = StepOutputs(
        step=name,
        status="recorded",
        collected_at=record.collected_at.isoformat(),
        report=report_state,
        truncated=bool(current and current.truncated),
        outputs=rows,
    )
    return step, report


def _report_state(report: StepReport | None, collected_at: datetime) -> str:
    if report is None:
        return "not_recorded"
    if report.destroyed_at is not None:
        return "destroyed"
    # One deploy writes both records with the same `collected_at`: any difference means two deploys.
    if report.collected_at != collected_at:
        return "mismatch"
    return "recorded"


def _add_warnings(step: StepOutputs, max_age: int | None, diagnostics: Diagnostics) -> None:
    if step.status != "recorded":
        return
    if step.report != "recorded":
        diagnostics.warning(
            f"Step '{step.step}': no report data from its last deploy (an earlier strata version deployed it, or "
            "the write failed), so sensitive output names and descriptions are not shown. Deploy the step again "
            "to record them.",
            location=step.step,
            code="report_not_recorded",
        )
    if max_age is not None and step.collected_at is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(step.collected_at)
        if age > timedelta(seconds=max_age):
            diagnostics.warning(
                f"Step '{step.step}': its outputs were collected at {step.collected_at}, longer ago than the "
                f"workspace's output_store_max_age ({timedelta(seconds=max_age)}). A deploy would refuse them; "
                "deploy the step again to refresh them.",
                location=step.step,
                code="output_record_old",
            )


def _only_key(step: StepOutputs, key: str) -> StepOutputs:
    if step.status == "destroyed":
        raise UsageError(f"Step '{step.step}' was destroyed at {step.destroyed_at}; it has no outputs.")
    if step.status != "recorded":
        raise UsageError(
            f"Step '{step.step}' has no outputs recorded yet: it has not been deployed with this output store."
        )
    rows = [row for row in step.outputs if row.name == key]
    if not rows:
        known = ", ".join(row.name for row in step.outputs) or "none"
        raise UsageError(f"Step '{step.step}' has no output '{key}'. Outputs: {known}.")
    return StepOutputs(
        step=step.step,
        status=step.status,
        collected_at=step.collected_at,
        report=step.report,
        truncated=step.truncated,
        outputs=rows,
    )
