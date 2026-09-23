#!/usr/bin/env python3
"""Cross-document references as field metadata, and the introspection that reads it.

A field that names another document carries `References(kind)` (or, for a
solution-manifest remote, `RemoteReference()`) directly in its type via
`Annotated`. `extract_references()` walks a model class recursively and
returns every one it finds, as a dotted path plus what it must resolve
against.

**Why metadata on the field, not a hand-written table.** The alternative —
kept in `strata.controllers.references` until this module replaced it — was
a dict of `(kind, path) -> target_kind` entries built by grepping field
descriptions. It drifts silently: a renamed field leaves a dangling path
string that quietly stops matching, and a new reference field is invisible
until someone remembers to add a row. Proof this already happened:
`ModuleReferenceModel.module` — embedded in both `TopologyComponentModel`
and `NamespaceSpecModel` — names a Module document, and neither kind was a
key in the old table. Annotating the field fixes both call sites at once,
because the walker follows the type wherever it is embedded.

The discovery risk does not disappear — "forgot to annotate" is still
possible — but it relocates from a separate file to the field itself, next
to its docstring, where a reviewer sees it. A completeness test still helps
(see `tests/strata/models/test_reference_fields.py`), but the annotation is
now the single source rather than a second one to keep in sync.

**Deliberately not annotated:** anything naming a workspace-local instance
rather than a document — `WorkspaceResourceModel.resource` (once resolved by
discovery) IS annotated, but `TopologyComponentModel.resource`/`.namespace`
and `DeploymentStageModel.step` name instances/steps *within* the same
workspace, not `(kind, name)` index entries, and annotating them would
invent failures on valid solutions.
"""

import types
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Union, get_args, get_origin

from pydantic import BaseModel

if TYPE_CHECKING:
    # Only for the type hints below. `common_models` imports `References`/
    # `RemoteReference` from here, so importing `PlatformKind` at runtime
    # would be circular.
    from strata.models.common_models import PlatformKind

#: Both spellings of a union type. `X | None` (PEP 604) and `Optional[X]`/
#: `Union[X, None]` produce different `get_origin()` results in Python
#: 3.10+ (`types.UnionType` vs `typing.Union`) even though they mean the
#: same thing — every v2 model uses the `|` form, so missing this silently
#: drops every optional reference field.
_UNION_ORIGINS = (Union, types.UnionType)


@dataclass(frozen=True)
class References:
    """Field metadata: this field's value(s) name a document of `kind`.

    Attach via `Annotated`: `Annotated[PlatformName, References(PlatformKind.PROVIDER)]`.
    Works nested inside `list[...]`, `X | None`, and inside another model
    embedded as a field — the walker follows all three.
    """

    kind: "PlatformKind"


@dataclass(frozen=True)
class RemoteReference:
    """Field metadata: this field names a remote from the solution manifest.

    Distinct from `References` because a remote resolves against
    `strata.yaml`, not the document index — remotes are bootstrap identity,
    not discovered documents (ADR-0015).
    """


@dataclass(frozen=True)
class ReferenceRule:
    """One discovered reference: a dotted path, and what it resolves against.

    `kind=None` means `RemoteReference` — check against manifest remotes
    rather than an index of one `PlatformKind`.
    """

    path: str
    kind: "PlatformKind | None"


def _effective_annotation(annotation: object, metadata: tuple[object, ...]) -> object:
    """Reconstruct the field's full annotated type.

    Pydantic hoists metadata out of a field's *own* top-level `Annotated`
    wrapper into `FieldInfo.metadata`, leaving `FieldInfo.annotation` bare —
    but leaves a *nested* `Annotated` (inside `list[...]`/`X | None`)
    untouched in `annotation`. Reconstructing the outer wrapper here means
    the same walk handles both shapes instead of needing two.
    """
    if metadata:
        return Annotated[(annotation, *metadata)]
    return annotation


def _walk(annotation: object, path: str) -> list[ReferenceRule]:
    """Return every reference reachable from `annotation` at `path`."""
    origin = get_origin(annotation)

    if origin is Annotated:
        base, *meta = get_args(annotation)
        for marker in meta:
            if isinstance(marker, References):
                return [ReferenceRule(path, marker.kind)]
            if isinstance(marker, RemoteReference):
                return [ReferenceRule(path, None)]
        return _walk(base, path)

    if origin in _UNION_ORIGINS:
        rules: list[ReferenceRule] = []
        for arg in get_args(annotation):
            if arg is not type(None):
                rules.extend(_walk(arg, path))
        return rules

    if origin is list:
        (item,) = get_args(annotation)
        return _walk(item, f"{path}[]")

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _extract(annotation, path)

    return []


def _extract(model_cls: type[BaseModel], prefix: str) -> list[ReferenceRule]:
    """Return every reference declared on `model_cls`'s own fields, recursively."""
    rules: list[ReferenceRule] = []
    for name, field in model_cls.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        annotation = _effective_annotation(field.annotation, tuple(field.metadata))
        rules.extend(_walk(annotation, path))
    return rules


@lru_cache(maxsize=None)
def extract_references(model_cls: type[BaseModel]) -> tuple[ReferenceRule, ...]:
    """Return every reference declared anywhere in `model_cls`, recursively.

    Cached per class: a document kind's model class is fixed, so this is
    computed once regardless of how many documents of that kind are loaded.

    Args:
        model_cls: A root document model (`WorkspaceModel`, `DeploymentModel`,
            ...) or any nested model.

    Returns:
        Every reference found, each as a dotted path (matching the convention
        `strata.controllers.references.references_in()` already walks values
        with) and the kind — or None for a remote — it must resolve against.
    """
    return tuple(_extract(model_cls, ""))
