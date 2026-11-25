#!/usr/bin/env python3
"""Read back audit trail records from the configured durable `git` sink —
`strata audit status`'s controller layer (docs/design/audit-commands.md).

Phase 3 of that doc's Implementation Plan. The read-side counterpart to
`audit_run.py`'s write side: where that module resolves the single
effective `Configuration`, picks every enabled sink and dispatches to each,
this module resolves the single *readable* (`git`-arm) sink, syncs a
dedicated read-only checkout (`controllers/git_checkout.py`, not
`audit_run.py`'s push checkout — see `layout.audit_read_checkout_path()`),
and globs for `_manifest.json` records rather than reconstructing
`audit_path_resolution.py`'s write-time directory shape.

**"Tell the user first" is the load-bearing ordering, not an afterthought.**
`resolve_readable_sink()` does zero I/O and always runs before
`sync_read_checkout()` ever touches git — a misconfigured solution (no
`git`-arm sink, or more than one) fails in milliseconds with a message
naming exactly what to add, never after a slow, confusing clone/fetch
attempt against nothing.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from pydantic import ValidationError as PydanticValidationError

from strata.controllers.audit_run import MANIFEST_FILENAME
from strata.controllers.git_checkout import ensure_synced_checkout
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_values
from strata.integrations.errors import IntegrationError
from strata.integrations.git_push import credential_env, credential_keys
from strata.models.audit_manifest_model import ChangeReferenceModel, DeploymentManifestModel
from strata.models.audit_model import AuditConfigModel, AuditGitSinkTargetModel
from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.integration_model import IntegrationModel
from strata.models.solution_model import RemoteType, SolutionModel
from strata.utils import layout
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import SystemError, UsageError


class AuditReadError(SystemError):
    """The configured audit sink could not be reached — fetch/clone failure,
    or its declared remote doesn't resolve to a real git remote.

    A `SystemError`, not a `UsageError`: by the time this can be raised,
    `resolve_readable_sink()` has already confirmed the *configuration*
    names exactly one readable sink — what's wrong here is the environment
    (network, auth, a remote that doesn't match what was declared), not the
    invocation.
    """


@dataclass(frozen=True)
class DeploymentStatusRecord:
    """One deployment's latest known audit record — `strata audit status`'s
    own result shape, not `DeploymentManifestModel` itself, so the command
    layer never depends on manifest fields this view doesn't need."""

    deployment: str
    status: str
    action: str
    version: str
    deployed_by: str
    completed_at: str
    change_reference: ChangeReferenceModel | None
    commit_sha: str | None = None
    failed_step: str | None = None


@dataclass(frozen=True)
class DeploymentChangeRecord:
    """One audit record, ungrouped — `strata audit changes`'s own result
    shape, a sibling of `DeploymentStatusRecord` (status's group-keep-latest
    view) rather than a reuse of it: `changes` needs `execution_id`/
    `started_at` to distinguish multiple runs of the same deployment, which
    status's one-row-per-deployment view has no use for."""

    execution_id: str
    deployment: str
    status: str
    action: str
    started_at: str
    completed_at: str
    deployed_by: str
    version: str
    change_reference: ChangeReferenceModel | None
    commit_sha: str | None = None
    failed_step: str | None = None


def failed_step(manifest: DeploymentManifestModel) -> str | None:
    """The step a failed or partial run stopped at: its first failed stage (docs/design/audit-trail.md, "Stages").
    `None` for a run without one, including a record written before stages were."""
    return next((stage.name for stage in manifest.stages or [] if stage.status == "failed"), None)


def resolve_readable_sink(audit_config: AuditConfigModel | None) -> AuditGitSinkTargetModel:
    """Return the one enabled `git`-arm sink `strata audit status` reads from.

    Zero I/O — pure inspection of already-loaded configuration. Always the
    first audit-specific thing `audit_status()` calls, and the only check
    guaranteed to run before any git/network operation (see this module's
    own docstring).

    Raises:
        UsageError: No enabled `git`-arm sink is configured at all, or more
            than one is — reading more than one is not supported yet
            (docs/design/audit-commands.md's "Sink selection"). Either way
            the message names exactly what's configured and what to do
            about it, never a generic "ambiguous"/"not found". Shared by
            both `audit_status()` and `audit_changes()`, so the message
            deliberately names neither subcommand specifically — an
            earlier draft said "'strata audit status' reads from..."
            unconditionally, which was actively misleading when this same
            check failed for `strata audit changes` instead.
    """
    sinks = audit_config.sinks if audit_config is not None else None
    git_sinks = [sink for sink in (sinks or []) if sink.enabled and sink.git is not None]

    if not git_sinks:
        raise UsageError(
            "No audit git sink is configured for this solution. Reading audit records ('strata audit "
            "status'/'strata audit changes') requires a configured 'git' audit sink, and none exists — "
            "add one under spec.audit.sinks in your Configuration document. See docs/design/audit-trail.md "
            "and docs/design/audit-commands.md."
        )
    if len(git_sinks) > 1:
        names = ", ".join(f"'{sink.name}'" for sink in git_sinks)
        raise UsageError(
            f"Multiple git audit sinks are configured ({names}) — reading more than one is not supported "
            "yet. See docs/design/audit-commands.md."
        )

    git_sink = git_sinks[0].git
    assert git_sink is not None  # filtered for above — narrows the type for mypy
    return git_sink


def sync_read_checkout(
    root: Path,
    solution: SolutionModel | None,
    sink: AuditGitSinkTargetModel,
    *,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve `sink.remote`, then fetch/reset a dedicated read-only checkout.

    Returns the checkout, or `None` when `sink.branch` does not exist on the
    remote yet: nothing was pushed to the sink, and the remote's default
    branch is not read in its place, since its files are not the sink's.

    By the time `audit_status()` calls this, `require_valid()` has already
    confirmed `sink.remote` names a real declared remote (`RemoteReference()`
    on `AuditGitSinkTargetModel.remote`) — the "not declared" branch below is
    defensive, not expected to trigger in the normal command flow. "Wrong
    remote type" is not schema-checked (a remote reference only confirms a
    name exists, not which `type` it has — the same gap `audit_push.py`'s
    own `push_audit_files()` guards against at runtime), so it is a real,
    reachable case here.

    The read checkout under `.strata/audit-read/` is strata's own, so this
    always clones and fetches it, whatever `remote.fetch` says: `fetch:
    external` names a checkout a pipeline places for builds, and nobody
    places this one. A remote with a `path` is read the same way, from
    `remote.url` at `sink.branch`; the pipeline's checkout is never read,
    because it may be on another branch, behind the branch the records were
    pushed to, or absent on a developer's machine (docs/design/
    audit-commands.md, "A sink on a path remote").

    Args:
        auth: `remote.integration`'s authentication, or `None` for ambient
            git credentials.
        resolved_values: The values `auth`'s key references resolve to.

    Raises:
        AuditReadError: The remote isn't declared, isn't a `git` remote, its
            credentials don't resolve, or the clone/fetch/reset itself failed.
    """
    remotes_by_name = {remote.name: remote for remote in (solution.spec.remotes or [])} if solution else {}
    remote = remotes_by_name.get(sink.remote)
    if remote is None:
        raise AuditReadError(f"sink remote '{sink.remote}' is not declared in this solution's spec.remotes")
    if remote.type != RemoteType.GIT:
        raise AuditReadError(
            f"remote '{remote.name}' has type '{remote.type.value}', but a readable audit sink needs 'git'"
        )

    checkout_path = layout.audit_read_checkout_path(root, remote.name, sink.branch)
    try:
        with credential_env(auth, resolved_values, None) as env:
            synced = ensure_synced_checkout(remote.url, sink.branch, checkout_path, env=env, fallback_to_default=False)
    except IntegrationError as exc:
        raise AuditReadError(f"remote '{remote.name}': {exc}") from exc
    if not synced.success:
        raise AuditReadError(synced.detail)
    return checkout_path if synced.branch_found else None


def _read_credentials(
    context: SolutionContext, sink: AuditGitSinkTargetModel, deployment: str | None
) -> tuple[AuthenticationModel | None, Mapping[str, str] | None, str]:
    """`(auth, resolved_values, hint)` for reading `sink.remote`.

    The same lookup a deploy makes for the push (`audit_run.
    _resolve_git_sink_auth()`): `remote.integration` -> its
    `spec.authentication`, and a missing remote or integration means ambient
    credentials. Its key references are resolved through `deployment`,
    because values belong to a deployment's environments. Without a known
    deployment the read uses ambient credentials, and `hint` says how to use
    the integration's instead, for the error message should that read fail.

    Raises:
        AuditReadError: A key the authentication references has no value for
            `deployment`.
    """
    solution = context.controller.solution
    remote = next((r for r in (solution.spec.remotes or []) if r.name == sink.remote), None) if solution else None
    if remote is None or not remote.integration:
        return None, None, ""
    entry = context.controller.index.get(PlatformKind.INTEGRATION, remote.integration)
    if entry is None:
        return None, None, ""
    auth = cast(IntegrationModel, entry.model).spec.authentication
    keys = credential_keys(auth)
    if not keys:
        return auth, None, ""
    if deployment is None or context.controller.index.get(PlatformKind.DEPLOYMENT, deployment) is None:
        return (
            None,
            None,
            f"remote '{remote.name}' authenticates through integration '{remote.integration}', whose credentials "
            "are resolved for a deployment: name one, for example 'strata audit status <deployment>', to use them "
            "instead of your own git credentials",
        )
    result = resolve_values(context, deployment, list(keys))
    missing = [key for key in keys if key not in result.values]
    if missing:
        detail = "; ".join(d.message for d in result.diagnostics.errors if d.location in keys)
        raise AuditReadError(
            f"remote '{remote.name}': could not resolve the credentials of integration '{remote.integration}' for "
            f"deployment '{deployment}': {detail or 'no value for ' + ', '.join(missing)}"
        )
    return auth, {key: result.values[key] for key in keys}, ""


def _read_checkout(
    context: SolutionContext, sink: AuditGitSinkTargetModel, deployment: str | None, diagnostics: Diagnostics
) -> Path | None:
    """`sync_read_checkout()` with the remote's credentials; `None`, with an
    info diagnostic, when the sink's branch does not exist yet.

    Raises:
        AuditReadError: The sink's remote can't be synced.
    """
    auth, resolved_values, hint = _read_credentials(context, sink, deployment)
    try:
        checkout_path = sync_read_checkout(
            context.root, context.controller.solution, sink, auth=auth, resolved_values=resolved_values
        )
    except AuditReadError as exc:
        if hint:
            raise AuditReadError(f"{exc} ({hint})") from exc
        raise
    if checkout_path is None:
        diagnostics.info(
            f"branch '{sink.branch}' does not exist on remote '{sink.remote}' yet: nothing has been pushed to this "
            "audit sink. With `action: commit` or `none`, a record appears once the pipeline pushes it.",
            code="audit_read_branch_missing",
        )
    return checkout_path


def _iter_manifests(search_root: Path, diagnostics: Diagnostics) -> Iterator[DeploymentManifestModel]:
    """Glob for every `_manifest.json` under `search_root`, parse each, and
    yield only the ones that parse successfully.

    Extracted from `list_latest_per_deployment()`'s own inline loop once
    `list_manifests_in_range()` needed the identical enumeration — shared,
    not duplicated, the same discipline Phase 2 already applied to
    `ensure_synced_checkout()`.

    A manifest that fails to parse (corrupted file, a record written by an
    incompatible strata version) is skipped with a warning, never aborts
    the whole command — one bad record must not hide every good one.
    """
    for manifest_path in sorted(search_root.rglob(MANIFEST_FILENAME)):
        try:
            yield DeploymentManifestModel.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, PydanticValidationError, ValueError) as exc:
            diagnostics.warning(
                f"could not read audit record: {exc}",
                source=str(manifest_path),
                code="audit_read_manifest_unparseable",
            )


def _parse_range_bound(value: str | None, flag_name: str, *, end_of_day: bool = False) -> datetime | None:
    """Parse a `--since`/`--until` value into an aware UTC `datetime`, or
    `None` when the flag was omitted.

    Every real `started_at` this gets compared against is written as
    `datetime.now(timezone.utc).isoformat()` (`audit_run.py`) — always
    timezone-aware. `datetime.fromisoformat('2026-10-01')` (a bare date, the
    common case for a human-typed `--since`) parses *naive* — comparing a
    naive and an aware datetime raises `TypeError`, not a wrong-but-silent
    answer, so a naive parse is explicitly promoted to UTC here rather than
    left to crash later inside the comparison itself.

    Args:
        value: The raw `--since`/`--until` value, or `None`.
        flag_name: `--since` or `--until`, named in the error message.
        end_of_day: `True` for `--until` only. A bare date (no time
            component) otherwise parses to that day's *midnight* —
            `--until 2026-10-04` would then exclude every record later
            that same day, contradicting the CLI help text's own
            "inclusive" claim. When `True` and `value` has no time
            component, the bound is pushed to the last microsecond of that
            day instead, so a bare-date `--until` really does include the
            whole day it names.

    Raises:
        UsageError: `value` isn't a valid ISO-8601 date or datetime at all.
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise UsageError(
            f"'{flag_name}' is not a valid ISO-8601 date or datetime: '{value}'. Examples: 2026-10-01, "
            "2026-10-01T00:00:00+00:00."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    is_date_only = "T" not in value and ":" not in value
    if end_of_day and is_date_only:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def list_latest_per_deployment(
    checkout_path: Path,
    sink: AuditGitSinkTargetModel,
    *,
    deployment: str | None,
    diagnostics: Diagnostics,
) -> list[DeploymentStatusRecord]:
    """Glob for every `_manifest.json` under `sink.path`, parse, optionally
    filter to one `deployment`, and keep only the max-`started_at` record
    per deployment name.

    Never reconstructs `audit_path_resolution.py`'s write-time directory
    shape (layers-dependent, deployment-specific) — the manifest's own
    fields are the only source of truth for identity, filtering and
    ordering (docs/design/audit-commands.md's "Enumeration").
    """
    search_root = checkout_path / sink.path
    latest_by_deployment: dict[str, DeploymentManifestModel] = {}

    for manifest in _iter_manifests(search_root, diagnostics):
        if deployment is not None and manifest.deployment != deployment:
            continue

        current = latest_by_deployment.get(manifest.deployment)
        if current is None or manifest.started_at > current.started_at:
            latest_by_deployment[manifest.deployment] = manifest

    if not latest_by_deployment:
        if deployment is not None:
            diagnostics.info(
                f"no audit records found for deployment '{deployment}'",
                code="audit_read_no_records_for_deployment",
            )
        else:
            diagnostics.info("no audit records found in this sink yet", code="audit_read_no_records")

    return [
        DeploymentStatusRecord(
            deployment=manifest.deployment,
            status=manifest.status,
            action=manifest.action,
            version=manifest.version or "unknown",
            deployed_by=manifest.deployed_by or "unknown",
            completed_at=manifest.completed_at or manifest.started_at,
            change_reference=manifest.change_reference,
            commit_sha=manifest.commit_sha,
            failed_step=failed_step(manifest),
        )
        for manifest in sorted(latest_by_deployment.values(), key=lambda m: m.deployment)
    ]


def list_manifests_in_range(
    checkout_path: Path,
    sink: AuditGitSinkTargetModel,
    *,
    deployment: str | None,
    since: datetime | None,
    until: datetime | None,
    status: str | None,
    diagnostics: Diagnostics,
) -> list[DeploymentChangeRecord]:
    """Every manifest matching all given filters, sorted oldest to newest by
    `started_at` — no grouping, unlike `list_latest_per_deployment()`.

    `since`/`until` are already-parsed, already-UTC-aware bounds (see
    `_parse_range_bound()`) — this function does no date parsing of its
    own, only comparison.
    """
    search_root = checkout_path / sink.path
    matches: list[DeploymentManifestModel] = []

    for manifest in _iter_manifests(search_root, diagnostics):
        if deployment is not None and manifest.deployment != deployment:
            continue
        if status is not None and manifest.status != status:
            continue
        started_at = datetime.fromisoformat(manifest.started_at)
        if since is not None and started_at < since:
            continue
        if until is not None and started_at > until:
            continue
        matches.append(manifest)

    if not matches:
        if deployment is not None:
            diagnostics.info(
                f"no audit records found for deployment '{deployment}'",
                code="audit_read_no_records_for_deployment",
            )
        elif since is not None or until is not None or status is not None:
            # Records may well exist in this sink — they just don't match the
            # given since/until/status filters. Saying "nothing in this sink"
            # here would be actively wrong, not merely vague, whenever the
            # sink is non-empty but every record fell outside the range.
            diagnostics.info(
                "no audit records match the given filters",
                code="audit_read_no_records_for_filters",
            )
        else:
            diagnostics.info("no audit records found in this sink yet", code="audit_read_no_records")

    return [
        DeploymentChangeRecord(
            execution_id=manifest.execution_id,
            deployment=manifest.deployment,
            status=manifest.status,
            action=manifest.action,
            started_at=manifest.started_at,
            completed_at=manifest.completed_at or manifest.started_at,
            deployed_by=manifest.deployed_by or "unknown",
            version=manifest.version or "unknown",
            change_reference=manifest.change_reference,
            commit_sha=manifest.commit_sha,
            failed_step=failed_step(manifest),
        )
        for manifest in sorted(matches, key=lambda m: datetime.fromisoformat(m.started_at))
    ]


def audit_status(context: SolutionContext, deployment: str | None) -> tuple[list[DeploymentStatusRecord], Diagnostics]:
    """Top-level orchestration `commands/audit_command.py`'s `status`
    subcommand calls — resolve the sink, sync the checkout, enumerate
    records, in that exact fail-fast order.

    Raises:
        UsageError: No readable sink configured, or more than one.
        AuditReadError: The sink's remote can't be synced.
    """
    configuration = _single_configuration(context)
    audit_config = configuration.spec.audit if configuration is not None else None

    sink = resolve_readable_sink(audit_config)
    diagnostics = Diagnostics()
    checkout_path = _read_checkout(context, sink, deployment, diagnostics)
    if checkout_path is None:
        return [], diagnostics

    records = list_latest_per_deployment(checkout_path, sink, deployment=deployment, diagnostics=diagnostics)
    return records, diagnostics


def audit_changes(
    context: SolutionContext,
    deployment: str | None,
    *,
    since: str | None,
    until: str | None,
    status: str | None,
) -> tuple[list[DeploymentChangeRecord], Diagnostics]:
    """Top-level orchestration `commands/audit_command.py`'s `changes`
    subcommand calls.

    Same fail-fast order as `audit_status()`, with one extra zero-I/O step
    first: parsing `since`/`until` can fail on a malformed value, and that
    must be caught before `resolve_readable_sink()` even runs, the same
    "tell the user first" discipline applied to a second, independent
    failure mode `status` never had.

    Raises:
        UsageError: A malformed `since`/`until` value, or no/multiple
            readable sinks configured.
        AuditReadError: The sink's remote can't be synced.
    """
    since_bound = _parse_range_bound(since, "--since")
    until_bound = _parse_range_bound(until, "--until", end_of_day=True)

    configuration = _single_configuration(context)
    audit_config = configuration.spec.audit if configuration is not None else None

    sink = resolve_readable_sink(audit_config)
    diagnostics = Diagnostics()
    checkout_path = _read_checkout(context, sink, deployment, diagnostics)
    if checkout_path is None:
        return [], diagnostics

    records = list_manifests_in_range(
        checkout_path,
        sink,
        deployment=deployment,
        since=since_bound,
        until=until_bound,
        status=status,
        diagnostics=diagnostics,
    )
    return records, diagnostics


def _single_configuration(context: SolutionContext) -> ConfigurationModel | None:
    """Same "one declared source of truth, or nothing to check against" rule
    `audit_run.py`'s own `_single_configuration()` already applies — kept as
    its own small copy rather than imported, since that one is private to
    `audit_run.py` and this module's only dependency on it is this one
    three-line lookup."""
    entries = context.controller.index.all_of(PlatformKind.CONFIGURATION)
    if len(entries) != 1:
        return None
    model = entries[0].model
    assert isinstance(model, ConfigurationModel)
    return model
