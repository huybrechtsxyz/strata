#!/usr/bin/env python3
"""Static Jinja2 template reference validation (docs/design/
value-token-resolution.md, "Value Supply Mechanisms" option C —
`output.template`) — never rendering, only checking that a template's
referenced names exist somewhere in a known schema. Build never has every
value resolved (secrets, integration-backed variables/features are
deploy-only), so a "final" render can't honestly happen here; this catches
the more common mistake (a typo'd reference) before deploy ever runs.

Deliberately has no dependency on `strata.integrations`/`ResolvedWorkspaceGraph`
— those sit above `strata.utils` in the import-linter layering (ADR-0003).
Callers build the `known_names` schema themselves and pass it in as plain
`dict`/`set` data.
"""

from jinja2 import Environment, TemplateSyntaxError, nodes
from jinja2.meta import find_undeclared_variables

#: Parse-only — no loader/undefined config needed since nothing is rendered.
_ENV = Environment()


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
