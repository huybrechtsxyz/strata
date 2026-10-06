#!/usr/bin/env python3
"""Building and validating the promotion view (docs/work/promotion.md).

`kind: promotion` was deliberately never built (see the doc's "No new kind"
section) — ring/order/wave live as metadata on `Version` documents
(`VersionPromotionModel`, Phase 1) instead of a dedicated, user-authored
kind a human has to keep in sync with reality. `PromotionView` is the
in-memory aggregate that replaces it: every tagged `Version` document
sharing one workspace, grouped by `(ring, order)` and split by `wave`,
computed fresh on every call rather than persisted anywhere. Nothing here
is written to disk by this module — `promote status`/`view` (Phase 3/6)
render it, and Phase 2 validation (this module's other job) just runs the
builder and surfaces its `Diagnostics`.

Two things validated, matching the doc's own split by how many documents a
check needs to see:

- Phase 1 (intra-document, `VersionPromotionModel`'s own validators) already
  shipped — `wave` without `ring`, `ring` without `order`.
- Phase 2 (cross-document, this module) — `ring` <-> `order` must biject
  across a workspace, `(ring, order, wave)` must be unique, and a group
  sharing a `(ring, order)` slot can't mix a waveless document with waved
  ones. An orphaned ring (no `Deployment` references it) is a warning, not
  an error — ambiguous enough (typo, or a ring provisioned ahead of real
  deployments) not to hard-fail on.

A `Version` document with no `spec.workspace` or no `spec.promotion.ring`
is simply excluded from every check here — "skip rather than guess", the
same restraint `_check_version_workspace()` applies to an undeclared
workspace. Grouping needs a *declared* workspace; inferring one the way
`_check_version_workspace()` does for its own, narrower purpose would mean
this module silently depending on that check having already run and
resolved order, which it may not have.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import yaml
from packaging.version import InvalidVersion
from packaging.version import Version as SemVer
from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.version_model import VersionModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


class PromotionWaveView(BaseModel):
    """One `Version` document occupying a `(ring, order)` slot.

    `wave` is `None` for a slot with exactly one occupant — the common
    case, worked example A. A slot split across waves has one entry per
    wave, each carrying its own distinct, non-null `wave`.
    """

    wave: str | None
    version: VersionModel


class PromotionRingView(BaseModel):
    """Every document sharing one `(ring, order)` slot, split by wave."""

    ring: str
    order: int
    waves: list[PromotionWaveView]


class PromotionView(BaseModel):
    """Every tagged `Version` document in one workspace, grouped and sorted."""

    workspace: str
    rings: list[PromotionRingView]


def build_promotion_view(
    workspace: str,
    index: DocumentIndex,
    resolved: dict[str, DeploymentModel] | None = None,
) -> tuple[PromotionView, Diagnostics]:
    """Group every tagged `Version` document in `workspace`, and validate it.

    Args:
        workspace: Only `Version` documents whose own `spec.workspace`
            equals this (declared, not inferred — see module docstring)
            participate.
        index: The loaded `DocumentIndex`.
        resolved: `Deployment` documents with `extends` already folded in
            (`resolve_deployment_chains()`'s own return value), used only
            for the orphan-ring warning — a deployment that only gets
            `spec.version` through `extends` should not be reported as
            missing. Pass `None` when extends-resolution was not run (e.g.
            a future standalone `promote status` call before full
            validation); the orphan check then falls back to each
            deployment's own raw, un-resolved document.

    Returns:
        The grouped view (sorted by `order`, each ring's `waves` sorted by
        wave name), and every Phase 2 finding — the view is still returned
        even when findings are non-empty (best-effort: a duplicate slot
        keeps its first-seen occupant, degrading the same way a dangling
        reference does elsewhere), so a caller that only wants the table
        for a workspace with a non-fatal warning is not blocked by it.
    """
    diagnostics = Diagnostics()

    tagged: list[IndexEntry] = []
    for entry in index.all_of(PlatformKind.VERSION):
        version = cast(VersionModel, entry.model)
        if version.spec.workspace != workspace:
            continue
        promotion = version.spec.promotion
        if promotion is None or promotion.ring is None:
            continue
        tagged.append(entry)

    # Check 1: ring <-> order must biject across the workspace.
    orders_by_ring: dict[str, set[int]] = defaultdict(set)
    rings_by_order: dict[int, set[str]] = defaultdict(set)
    for entry in tagged:
        promotion = cast(VersionModel, entry.model).spec.promotion
        assert promotion is not None and promotion.ring is not None and promotion.order is not None
        orders_by_ring[promotion.ring].add(promotion.order)
        rings_by_order[promotion.order].add(promotion.ring)
    for ring, orders in orders_by_ring.items():
        if len(orders) > 1:
            diagnostics.error(
                f"Ring '{ring}' is tagged with more than one order ({sorted(orders)}) in workspace "
                f"'{workspace}' — the same ring must always map to the same order.",
                location="spec.promotion.order",
                code="promotion_ring_order_not_bijective",
            )
    for order, order_rings in rings_by_order.items():
        if len(order_rings) > 1:
            diagnostics.error(
                f"Order {order} is tagged with more than one ring ({sorted(order_rings)}) in workspace "
                f"'{workspace}' — the same order must always map to the same ring.",
                location="spec.promotion.ring",
                code="promotion_ring_order_not_bijective",
            )

    # Group by (ring, order); checks 2 and 3 run per group.
    groups: dict[tuple[str, int], list[IndexEntry]] = defaultdict(list)
    for entry in tagged:
        promotion = cast(VersionModel, entry.model).spec.promotion
        assert promotion is not None and promotion.ring is not None and promotion.order is not None
        groups[(promotion.ring, promotion.order)].append(entry)

    rings: list[PromotionRingView] = []
    for (ring, order), members in groups.items():
        seen_waves: set[str | None] = set()
        waves: list[PromotionWaveView] = []
        for entry in members:
            version = cast(VersionModel, entry.model)
            promotion = version.spec.promotion
            assert promotion is not None
            wave = promotion.wave
            if wave in seen_waves:
                # Check 2: (ring, order, wave) must be unique. Keep the
                # first-seen occupant, same degrade-gracefully pattern a
                # dangling reference uses elsewhere.
                diagnostics.error(
                    f"Version '{entry.ref.name}' duplicates the slot (ring='{ring}', order={order}, "
                    f"wave={wave!r}) already claimed by another document in workspace '{workspace}'.",
                    source=str(entry.source),
                    location="spec.promotion",
                    code="promotion_slot_duplicate",
                )
                continue
            seen_waves.add(wave)
            waves.append(PromotionWaveView(wave=wave, version=version))

        # Check 3: no mixing a waveless document with waved ones.
        if len(waves) > 1 and any(w.wave is None for w in waves):
            diagnostics.error(
                f"Ring '{ring}' order {order} in workspace '{workspace}' mixes a waveless document with "
                "waved ones — every document sharing a (ring, order) slot needs its own distinct 'wave' "
                "once more than one document occupies it.",
                location="spec.promotion.wave",
                code="promotion_mixed_waveless",
            )

        waves.sort(key=lambda w: w.wave or "")
        rings.append(PromotionRingView(ring=ring, order=order, waves=waves))

    rings.sort(key=lambda r: r.order)

    # Orphan-ring warning: a tagged document no Deployment references.
    referenced_versions: set[str] = set()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = (resolved or {}).get(entry.ref.name, cast(DeploymentModel, entry.model))
        if deployment.spec.version:
            referenced_versions.add(deployment.spec.version)
    for entry in tagged:
        if entry.ref.name in referenced_versions:
            continue
        promotion = cast(VersionModel, entry.model).spec.promotion
        assert promotion is not None
        wave_part = f", wave={promotion.wave!r}" if promotion.wave else ""
        diagnostics.warning(
            f"Version '{entry.ref.name}' is tagged (ring='{promotion.ring}', order={promotion.order}"
            f"{wave_part}) but no Deployment references it via 'spec.version'.",
            source=str(entry.source),
            location="spec.promotion",
            code="promotion_orphan_ring",
        )

    return PromotionView(workspace=workspace, rings=rings), diagnostics


def validate_promotions(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """Run Phase 2 promotion validation for every workspace with tagged documents.

    Discovers the distinct, *declared* `spec.workspace` values among tagged
    `Version` documents (an untagged document, or one with no `spec.workspace`
    of its own, contributes nothing to discover — see module docstring), then
    runs `build_promotion_view()` once per workspace and merges every finding.

    Args:
        index: The loaded `DocumentIndex`.
        resolved: `resolve_deployment_chains()`'s own return value — threaded
            through to `build_promotion_view()`'s orphan-ring check.

    Returns:
        Every Phase 2 finding across every tagged workspace.
    """
    diagnostics = Diagnostics()
    workspaces: set[str] = set()
    for entry in index.all_of(PlatformKind.VERSION):
        version = cast(VersionModel, entry.model)
        promotion = version.spec.promotion
        if version.spec.workspace is not None and promotion is not None and promotion.ring is not None:
            workspaces.add(version.spec.workspace)

    for workspace in sorted(workspaces):
        _, found = build_promotion_view(workspace, index, resolved)
        diagnostics.extend(found)
    return diagnostics


@dataclass(frozen=True)
class PromotionStatusRow:
    """One rendered row of `strata promote status` — one pin, in one wave,
    in one ring.

    Not `PromotionView` itself re-shaped: a `Version` document may pin
    several targets at once (the real, evidenced case — `version_model.py`'s
    own docstring cites a 14-pin production file), so a row is per-pin, not
    per-document. `target` is `f"{category}/{name}"` (e.g. `images/dspapi`),
    matching `check_version_pins()`'s own `location` convention. `category`/
    `target`/`value`/`status` are all `None` together exactly once per empty
    wave — a tagged document with no pins declared yet still gets one row,
    so an operator sees the ring exists rather than it silently vanishing
    from the table.
    """

    ring: str
    order: int
    wave: str | None
    category: str | None
    target: str | None
    value: str | None
    status: str | None
    behind: bool


def build_status_rows(view: PromotionView) -> list[PromotionStatusRow]:
    """Expand a `PromotionView` into `promote status`'s rendered rows.

    The `← behind` marker (doc: "computed by comparing orders against each
    other") is computed **within one `(ring, order)` group, per pin target**
    — not across different orders. Worked example B's own "canary runs ahead
    of general" is exactly this: both share `(ring=prd, order=3)`, and only
    `general`'s `images/dspapi` pin trails its sibling wave's value for the
    same target. Comparing across different orders instead would not single
    out `general` the way the worked example shows.

    Deliberately conservative: only computed when a target's value is
    declared by **more than one wave** in the same group (nothing to be
    behind *of* otherwise) **and every one of those values parses as a
    `packaging.version.Version`** — a git ref, commit SHA, or `track: latest`-
    style string cannot be safely ordered, and guessing would be worse than
    not marking it at all ("skip rather than guess", the rule this whole
    design already applies everywhere else).
    """
    rows: list[PromotionStatusRow] = []
    for ring_view in view.rings:
        values_by_pin: dict[tuple[str, str], list[str]] = defaultdict(list)
        for wave_view in ring_view.waves:
            for category, target, pin in wave_view.version.spec.pins.iter_pins():
                values_by_pin[(category, target)].append(pin.version)

        max_by_pin: dict[tuple[str, str], SemVer] = {}
        for pin_key, values in values_by_pin.items():
            if len(values) < 2:
                continue
            try:
                max_by_pin[pin_key] = max(SemVer(value) for value in values)
            except InvalidVersion:
                continue  # Not every sibling value is comparable — skip, don't guess.

        for wave_view in ring_view.waves:
            pins = list(wave_view.version.spec.pins.iter_pins())
            if not pins:
                rows.append(
                    PromotionStatusRow(
                        ring=ring_view.ring,
                        order=ring_view.order,
                        wave=wave_view.wave,
                        category=None,
                        target=None,
                        value=None,
                        status=None,
                        behind=False,
                    )
                )
                continue

            for category, target, pin in pins:
                behind = False
                best = max_by_pin.get((category, target))
                if best is not None:
                    try:
                        behind = SemVer(pin.version) < best
                    except InvalidVersion:
                        behind = False
                rows.append(
                    PromotionStatusRow(
                        ring=ring_view.ring,
                        order=ring_view.order,
                        wave=wave_view.wave,
                        category=category,
                        target=target,
                        value=pin.version,
                        status=pin.status.value,
                        behind=behind,
                    )
                )
    return rows


@dataclass(frozen=True)
class ApplyResult:
    """What `promote apply` actually did — rendered by the command, and the
    return value a test asserts against directly."""

    workspace: str
    ring: str
    order: int
    wave: str | None
    source_version: str
    """`meta.name` of the preceding order's document pins were copied from."""
    target_version: str
    """`meta.name` of the document pins were copied into — same as `ring`/
    `wave`'s own occupant, named explicitly since a caller renders it
    without re-deriving it from the view."""
    copied: list[tuple[str, str, str]]
    """`(category, target, value)` for every pin actually copied — the
    *new* value, post-copy. Empty when source and target share no common
    pin key at all; a real, reportable outcome, not an error (see
    `apply_promotion()`)."""
    path: Path
    """The target document's real file, for the command layer to report."""


def _round_trip_yaml() -> YAML:
    """Mirrors `version_controller._round_trip_yaml()` exactly — quotes
    preserved, no forced line-wrap. Kept as its own copy rather than a
    cross-module import of a private helper: both modules independently
    need "the one ruamel.yaml config for a surgical kind: version edit",
    and the six lines are cheaper to duplicate than to introduce a
    private coupling between two controller modules for.
    """
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    return rt


def _select_target_wave(ring_view: PromotionRingView, wave: str | None) -> PromotionWaveView:
    """Resolve `--wave` against the target ring — strict: a ring with more
    than one wave requires an explicit, matching `--wave` (the doc's own
    settled mechanics: "errors with the wave list rather than guessing").
    """
    if len(ring_view.waves) > 1:
        names = [w.wave for w in ring_view.waves]
        if wave is None:
            raise UsageError(
                f"Ring '{ring_view.ring}' (order {ring_view.order}) has more than one wave — specify --wave. "
                f"Available: {names}."
            )
        match = next((w for w in ring_view.waves if w.wave == wave), None)
        if match is None:
            raise UsageError(
                f"Ring '{ring_view.ring}' (order {ring_view.order}) has no wave named '{wave}'. Available: {names}."
            )
        return match

    only = ring_view.waves[0]
    if wave is not None and only.wave != wave:
        detail = "it has only one, waveless document" if only.wave is None else f"its only wave is '{only.wave}'"
        raise UsageError(f"Ring '{ring_view.ring}' (order {ring_view.order}) has no wave named '{wave}' — {detail}.")
    return only


def _select_source_wave(ring_view: PromotionRingView, target_wave: str | None) -> PromotionWaveView:
    """Resolve the preceding ring's occupant to promote *from* — lenient
    when there is only one: a single-occupant ring applies regardless of
    the target's own wave name (worked example B: `qas` is waveless, but
    still the correct source for `prd`'s `canary` wave). Only a preceding
    ring that is *itself* split requires a same-named wave to match,
    since there would otherwise be no principled way to pick one.
    """
    if len(ring_view.waves) == 1:
        return ring_view.waves[0]

    match = next((w for w in ring_view.waves if w.wave == target_wave), None)
    if match is None:
        names = [w.wave for w in ring_view.waves]
        raise UsageError(
            f"Preceding ring '{ring_view.ring}' (order {ring_view.order}) has more than one wave and none is "
            f"named {target_wave!r} — cannot determine which to promote from. Available: {names}."
        )
    return match


def _common_pin_keys(source: VersionModel, target: VersionModel) -> list[tuple[str, str, str]]:
    """`(category, name, source_value)` for every pin key **both** documents
    already declare — not every key the source declares. A key only the
    source has is not "common", so it is never created on the target as a
    side effect of promoting; that is `version update`'s reconciliation job,
    a deliberately separate, read-only concern (docs/work/version-lifecycle.md).
    """
    target_keys = {(category, name) for category, name, _pin in target.spec.pins.iter_pins()}
    return [
        (category, name, pin.version)
        for category, name, pin in source.spec.pins.iter_pins()
        if (category, name) in target_keys
    ]


def _write_copied_pins(path: Path, common_keys: list[tuple[str, str, str]]) -> None:
    """Surgically set each `(category, name)` pin in `path` to its copied
    `value`, **always** resetting `status` to `current` and `reviewed` to
    today (the doc's own settled mechanics) — never a no-op shorthand
    write, since a reset `status`/`reviewed` always requires the
    structured form. `available`/`reason` are not touched by this function
    at all: `available` is independent upstream-tracking bookkeeping the
    doc never mentions resetting, and `reason` is dropped implicitly by
    writing a fresh structured entry — a `current` pin needs none, and the
    old hold's rationale is exactly what a fresh promotion retires.

    Re-parses the written file through `VersionModel` before returning —
    the same safety net `version_controller.set_version_pin()` already
    established for a surgical `kind: version` edit: a guarantee the write
    produced a still-valid document, not a second read the caller has to
    remember to do itself.
    """
    if not common_keys:
        return
    rt = _round_trip_yaml()
    data = rt.load(path.read_text(encoding="utf-8"))
    pins = data.setdefault("spec", CommentedMap()).setdefault("pins", CommentedMap())
    today = date.today()
    for category, name, value in common_keys:
        category_map = pins.setdefault(category, CommentedMap())
        entry = CommentedMap()
        entry["version"] = value
        entry["status"] = "current"
        entry["reviewed"] = today
        category_map[name] = entry
    with path.open("w", encoding="utf-8") as handle:
        rt.dump(data, handle)
    VersionModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def apply_promotion(
    workspace: str,
    ring: str,
    index: DocumentIndex,
    *,
    wave: str | None = None,
) -> ApplyResult:
    """Copy every common pin key from the preceding order's document into
    `ring`'s, within `workspace` (docs/work/promotion.md's settled
    "mechanics, settled" section).

    Refuses to run at all when `build_promotion_view()` itself reports a
    Phase 2 error for this workspace (ring↔order bijection, duplicate
    slot, mixed waveless/waved) — a deliberate safety margin beyond the
    doc's own literal scope: `promote status` only *displays* a view that
    might be ambiguous, but `apply` *writes a file*, and silently picking
    the first-seen occupant of an ambiguous slot (`build_promotion_view()`'s
    own degrade-gracefully behaviour) is the wrong default for a mutating
    command. Run `strata validate` to see and fix the violation first.

    Args:
        workspace: Same scoping rule as `build_promotion_view()` — only
            `Version` documents with a *declared* `spec.workspace` equal
            to this one participate.
        ring: The target ring's name (`spec.promotion.ring`).
        index: The loaded `DocumentIndex`.
        wave: Required when the target ring has more than one wave;
            rejected (as a mismatch) when given for a single-wave ring
            whose own wave does not match it.

    Returns:
        What happened — including `copied == []`, a real, reportable
        outcome (not an error) when source and target share no common pin
        key at all.

    Raises:
        UsageError: The workspace has an unresolved Phase 2 error; `ring`
            is not tagged in `workspace`; `wave` is required/unmatched/
            mismatched for the target or (less commonly) the source ring;
            or the target ring's order has no preceding order to promote
            from at all.
    """
    view, diagnostics = build_promotion_view(workspace, index)
    if diagnostics.errors:
        raise UsageError(
            f"Workspace '{workspace}' has unresolved promotion inconsistencies — run 'strata validate' and fix "
            f"them before applying a promotion: {'; '.join(d.message for d in diagnostics.errors)}"
        )
    if not view.rings:
        raise UsageError(f"No ring-tagged version documents exist in workspace '{workspace}'.")

    target_ring = next((r for r in view.rings if r.ring == ring), None)
    if target_ring is None:
        available = sorted({r.ring for r in view.rings})
        raise UsageError(f"No ring named '{ring}' is tagged in workspace '{workspace}'. Available: {available}.")
    target_wave = _select_target_wave(target_ring, wave)

    preceding_orders = sorted((r.order for r in view.rings if r.order < target_ring.order), reverse=True)
    if not preceding_orders:
        raise UsageError(
            f"Ring '{ring}' (order {target_ring.order}) has no preceding order in workspace '{workspace}' to "
            "promote from."
        )
    source_ring = next(r for r in view.rings if r.order == preceding_orders[0])
    source_wave = _select_source_wave(source_ring, target_wave.wave)

    source_version = source_wave.version
    target_version = target_wave.version
    common_keys = _common_pin_keys(source_version, target_version)

    target_entry = index.get(PlatformKind.VERSION, target_version.meta.name)
    assert target_entry is not None  # came from the index itself; always resolves

    _write_copied_pins(target_entry.source, common_keys)

    return ApplyResult(
        workspace=workspace,
        ring=ring,
        order=target_ring.order,
        wave=target_wave.wave,
        source_version=source_version.meta.name,
        target_version=target_version.meta.name,
        copied=common_keys,
        path=target_entry.source,
    )
