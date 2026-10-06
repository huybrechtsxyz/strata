#!/usr/bin/env python3
"""`strata version new`/`update`/`set` — docs/work/version-lifecycle.md
Phases 3-4: scaffolding a new `kind: version` document, reconciling an
existing one's pin *keys* against the solution's real inventory of
pinnable targets, and surgically mutating one pin's `version`/`available`.

Shares the same four categories `check_version_pins()` (`version_pins.py`)
already checks, but answers the inverse question: that module asks "does
this *already-declared* pin's target exist"; this asks "what targets exist
with **no** pin yet (candidates to add), and what pins name a target that
no longer exists (candidates to remove)". A deliberately separate,
small inventory walk here, rather than threading a second return shape
through the existing Phase 2 check — the two questions read more clearly
kept apart.

Both `new --from` and `update` share one rule, ADR-0019's own: a newly
added pin key is seeded with its target's CURRENT real value (so adding it
changes nothing by itself), and this module never invents a value for a
target that declares none of its own — such a target is simply not
offered as an add candidate (`VersionPinModel.version` is non-empty by
schema; there is nothing honest to seed it with).

`set_version_pin()` (Phase 4) is the one function here that writes to an
*existing* file rather than a brand-new one, and does it with `ruamel.yaml`'s
round-trip mode, not `yaml.safe_dump` — the whole reason this doc settled
on that library: a hand-edited `kind: version` document's comments,
ordering and quote style must survive a single targeted pin edit
untouched, which a parse-and-re-dump through plain PyYAML cannot do.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError as PydanticValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from strata.controllers.solution_controller import DocumentIndex
from strata.models.artifact_model import ArtifactModel
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.module_model import ModuleModel
from strata.models.solution_model import RemoteFetch, SolutionModel
from strata.models.version_model import PIN_CATEGORIES, VersionModel
from strata.utils.errors import UsageError


def _image_targets(index: DocumentIndex) -> dict[str, str | None]:
    """`ModuleServiceModel.name` -> its own `.image`, across every Module."""
    targets: dict[str, str | None] = {}
    for entry in index.all_of(PlatformKind.MODULE):
        module = cast(ModuleModel, entry.model)
        for service in module.spec.services or []:
            targets[service.name] = service.image
    return targets


def _chart_targets(index: DocumentIndex) -> dict[str, str | None]:
    """Module document name -> its source's `chart_version`, chart-based
    modules only — a chart pin only ever applies to one
    (`check_version_pins()`'s own `pin_not_applicable` rule)."""
    targets: dict[str, str | None] = {}
    for entry in index.all_of(PlatformKind.MODULE):
        module = cast(ModuleModel, entry.model)
        if module.spec.source.chart_name is not None:
            targets[entry.ref.name] = module.spec.source.chart_version
    return targets


def _remote_targets(solution: SolutionModel | None) -> dict[str, str | None]:
    """`SolutionRemoteModel.name` -> `.reference`, `fetch: strata` only — a
    `fetch: external` remote is placed before strata runs, so a pin can
    never take effect against it (same rule `check_version_pins()` already
    enforces as an error, not silently skipped here)."""
    if solution is None:
        return {}
    return {
        remote.name: remote.reference for remote in (solution.spec.remotes or []) if remote.fetch is RemoteFetch.STRATA
    }


def _artifact_targets(index: DocumentIndex) -> dict[str, str | None]:
    """`ArtifactModel.meta.name` -> `spec.image_tag`."""
    targets: dict[str, str | None] = {}
    for entry in index.all_of(PlatformKind.ARTIFACT):
        artifact = cast(ArtifactModel, entry.model)
        targets[entry.ref.name] = artifact.spec.image_tag
    return targets


def _inventory(index: DocumentIndex, solution: SolutionModel | None) -> dict[str, dict[str, str | None]]:
    """Every real pinnable target, by category, with its own current value."""
    return {
        "images": _image_targets(index),
        "charts": _chart_targets(index),
        "remotes": _remote_targets(solution),
        "artifacts": _artifact_targets(index),
    }


@dataclass(frozen=True)
class ReconcileRow:
    """One pin-key reconciliation candidate. Never applied automatically —
    `version update` only ever reports these; the operator edits by hand."""

    category: str
    name: str
    action: str
    """`"add"` or `"remove"`."""
    seed_value: str | None = None
    """For `"add"` only: the target's current real value, to seed the new
    pin with so adding the key changes nothing by itself."""


def reconcile_version(
    index: DocumentIndex, solution: SolutionModel | None, version: VersionModel
) -> list[ReconcileRow]:
    """Compare `version.spec.pins` against the solution's real inventory.

    Read-only: never mutates `version` or any file. Returns every add/remove
    candidate, sorted by category (declaration order) then name. A real
    target with no current value of its own (e.g. a module service that
    declares no `image`) is never offered as an "add" candidate — there is
    nothing honest to seed it with, same rule `scaffold_version()`'s own
    `--from` cloning follows.
    """
    inventory = _inventory(index, solution)
    declared: dict[str, set[str]] = {category: set() for category in PIN_CATEGORIES}
    for category, name, _pin in version.spec.pins.iter_pins():
        declared[category].add(name)

    rows: list[ReconcileRow] = []
    for category in PIN_CATEGORIES:
        real_targets = inventory[category]
        for name in sorted(declared[category] - real_targets.keys()):
            rows.append(ReconcileRow(category=category, name=name, action="remove"))
        for name in sorted(real_targets.keys() - declared[category]):
            seed_value = real_targets[name]
            if seed_value is not None:
                rows.append(ReconcileRow(category=category, name=name, action="add", seed_value=seed_value))
    return rows


def scaffold_version(
    root: Path,
    index: DocumentIndex,
    solution: SolutionModel | None,
    name: str,
    *,
    workspace: str,
    from_name: str | None,
) -> Path:
    """Create `versions/<name>.yaml` — a new, minimal `kind: version` document.

    `--from` (`from_name`) clones an existing version document's pin *keys*
    only: each cloned key is reseeded with its target's CURRENT real
    effective value, never the source document's own value/status/reason/
    reviewed — a starting-point skeleton, not a copy of history. A cloned
    key whose target currently declares no value of its own is dropped
    rather than invented (same rule `reconcile_version()`'s "add" rows
    already follow).

    Raises:
        UsageError: `name` already names an indexed version document or an
            on-disk file at the target path; `workspace` names no indexed
            Workspace document; `from_name` names no indexed version
            document; or `name` would not produce a schema-valid `kind:
            version` document (e.g. doesn't match `PlatformName`'s
            pattern) — caught before writing, not left for the next
            `strata validate` to discover.
    """
    if index.get(PlatformKind.VERSION, name) is not None:
        raise UsageError(f"A version document named '{name}' already exists.")
    if index.get(PlatformKind.WORKSPACE, workspace) is None:
        raise UsageError(f"--workspace names an unknown workspace: '{workspace}'.")

    destination = root / "versions" / f"{name}.yaml"
    if destination.exists():
        raise UsageError(f"'{destination.relative_to(root)}' already exists.")

    pins: dict[str, dict[str, str]] = {}
    if from_name is not None:
        source_entry = index.get(PlatformKind.VERSION, from_name)
        if source_entry is None:
            raise UsageError(f"--from names an unknown version document: '{from_name}'.")
        source = cast(VersionModel, source_entry.model)
        inventory = _inventory(index, solution)
        for category, target_name, _pin in source.spec.pins.iter_pins():
            seed = inventory.get(category, {}).get(target_name)
            if seed is not None:
                pins.setdefault(category, {})[target_name] = seed

    document: dict[str, Any] = {
        "apiVersion": PlatformVersion.v2.value,
        "kind": PlatformKind.VERSION.value,
        "meta": {"name": name},
        "spec": {"workspace": workspace, "pins": pins},
    }
    try:
        VersionModel.model_validate(document)
    except PydanticValidationError as exc:
        raise UsageError(f"'{name}' would not be a valid version document: {exc}") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return destination


#: Statuses that refuse a `version` change without `--force` (`VersionPinStatus`'s
#: own `held`/`unverified` values, as plain strings — the raw ruamel node may
#: be a bare string's implicit `current`, never one of these, so comparing
#: against the literal values here avoids importing the enum just to re-wrap
#: a value already read off the document).
_PROTECTED_STATUSES = ("held", "unverified")


def _round_trip_yaml() -> YAML:
    """One `ruamel.yaml` instance per call, configured for a surgical edit:
    quotes preserved verbatim, no forced line-wrapping of a long value
    (image refs/chart versions routinely exceed the default ~80-column
    wrap width), and otherwise untouched defaults — round-trip mode's own
    job is to keep whatever formatting was already there, not re-derive it.
    """
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    return rt


def set_version_pin(
    path: Path,
    category: str,
    target: str,
    value: str | None,
    *,
    available: str | None,
    force: bool,
) -> VersionModel:
    """Surgically set `spec.pins.<category>.<target>`'s `version` and/or
    `available` in `path`, preserving every other byte of the file —
    comments, key ordering, quote style, unrelated pins.

    Creates the pin if `target` is not yet declared (requires `value`: there
    is nothing honest to create an entry with otherwise). Never touches
    `status`/`reason`/`reviewed` — resolving a hold is a separate, deliberate
    edit. Refuses to change an existing `held`/`unverified` pin's `version`
    without `force`; `available`-only changes are always allowed regardless
    of status.

    Args:
        path: The version document's real file path (e.g. from
            `path_controller.get_path()`).
        category: One of `PIN_CATEGORIES`.
        target: The pin's own key (service/module/remote/artifact name).
        value: New `version` value, or `None` to leave it unchanged.
        available: New `available` value, or `None` to leave it unchanged.
        force: Override the held/unverified refusal.

    Returns:
        The written document, re-parsed through `VersionModel` — a
        guarantee the write produced a still-valid document, not a second
        read the caller has to remember to do.

    Raises:
        UsageError: `category` is not a real pin category; neither `value`
            nor `available` given; `value`/`available` given as an empty
            string; `target` does not exist yet and `value` is `None`; or
            `target` exists, is `held`/`unverified`, `value` is given, and
            `force` is not set.
    """
    if category not in PIN_CATEGORIES:
        raise UsageError(f"Unknown pin category '{category}'. Expected one of: {', '.join(PIN_CATEGORIES)}.")
    if value is None and available is None:
        raise UsageError("Provide <value> and/or --available — nothing to set.")
    if value is not None and not value.strip():
        raise UsageError("<value> must not be empty.")
    if available is not None and not available.strip():
        raise UsageError("--available must not be empty.")

    rt = _round_trip_yaml()
    data = rt.load(path.read_text(encoding="utf-8"))

    pins = data.setdefault("spec", CommentedMap()).setdefault("pins", CommentedMap())
    category_map = pins.setdefault(category, CommentedMap())
    existing = category_map.get(target)

    if existing is None:
        if value is None:
            raise UsageError(
                f"Pin '{target}' does not exist yet in category '{category}' — provide <value> to create it."
            )
        status = None
    elif isinstance(existing, str):
        status = None  # shorthand always means status: current (VersionPinModel's own default)
    else:
        status = existing.get("status")

    if value is not None and status in _PROTECTED_STATUSES and not force:
        raise UsageError(f"Pin '{target}' is '{status}' — refusing to change its version without --force.")

    if existing is None or isinstance(existing, str):
        current_version = value if value is not None else existing
        if available is None:
            category_map[target] = current_version  # stays/becomes shorthand — minimal diff
        else:
            new_entry = CommentedMap()
            new_entry["version"] = current_version
            new_entry["available"] = available
            category_map[target] = new_entry
    else:
        if value is not None:
            existing["version"] = value
        if available is not None:
            existing["available"] = available

    with path.open("w", encoding="utf-8") as handle:
        rt.dump(data, handle)

    return VersionModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
