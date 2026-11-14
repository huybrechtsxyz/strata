#!/usr/bin/env python3
"""A deployment's version documents, composed into one set of pins (docs/design/version-composition.md V1-V9).

`Deployment.spec.version` names one `kind: version` document or a list of them. Their pins apply together: the
effective pins are the union, and every `category/target` has exactly one owning document. Two listed documents
pinning the same target is an error (`version_pin_conflict`, V3), even with equal values, so `version set`,
`version update` and `promote apply` never have to choose between two owners.

`compose_versions()` is the one place the union is built. Every consumer still takes one `VersionModel`:

- one listed document: that document, unchanged (R9), so its `meta.name`, logging and every output stay as they
  were;
- several: a `ComposedVersionModel` named after the deployment, whose `spec.pins` is the union and which remembers
  the owner of each pin (`pin_owner()`) and the documents it was built from (`version_names()`).

Consumers that log a pin or report where it came from call `pin_owner()` instead of reading `meta.name`, so a
composed version never hides which document a pin came from (V14).
"""

from dataclasses import dataclass
from typing import Any, cast

from strata.controllers.deployment_resolution import deployment_of, first_tagged_version
from strata.controllers.pinnable_targets import TargetCache, pinnable_targets
from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import AnyDeploymentModel, DeploymentModel, version_name_list
from strata.models.version_model import (
    PIN_CATEGORIES,
    ComposedVersionModel,
    VersionModel,
    VersionPinModel,
    VersionPinsModel,
    VersionPromotionModel,
    pin_key,
    pin_owner,
    pin_owners,
    version_names,
)
from strata.utils.diagnostics import Diagnostics

__all__ = [
    "ComposedVersionModel",
    "DeploymentPins",
    "VersionMatrix",
    "compose_versions",
    "deployment_version",
    "pin_key",
    "pin_owner",
    "pin_owners",
    "version_names",
]


def _promotion(
    documents: list[VersionModel], label: str, diagnostics: Diagnostics, source: str | None
) -> VersionPromotionModel | None:
    """The ring the tagged documents agree on (V9); untagged documents take no part. A disagreement is an error."""
    tagged = [document for document in documents if document.spec.promotion and document.spec.promotion.ring]
    if not tagged:
        return None
    first = tagged[0].spec.promotion
    assert first is not None
    for other in tagged[1:]:
        promotion = other.spec.promotion
        assert promotion is not None
        if (promotion.ring, promotion.order, promotion.wave) != (first.ring, first.order, first.wave):
            diagnostics.error(
                f"Deployment '{label}' lists version documents tagged for different rings: '{tagged[0].meta.name}' "
                f"({_slot(first)}) and '{other.meta.name}' ({_slot(promotion)}). A deployment is in one ring.",
                source=source,
                location="spec.version",
                code="version_ring_conflict",
                hint="Tag only the product document of this ring with 'spec.promotion', and leave shared documents "
                "untagged.",
            )
    return first


def _slot(promotion: VersionPromotionModel) -> str:
    wave = f", wave {promotion.wave}" if promotion.wave else ""
    return f"ring {promotion.ring}, order {promotion.order}{wave}"


def compose_versions(
    index: DocumentIndex, names: list[str], *, label: str, source: str | None = None
) -> tuple[VersionModel | None, Diagnostics]:
    """The union of the pins of the version documents `names` (V3), for the deployment `label`.

    A name not in the index is skipped: reference validation reports it. No document gives `None`; one gives that
    document unchanged. Two documents pinning the same `category/target` is `version_pin_conflict`, naming both; the
    first-listed owner's pin is kept, so a caller still gets a usable model.
    """
    diagnostics = Diagnostics()
    documents: list[VersionModel] = []
    for name in version_name_list(names):
        entry = index.get(PlatformKind.VERSION, name)
        if entry is not None:
            documents.append(cast(VersionModel, entry.model))
    if not documents:
        return None, diagnostics
    if len(documents) == 1:
        return documents[0], diagnostics

    owners: dict[str, str] = {}
    merged: dict[str, dict[str, VersionPinModel]] = {category: {} for category in PIN_CATEGORIES}
    for document in documents:
        for category, target, pin in document.spec.pins.iter_pins():
            key = pin_key(category, target)
            owner = owners.get(key)
            if owner is not None:
                kept = merged[category][target]
                same = "the same value" if kept.version == pin.version else f"'{kept.version}' and '{pin.version}'"
                diagnostics.error(
                    f"Deployment '{label}': {key} is pinned by both '{owner}' and '{document.meta.name}' ({same}). "
                    "Each pin has one owning document per deployment.",
                    source=source,
                    location="spec.version",
                    code="version_pin_conflict",
                    hint=f"Remove {key} from one of the two documents; a pin several products share belongs in a "
                    "shared document they all list.",
                )
                continue
            owners[key] = document.meta.name
            merged[category][target] = pin

    pins = VersionPinsModel(**{category: entries or None for category, entries in merged.items()})
    promotion = _promotion(documents, label, diagnostics, source)
    composed = ComposedVersionModel.model_validate(
        {"meta": {"name": label}, "spec": {"pins": pins, "promotion": promotion}}
    )
    composed._owners = owners
    composed._names = [document.meta.name for document in documents]
    return composed, diagnostics


def pin_dropped_findings(
    own: VersionModel | None, pinned: VersionModel | None, targets: set[tuple[str, str]]
) -> Diagnostics:
    """`version_pin_dropped` (V7): a target the deployment uses that its own version list pins but the `--pin` list
    does not. `--pin` replaces the whole list, so leaving a document out quietly falls back to the spec's own value
    (for a remote, its `reference`)."""
    diagnostics = Diagnostics()
    if own is None:
        return diagnostics
    pinned_keys = {(category, target) for category, target, _pin in pinned.spec.pins.iter_pins()} if pinned else set()
    for category, target, pin in own.spec.pins.iter_pins():
        if (category, target) in targets and (category, target) not in pinned_keys:
            diagnostics.warning(
                f"{pin_key(category, target)} is '{pin.version}' under the deployment's own versions "
                f"({pin_owner(own, category, target)}) but not pinned by --pin, so its own declared value applies: "
                "--pin replaces the deployment's whole list.",
                location="--pin",
                code="version_pin_dropped",
                hint=f"Add --pin {pin_owner(own, category, target)} to keep the pin.",
            )
    return diagnostics


def deployment_version(index: DocumentIndex, deployment: DeploymentModel) -> VersionModel | None:
    """The composed version of `deployment`, findings dropped: for consumers that only apply pins. `strata validate`
    reports the findings (`check_version_composition()`)."""
    return compose_versions(index, deployment.spec.version_names(), label=deployment.meta.name)[0]


def listing_deployments(index: DocumentIndex, resolved: dict[str, DeploymentModel], name: str) -> list[str]:
    """The deployments (partial bases included, through `extends`) that list version document `name`."""
    found: list[str] = []
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = deployment_of(entry, resolved)
        if deployment is not None and name in deployment.spec.version_names():
            found.append(entry.ref.name)
    return found


def version_scope(
    index: DocumentIndex,
    resolved: dict[str, DeploymentModel],
    name: str,
    matrix: "VersionMatrix | None" = None,
) -> set[tuple[str, str]] | None:
    """What version document `name` could still usefully pin (V13): the targets the deployable deployments listing it
    use, minus those another of their listed documents already pins. `None` when no deployable deployment lists it:
    then every target in the solution is a candidate, as before composition."""
    scope: set[tuple[str, str]] = set()
    listed = False
    matrix = matrix if matrix is not None else VersionMatrix.build(index, resolved)
    for item in matrix.listed:
        names = item.deployment.spec.version_names()
        if name not in names:
            continue
        listed = True
        others: set[tuple[str, str]] = set()
        for other in names:
            entry = index.get(PlatformKind.VERSION, other)
            if other != name and entry is not None:
                others |= {(c, t) for c, t, _pin in cast(VersionModel, entry.model).spec.pins.iter_pins()}
        scope |= item.targets - others
    return scope if listed else None


@dataclass(frozen=True)
class DeploymentPins:
    """One deployable deployment's version picture, computed once per run (`VersionMatrix`).

    `version` is its listed documents composed and `findings` what composing them found (`version_pin_conflict`,
    `version_ring_conflict`), located at the deployment's file. `own` are the documents it lists itself, `targets`
    what it uses (`pinnable_targets()`; empty when it lists no version document), and `slot` the first tagged
    document it lists: the ring it is in (`first_tagged_version()`).
    """

    entry: IndexEntry
    deployment: DeploymentModel
    version: VersionModel | None
    findings: Diagnostics
    own: frozenset[str]
    targets: frozenset[tuple[str, str]]
    slot: IndexEntry | None


@dataclass(frozen=True)
class VersionMatrix:
    """Every deployable deployment's `DeploymentPins`, built once per validation run or command.

    `strata validate` builds one in `SolutionContext.resolve()` and hands it to the matrix checks
    (`check_version_composition()`) and the promotion checks (`validate_promotions()`), so each deployment is
    composed and walked once, and every walk shares one `TargetCache`. A caller that passes none gets one built for
    it. Valid only while the index does not change.
    """

    deployments: tuple[DeploymentPins, ...]

    @classmethod
    def build(
        cls, index: DocumentIndex, resolved: dict[str, DeploymentModel], cache: TargetCache | None = None
    ) -> "VersionMatrix":
        """Every deployable deployment (no partial base, a workspace), resolved through `extends`."""
        cache = cache if cache is not None else TargetCache()
        found: list[DeploymentPins] = []
        for entry in index.all_of(PlatformKind.DEPLOYMENT):
            deployment = deployment_of(entry, resolved)
            if deployment is None or deployment.spec.partial or deployment.spec.workspace is None:
                continue
            names = deployment.spec.version_names()
            version, findings = compose_versions(index, names, label=deployment.meta.name, source=str(entry.source))
            found.append(
                DeploymentPins(
                    entry=entry,
                    deployment=deployment,
                    version=version,
                    findings=findings,
                    own=frozenset(_own_names(entry)),
                    targets=frozenset(pinnable_targets(index, deployment, cache)) if names else frozenset(),
                    slot=first_tagged_version(index, names),
                )
            )
        return cls(tuple(found))

    @property
    def listed(self) -> list[DeploymentPins]:
        """The deployments that list at least one version document."""
        return [item for item in self.deployments if item.deployment.spec.version_names()]

    def of(self, name: str) -> DeploymentPins | None:
        """The deployment named `name`, or `None` when it is not deployable."""
        return next((item for item in self.deployments if item.deployment.meta.name == name), None)


def _own_names(entry: IndexEntry) -> set[str]:
    """The version documents a deployment lists itself, not through `extends` (V5)."""
    written = _written_version(entry)
    if isinstance(written, str):
        return {written}
    return {name for name in written or [] if isinstance(name, str)}


def _written_version(entry: IndexEntry) -> Any:
    """`spec.version` as the document writes it; a template keeps it as written (`extra="allow"`)."""
    return getattr(cast(AnyDeploymentModel, entry.model).spec, "version", None)


def check_version_composition(
    index: DocumentIndex, resolved: dict[str, DeploymentModel], matrix: VersionMatrix | None = None
) -> Diagnostics:
    """The pin matrix checks of `strata validate` (docs/design/version-composition.md V1, V3, V5, V9, V12).

    Per deployable deployment: two listed documents pinning one target (`version_pin_conflict`), tagged documents
    for different rings (`version_ring_conflict`), a name listed twice in one written list (`version_listed_twice`),
    and a document the deployment lists itself that pins nothing it uses (`version_document_unused`). Per document:
    pins none of the deployments listing it uses (`version_pin_unused`, information). Per workspace: a deployment
    outside the rollout its siblings are in (`version_ring_none`, information). Per ring: a shared (untagged)
    document's key at two values (`version_ring_drift`).
    """
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        written = _written_version(entry)
        if isinstance(written, list):
            repeated = sorted({name for name in written if isinstance(name, str) and written.count(name) > 1})
            for name in repeated:
                diagnostics.warning(
                    f"Deployment '{entry.ref.name}' lists version document '{name}' more than once; it counts once.",
                    source=str(entry.source),
                    location="spec.version",
                    code="version_listed_twice",
                    hint=f"Remove the second '{name}' from the list.",
                )

    matrix = matrix if matrix is not None else VersionMatrix.build(index, resolved)
    listed = matrix.listed
    used_anywhere = {target for item in listed for target in item.targets}
    for item in listed:
        diagnostics.extend(item.findings)
        diagnostics.extend(_unused_documents(index, item, used_anywhere))
    diagnostics.extend(_unused_pins(index, listed))
    diagnostics.extend(_ring_none(listed))
    diagnostics.extend(_ring_drift(index, listed))
    diagnostics.extend(_unlisted_documents(index, resolved))
    return diagnostics


def _unlisted_documents(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """`version_document_unlisted`: an untagged version document no deployment (a partial base included) lists.
    Information: CI may use it only through `--pin`, which strata cannot see. A tagged one is
    `promotion_orphan_ring` already."""
    diagnostics = Diagnostics()
    for entry in unlisted_versions(index, resolved):
        version = cast(VersionModel, entry.model)
        if version.spec.promotion is not None and version.spec.promotion.ring:
            continue
        diagnostics.info(
            f"Version '{entry.ref.name}' is listed by no deployment.",
            source=str(entry.source),
            location="meta.name",
            code="version_document_unlisted",
            hint=f"Nothing to do if CI uses it through --pin {entry.ref.name}; otherwise list it in a deployment's "
            "spec.version, or delete it.",
        )
    return diagnostics


def unlisted_versions(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> list[IndexEntry]:
    """Every version document no deployment (a partial base included) lists, tagged or not: what `strata validate`
    reports as `version_document_unlisted` (untagged) and `promotion_orphan_ring` (tagged), and what `strata graph
    --unreferenced` lists."""
    listed = {name for entry in index.all_of(PlatformKind.DEPLOYMENT) for name in _listed_names(entry, resolved)}
    return [entry for entry in index.all_of(PlatformKind.VERSION) if entry.ref.name not in listed]


def _listed_names(entry: IndexEntry, resolved: dict[str, DeploymentModel]) -> list[str]:
    deployment = deployment_of(entry, resolved)
    if deployment is not None:
        return deployment.spec.version_names()
    written = _written_version(entry)
    return version_name_list(written if isinstance(written, (str, list)) else None)


def _unused_documents(index: DocumentIndex, item: DeploymentPins, used_anywhere: set[tuple[str, str]]) -> Diagnostics:
    """`version_document_unused`: a document the deployment lists itself whose pins it uses none of, while other
    deployments do: another product's document. A document whose pins no deployment uses is not this mistake (its
    targets are gone, `stale_pin`, or not used yet, `version_pin_unused`)."""
    diagnostics = Diagnostics()
    for name in item.deployment.spec.version_names():
        entry = index.get(PlatformKind.VERSION, name)
        if name not in item.own or entry is None:
            continue
        pins = {(category, target) for category, target, _pin in cast(VersionModel, entry.model).spec.pins.iter_pins()}
        if pins & used_anywhere and not pins & item.targets:
            diagnostics.warning(
                f"Deployment '{item.deployment.meta.name}' lists version document '{name}', which pins nothing this "
                f"deployment uses ({', '.join(sorted(pin_key(*pin) for pin in pins))}), while other deployments do: "
                "it looks like another product's document.",
                source=str(item.entry.source),
                location="spec.version",
                code="version_document_unused",
                hint=f"List the version document for workspace '{item.deployment.spec.workspace}' instead "
                f"('strata version show {name}' shows who uses '{name}').",
            )
    return diagnostics


def _unused_pins(index: DocumentIndex, listed: list[DeploymentPins]) -> Diagnostics:
    """`version_pin_unused`: a pin of a listed document that none of the deployments listing it uses."""
    diagnostics = Diagnostics()
    used: dict[str, set[tuple[str, str]]] = {}
    for item in listed:
        for name in item.deployment.spec.version_names():
            used.setdefault(name, set()).update(item.targets)
    for name, targets in sorted(used.items()):
        entry = index.get(PlatformKind.VERSION, name)
        if entry is None:
            continue
        for category, target, _pin in cast(VersionModel, entry.model).spec.pins.iter_pins():
            if (category, target) not in targets:
                diagnostics.info(
                    f"Version '{name}': {pin_key(category, target)} is pinned, but no deployment listing this "
                    "document uses it.",
                    source=str(entry.source),
                    location=f"spec.pins.{category}.{target}",
                    code="version_pin_unused",
                    hint="Nothing to do for a pin staged ahead of its first use; otherwise remove it, or move it to "
                    "the document of the deployments that use it.",
                )
    return diagnostics


def _ring(item: DeploymentPins) -> tuple[str, int | None, str | None] | None:
    promotion = item.version.spec.promotion if item.version is not None else None
    if promotion is None or promotion.ring is None:
        return None
    return promotion.ring, promotion.order, promotion.wave


def _ring_none(listed: list[DeploymentPins]) -> Diagnostics:
    """`version_ring_none` (V9): a deployment in no ring while others of its workspace are in a rollout."""
    diagnostics = Diagnostics()
    in_rollout = {item.deployment.spec.workspace for item in listed if _ring(item) is not None}
    for item in listed:
        if _ring(item) is None and item.deployment.spec.workspace in in_rollout:
            diagnostics.info(
                f"Deployment '{item.deployment.meta.name}' is in no ring: none of its version documents "
                f"({', '.join(item.deployment.spec.version_names())}) has 'spec.promotion', while other deployments "
                f"of workspace '{item.deployment.spec.workspace}' are in a rollout. 'promote status' leaves it out.",
                source=str(item.entry.source),
                location="spec.version",
                code="version_ring_none",
                hint="List this ring's tagged product document too, or leave it out of the rollout on purpose.",
            )
    return diagnostics


def _ring_drift(index: DocumentIndex, listed: list[DeploymentPins]) -> Diagnostics:
    """`version_ring_drift` (V12): in one ring, a key owned by untagged (shared) documents at different values."""
    diagnostics = Diagnostics()
    seen: dict[tuple[str, int | None, str], dict[str, set[str]]] = {}
    for item in listed:
        ring = _ring(item)
        if ring is None or item.version is None:
            continue
        for category, target, pin in item.version.spec.pins.iter_pins():
            owner = pin_owner(item.version, category, target)
            owner_entry = index.get(PlatformKind.VERSION, owner)
            owner_promotion = cast(VersionModel, owner_entry.model).spec.promotion if owner_entry is not None else None
            # Tagged means a ring, as everywhere else (V9): a shared document with only an `order` is still shared.
            if owner_entry is None or (owner_promotion is not None and owner_promotion.ring is not None):
                continue
            values = seen.setdefault((ring[0], ring[1], pin_key(category, target)), {})
            values.setdefault(pin.version, set()).add(owner)
    for (ring_name, _order, key), values in sorted(seen.items(), key=lambda item: (item[0][0], item[0][2])):
        if len(values) > 1:
            shown = "; ".join(f"'{value}' ({', '.join(sorted(owners))})" for value, owners in sorted(values.items()))
            diagnostics.warning(
                f"Ring '{ring_name}': the shared pin {key} has more than one value across its deployments: {shown}.",
                location="spec.version",
                code="version_ring_drift",
                hint="List one shared document in every deployment of the ring, or align the shared documents' values.",
            )
    return diagnostics
