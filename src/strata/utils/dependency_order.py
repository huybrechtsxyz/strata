#!/usr/bin/env python3
"""One shared Kahn's-algorithm topological sort, reused by every
`depends_on`-shaped field in the schema (`ProvisioningStepModel.depends_on`
today; `ProvisionerModel.depends_on` since
docs/design/provisioner-source-dependencies.md) instead of a
third/fourth copy of the same ~15-line block.

Phase 1 of that feature's build-out — pure extraction, no schema change:
`provisioning_model.validate_provisioning_steps()`'s cycle check and
`build_controller.ordered_by_depends_on()` both become thin callers of
`topological_order()` below.
"""

from collections.abc import Iterable, Mapping, Sequence


def topological_order(
    names: Iterable[str], depends_on: Mapping[str, Sequence[str]], *, label: str = "items"
) -> list[str]:
    """Kahn's algorithm. Returns `names` ordered so every dependency listed
    in `depends_on[name]` precedes `name` in the result — ties (independent
    names with no ordering constraint between them) keep their relative
    order from the input `names` sequence.

    Only orders/cycle-checks the given `names` — does **not** check that
    every name referenced in `depends_on` is itself a member of `names`;
    existence-checking is the caller's own job (every real caller already
    validates unknown names in a separate pass before calling this, the
    same two-pass structure `validate_provisioning_steps()` already uses).
    A `depends_on` entry naming something outside `names` is silently
    ignored by the ordering itself — it can never block a real name from
    being ordered, and never appears in the result either, since this
    function has no way to resolve what it would mean.

    Args:
        names: Every name to order. Consumed into a list immediately (safe
            to pass a one-shot iterable).
        depends_on: `{name: [other names that must precede it]}`. A name
            with no entry (or an empty one) has no constraint.
        label: Only used to phrase the cycle error message
            (`"Circular dependency in {label}: ..."`) — lets each call site
            report a cycle in its own vocabulary (e.g. "provisioning step
            depends_on", "provisioner depends_on") without this function
            knowing what kind of graph it was given.

    Raises:
        ValueError: a cycle exists among `names` — message lists every name
            that never reached zero in-degree (the cycle itself, plus
            anything transitively blocked by it), sorted for a stable
            message.
    """
    names_list = list(names)
    name_set = set(names_list)
    graph: dict[str, set[str]] = {name: set(depends_on.get(name, ())) & name_set for name in names_list}
    in_degree = {name: len(deps) for name, deps in graph.items()}
    queue = [name for name in names_list if in_degree[name] == 0]
    ordered: list[str] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for name, deps in graph.items():
            if current in deps:
                in_degree[name] -= 1
                if in_degree[name] == 0:
                    queue.append(name)
    if len(ordered) < len(names_list):
        cyclic = sorted(name for name in names_list if name not in ordered)
        raise ValueError(f"Circular dependency in {label}: {' -> '.join(cyclic)}")
    return ordered
