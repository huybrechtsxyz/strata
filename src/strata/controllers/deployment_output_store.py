#!/usr/bin/env python3
"""`DeploymentOutputStore` — the backend-agnostic business logic layer on
top of `KeyValueStoreIntegration` (docs/work/cross-run-step-output-delivery.md's
Implementation Plan steps 2-3).

Not itself an `Integration` — plain code wrapping one already-resolved
`KeyValueStoreIntegration` instance (`resolve_output_store_integration()`'s
job, not this module's). Owns exactly what every backend would otherwise
have to duplicate: the record's own JSON shape, the sensitive-key filter,
the merge-on-write (read-modify-write with CAS retry), and the staleness
check on read — written once here, reused by every backend.

One record per deployment (settled), not one per step — a single small
JSON document holding every step's outputs collected so far, keyed
internally by step name:

```jsonc
{
  "step1": {
    "collected_at": "2026-10-11T14:00:00+00:00",
    "values": {"vm_ip": "10.0.0.5"}
  }
}
```

Key naming: `strata/deployment-outputs/<deployment-name>` (settled convention,
`/`-delimited — see that doc's own reasoning for why).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration
from strata.integrations.errors import KeyValueConflictError
from strata.utils.errors import SystemError, UsageError

#: `/`-delimited namespace prefix so an operator sharing one backend
#: across unrelated purposes doesn't collide with something else living
#: in it (docs/work/cross-run-step-output-delivery.md's Implementation
#: Plan step 2).
_RECORD_KEY_PREFIX = "strata/deployment-outputs/"

#: How many times `record()` retries its read-modify-write loop on a
#: `KeyValueConflictError` before giving up. Only matters in practice when
#: two *unrelated* steps in two parallel pipelines finish at nearly the
#: same instant and both read-modify-write the same record — dependent
#: steps are sequential by construction and never race with each other,
#: so real contention here is rare; a handful of retries is plenty.
_MAX_CAS_RETRIES = 5


class OutputRecordNotFoundError(UsageError):
    """No durable record exists for this deployment at all, or this
    deployment's record has no entry for the requested step.

    Distinguishing "never ran in any invocation" from "ran in this
    invocation" is the caller's job — `DeploymentOutputStore.read()` is
    only ever consulted as the fallback once an in-memory lookup already
    missed (docs/work/cross-run-step-output-delivery.md)."""


class OutputRecordStaleError(UsageError):
    """A durable record exists for the requested step, but its
    `collected_at` is older than the configured trust window — a hard
    failure, always, no warn-and-use-anyway mode (settled in that doc)."""


@dataclass(frozen=True)
class DeploymentStepOutputs:
    """One step's durably-recorded outputs, deserialised from the shared
    per-deployment record."""

    collected_at: datetime
    values: dict[str, str]


class DeploymentOutputStore:
    """One instance per deployment; `deployment_name` identifies this
    deployment's own record so two unrelated deployments sharing one
    backend can't collide (combined with `_RECORD_KEY_PREFIX` above)."""

    def __init__(
        self, backend: KeyValueStoreIntegration, deployment_name: str, *, max_age: timedelta | None = None
    ) -> None:
        self._backend = backend
        self._key = f"{_RECORD_KEY_PREFIX}{deployment_name}"
        self._max_age = max_age

    def read(self, step: str) -> dict[str, str]:
        """Return `step`'s durably-recorded, non-sensitive output values.

        Raises:
            OutputRecordNotFoundError: no durable record exists for this
                deployment at all, or it has no entry for `step`.
            OutputRecordStaleError: an entry exists but is older than
                `max_age` (when set).
            strata.integrations.errors.IntegrationError: the backend
                itself is unreachable or unauthenticated.
        """
        step_record = self._read_step_record(step)
        if self._max_age is not None:
            age = datetime.now(timezone.utc) - step_record.collected_at
            if age > self._max_age:
                raise OutputRecordStaleError(
                    f"Step '{step}': durable output was collected at "
                    f"{step_record.collected_at.isoformat()}, older than the {self._max_age} trust window — "
                    "re-run the step that produces it to refresh."
                )
        return step_record.values

    def _read_step_record(self, step: str) -> DeploymentStepOutputs:
        entry = self._backend.get(self._key)
        if entry is None:
            raise OutputRecordNotFoundError(f"No durable output record exists for this deployment (key '{self._key}').")
        record = json.loads(entry.value)
        step_entry = record.get(step)
        if step_entry is None:
            raise OutputRecordNotFoundError(
                f"No durable output recorded for step '{step}' in this deployment's record."
            )
        return DeploymentStepOutputs(
            collected_at=datetime.fromisoformat(step_entry["collected_at"]), values=step_entry["values"]
        )

    def record(self, step: str, values: dict[str, str], sensitive_keys: set[str]) -> None:
        """Merge `step`'s non-sensitive output values into this
        deployment's shared durable record, via read-modify-write with
        compare-and-swap retry — never a blind unconditional `put()`,
        which would silently discard a concurrent writer's own update.

        A sensitive output is never serialised here, full stop — matches
        the non-negotiable security constraint in docs/work/
        cross-run-step-output-delivery.md.

        Args:
            values: This step's full output values, as collected this run.
            sensitive_keys: Keys within `values` that must never be
                persisted (gap 1's own filter, `collect_step_outputs()`'s
                return value).

        Raises:
            strata.utils.errors.SystemError: `_MAX_CAS_RETRIES` conflicting
                writes in a row — real contention, not a caller mistake.
            strata.integrations.errors.IntegrationError: the backend
                itself is unreachable or unauthenticated.
        """
        filtered_values = {key: value for key, value in values.items() if key not in sensitive_keys}
        collected_at = datetime.now(timezone.utc).isoformat()

        for _ in range(_MAX_CAS_RETRIES):
            entry = self._backend.get(self._key)
            if entry is None:
                record: dict[str, Any] = {}
                if_version = NEW_KEY_VERSION
            else:
                record = json.loads(entry.value)
                if_version = entry.version
            record[step] = {"collected_at": collected_at, "values": filtered_values}

            try:
                self._backend.put(self._key, json.dumps(record).encode("utf-8"), if_version=if_version)
                return
            except KeyValueConflictError:
                continue

        raise SystemError(
            f"Step '{step}': could not durably record output after {_MAX_CAS_RETRIES} conflicting writes in a "
            "row — the backend is under heavier concurrent write contention than expected."
        )
