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

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from strata.controllers.audit_run import MANIFEST_FILENAME
from strata.controllers.git_checkout import ensure_synced_checkout
from strata.controllers.solution_context import SolutionContext
from strata.models.audit_manifest_model import ChangeReferenceModel, DeploymentManifestModel
from strata.models.audit_model import AuditConfigModel, AuditGitSinkTargetModel
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
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
    version: str
    deployed_by: str
    completed_at: str
    change_reference: ChangeReferenceModel | None


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
            about it, never a generic "ambiguous"/"not found".
    """
    sinks = audit_config.sinks if audit_config is not None else None
    git_sinks = [sink for sink in (sinks or []) if sink.enabled and sink.git is not None]

    if not git_sinks:
        raise UsageError(
            "No audit git sink is configured for this solution. 'strata audit status' reads from a "
            "configured 'git' audit sink, and none exists — add one under spec.audit.sinks in your "
            "Configuration document. See docs/design/audit-trail.md and docs/design/audit-commands.md."
        )
    if len(git_sinks) > 1:
        names = ", ".join(f"'{sink.name}'" for sink in git_sinks)
        raise UsageError(
            f"Multiple git audit sinks are configured ({names}) — strata audit status supports exactly "
            "one for now. See docs/design/audit-commands.md."
        )

    git_sink = git_sinks[0].git
    assert git_sink is not None  # filtered for above — narrows the type for mypy
    return git_sink


def sync_read_checkout(root: Path, solution: SolutionModel | None, sink: AuditGitSinkTargetModel) -> Path:
    """Resolve `sink.remote`, then fetch/reset a dedicated read-only checkout.

    By the time `audit_status()` calls this, `require_valid()` has already
    confirmed `sink.remote` names a real declared remote (`RemoteReference()`
    on `AuditGitSinkTargetModel.remote`) — the "not declared" branch below is
    defensive, not expected to trigger in the normal command flow. "Wrong
    remote type" is not schema-checked (a remote reference only confirms a
    name exists, not which `type` it has — the same gap `audit_push.py`'s
    own `push_audit_files()` guards against at runtime), so it is a real,
    reachable case here.

    Raises:
        AuditReadError: The remote isn't declared, isn't a `git` remote, or
            the clone/fetch/reset itself failed.
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
    synced = ensure_synced_checkout(remote.url, sink.branch, checkout_path)
    if not synced.success:
        raise AuditReadError(synced.detail)
    return checkout_path


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

    A manifest that fails to parse (corrupted file, a record written by an
    incompatible strata version) is skipped with a warning, never aborts
    the whole command — one bad record must not hide every good one.
    """
    search_root = checkout_path / sink.path
    latest_by_deployment: dict[str, DeploymentManifestModel] = {}

    for manifest_path in sorted(search_root.rglob(MANIFEST_FILENAME)):
        try:
            manifest = DeploymentManifestModel.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, PydanticValidationError, ValueError) as exc:
            diagnostics.warning(
                f"could not read audit record: {exc}",
                source=str(manifest_path),
                code="audit_read_manifest_unparseable",
            )
            continue

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
            version=manifest.version or "unknown",
            deployed_by=manifest.deployed_by or "unknown",
            completed_at=manifest.completed_at or manifest.started_at,
            change_reference=manifest.change_reference,
        )
        for manifest in sorted(latest_by_deployment.values(), key=lambda m: m.deployment)
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
    checkout_path = sync_read_checkout(context.root, context.controller.solution, sink)

    diagnostics = Diagnostics()
    records = list_latest_per_deployment(checkout_path, sink, deployment=deployment, diagnostics=diagnostics)
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
