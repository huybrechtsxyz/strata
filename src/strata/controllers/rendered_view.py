#!/usr/bin/env python3
"""The rendered view: a resolved document, and where each of its values comes from.

`base-parameters-v2.md` (work doc), phase 4. A deployment, environment or tenant built with `extends` and parameter
sets is spread over several files. `render_document()` returns the document strata actually uses, with an origin
for every value: the document that wrote it, its file, and for a value filled in from a parameter set, the text as
written and each `${param:}` read (role, key, value, and the set that supplied it).

**The origin map is computed beside the merge, not inside it.** Every layer of the merge (a deployment's tenant
defaults, then its chain root first; an environment's or tenant's chain root first) is substituted with the chain's
values, as resolution does, and walked into leaf paths. A list of named items (`stages` by `step`, `providers` and
`assets` by `slot`, `variables`, `secrets` and `features` by `key`) is addressed by that name, and a list of plain
values by the value, so a leaf of the merged result finds the last layer that wrote it whichever merge rule moved it.
The merge functions stay as they are.

**Secrets are always masked**: every `value` under an environment's `spec.secrets`, empty or not, in the document
and in the text as written. A `store: constant` secret's value is the secret itself.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from strata.controllers.deployment_resolution import resolve_deployments
from strata.controllers.document_chains import (
    CircularExtendsError,
    ParamSets,
    chain_param_sets,
    require,
    resolve_chain,
    set_values,
    value_supplier,
)
from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.models.common_models import PlatformKind
from strata.models.template_model import DocumentTemplateModel
from strata.models.tenant_model import TenantModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.param_tokens import ParamOrigin, merge_param_values, substitute_params

#: The kinds `render_document()` renders: those built with `extends` and parameter sets.
RENDERABLE_KINDS = (PlatformKind.DEPLOYMENT, PlatformKind.ENVIRONMENT, PlatformKind.TENANT)

#: The field that names an item of a list of named items, in the order they are tried.
_IDENTITY_KEYS = ("step", "slot", "key")

#: What a masked secret value is rendered as.
SECRET_MASK = "***"


@dataclass(frozen=True)
class ValueRead:
    """One `${param:role.key}` filled into a value: what it read, and the parameter set that supplied it."""

    role: str
    key: str
    value: Any
    parameter_set: str


@dataclass(frozen=True)
class FieldOrigin:
    """One leaf of a rendered document and where it comes from.

    `document` is `None` for a value no document wrote (a schema default). `written` is the text as written when a
    `${param:}` token was filled into the value, with `reads` the tokens it read.
    """

    path: str
    value: Any
    document_kind: str | None
    document: str | None
    source: str | None
    written: str | None = None
    reads: tuple[ValueRead, ...] = ()


@dataclass(frozen=True)
class RenderedDocument:
    """A resolved deployment, environment or tenant, with the origin of every value.

    `chain` is the document and its bases, leaf first. `param_sets` is role -> the parameter sets the chain names,
    root first; `params` the merged values per role. `spec` is the resolved spec, secrets masked.
    """

    kind: str
    name: str
    source: str
    chain: tuple[str, ...]
    param_sets: dict[str, tuple[str, ...]]
    params: dict[str, Any]
    spec: dict[str, Any]
    fields: tuple[FieldOrigin, ...] = field(default=())


@dataclass(frozen=True)
class _Layer:
    """One document's contribution to a merge: its spec as substituted, and the origin of each substituted string."""

    kind: PlatformKind
    name: str
    source: str
    spec: dict[str, Any]
    origins: dict[str, ParamOrigin]


def render_document(index: DocumentIndex, kind: PlatformKind, name: str) -> tuple[RenderedDocument | None, Diagnostics]:
    """The resolved `kind` document `name`, with an origin for every value.

    Returns:
        `(rendered, diagnostics)`. `rendered` is `None` when there is nothing to render: no such document, a
        `partial: true` base, or a document that does not resolve; `diagnostics` then says why.
    """
    diagnostics = Diagnostics()
    entry = index.get(kind, name)
    if entry is None:
        known = ", ".join(sorted(index.names_of(kind))) or "none"
        diagnostics.error(f"No {kind.value} document named '{name}'. Available: {known}", code="render_not_found")
        return None, diagnostics
    written: Any = entry.as_written
    if written.spec.partial:
        diagnostics.error(
            f"{kind.value.capitalize()} '{name}' is 'partial: true': a base, which is only complete in a "
            f"{kind.value} built on it. Render one of those.",
            source=str(entry.source),
            location="spec.partial",
            code="render_partial_base",
        )
        return None, diagnostics

    try:
        chain = resolve_chain(kind, name, index, {}, set(), visiting=[])
    except CircularExtendsError:  # reported by `strata validate`
        chain = None
    param_sets = chain_param_sets(kind, chain, index) if chain is not None else None
    model = _resolved_model(index, entry, diagnostics)
    if chain is None or param_sets is None or model is None:
        diagnostics.error(
            f"{kind.value.capitalize()} '{name}' does not resolve; run 'strata validate' for the reason.",
            source=str(entry.source),
            code="render_not_resolved",
        )
        return None, diagnostics

    values = set_values(param_sets, index)
    params = merge_param_values(param_sets, values)
    layers = _layers(index, kind, chain, param_sets, params, model)
    spec = model.spec.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    secret_paths = kind is PlatformKind.ENVIRONMENT
    rendered = RenderedDocument(
        kind=kind.value,
        name=name,
        source=str(entry.source),
        chain=chain,
        param_sets=dict(param_sets),
        params=params,
        spec=_mask_secrets(spec) if secret_paths else spec,
        fields=tuple(_origins(spec, layers, param_sets, values, mask=secret_paths)),
    )
    return rendered, diagnostics


def _resolved_model(index: DocumentIndex, entry: IndexEntry, diagnostics: Diagnostics) -> Any:
    """The model strata uses for `entry`: the resolved deployment, or the environment or tenant in the index."""
    if entry.ref.kind is PlatformKind.DEPLOYMENT:
        resolved, found = resolve_deployments(index)
        if entry.ref.name in resolved:
            return resolved[entry.ref.name].model
        mine = f"(for {entry.ref.name})"
        for finding in found.errors:
            if finding.message.endswith(mine) or finding.source == str(entry.source):
                diagnostics.items.append(finding)
        return None
    return None if isinstance(entry.model, DocumentTemplateModel) else entry.model


def _layers(
    index: DocumentIndex,
    kind: PlatformKind,
    chain: tuple[str, ...],
    param_sets: ParamSets,
    params: dict[str, dict[str, Any]],
    model: Any,
) -> list[_Layer]:
    """The layers merged into the document, lowest precedence first, each substituted as resolution does."""
    layers: list[_Layer] = []
    if kind is PlatformKind.DEPLOYMENT and model.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, model.spec.tenant)
        if tenant_entry is not None and isinstance(tenant_entry.model, TenantModel):
            tenant = tenant_entry.model.spec  # tenant defaults (ADR-0024): the layer under the chain
            defaults = {"environments": tenant.environments, "properties": tenant.properties, "custom": tenant.custom}
            layers.append(
                _Layer(
                    PlatformKind.TENANT,
                    tenant_entry.ref.name,
                    str(tenant_entry.source),
                    {key: value for key, value in defaults.items() if value},
                    {},
                )
            )
    for holder in reversed(chain):
        holder_entry = require(index, kind, holder)
        written: Any = holder_entry.as_written
        origins: dict[str, ParamOrigin] = {}
        own, _ = substitute_params(written.spec.model_dump(exclude_none=True), params, param_sets, "spec", origins)
        layers.append(_Layer(kind, holder, str(holder_entry.source), own, origins))
    return layers


def _identity(item: Any) -> tuple[str, Any] | None:
    if isinstance(item, dict):
        for key in _IDENTITY_KEYS:
            if key in item:
                return key, item[key]
    return None


def _text(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _leaves(node: Any, raw: str, normal: str) -> Iterator[tuple[str, str, Any]]:
    """`(normalized path, path, value)` for every leaf under `node`; an empty map or list is a leaf.

    The normalized path addresses a named list item by its name (`spec.stages[step=infra]`) and a plain list item
    by its value, so the same item has the same normalized path in every layer, wherever a merge put it.
    """
    if isinstance(node, dict) and node:
        for key, child in node.items():
            yield from _leaves(child, f"{raw}.{key}", f"{normal}.{key}")
    elif isinstance(node, list) and node:
        for position, item in enumerate(node):
            identity = _identity(item)
            if identity is not None:
                segment = f"[{identity[0]}={_text(identity[1])}]"
            elif isinstance(item, (dict, list)):
                segment = f"[{position}]"
            else:
                segment = f"[={_text(item)}]"
            yield from _leaves(item, f"{raw}[{position}]", normal + segment)
    else:
        yield normal, raw, node


def _origins(
    spec: dict[str, Any],
    layers: list[_Layer],
    param_sets: ParamSets,
    values: dict[str, dict[str, Any]],
    *,
    mask: bool,
) -> Iterator[FieldOrigin]:
    """Every leaf of `spec`, with the last layer that wrote it."""
    written_by: dict[str, tuple[_Layer, ParamOrigin | None]] = {}
    for layer in layers:
        for normal, raw, _ in _leaves(layer.spec, "spec", "spec"):
            written_by[normal] = (layer, _origin_at(raw, layer.origins))
    for normal, raw, value in _leaves(spec, "spec", "spec"):
        secret = mask and _is_secret_value(raw)
        found = written_by.get(normal)
        if found is None and value in ({}, []):
            continue  # an empty block a merge added (`custom: {}` from tenant defaults): no value to explain
        if found is None:
            yield FieldOrigin(raw, SECRET_MASK if secret else value, None, None, None)
            continue
        layer, origin = found
        reads = tuple(
            ValueRead(read.role, read.key, read.value, value_supplier(param_sets, values, read.role, read.key))
            for read in (origin.reads if origin else ())
        )
        yield FieldOrigin(
            raw,
            SECRET_MASK if secret else value,
            layer.kind.value,
            layer.name,
            layer.source,
            (SECRET_MASK if secret else origin.text) if origin else None,
            reads,
        )


def _origin_at(raw: str, origins: dict[str, ParamOrigin]) -> ParamOrigin | None:
    """The substitution origin of `raw` or of the nearest value above it (a whole-value token inserted a map)."""
    path = raw
    while path:
        if path in origins:
            return origins[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return None


def _is_secret_value(raw: str) -> bool:
    return raw.startswith("spec.secrets[") and raw.endswith(".value")


def _mask_secrets(spec: dict[str, Any]) -> dict[str, Any]:
    """`spec` with every secret's `value` masked, empty or not."""
    if not spec.get("secrets"):
        return spec
    return {**spec, "secrets": [{**item, "value": SECRET_MASK} for item in spec["secrets"]]}
