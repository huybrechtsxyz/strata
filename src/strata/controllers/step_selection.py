#!/usr/bin/env python3
"""Which execution steps a `--stage`/`--scope` selection keeps (docs/design/deploy-steps-command.md).

One function for every deploy command and `deploy steps`, so the steps a pipeline lists are the steps it runs.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from strata.models.provisioning_model import ProvisioningStepModel


@dataclass(frozen=True)
class StepSelection:
    """The kept steps, in input order, and the filter values that name no step at all."""

    steps: list[ProvisioningStepModel]
    unmatched_stages: list[str] = field(default_factory=list)
    unmatched_scopes: list[str] = field(default_factory=list)


def select_steps(
    steps: Sequence[ProvisioningStepModel], *, stages: Sequence[str] = (), scopes: Sequence[str] = ()
) -> StepSelection:
    """Keep the steps that match one of `stages` (by name) and one of `scopes` (by `scope:` label).

    OR within a filter, AND across the two; an empty filter keeps everything. An empty result is not an error.
    A value is unmatched when no step in `steps` has that name or label, so a stage that exists but is excluded
    by `scopes` is not reported.
    """
    stage_set, scope_set = set(stages), set(scopes)
    kept = [
        step
        for step in steps
        if (not stage_set or step.name in stage_set) and (not scope_set or step.scope in scope_set)
    ]
    names = {step.name for step in steps}
    labels = {step.scope for step in steps if step.scope is not None}
    return StepSelection(
        steps=kept,
        unmatched_stages=_unique(value for value in stages if value not in names),
        unmatched_scopes=_unique(value for value in scopes if value not in labels),
    )


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
