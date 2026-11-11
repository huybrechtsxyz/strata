#!/usr/bin/env python3
"""`DeploymentOutputStore` — the backend-agnostic business logic layer on
top of `KeyValueStoreIntegration` (docs/archive/cross-run-step-output-delivery.md's
Implementation Plan steps 2-3, and ADR-0057).

Not itself an `Integration` — plain code wrapping one already-resolved
`KeyValueStoreIntegration` instance (`resolve_output_store_integration()`'s
job, not this module's). Owns exactly what every backend would otherwise
have to duplicate: the record's own JSON shape, the sensitive-key filter,
the size check, and the staleness check on read — written once here,
reused by every backend.

One key per step (ADR-0057, replacing ADR-0036 D3's one record per
deployment), so a backend's per-key size limit applies to one step, not to
the whole deployment:

```jsonc
// strata/step-outputs/<deployment>/<step>
{"collected_at": "2026-10-11T14:00:00+00:00", "values": {"vm_ip": "10.0.0.5"}}
// after destroy (the key-value capability has no delete)
{"destroyed_at": "2026-10-12T09:00:00+00:00"}
```

The per-step keys live under their own prefix, not under the old
`strata/deployment-outputs/<deployment>` key: on a Blob container with a
hierarchical namespace, that old key is a file, so nothing can be created
"inside" it.

**Transition** (ADR-0057). The old per-deployment key
`strata/deployment-outputs/<deployment>` is still read when a step has no
key of its own, so existing deployments keep working without redeploying.
While older strata versions may still read that old key, `record()` also
mirrors the step into it, but only when it already exists: a deployment
that never had the old key never gets one. A failed mirror is a warning.
Both are removed in a later release.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from strata.integrations.capabilities import KeyValueStoreIntegration
from strata.integrations.errors import IntegrationError, KeyValueConflictError
from strata.utils.errors import SystemError, UsageError

#: Per-step keys: `strata/step-outputs/<deployment>/<step>` (ADR-0057).
_STEP_KEY_PREFIX = "strata/step-outputs/"

#: The old per-deployment key, `strata/deployment-outputs/<deployment>` (ADR-0036 D3), read as a fallback and
#: mirrored into during the transition.
_LEGACY_KEY_PREFIX = "strata/deployment-outputs/"

#: Above this many bytes (key plus value), on a backend with a size limit, `record()` warns (ADR-0057).
SIZE_WARNING_BYTES = 8_000

#: How many times the old key's read-modify-write retries on a `KeyValueConflictError` before giving up.
#: Steps run one after another, so contention only comes from two pipelines finishing at the same moment.
_MAX_CAS_RETRIES = 5


class OutputRecordNotFoundError(UsageError):
    """No durable record exists for the requested step: it never ran with an
    output store, or it was destroyed since.

    Distinguishing "never ran in any invocation" from "ran in this
    invocation" is the caller's job — `DeploymentOutputStore.read()` is
    only ever consulted as the fallback once an in-memory lookup already
    missed (docs/archive/cross-run-step-output-delivery.md)."""


class OutputRecordDestroyedError(OutputRecordNotFoundError):
    """The step's key holds the destroyed marker: its infrastructure was destroyed since it last recorded."""

    def __init__(self, message: str, destroyed_at: str) -> None:
        super().__init__(message)
        self.destroyed_at = destroyed_at


class OutputRecordOmittedError(OutputRecordNotFoundError):
    """The step's key holds the omitted marker: its last deploy's outputs were larger than the backend accepts, so
    none were recorded. A reader fails instead of reading the values from an earlier deploy."""

    def __init__(self, message: str, omitted_at: str) -> None:
        super().__init__(message)
        self.omitted_at = omitted_at


class OutputRecordStaleError(UsageError):
    """A durable record exists for the requested step, but its
    `collected_at` is older than the configured trust window — a hard
    failure, always, no warn-and-use-anyway mode (settled in that doc)."""


class OutputRecordTooLargeError(SystemError):
    """A step's record is larger than the backend accepts for one key (ADR-0057). Output values cannot be
    shortened, so no value is written: the step's key gets the omitted marker instead, and the error names the
    step, the size and the limit."""


@dataclass(frozen=True)
class DeploymentStepOutputs:
    """One step's durably-recorded outputs, deserialised from its record."""

    collected_at: datetime
    values: dict[str, str]


def dump_record(record: Any) -> bytes:
    """Compact JSON: the per-key size limit counts every byte."""
    return json.dumps(record, separators=(",", ":")).encode("utf-8")


def load_record_object(value: bytes, key: str) -> dict[str, Any]:
    """Parse one stored record, which must be a JSON object.

    Raises:
        strata.utils.errors.SystemError: it is not valid JSON, or not an object.
    """
    try:
        record = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        record = None
    if not isinstance(record, dict):
        raise SystemError(
            f"The durable record (key '{key}') is not a valid JSON object; inspect or overwrite that key in the "
            "output store."
        )
    return record


class DeploymentOutputStore:
    """One instance per deployment; `deployment_name` keeps two unrelated
    deployments sharing one backend apart."""

    def __init__(
        self, backend: KeyValueStoreIntegration, deployment_name: str, *, max_age: timedelta | None = None
    ) -> None:
        self._backend = backend
        self._deployment = deployment_name
        self._legacy_key = f"{_LEGACY_KEY_PREFIX}{deployment_name}"
        self._max_age = max_age

    def step_key(self, step: str) -> str:
        """The key holding `step`'s record."""
        return f"{_STEP_KEY_PREFIX}{self._deployment}/{step}"

    def read(self, step: str) -> dict[str, str]:
        """Return `step`'s durably-recorded, non-sensitive output values.

        Raises:
            OutputRecordNotFoundError: nothing is recorded for `step`, or it was destroyed.
            OutputRecordStaleError: a record exists but is older than `max_age` (when set).
            strata.utils.errors.SystemError: the stored record is not valid JSON.
            strata.integrations.errors.IntegrationError: the backend
                itself is unreachable or unauthenticated.
        """
        step_record = self.read_record(step)
        if self._max_age is not None:
            age = datetime.now(timezone.utc) - step_record.collected_at
            if age > self._max_age:
                raise OutputRecordStaleError(
                    f"Step '{step}': durable output was collected at "
                    f"{step_record.collected_at.isoformat()}, older than the {self._max_age} trust window — "
                    "re-run the step that produces it to refresh."
                )
        return step_record.values

    def read_record(self, step: str) -> DeploymentStepOutputs:
        """Return `step`'s record with its `collected_at`, without the staleness check `read()` applies: for a
        person reading values (`strata deploy output`), not for a deploy consuming them.

        Raises:
            OutputRecordDestroyedError: the step's key holds the destroyed marker.
            OutputRecordOmittedError: the step's last deploy had outputs too large to record.
            OutputRecordNotFoundError: nothing is recorded for `step`.
            strata.utils.errors.SystemError: the stored record is not valid JSON.
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        key = self.step_key(step)
        entry = self._backend.get(key)
        if entry is not None:
            record = load_record_object(entry.value, key)
            if "destroyed_at" in record:
                # Never fall back to the old key here: it may still hold the values from before the destroy.
                raise OutputRecordDestroyedError(
                    f"No durable output recorded for step '{step}': it was destroyed at {record['destroyed_at']}.",
                    str(record["destroyed_at"]),
                )
            if "omitted_at" in record:
                # Nor here: the old key may still hold the values from the deploy before this one.
                raise OutputRecordOmittedError(
                    f"No durable output recorded for step '{step}': its deploy at {record['omitted_at']} produced "
                    "outputs larger than the output store accepts for one key, so none were recorded. Make them "
                    "smaller and deploy the step again.",
                    str(record["omitted_at"]),
                )
            return DeploymentStepOutputs(
                collected_at=datetime.fromisoformat(record["collected_at"]), values=record["values"]
            )

        legacy = self._backend.get(self._legacy_key)
        step_entry = load_record_object(legacy.value, self._legacy_key).get(step) if legacy is not None else None
        if step_entry is None:
            raise OutputRecordNotFoundError(f"No durable output recorded for step '{step}' (key '{key}').")
        return DeploymentStepOutputs(
            collected_at=datetime.fromisoformat(step_entry["collected_at"]), values=step_entry["values"]
        )

    def record(
        self,
        step: str,
        values: dict[str, str],
        sensitive_keys: set[str],
        *,
        collected_at: datetime | None = None,
    ) -> list[str]:
        """Write `step`'s non-sensitive output values to its own key.

        A sensitive output is never serialised here, full stop — matches
        the non-negotiable security constraint in docs/archive/
        cross-run-step-output-delivery.md.

        A plain `put()`: the key has one writer, the step that just deployed, and there is nothing to merge.

        Args:
            values: This step's full output values, as collected this run.
            sensitive_keys: Keys within `values` that must never be
                persisted (`collect_step_outputs()`'s return value).
            collected_at: When the outputs were collected; now when omitted. The deploy passes the same time to
                the report record, so a reader can tell whether both come from the same deploy.

        Returns:
            Warnings for the caller to report: the record passed `SIZE_WARNING_BYTES` on a backend with a
            limit, or the transition mirror into the old key failed.

        Raises:
            OutputRecordTooLargeError: the record is larger than the backend's `MAX_VALUE_BYTES`. The step's key
                then holds the omitted marker and the step is gone from the old key, so no reader gets the
                values of an earlier deploy.
            strata.integrations.errors.IntegrationError: the backend
                itself is unreachable or unauthenticated.
        """
        payload = {
            "collected_at": (collected_at or datetime.now(timezone.utc)).isoformat(),
            "values": {key: value for key, value in values.items() if key not in sensitive_keys},
        }
        key = self.step_key(step)
        data = dump_record(payload)
        warnings: list[str] = []

        limit = self._backend.MAX_VALUE_BYTES
        size = len(key.encode("utf-8")) + len(data)
        if limit is not None and size > limit:
            # Never leave an earlier deploy's values readable: they no longer describe what is deployed.
            self._backend.put(key, dump_record({"omitted_at": payload["collected_at"], "size": size}))
            self._mirror_into_legacy(step, None)
            raise OutputRecordTooLargeError(
                f"Step '{step}': its outputs take {size} bytes, more than the {limit} bytes the "
                f"'{self._backend.TYPE}' output store accepts for one key. No value was recorded, and a later step "
                f"that reads one of its outputs fails until it is deployed again with smaller outputs. Make the "
                "largest outputs smaller, mark them sensitive (they are then never stored), or use an "
                "'azure-blob' output store."
            )
        if limit is not None and size > SIZE_WARNING_BYTES:
            warnings.append(
                f"Step '{step}': its recorded outputs take {size} bytes, close to the {limit} bytes the "
                f"'{self._backend.TYPE}' output store accepts for one key."
            )

        self._backend.put(key, data)

        mirror_warning = self._mirror_into_legacy(step, payload)
        if mirror_warning:
            warnings.append(mirror_warning)
        return warnings

    def _mirror_into_legacy(self, step: str, payload: dict[str, Any] | None) -> str | None:
        """During the transition, keep the old per-deployment key current for older strata versions that read
        only that key — so they never read a value from before this deploy. Only when the old key exists.

        When the mirrored record would be too large, or `payload` is `None`, the step is removed from the old
        key instead: an older strata then finds nothing for it, which fails loudly, instead of an old value,
        which would not.

        Returns:
            A warning when the mirror could not be written, else `None`.
        """
        limit = self._backend.MAX_VALUE_BYTES
        try:
            for _ in range(_MAX_CAS_RETRIES):
                entry = self._backend.get(self._legacy_key)
                if entry is None:
                    return None
                record = load_record_object(entry.value, self._legacy_key)
                if payload is None and step not in record:
                    return None
                record[step] = payload
                data = dump_record(record)
                if payload is None or (limit is not None and len(self._legacy_key.encode("utf-8")) + len(data) > limit):
                    del record[step]
                    data = dump_record(record)
                try:
                    self._backend.put(self._legacy_key, data, if_version=entry.version)
                    return None
                except KeyValueConflictError:
                    continue
            return (
                f"Step '{step}': could not update the old output record (key '{self._legacy_key}') after "
                f"{_MAX_CAS_RETRIES} conflicting writes; an older strata version may read an outdated value."
            )
        except (SystemError, IntegrationError) as exc:
            return (
                f"Step '{step}': could not update the old output record (key '{self._legacy_key}') - {exc}. An "
                "older strata version may read an outdated value."
            )

    def clear(self, steps: Iterable[str]) -> None:
        """Forget `steps`' outputs, so a later `deploy run` of what was just destroyed cannot read the outputs
        of infrastructure that no longer exists (docs/design/deploy-destroy.md, D8).

        The key-value capability has no delete, so a step's own key is overwritten with a destroyed marker
        (only when the key exists), which also stops `read()` from falling back to the old key. The steps are
        then removed from the old per-deployment key, with read-modify-write and compare-and-swap so another
        step's entry written concurrently is kept. The old key is rewritten once for all the steps, and not
        at all when none of them is in it.

        Args:
            steps: Step names to forget. Read once up front, so a generator is fine.

        Raises:
            strata.utils.errors.SystemError: the old record is not a JSON object, or
                `_MAX_CAS_RETRIES` conflicting writes in a row.
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        names = set(steps)
        if not names:
            return

        marker = dump_record({"destroyed_at": datetime.now(timezone.utc).isoformat()})
        for step in sorted(names):
            key = self.step_key(step)
            if self._backend.get(key) is not None:
                self._backend.put(key, marker)

        for _ in range(_MAX_CAS_RETRIES):
            entry = self._backend.get(self._legacy_key)
            if entry is None:
                return
            record = load_record_object(entry.value, self._legacy_key)
            remaining = {step: value for step, value in record.items() if step not in names}
            if len(remaining) == len(record):
                return

            try:
                self._backend.put(self._legacy_key, dump_record(remaining), if_version=entry.version)
                return
            except KeyValueConflictError:
                continue

        raise SystemError(
            f"Steps {sorted(names)}: could not durably clear their output after {_MAX_CAS_RETRIES} conflicting "
            "writes in a row - the backend is under heavier concurrent write contention than expected."
        )
