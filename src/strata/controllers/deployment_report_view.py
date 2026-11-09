#!/usr/bin/env python3
"""What `strata deploy report` shows: the handover document's data (docs/work/deployment-documentation.md,
sections 1 and 2).

Read-only, like `deploy output`, and built on it: the step outputs come from `deployment_outputs()`. On top of
that, each step's report record says which secrets it used and where each lives, and the deployment's effective
images come from `effective_images()` (offline). Nothing here reads a secret value, so nothing here can print
one: every secret row's value is `MASK`, for every store, also an empty or unresolvable one.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from strata.controllers.deployment_output_view import StepOutputs, deployment_outputs
from strata.controllers.deployment_report_store import DeploymentReportStore, StepReport
from strata.controllers.effective_images import effective_images
from strata.controllers.integration_resolution import resolve_output_store_integration
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError

#: Every secret's value, and every sensitive output's, in the document. Never anything else.
MASK = "********"


@dataclass(frozen=True)
class SecretRow:
    """One secret a reported step used, by location. `value` is always `MASK`."""

    key: str
    store: str
    location: dict[str, str | None]
    where: str
    description: str | None
    declared_in: str | None
    used_by: list[str]
    value: str = MASK


@dataclass(frozen=True)
class ReportStep:
    """One step: its outputs (as `deploy output` shows them) and the state of its secrets record.

    `secrets`: `recorded`, `not_recorded` (never deployed with this output store, an earlier strata version
    deployed it, or the write failed), `older` (from an earlier deploy than the outputs) or `destroyed`.
    `all_secrets`: the step receives every declared secret (a script without `script.values`, a template that
    reads `secrets` dynamically, or a container tool that cannot tell).
    """

    step: str
    outputs: StepOutputs
    secrets: str
    all_secrets: bool = False
    secret_keys: list[str] = field(default_factory=list)


@dataclass
class DeploymentReport:
    deployment: str
    workspace: str
    environments: list[str]
    steps: list[ReportStep]
    secrets: list[SecretRow]
    images: list[dict[str, Any]]
    diagnostics: Diagnostics
    #: The last successful deploy's audit summary from the output store, or `None` before the first one.
    audit: dict[str, Any] | None = None

    def to_data(self) -> dict[str, Any]:
        """The `--output json` data, and the template context in a later release. No secret value anywhere."""
        return {
            "deployment": self.deployment,
            "workspace": self.workspace,
            "environments": self.environments,
            "steps": [
                {
                    **asdict(step.outputs),
                    "secrets": step.secrets,
                    "all_secrets": step.all_secrets,
                    "secret_keys": step.secret_keys,
                }
                for step in self.steps
            ],
            "secrets": [asdict(row) for row in self.secrets],
            "images": self.images,
            "audit": self.audit,
        }


def deployment_report(
    context: SolutionContext, deployment_name: str, steps: list[str] | None = None
) -> DeploymentReport:
    """Gather the handover data for `deployment_name`, for every execution step or only `steps`.

    Raises:
        UsageError: the deployment does not exist, or `steps` names a step that is not in its workspace.
        ValidationError: the workspace sets no `output_store`.
        strata.utils.errors.SystemError: a stored record is not valid JSON.
        strata.integrations.errors.IntegrationError: the output store is unreachable or unauthenticated.
    """
    deployment, workspace = resolve_deployment_workspace(context, deployment_name)
    outputs = deployment_outputs(context, deployment_name)
    known = [step.step for step in outputs.steps]
    unknown = sorted(set(steps or []) - set(known))
    if unknown:
        raise UsageError(
            f"No execution step {', '.join(repr(name) for name in unknown)} in this deployment's workspace. "
            f"Steps: {', '.join(known) or 'none'}."
        )
    selected = [step for step in outputs.steps if not steps or step.step in steps]

    backend = resolve_output_store_integration(context.controller.index, workspace)
    assert backend is not None  # deployment_outputs() already raised without one
    report_store = DeploymentReportStore(backend, deployment.meta.name)
    diagnostics = outputs.diagnostics

    report_steps: list[ReportStep] = []
    rows: dict[str, SecretRow] = {}
    for step in selected:
        report = report_store.read(step.step)
        state = _secrets_state(step, report)
        if state in ("not_recorded", "older") and (step.status == "recorded" or report is not None):
            diagnostics.warning(
                f"Step '{step.step}': which secrets it used is not recorded"
                + (" for its last deploy" if state == "older" else "")
                + " (an earlier strata version deployed it, or the write failed). Deploy the step again to record "
                "them; until then this document may leave out secrets it uses.",
                location=step.step,
                code="report_secrets_not_recorded",
            )
        uses = report.secrets if report is not None and state == "recorded" and report.secrets else ()
        for use in uses:
            existing = rows.get(use.key)
            if existing is not None:
                existing.used_by.append(step.step)
                continue
            rows[use.key] = SecretRow(
                key=use.key,
                store=use.store,
                location=use.location,
                where=render_location(use.store, use.location, use.declared_in),
                description=use.description,
                declared_in=use.declared_in,
                used_by=[step.step],
            )
        report_steps.append(
            ReportStep(
                step=step.step,
                outputs=step,
                secrets=state,
                all_secrets=bool(report and state == "recorded" and report.all_secrets),
                secret_keys=sorted(use.key for use in uses),
            )
        )

    secrets = sorted(rows.values(), key=lambda row: row.key)
    for row in secrets:
        if row.store == "constant":
            diagnostics.warning(
                f"Secret '{row.key}' is held in source ({row.declared_in or 'an environment document'}), so it cannot "
                "be handed over by location. Move it to a vault.",
                location=row.key,
                code="secret_held_in_source",
            )
        elif any(value is None for value in row.location.values()):
            diagnostics.warning(
                f"Secret '{row.key}': its location is not fully known (a connection setting was not set where the "
                "deploy ran); the document shows 'unknown'.",
                location=row.key,
                code="secret_location_unknown",
            )

    images = [asdict(image) for image in effective_images(context, deployment, pinned_by_flag=False)]
    return DeploymentReport(
        deployment=str(deployment.meta.name),
        workspace=str(workspace.meta.name),
        environments=[str(name) for name in deployment.spec.environments or []],
        steps=report_steps,
        secrets=secrets,
        images=images,
        diagnostics=diagnostics,
        audit=report_store.read_last_deploy(),
    )


def _secrets_state(step: StepOutputs, report: StepReport | None) -> str:
    if report is None or (report.secrets is None and report.destroyed_at is None):
        return "not_recorded"
    if report.destroyed_at is not None:
        return "destroyed"
    if step.collected_at is not None and (
        report.collected_at is None or report.collected_at < datetime.fromisoformat(step.collected_at)
    ):
        return "older"
    return "recorded"


def render_location(store: str, location: dict[str, str | None], declared_in: str | None) -> str:
    """One line saying where to find a secret, from its structured location. Never a value."""

    def _get(name: str) -> str:
        return location.get(name) or "unknown"

    if store == "constant":
        return f"held in source ({declared_in or 'unknown'}), move to a vault"
    if store == "azure-keyvault":
        vault = (location.get("vault") or "unknown").removeprefix("https://").rstrip("/")
        return f"{vault} / {_get('name')} (latest)"
    if store == "infisical":
        return (
            f"{_get('address')} / project {_get('project_id')} / env {_get('environment')} / "
            f"{_get('path')} / {_get('name')}"
        )
    if store == "github":
        return f"GitHub Actions secret, read from env var {_get('env')}; see the workflow for its source"
    if store == "environment":
        return f"env var {_get('env')} (set on the deploy runner)"
    return _get("name")
