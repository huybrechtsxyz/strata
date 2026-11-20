#!/usr/bin/env python3
"""Which documents no deployment reaches (docs/design/unreferenced-documents.md).

A document that no deployment reaches through any reference is either a leftover, a mistyped name somewhere, or
staged ahead of its first use. `strata validate` reports it as information, `document_unreferenced`, so
`--strict` never fails on it.

Reached means: from a deployable deployment (no partial base, resolved through `extends`), following every
reference strata knows by name — every `References` field (`build_reference_graph()`, the edges `strata graph`
draws), the resolved deployment's own fields (a template's references exist only once its values are substituted),
and every `${value:kind.name...}` token in a reached document.

Only the kinds a deployment reaches by name are judged (`CHECKED_KINDS`). Left out:

- kinds bound by type, not by name: `configuration`, `providerconfig`, `topologyconfig`, `attributeconfig`,
  `integration`;
- kinds with a check of their own: `version` (`version_document_unlisted`, `promotion_orphan_ring`), `artifact`
  (`artifact_unused`), `parameterset` (an unread key is reported per set);
- `deployment` itself: every deployment is a starting point;
- a `partial: true` base: it exists only for `extends`, and the index holds the resolved documents built on it.

A solution with no deployable deployment (a library of documents) is not judged at all.
"""

from collections import defaultdict
from typing import Any, cast

from pydantic import BaseModel

from strata.controllers.deployment_resolution import deployment_of
from strata.controllers.graph_controller import build_reference_graph
from strata.controllers.references import references_in
from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.controllers.version_composition import unlisted_versions
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import AnyDeploymentModel, DeploymentModel
from strata.models.reference_fields import extract_references
from strata.utils.diagnostics import Diagnostics
from strata.utils.value_tokens import extract_value_tokens

#: The kinds whose documents a deployment reaches by name.
CHECKED_KINDS = (
    PlatformKind.WORKSPACE,
    PlatformKind.PROVIDER,
    PlatformKind.RESOURCE,
    PlatformKind.DNS,
    PlatformKind.NETWORK,
    PlatformKind.FIREWALL,
    PlatformKind.NAMESPACE,
    PlatformKind.MODULE,
    PlatformKind.TENANT,
    PlatformKind.ENVIRONMENT,
    PlatformKind.ASSET,
)

Node = tuple[str, str]


def _value_token_targets(model: Any) -> set[Node]:
    """`(kind, name)` of every `${value:kind.name...}` token in a document's strings."""
    targets: set[Node] = set()
    for text in _strings(model.model_dump(by_alias=True, mode="json")):
        for token_kind, key in extract_value_tokens(text):
            parts = key.split(".")
            if token_kind == "value" and len(parts) >= 2:
                targets.add((parts[0], parts[1]))
    return targets


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    if isinstance(node, list):
        return [text for value in node for text in _strings(value)]
    return []


def _model_edges(model: BaseModel) -> set[Node]:
    """`(kind, name)` of every `References` field of one model (a resolved deployment, say)."""
    targets: set[Node] = set()
    for rule in extract_references(type(model)):
        if rule.kind is None:
            continue
        targets |= {(rule.kind.value, name) for _location, name in references_in(model, rule.path)}
    return targets


def reached_documents(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> set[Node] | None:
    """Every `(kind, name)` some deployable deployment reaches, or `None` when there is no deployable deployment."""
    edges: dict[Node, set[Node]] = defaultdict(set)
    for edge in build_reference_graph(index):
        edges[(edge.source_kind, edge.source_name)].add((edge.target_kind, edge.target_name))

    roots: list[Node] = []
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        node = (PlatformKind.DEPLOYMENT.value, entry.ref.name)
        deployment = deployment_of(entry, resolved)
        if deployment is not None:
            edges[node] |= _model_edges(deployment) | _value_token_targets(deployment)
            if not deployment.spec.partial and deployment.spec.workspace:
                roots.append(node)
    if not roots:
        return None

    reached: set[Node] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        found = index.get(PlatformKind(node[0]), node[1]) if node[0] in _KIND_VALUES else None
        targets = set(edges.get(node, set()))
        if found is not None:
            targets |= _value_token_targets(found.model)
        pending.extend(target for target in targets if target not in reached)
    return reached


_KIND_VALUES = {kind.value for kind in PlatformKind}


def _is_partial(entry: IndexEntry) -> bool:
    return bool(getattr(getattr(cast(AnyDeploymentModel, entry.model), "spec", None), "partial", False))


#: The kinds that have `partial: true` bases and `extends` (docs/design/parameter-sets.md).
BASE_KINDS = (PlatformKind.DEPLOYMENT, PlatformKind.ENVIRONMENT, PlatformKind.TENANT)


def _written_spec_value(entry: IndexEntry, field: str) -> Any:
    """A field of the document as written: an environment or tenant in the index is already resolved, and resolution
    drops `extends` and `partial`."""
    return getattr(getattr(entry.as_written, "spec", None), field, None)


def unused_bases(index: DocumentIndex) -> list[IndexEntry]:
    """Every `partial: true` deployment, environment or tenant that no document of its kind `extends`."""
    found: list[IndexEntry] = []
    for kind in BASE_KINDS:
        entries = index.all_of(kind)
        extended = {_written_spec_value(entry, "extends") for entry in entries}
        found.extend(
            entry for entry in entries if _written_spec_value(entry, "partial") and entry.ref.name not in extended
        )
    return found


def check_unused_bases(index: DocumentIndex) -> Diagnostics:
    """`base_unused` (information): a `partial: true` base nothing extends (docs/design/unreferenced-documents.md).

    A base exists only to be extended, so one nothing extends does nothing: a leftover, or a leaf that names
    another base by mistake. A base extended only by another base that nothing extends is not reported itself;
    the unused base at the end of the chain is.
    """
    diagnostics = Diagnostics()
    for entry in unused_bases(index):
        kind = entry.ref.kind.value
        diagnostics.info(
            f"{kind.capitalize()} '{entry.ref.name}' is a partial base that no {kind} extends.",
            source=str(entry.source),
            location="spec.partial",
            code="base_unused",
            hint=f"Nothing to do if leaves are still to come; otherwise set 'extends: {entry.ref.name}' on the "
            f"{kind} documents meant to build on it, or delete it.",
        )
    return diagnostics


def unreferenced_documents(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> list[IndexEntry]:
    """Every document of a `CHECKED_KINDS` kind no deployable deployment reaches (none without such a deployment)."""
    reached = reached_documents(index, resolved)
    if reached is None:
        return []
    return [
        entry
        for kind in CHECKED_KINDS
        for entry in index.all_of(kind)
        if (kind.value, entry.ref.name) not in reached and not _is_partial(entry)
    ]


def cleanup_candidates(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> list[tuple[IndexEntry, str]]:
    """`(entry, reason)` for every document `strata graph --unreferenced` lists from the index alone:
    `unreferenced` (no deployment reaches it), `base_unused` (a partial base nothing extends) and `version_unlisted`
    (a version document no deployment lists; CI may still use it through `--pin`). The same sets `strata validate`
    reports. Unused artifacts need the solution context and are added by the command (`artifact_unreferenced`)."""
    found = [(entry, "unreferenced") for entry in unreferenced_documents(index, resolved)]
    found += [(entry, "base_unused") for entry in unused_bases(index)]
    found += [(entry, "version_unlisted") for entry in unlisted_versions(index, resolved)]
    return sorted(found, key=lambda item: (item[0].ref.kind.value, item[0].ref.name))


def check_unreferenced_documents(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """`document_unreferenced` (information): a document of a `CHECKED_KINDS` kind that no deployment reaches."""
    diagnostics = Diagnostics()
    for entry in unreferenced_documents(index, resolved):
        diagnostics.info(
            f"{entry.ref.kind.value.capitalize()} '{entry.ref.name}' is reached by no deployment.",
            source=str(entry.source),
            location="meta.name",
            code="document_unreferenced",
            hint="Nothing to do if it is staged for a deployment still to come; otherwise check the name where it "
            "should be used ('strata graph' shows what references what; 'strata graph --unreferenced' lists every "
            "such document), or delete it.",
        )
    return diagnostics
