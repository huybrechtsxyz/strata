#!/usr/bin/env python3
"""Building and validating the promotion view (docs/work/promotion.md, rebuilt on the deployments' pin matrices in
docs/work/version-composition.md V9-V11).

`kind: promotion` was deliberately never built (see promotion.md's "No new kind" section) — ring/order/wave live
as metadata on `Version` documents (`VersionPromotionModel`) instead of a dedicated, user-authored kind a human has
to keep in sync with reality. `PromotionView` is the in-memory aggregate that replaces it, computed fresh on every
call rather than persisted anywhere.

What a workspace runs in a ring is read from its **deployments** (a version document names no workspace, ADR-0065):
a deployment lists one or more version documents, and the ones tagged with `spec.promotion` say which
`(ring, order, wave)` slot it is in (V9). A slot's rows are the pins its deployments run with (their composed
matrices, restricted to the targets they use), each with the document that owns it (V10). A partial base deploys
nothing, so only deployable deployments place a workspace in a slot.

Checked across a workspace's slots, as before: `ring` <-> `order` must biject, `(ring, order, wave)` must be unique,
and a `(ring, order)` slot cannot mix a waveless document with waved ones. A tagged document no deployment lists is
a warning (`promotion_orphan_ring`).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

import yaml
from packaging.version import InvalidVersion
from packaging.version import Version as SemVer
from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from strata.controllers.deployment_resolution import deployment_of, first_tagged_version, resolve_deployment_chains
from strata.controllers.pinnable_targets import pinnable_targets
from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.controllers.version_composition import compose_versions
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.version_model import VersionModel, pin_key, pin_owner
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.sbom_utils import canonical_image_name, parse_image_ref, split_image_tag


class PromotionPinView(BaseModel):
    """One pin a slot's deployments run with: its value, the document that owns it, and who runs it.

    `shared` is true when deployments of another workspace also list the owning document: promoting it moves
    those too, so `promote apply` skips it unless `--shared` is given (V11).
    """

    category: str
    target: str
    value: str
    status: str
    owner: str
    shared: bool = False
    deployments: list[str] = []


class PromotionWaveView(BaseModel):
    """One tagged `Version` document occupying a `(ring, order)` slot, with the deployments that list it.

    `wave` is `None` for a slot with exactly one occupant — the common case, worked example A. A slot split
    across waves has one entry per wave, each carrying its own distinct, non-null `wave`. `pins` are what the
    deployments run with (their composed pins, owners included); for a document no deployment lists, its own.
    """

    wave: str | None
    version: VersionModel
    deployments: list[str] = []
    pins: list[PromotionPinView] = []


class PromotionRingView(BaseModel):
    """Every document sharing one `(ring, order)` slot, split by wave."""

    ring: str
    order: int
    waves: list[PromotionWaveView]


class PromotionView(BaseModel):
    """Every slot of one workspace's rollout, grouped and sorted."""

    workspace: str
    rings: list[PromotionRingView]


@dataclass
class _Slot:
    """A tagged document as a slot of one workspace, and that workspace's deployments listing it."""

    entry: IndexEntry
    deployments: list[DeploymentModel] = field(default_factory=list)


def _deployable(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> list[DeploymentModel]:
    """Every deployable deployment: no partial base, a workspace, resolved through `extends`."""
    found: list[DeploymentModel] = []
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = deployment_of(entry, resolved)
        if deployment is not None and not deployment.spec.partial and deployment.spec.workspace:
            found.append(deployment)
    return found


def _tagged_document(index: DocumentIndex, deployment: DeploymentModel) -> IndexEntry | None:
    """The first tagged version document `deployment` lists: the slot it is in (V9)."""
    return first_tagged_version(index, deployment.spec.version_names())


def _listing_workspaces(deployments: list[DeploymentModel]) -> dict[str, set[str]]:
    """Version document name -> the workspaces of the deployments listing it."""
    users: dict[str, set[str]] = defaultdict(set)
    for deployment in deployments:
        for name in deployment.spec.version_names():
            users[name].add(str(deployment.spec.workspace))
    return users


def _slots(index: DocumentIndex, workspace: str, deployments: list[DeploymentModel]) -> list[_Slot]:
    """The tagged documents `workspace`'s deployments are in, each with those deployments."""
    slots: dict[str, _Slot] = {}
    for deployment in deployments:
        if deployment.spec.workspace != workspace:
            continue
        entry = _tagged_document(index, deployment)
        if entry is not None:
            slots.setdefault(entry.ref.name, _Slot(entry)).deployments.append(deployment)
    return list(slots.values())


def _slot_pins(index: DocumentIndex, workspace: str, slot: _Slot, users: dict[str, set[str]]) -> list[PromotionPinView]:
    """What the slot's deployments run with, one row per (pin, value, owner).

    Every pin of the slot's own tagged document is shown: it is this product's document, so a deployment listing only
    it shows exactly its pins, as before composition (R9). A pin another listed document owns (a shared core) is
    shown only where the deployment uses its target, so a shared document's pins for other products stay out.
    """
    rows: dict[tuple[str, str, str, str], PromotionPinView] = {}
    for deployment in slot.deployments:
        composed, _ = compose_versions(index, deployment.spec.version_names(), label=deployment.meta.name)
        if composed is None:
            continue
        targets = pinnable_targets(index, deployment)
        for category, target, pin in composed.spec.pins.iter_pins():
            owner = pin_owner(composed, category, target)
            if owner != slot.entry.ref.name and (category, target) not in targets:
                continue
            key = (category, target, pin.version, owner)
            if key not in rows:
                rows[key] = PromotionPinView(
                    category=category,
                    target=target,
                    value=pin.version,
                    status=pin.status.value,
                    owner=owner,
                    shared=bool(users.get(owner, set()) - {workspace}),
                )
            rows[key].deployments.append(deployment.meta.name)
    return sorted(rows.values(), key=lambda row: (row.category, row.target, row.owner))


def build_promotion_view(
    workspace: str,
    index: DocumentIndex,
    resolved: dict[str, DeploymentModel] | None = None,
) -> tuple[PromotionView, Diagnostics]:
    """Group `workspace`'s rollout slots, and validate them.

    Args:
        workspace: The workspace whose deployments' slots are shown.
        index: The loaded `DocumentIndex`.
        resolved: `Deployment` documents with `extends` already folded in (`resolve_deployment_chains()`'s own
            return value); resolved here when not given.

    Returns:
        The grouped view (sorted by `order`, each ring's `waves` sorted by wave name), and every finding — the view
        is still returned when findings are non-empty (a duplicate slot keeps its first-seen occupant), so a caller
        that only wants the table is not blocked by a non-fatal warning.
    """
    diagnostics = Diagnostics()
    if resolved is None:
        resolved, _ = resolve_deployment_chains(index)
    deployments = _deployable(index, resolved)
    users = _listing_workspaces(deployments)
    slots = _slots(index, workspace, deployments)
    tagged = [slot.entry for slot in slots]

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
    groups: dict[tuple[str, int], list[_Slot]] = defaultdict(list)
    for slot in slots:
        promotion = cast(VersionModel, slot.entry.model).spec.promotion
        assert promotion is not None and promotion.ring is not None and promotion.order is not None
        groups[(promotion.ring, promotion.order)].append(slot)

    rings: list[PromotionRingView] = []
    for (ring, order), members in groups.items():
        seen_waves: set[str | None] = set()
        waves: list[PromotionWaveView] = []
        for slot in members:
            version = cast(VersionModel, slot.entry.model)
            promotion = version.spec.promotion
            assert promotion is not None
            wave = promotion.wave
            if wave in seen_waves:
                # Check 2: (ring, order, wave) must be unique. Keep the first-seen occupant.
                diagnostics.error(
                    f"Version '{slot.entry.ref.name}' duplicates the slot (ring='{ring}', order={order}, "
                    f"wave={wave!r}) already claimed by another document in workspace '{workspace}'.",
                    source=str(slot.entry.source),
                    location="spec.promotion",
                    code="promotion_slot_duplicate",
                )
                continue
            seen_waves.add(wave)
            waves.append(
                PromotionWaveView(
                    wave=wave,
                    version=version,
                    deployments=sorted(deployment.meta.name for deployment in slot.deployments),
                    pins=_slot_pins(index, workspace, slot, users),
                )
            )

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

    return PromotionView(workspace=workspace, rings=rings), diagnostics


def _referenced(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> set[str]:
    """Every version document some deployment lists, a partial base included (it may be listed for later)."""
    names: set[str] = set()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = deployment_of(entry, resolved)
        if deployment is not None:
            names.update(deployment.spec.version_names())
    return names


def _orphan(entry: IndexEntry) -> Diagnostics:
    diagnostics = Diagnostics()
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
    return diagnostics


def rollout_workspaces(index: DocumentIndex, resolved: dict[str, DeploymentModel] | None = None) -> list[str]:
    """Every workspace in a rollout: those whose deployable deployments list a tagged document. `promote status`
    without a workspace shows each of them."""
    if resolved is None:
        resolved, _ = resolve_deployment_chains(index)
    workspaces: set[str] = set()
    for deployment in _deployable(index, resolved):
        if _tagged_document(index, deployment) is not None:
            workspaces.add(str(deployment.spec.workspace))
    return sorted(workspaces)


def validate_promotions(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """Run the promotion checks for every workspace in a rollout, and warn on every tagged document no deployment (a
    partial base included) lists (`promotion_orphan_ring`): it belongs to no workspace's view."""
    diagnostics = Diagnostics()
    listed = _referenced(index, resolved)
    for entry in index.all_of(PlatformKind.VERSION):
        promotion = cast(VersionModel, entry.model).spec.promotion
        if promotion is not None and promotion.ring is not None and entry.ref.name not in listed:
            diagnostics.extend(_orphan(entry))

    for workspace in rollout_workspaces(index, resolved):
        _, found = build_promotion_view(workspace, index, resolved)
        diagnostics.extend(found)
    return diagnostics


@dataclass(frozen=True)
class PromotionStatusRow:
    """One rendered row of `strata promote status` — one pin, in one wave, in one ring.

    `target` is `f"{category}/{name}"`-able (`category` and `target` separately), matching `check_version_pins()`'s
    own `location` convention. `category`/`target`/`value`/`status`/`owner` are all `None` together exactly once
    per empty wave — a tagged document with no pins declared yet still gets one row, so an operator sees the ring
    exists rather than it silently vanishing from the table. `owner` is the document the pin comes from, `shared`
    whether deployments of another workspace list it too.
    """

    ring: str
    order: int
    wave: str | None
    category: str | None
    target: str | None
    value: str | None
    status: str | None
    behind: bool
    owner: str | None = None
    shared: bool = False


def build_status_rows(view: PromotionView) -> list[PromotionStatusRow]:
    """Expand a `PromotionView` into `promote status`'s rendered rows.

    The `← behind` marker is computed **within one `(ring, order)` group, per pin target** — not across different
    orders: only `general`'s `images/api` pin trailing its sibling wave's value for the same target is marked
    (promotion.md worked example B). Deliberately conservative: only when a target's value is declared by more than
    one wave in the group and every one of those values parses as a version — a git ref or commit SHA cannot be
    safely ordered.
    """
    rows: list[PromotionStatusRow] = []
    for ring_view in view.rings:
        values_by_pin: dict[tuple[str, str], list[str]] = defaultdict(list)
        for wave_view in ring_view.waves:
            for pin in wave_view.pins:
                values_by_pin[(pin.category, pin.target)].append(pin.value)

        max_by_pin: dict[tuple[str, str], SemVer] = {}
        for pin_ref, values in values_by_pin.items():
            if len(values) < 2:
                continue
            try:
                parsed = [_pin_order(pin_ref[0], value) for value in values]
            except InvalidVersion:
                continue  # Not every sibling value is comparable — skip, don't guess.
            if len({image for image, _ in parsed}) > 1:
                continue  # Siblings pin different images: their tags do not order.
            max_by_pin[pin_ref] = max(version for _, version in parsed)

        for wave_view in ring_view.waves:
            if not wave_view.pins:
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

            for pin in wave_view.pins:
                behind = False
                best = max_by_pin.get((pin.category, pin.target))
                if best is not None:
                    try:
                        behind = _pin_order(pin.category, pin.value)[1] < best
                    except InvalidVersion:
                        behind = False
                rows.append(
                    PromotionStatusRow(
                        ring=ring_view.ring,
                        order=ring_view.order,
                        wave=wave_view.wave,
                        category=pin.category,
                        target=pin.target,
                        value=pin.value,
                        status=pin.status,
                        behind=behind,
                        owner=pin.owner,
                        shared=pin.shared,
                    )
                )
    return rows


def _pin_order(category: str, value: str) -> tuple[str, SemVer]:
    """`(image, version)` to order a pin's value by; `image` is empty except for an `images` pin.

    An `images` pin is a whole reference (`acme/api:1.2.0`), so its tag is the version and the image it names must
    match its siblings'. An `artifacts` pin of `tag@digest` orders by its tag. Raises `InvalidVersion` for a value with
    no version-shaped part (`16-alpine`, a digest alone, a git ref).
    """
    image = ""
    version: str | None = value
    if category == "images":
        name, version, _ = parse_image_ref(value)
        image = canonical_image_name(name)
    elif category == "artifacts":
        version = split_image_tag(value)[0]
    if not version:
        raise InvalidVersion(value)
    return image, SemVer(version)


@dataclass(frozen=True)
class ApplyResult:
    """What `promote apply` actually did — rendered by the command, and the return value a test asserts against
    directly."""

    workspace: str
    ring: str
    order: int
    wave: str | None
    source_version: str
    """`meta.name` of the preceding order's slot document pins were copied from."""
    target_version: str
    """`meta.name` of the target slot's tagged document."""
    copied: list[tuple[str, str, str]]
    """`(category, target, value)` for every pin actually written, in any owning document — the *new* value."""
    path: Path
    """The target slot document's real file, for the command layer to report."""
    written: list[tuple[str, str, str, str]] = field(default_factory=list)
    """`(category, target, value, owner)`: each written pin and the document it was written into (V11)."""
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    """`(category/target, owner, reason)`: each source pin not written, and why — `shared`, `own document`, or
    `not pinned`. Nothing is dropped silently."""


def _round_trip_yaml() -> YAML:
    """Mirrors `version_controller._round_trip_yaml()` exactly — quotes preserved, no forced line-wrap."""
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    return rt


def _select_target_wave(ring_view: PromotionRingView, wave: str | None) -> PromotionWaveView:
    """Resolve `--wave` against the target ring — strict: a ring with more than one wave requires an explicit,
    matching `--wave` ("errors with the wave list rather than guessing")."""
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
    """Resolve the preceding ring's occupant to promote *from* — lenient when there is only one: a single-occupant
    ring applies regardless of the target's own wave name (worked example B). Only a preceding ring that is itself
    split requires a same-named wave to match."""
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


def _is_own_document(pin: PromotionPinView, wave: PromotionWaveView) -> bool:
    """A pin owned by a document only some of the wave's deployments list: a customer's own document, which moves on
    its own cadence (R5), never promoted from or into."""
    everyone = set(wave.deployments)
    return bool(everyone) and pin.owner != wave.version.meta.name and set(pin.deployments) != everyone


def _source_values(source: PromotionWaveView) -> dict[tuple[str, str], set[str]]:
    """`(category, target) -> values` the source wave's deployments run in common: rows of a customer's own document
    are left out, since what one customer runs is not what the ring promotes."""
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for pin in source.pins:
        if not _is_own_document(pin, source):
            values[(pin.category, pin.target)].add(pin.value)
    return values


def _write_copied_pins(path: Path, common_keys: list[tuple[str, str, str]]) -> None:
    """Surgically set each `(category, name)` pin in `path` to its copied `value`, **always** resetting `status`
    to `current` and `reviewed` to today — never a no-op shorthand write. `available` is not touched; `reason` is
    dropped by writing a fresh structured entry (a `current` pin needs none).

    Re-parses the written file through `VersionModel` before returning, the same safety net
    `version_controller.set_version_pin()` established for a surgical `kind: version` edit.
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
    shared: bool = False,
    resolved: dict[str, DeploymentModel] | None = None,
) -> ApplyResult:
    """Promote `workspace` into `ring`: every pin its deployments in `ring` run that the preceding ring's deployments
    also run gets the preceding ring's value, written into the document that owns it (V11).

    - A pin owned by a document that deployments of another workspace list too (a shared `prd-core`) is skipped
      unless `shared` (`--shared`): promoting one product never silently moves another.
    - A pin owned by a document only some of the wave's deployments list (a customer's own document) is never
      written: customers move on their own cadence (R5).
    - A source pin the target does not pin is reported, not created (`version update`'s job).

    Refuses to run when `build_promotion_view()` reports an error for this workspace (ring/order bijection,
    duplicate slot, mixed waveless/waved): `apply` writes files, so an ambiguous slot is not guessed.

    Raises:
        UsageError: The workspace has an unresolved promotion error; `ring` is not in its rollout; `wave` is
            required/unmatched/mismatched; there is no preceding order; or the source deployments disagree.
    """
    view, diagnostics = build_promotion_view(workspace, index, resolved)
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
    source_values = _source_values(source_wave)

    by_owner: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    written: list[tuple[str, str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    disagreeing: dict[tuple[str, str], set[str]] = {}
    seen: set[tuple[str, str, str]] = set()
    source_any = {(pin.category, pin.target) for pin in source_wave.pins}
    for pin in target_wave.pins:
        key = (pin.category, pin.target)
        # One decision per (key, owner): two rows of one owner differ only in value or in who runs them.
        if (*key, pin.owner) in seen:
            continue
        seen.add((*key, pin.owner))
        if key not in source_values:
            if key in source_any:
                # The source ring runs it only from customers' own documents: no one value to promote.
                skipped.append((pin_key(*key), pin.owner, "source own documents"))
            continue
        if pin.shared and not shared:
            skipped.append((pin_key(*key), pin.owner, "shared"))
            continue
        if _is_own_document(pin, target_wave):
            skipped.append((pin_key(*key), pin.owner, "own document"))
            continue
        values = source_values[key]
        if len(values) > 1:
            disagreeing[key] = values
            continue
        value = next(iter(values))
        by_owner[pin.owner].append((pin.category, pin.target, value))
        written.append((pin.category, pin.target, value, pin.owner))
    if disagreeing:
        # Only for what would be written: a key the target skips anyway does not block the rest.
        shown = "; ".join(f"{pin_key(*key)}: {sorted(found)}" for key, found in sorted(disagreeing.items()))
        raise UsageError(
            f"The deployments of ring '{source_ring.ring}' do not agree on what to promote ({shown}) — align them "
            "first, or promote from one wave with --wave."
        )
    for key in sorted(set(source_values) - {(pin.category, pin.target) for pin in target_wave.pins}):
        skipped.append((pin_key(*key), "", "not pinned"))

    for owner, keys in by_owner.items():
        owner_entry = index.get(PlatformKind.VERSION, owner)
        assert owner_entry is not None  # came from the index itself; always resolves
        _write_copied_pins(owner_entry.source, keys)

    target_entry = index.get(PlatformKind.VERSION, target_wave.version.meta.name)
    assert target_entry is not None
    return ApplyResult(
        workspace=workspace,
        ring=ring,
        order=target_ring.order,
        wave=target_wave.wave,
        source_version=source_wave.version.meta.name,
        target_version=target_wave.version.meta.name,
        copied=[(category, target, value) for category, target, value, _owner in written],
        path=target_entry.source,
        written=written,
        skipped=skipped,
    )
