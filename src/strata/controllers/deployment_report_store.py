#!/usr/bin/env python3
"""`DeploymentReportStore`: the report record, what `strata deploy output` and `strata deploy report` read and a
deploy never consumes (docs/design/deployment-documentation.md, section 1).

One key per step, next to the output record (ADR-0057), in the same `output_store` backend:

```jsonc
// strata/deployment-report/<deployment>/<step>
{"collected_at": "2026-11-09T14:28:00+00:00", "sensitive": ["admin_password"],
 "d": {"admin_password": "Also in Key Vault kv-web-prd"}}
// with the secrets the step used (release 3): key, store, location, description, declaring file
{"collected_at": "...", "secrets": [{"k": "SQL_PWD", "s": "azure-keyvault",
 "loc": {"vault": "https://kv.vault.azure.net/", "name": "sql-pwd"}, "d": "SQL admin", "in": "env/prd.yaml"}]}
// descriptions shortened to fit the backend's limit
{"collected_at": "...", "sensitive": [...], "d": {...}, "truncated": true}
// after destroy (the key-value capability has no delete)
{"destroyed_at": "2026-11-10T09:00:00+00:00"}
```

Never a value, sensitive or not: values stay in the output record, and a secret is recorded by its location only.
`secrets` is always written, empty or not, so a record from before release 3 (no `secrets` field) reads as "secrets
not recorded", never as "no secrets". `all_secrets` marks a step that receives every declared secret (a script
without `script.values`, a template that reads `secrets` dynamically). Fields are left out when empty, and the names
are short (`d` for descriptions, `loc` for a location), because the backend's per-key limit counts every byte.

Written with a plain `put()`: each key has one writer, the step that just deployed, and a lost race only loses
document metadata. `collected_at` is the same time the output record carries, so a reader can tell a report record
from an earlier deploy. Reads never apply `output_store_max_age`: a person reading is shown old data with a
warning, not refused.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from strata.controllers.deployment_output_store import (
    SIZE_WARNING_BYTES,
    OutputRecordTooLargeError,
    dump_record,
    load_record_object,
)
from strata.integrations.capabilities import KeyValueStoreIntegration

#: Report keys: `strata/deployment-report/<deployment>/<step>`.
_REPORT_KEY_PREFIX = "strata/deployment-report/"

#: The last successful deploy's audit summary: `strata/deployment-audit/<deployment>`. Its own prefix, so it can
#: never collide with a step's key.
_AUDIT_KEY_PREFIX = "strata/deployment-audit/"

#: The audit summary's fields: always all present (`None` when unknown), so a template can use any of them.
AUDIT_SUMMARY_KEYS = ("deployed_by", "completed_at", "status", "action", "version", "execution_id", "commit_sha")

#: Above the backend's limit, descriptions are first shortened to this many characters.
SHORT_DESCRIPTION_LENGTH = 200


@dataclass(frozen=True)
class StepReport:
    """One step's report record. `destroyed_at` is set (and nothing else) when the step was destroyed."""

    collected_at: datetime | None = None
    sensitive_keys: tuple[str, ...] = ()
    descriptions: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    destroyed_at: str | None = None
    #: `None` when the record predates the secrets part: not recorded, which is not the same as none used.
    secrets: tuple["SecretUse", ...] | None = None
    all_secrets: bool = False


@dataclass(frozen=True)
class SecretUse:
    """One secret a step used: where it lives, never its value."""

    key: str
    store: str
    location: dict[str, str | None] = field(default_factory=dict)
    description: str | None = None
    declared_in: str | None = None

    def to_record(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"k": self.key, "s": self.store}
        if self.location:
            entry["loc"] = self.location
        if self.description:
            entry["d"] = self.description
        if self.declared_in:
            entry["in"] = self.declared_in
        return entry

    @staticmethod
    def from_record(entry: dict[str, Any]) -> "SecretUse":
        location = entry.get("loc")
        return SecretUse(
            key=str(entry.get("k", "")),
            store=str(entry.get("s", "")),
            location={str(k): (None if v is None else str(v)) for k, v in location.items()}
            if isinstance(location, dict)
            else {},
            description=str(entry["d"]) if entry.get("d") else None,
            declared_in=str(entry["in"]) if entry.get("in") else None,
        )


@dataclass(frozen=True)
class PreparedReport:
    """A report record ready to write, sized against the backend's limit."""

    key: str
    data: bytes
    size: int
    warnings: list[str]


def _shorten(text: str) -> str:
    if len(text) <= SHORT_DESCRIPTION_LENGTH:
        return text
    return text[: SHORT_DESCRIPTION_LENGTH - 3] + "..."


class DeploymentReportStore:
    """One instance per deployment, over the workspace's `output_store` backend."""

    def __init__(self, backend: KeyValueStoreIntegration, deployment_name: str) -> None:
        self._backend = backend
        self._deployment = deployment_name

    def step_key(self, step: str) -> str:
        """The key holding `step`'s report record."""
        return f"{_REPORT_KEY_PREFIX}{self._deployment}/{step}"

    def prepare(
        self,
        step: str,
        *,
        sensitive_keys: Iterable[str],
        descriptions: dict[str, str],
        collected_at: datetime | None = None,
        secrets: Iterable[SecretUse] = (),
        all_secrets: bool = False,
    ) -> PreparedReport:
        """Build `step`'s report record and fit it to the backend's limit, without writing it.

        `strata build run` calls this to estimate the size before a deploy, with the same code the deploy uses.

        Above the limit, descriptions (of outputs and of secrets) are shortened to `SHORT_DESCRIPTION_LENGTH` characters and the record is
        marked `truncated`; if it is still too large, descriptions are left out. Names and locations are always kept.

        Raises:
            OutputRecordTooLargeError: even the names alone are larger than the backend's limit.
        """
        key = self.step_key(step)
        key_size = len(key.encode("utf-8"))
        limit = self._backend.MAX_VALUE_BYTES
        record: dict[str, Any] = {"collected_at": (collected_at or datetime.now(timezone.utc)).isoformat()}
        sensitive = sorted(set(sensitive_keys))
        if sensitive:
            record["sensitive"] = sensitive
        if descriptions:
            record["d"] = dict(sorted(descriptions.items()))
        record["secrets"] = [use.to_record() for use in sorted(secrets, key=lambda use: use.key)]
        if all_secrets:
            record["all_secrets"] = True

        warnings: list[str] = []
        data = dump_record(record)
        has_descriptions = "d" in record or any("d" in entry for entry in record["secrets"])
        if limit is not None and key_size + len(data) > limit and has_descriptions:
            if "d" in record:
                record["d"] = {name: _shorten(text) for name, text in record["d"].items()}
            for entry in record["secrets"]:
                if "d" in entry:
                    entry["d"] = _shorten(entry["d"])
            record["truncated"] = True
            data = dump_record(record)
            if key_size + len(data) > limit:
                record.pop("d", None)
                for entry in record["secrets"]:
                    entry.pop("d", None)
                data = dump_record(record)
                warnings.append(
                    f"Step '{step}': its report record is larger than the {limit} bytes the "
                    f"'{self._backend.TYPE}' output store accepts for one key, so its descriptions are left "
                    "out. Output names and secret locations are kept."
                )
            else:
                warnings.append(
                    f"Step '{step}': its report record is larger than the {limit} bytes the "
                    f"'{self._backend.TYPE}' output store accepts for one key, so its descriptions are "
                    f"shortened to {SHORT_DESCRIPTION_LENGTH} characters."
                )

        size = key_size + len(data)
        if limit is not None and size > limit:
            raise OutputRecordTooLargeError(
                f"Step '{step}': its report record takes {size} bytes even without descriptions, more than the "
                f"{limit} bytes the '{self._backend.TYPE}' output store accepts for one key. Nothing was recorded."
            )
        if limit is not None and size > SIZE_WARNING_BYTES:
            warnings.append(
                f"Step '{step}': its report record takes {size} bytes, close to the {limit} bytes the "
                f"'{self._backend.TYPE}' output store accepts for one key."
            )
        return PreparedReport(key=key, data=data, size=size, warnings=warnings)

    def record(
        self,
        step: str,
        *,
        sensitive_keys: Iterable[str],
        descriptions: dict[str, str],
        collected_at: datetime | None = None,
        secrets: Iterable[SecretUse] = (),
        all_secrets: bool = False,
    ) -> list[str]:
        """Write `step`'s report record with a plain `put()`.

        Returns:
            Warnings for the caller to report (descriptions shortened or left out, close to the limit).

        Raises:
            OutputRecordTooLargeError: even the names alone are larger than the backend's limit.
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        prepared = self.prepare(
            step,
            sensitive_keys=sensitive_keys,
            descriptions=descriptions,
            collected_at=collected_at,
            secrets=secrets,
            all_secrets=all_secrets,
        )
        self._backend.put(prepared.key, prepared.data)
        return prepared.warnings

    def read(self, step: str) -> StepReport | None:
        """Return `step`'s report record, or `None` when it has none.

        Raises:
            strata.utils.errors.SystemError: the stored record is not valid JSON.
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        key = self.step_key(step)
        entry = self._backend.get(key)
        if entry is None:
            return None
        record = load_record_object(entry.value, key)
        if "destroyed_at" in record:
            return StepReport(destroyed_at=str(record["destroyed_at"]))
        collected_at = record.get("collected_at")
        descriptions = record.get("d")
        return StepReport(
            collected_at=datetime.fromisoformat(collected_at) if isinstance(collected_at, str) else None,
            sensitive_keys=tuple(str(name) for name in record.get("sensitive") or []),
            descriptions={str(k): str(v) for k, v in descriptions.items()} if isinstance(descriptions, dict) else {},
            truncated=record.get("truncated") is True,
            secrets=tuple(SecretUse.from_record(entry) for entry in raw_secrets if isinstance(entry, dict))
            if isinstance(raw_secrets := record.get("secrets"), list)
            else None,
            all_secrets=record.get("all_secrets") is True,
        )

    def record_last_deploy(self, summary: dict[str, str | None]) -> None:
        """Keep the last successful deploy's audit summary (who, when, which commit, which audit record), written
        at the end of `deploy run` with a plain `put()`. Never a value.

        Raises:
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        self._backend.put(
            f"{_AUDIT_KEY_PREFIX}{self._deployment}",
            dump_record({key: summary.get(key) for key in AUDIT_SUMMARY_KEYS}),
        )

    def read_last_deploy(self) -> dict[str, Any] | None:
        """The last successful deploy's audit summary, or `None` when none was recorded. Every key in
        `AUDIT_SUMMARY_KEYS` is present, `None` when the record lacks it.

        Raises:
            strata.utils.errors.SystemError: the stored record is not valid JSON.
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        key = f"{_AUDIT_KEY_PREFIX}{self._deployment}"
        entry = self._backend.get(key)
        if entry is None:
            return None
        record = load_record_object(entry.value, key)
        return {name: record.get(name) for name in AUDIT_SUMMARY_KEYS}

    def clear(self, steps: Iterable[str]) -> None:
        """Overwrite each destroyed step's report key with `{"destroyed_at": ...}`, when the key exists.

        Raises:
            strata.integrations.errors.IntegrationError: the backend itself is unreachable or unauthenticated.
        """
        marker = dump_record({"destroyed_at": datetime.now(timezone.utc).isoformat()})
        for step in sorted(set(steps)):
            key = self.step_key(step)
            if self._backend.get(key) is not None:
                self._backend.put(key, marker)
