#!/usr/bin/env python3
"""What each provisioning step of a deploy did, for the audit manifest's `stages` (docs/design/audit-trail.md,
"Stages").

`deploy_run()` keeps its one return value, the run's `Diagnostics`, and reports through this log as it goes: which
steps it means to run (`expect`), when each one starts (`begin`) and which phase it is in (`phase`). Everything
else is worked out at the end from the run's own findings, so the loop needs no bookkeeping at its many early
returns:

- a stage the loop left open returned out of it, which it only does after an error, so it failed;
- a closed stage failed when one of its phases did or when an error names the step (`location`);
- a stage never begun is skipped, unless a preflight error names it, which fails it.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from strata.controllers.verification import all_verified
from strata.models.audit_manifest_model import ManifestCheckModel, ManifestStageModel, ManifestStepModel
from strata.utils.diagnostics import Diagnostics, Severity

#: Why a step that never started did not run.
NOT_REACHED_AFTER_FAILURE = "an earlier step failed"
NOT_REACHED = "the run stopped before this step"


@dataclass
class _Phase:
    name: str
    errors_before: int
    started: float
    duration: float | None = None
    success: bool | None = None


@dataclass
class _Stage:
    name: str
    provisioner: str | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    started: float | None = None
    duration: float | None = None
    ran_to_end: bool = False
    skip_reason: str | None = None
    phases: list[_Phase] = field(default_factory=list)
    checks: list[ManifestCheckModel] = field(default_factory=list)


class StageLog:
    """The per-step record one `deploy_run()` fills; read it with `stages()` once the run returned."""

    def __init__(self) -> None:
        self._stages: dict[str, _Stage] = {}
        self._current: _Stage | None = None
        self._diagnostics = Diagnostics()
        self._deployment_checks: list[ManifestCheckModel] = []

    def expect(self, steps: Sequence[tuple[str, str | None]], diagnostics: Diagnostics) -> None:
        """The steps this run means to run, in order, as `(step, provisioner)`; `diagnostics` is the run's own bag,
        read to tell whether a phase added an error."""
        self._diagnostics = diagnostics
        self._stages = {name: _Stage(name, provisioner) for name, provisioner in steps}

    def begin(self, step: str) -> None:
        """`step` starts; the stage before it ran to its end."""
        self._close()
        stage = self._stages.setdefault(step, _Stage(step, None))
        stage.started_at = datetime.now(timezone.utc)
        stage.started = time.monotonic()
        self._current = stage

    def phase(self, name: str) -> None:
        """The current stage enters phase `name` (`setup`, `check`, `plan`, `apply` or `destroy`); the phase before
        it ended."""
        if self._current is None:
            return
        self.end_phase()
        self._current.phases.append(_Phase(name, len(self._diagnostics.errors), time.monotonic()))

    def end_phase(self) -> None:
        """The current phase ended, so work after it (collecting outputs) is not counted in it."""
        if self._current is not None and self._current.phases:
            self._end(self._current.phases[-1])

    def record_checks(self, step: str, checks: Sequence[ManifestCheckModel]) -> None:
        """The answers of `step`'s verify checks (docs/design/post-deploy-verification.md, D9)."""
        self._stages.setdefault(step, _Stage(step, None)).checks.extend(checks)

    def record_deployment_checks(self, checks: Sequence[ManifestCheckModel]) -> None:
        """The answers of the deployment's own `spec.verify` checks, run after every step (D2, D9)."""
        self._deployment_checks.extend(checks)

    def deployment_checks(self) -> list[ManifestCheckModel]:
        """What `record_deployment_checks()` recorded: the manifest's top-level `checks`."""
        return list(self._deployment_checks)

    def verified(self) -> bool | None:
        """The manifest's `verified`: `None` when no check ran (none declared, `--no-verify`, or the run stopped
        first), `True` when every check that ran answered pass or warn, `False` when one failed or could not
        answer, whatever its `enforcement` made of it. Whether every step ran is the manifest's `status`."""
        return all_verified(
            [*(check for stage in self._stages.values() for check in stage.checks), *self._deployment_checks]
        )

    def skip(self, step: str, reason: str) -> None:
        """`step` does not run, for `reason`."""
        self._close()
        self._stages.setdefault(step, _Stage(step, None)).skip_reason = reason

    def finish(self) -> None:
        """The loop ran to its end."""
        self._close()

    def stages(self, diagnostics: Diagnostics) -> list[ManifestStageModel]:
        """One stage per expected or begun step, in run order. `diagnostics` is what the run returned."""
        # A stage still open here is the one the loop returned out of; it ended when the run did.
        self._close(ran_to_end=False)
        stages: list[ManifestStageModel] = []
        failed_before = False
        for stage in self._stages.values():
            located = [item for item in diagnostics if item.location == stage.name]
            errors = [str(item) for item in located if item.severity is Severity.ERROR]
            status: Literal["success", "failed", "skipped"]
            if stage.started is None:
                status = "failed" if errors else "skipped"
                reason = stage.skip_reason or (NOT_REACHED_AFTER_FAILURE if failed_before else NOT_REACHED)
            else:
                phase_failed = any(phase.success is False for phase in stage.phases)
                status = "success" if stage.ran_to_end and not phase_failed and not errors else "failed"
                reason = None
            failed_before = failed_before or status == "failed"
            stages.append(
                ManifestStageModel(
                    name=stage.name,
                    provisioner=stage.provisioner,
                    status=status,
                    skip_reason=reason if status == "skipped" else None,
                    started_at=None if stage.started_at is None else stage.started_at.isoformat(),
                    completed_at=None if stage.completed_at is None else stage.completed_at.isoformat(),
                    duration_seconds=stage.duration,
                    steps=[
                        ManifestStepModel(step=phase.name, success=bool(phase.success), duration_seconds=phase.duration)
                        for phase in stage.phases
                    ]
                    or None,
                    checks=list(stage.checks) or None,
                    errors=errors or None,
                    warnings=[str(item) for item in located if item.severity is Severity.WARNING] or None,
                    messages=[str(item) for item in located if item.severity is Severity.INFO] or None,
                )
            )
        return stages

    def _end(self, phase: _Phase) -> None:
        if phase.duration is None:
            phase.duration = round(time.monotonic() - phase.started, 3)
            phase.success = len(self._diagnostics.errors) == phase.errors_before

    def _close(self, *, ran_to_end: bool = True) -> None:
        stage = self._current
        if stage is None:
            return
        for phase in stage.phases:
            self._end(phase)
        stage.completed_at = datetime.now(timezone.utc)
        if stage.started is not None:
            stage.duration = round(time.monotonic() - stage.started, 3)
        stage.ran_to_end = ran_to_end
        self._current = None
