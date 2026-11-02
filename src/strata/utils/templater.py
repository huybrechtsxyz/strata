#!/usr/bin/env python3
"""Jinja2 template handling for `output.template` (docs/design/
value-token-resolution.md, "Value Supply Mechanisms" option C).

Two halves, deliberately separate (docs/design/deploy-command.md's
Implementation Plan, phases 3/8): `validate_template_references()` is
build-time-only — never rendering, only checking that a template's
referenced names exist somewhere in a known schema, since build never has
every value resolved (secrets, integration-backed variables/features are
deploy-only). `render_template()` is deploy-time-only — every value is
fully resolved by then, so an honest render can finally happen; callers are
expected to have already validated the same template at build time.

Deliberately has no dependency on `strata.integrations`/`ResolvedWorkspaceGraph`
— those sit above `strata.utils` in the import-linter layering (ADR-0003).
Callers build the `known_names`/render context themselves and pass it in as
plain `dict`/`set` data.
"""

from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, nodes
from jinja2.meta import find_undeclared_variables

#: `StrictUndefined` so a reference `validate_template_references()` couldn't
#: check statically (any nested access under a `None`-valued `known_names`
#: root, e.g. `graph.*`, or a dynamic key) still raises loudly at render
#: time instead of silently rendering as an empty string — matches
#: `resolve_value_tokens()`'s own "unknown reference is always an error"
#: precedent. Has no effect on `validate_template_references()`'s own
#: `_ENV.parse()` call, which never evaluates Undefined access at all.
_ENV = Environment(undefined=StrictUndefined)


def validate_template_references(source: str, known_names: dict[str, set[str] | None]) -> list[str]:
    """Check every name `source` (a Jinja2 template) references against
    `known_names`.

    Args:
        source: The template's raw text.
        known_names: `{context_key: known_nested_keys}`. A `None` value
            means "this context key exists but its nested keys aren't
            checked" (e.g. an arbitrary-shape dict like `properties`/
            `custom`) — only the top-level name is validated for it.

    Returns:
        One message per problem found; empty means the template is
        consistent with `known_names` (not a guarantee it renders —
        filters, control flow, and unchecked roots are not verified).
    """
    try:
        ast = _ENV.parse(source)
    except TemplateSyntaxError as exc:
        return [f"template syntax error: {exc}"]

    errors: list[str] = []

    undeclared = find_undeclared_variables(ast)
    errors.extend(
        f"'{name}' is not a known template variable — available: {sorted(known_names)}"
        for name in sorted(undeclared)
        if name not in known_names
    )

    for root, keys in _referenced_keys(ast).items():
        allowed = known_names.get(root)
        if allowed is None:
            continue  # unknown root (already reported above) or an unchecked, arbitrary-shape root
        errors.extend(f"'{root}.{key}' is not declared" for key in sorted(keys) if key not in allowed)

    return errors


def _referenced_keys(ast: nodes.Template) -> dict[str, set[str]]:
    """`{root_name: {attr_or_item_key, ...}}` for every `root.key`/`root['key']`
    access found anywhere in `ast` — static-only; a dynamic key
    (`variables[some_var]`) can't be checked and is skipped.
    """
    result: dict[str, set[str]] = {}
    for node in ast.find_all((nodes.Getattr, nodes.Getitem)):
        if not isinstance(node, (nodes.Getattr, nodes.Getitem)) or not isinstance(node.node, nodes.Name):
            continue
        root = node.node.name
        if isinstance(node, nodes.Getattr):
            key = node.attr
        else:
            arg = node.arg
            if not isinstance(arg, nodes.Const) or not isinstance(arg.value, str):
                continue
            key = arg.value
        result.setdefault(root, set()).add(key)
    return result


def render_template(source: str, context: dict[str, Any]) -> str:
    """Render `source` (a Jinja2 template) with `context` — the deploy-time
    counterpart to `validate_template_references()`'s build-time,
    render-nothing check (docs/design/deploy-command.md's Implementation
    Plan phase 8). `context`'s keys mirror `validate_template_references()`'s
    `known_names` exactly (`graph`/`variables`/`flags`/`secrets`/
    `properties`/`custom`/`provisioner`, `InfraIntegration.render_output_
    template()`'s own job to assemble) — a template that already passed
    build-time validation is guaranteed every name it references exists in
    this same shape.

    Raises:
        jinja2.TemplateError: `source` is malformed, or a reference that
            passed static validation still fails at render time (a dynamic
            key access `validate_template_references()` couldn't check
            statically, for example) — not caught here; callers decide how
            to surface it (`deploy_controller.py` converts it to a
            `Diagnostics.error()`, matching every other per-step failure).
    """
    return _ENV.from_string(source).render(**context)
