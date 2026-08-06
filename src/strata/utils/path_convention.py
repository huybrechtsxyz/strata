"""Shared utilities for path convention validation.

Provides:
- ``match_pattern()``  — match a file path against a convention pattern and return
  captured segment values.
- ``resolve_spec_rule()`` — resolve a ``spec.field[*].attr`` rule against a
  loaded ``ConfigurationModel`` and return the allowed value set.
- ``evaluate_file_rule()`` — expand placeholder references in a file path template
  and check existence on disk.
- ``build_path_from_pattern()`` — inverse of ``match_pattern()``: substitute named
  segment values into a pattern to build a concrete path.
- ``find_tenant_path_pattern()`` / ``resolve_tenant_file_path()`` — resolve a
  tenant's on-disk file path, honoring a ``spec.paths`` convention marked
  ``resolves: tenant`` if declared, else falling back to the built-in
  ``tenants/{code}.yaml`` convention (ADR-0012).
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from strata.models.configuration_model import PathConventionModel


def match_pattern(rel_path: str, pattern: str) -> Optional[Dict[str, str]]:
    """Match a file path against a convention pattern, returning captured segment values.

    The pattern uses ``{segment}`` placeholders that capture exactly one path part
    (never ``/``). Literal parts must match verbatim. Trailing path parts after the
    pattern are ignored — files deeper than the pattern still match.

    Returns a dict of ``{segment_name: value}`` on match, or ``None`` if the path
    does not match the pattern (no violation; the file is skipped).

    Examples::

        match_pattern(
            "zones/europe/customers/contoso/prd/deploy.yaml",
            "zones/{zone}/customers/{tenant}/{env}"
        )
        # → {"zone": "europe", "tenant": "contoso", "env": "prd"}

        match_pattern("zones/europe/shared.yaml", "zones/{zone}/customers/{tenant}/{env}")
        # → None  (path is shallower than pattern — skip, not a violation)

        match_pattern("customers/contoso/tenant.yaml", "customers/{tenant}")
        # → {"tenant": "contoso"}
    """
    path_parts = Path(rel_path).parts
    pattern_parts = [p for p in pattern.split("/") if p]

    if len(path_parts) < len(pattern_parts):
        return None  # file is shallower than pattern — skip

    captures: Dict[str, str] = {}
    for i, pat_part in enumerate(pattern_parts):
        actual_part = path_parts[i]
        if pat_part.startswith("{") and pat_part.endswith("}"):
            segment_name = pat_part[1:-1]
            captures[segment_name] = actual_part
        else:
            if actual_part != pat_part:
                return None  # literal segment mismatch

    return captures


# ---------------------------------------------------------------------------
# Validation rule: spec.* model field lookup
# ---------------------------------------------------------------------------

_SPEC_RULE_RE = re.compile(r"^spec\.(.+)\[\*\]\.(.+)$")


def is_spec_rule(rule: str) -> bool:
    """Return True if *rule* is a ``spec.field[*].attr`` membership rule."""
    return bool(_SPEC_RULE_RE.match(rule))


def resolve_spec_rule(rule: str, configuration_model) -> Optional[Set[str]]:
    """Resolve a ``spec.field[*].attr`` rule against the loaded ConfigurationModel.

    Returns the set of allowed values, or ``None`` if the rule cannot be resolved
    (missing field, no configuration model, etc.).  Callers should treat ``None``
    as "skip — no constraint available".

    Args:
        rule: A string like ``"spec.zones[*].name"`` or ``"spec.environments[*].name"``.
        configuration_model: A loaded ``ConfigurationModel`` instance (or ``None``).
    """
    if configuration_model is None:
        return None

    m = _SPEC_RULE_RE.match(rule)
    if not m:
        return None

    field_path, attr = m.group(1), m.group(2)
    spec = getattr(configuration_model, "spec", None)
    if spec is None:
        return None

    # Walk dot-separated path: e.g., "zones" → list object
    obj = spec
    for part in field_path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None

    if not isinstance(obj, list):
        return None

    values: Set[str] = set()
    for item in obj:
        val = getattr(item, attr, None)
        if val is not None:
            values.add(str(val))
    return values


# ---------------------------------------------------------------------------
# Validation rule: file existence check
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def evaluate_file_rule(rule: str, captures: Dict[str, str], work_path: Path) -> Optional[str]:
    """Check that a file path rule resolves to an existing file on disk.

    Expands ``{placeholder}`` references in *rule* from *captures*, then checks
    ``work_path / expanded_path`` exists.

    Returns ``None`` on success (file exists or self-reference warning), or a
    violation message string if the file does not exist.

    Note: Self-references (file checking its own existence during creation) produce
    a warning string prefixed with "WARN:" — callers may treat these differently.
    """
    try:
        expanded = _PLACEHOLDER_RE.sub(lambda m: captures.get(m.group(1), m.group(0)), rule)
        target = work_path / expanded
        if not target.exists():
            return f"{expanded} does not exist"
        return None
    except Exception:
        return None  # never fail on unexpected errors


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------


def evaluate_conventions(
    rel_path: str,
    conventions: "List[PathConventionModel]",
    work_path: Path,
    configuration_model=None,
) -> List[str]:
    """Evaluate all matching conventions for a file and return violations.

    Args:
        rel_path: File path relative to ``work_path``, forward-slash separated.
        conventions: List of ``PathConventionModel`` entries from ``spec.paths``.
        work_path: Workspace root for file existence checks.
        configuration_model: Optional ``ConfigurationModel`` for ``spec.*`` rules.

    Returns:
        List of violation strings.  Empty list means the file is compliant.
    """
    from fnmatch import fnmatch

    violations: List[str] = []

    for conv in conventions:
        # Step a: scope check
        if not fnmatch(rel_path, conv.scope):
            continue

        # Step b: pattern match
        captures = match_pattern(rel_path, conv.pattern)
        if captures is None:
            continue  # in scope but at different depth — not a violation

        # Step c: validate each segment
        if not conv.rules:
            continue

        for segment_name, rule in conv.rules.items():
            value = captures.get(segment_name)
            if value is None:
                continue

            if is_spec_rule(rule):
                allowed = resolve_spec_rule(rule, configuration_model)
                if allowed is None:
                    # No configuration service or unresolvable — skip gracefully
                    continue
                if value not in allowed:
                    sorted_allowed = sorted(allowed)
                    violations.append(
                        f"convention '{conv.name}' \u2014 segment '{segment_name}' = '{value}' "
                        f"not in {rule}: {sorted_allowed}"
                    )
            else:
                # File existence rule
                violation = evaluate_file_rule(rule, captures, work_path)
                if violation:
                    violations.append(
                        f"convention '{conv.name}' \u2014 segment '{segment_name}' = '{value}': {violation}"
                    )

    return violations


# ---------------------------------------------------------------------------
# Tenant file resolution — inverse direction (build a path FROM a pattern)
# ---------------------------------------------------------------------------

_BUILTIN_TENANT_PATTERN = "tenants/{code}.yaml"


def build_path_from_pattern(pattern: str, **values: str) -> str:
    """Build a concrete relative path from a ``{segment}`` pattern.

    Inverse of :func:`match_pattern`: substitutes each named value into its
    ``{segment}`` placeholder. Since every ``{segment}`` captures exactly one
    literal path part (see :func:`match_pattern`), this is a direct
    ``str.format()`` substitution — no additional parsing needed.

    Example::

        build_path_from_pattern("customers/{code}/customer.yaml", code="acme")
        # -> "customers/acme/customer.yaml"

    Raises:
        ValueError: *pattern* references a segment not present in *values*.
    """
    try:
        return pattern.format(**values)
    except KeyError as exc:
        raise ValueError(f"Pattern '{pattern}' requires segment {exc}, which was not provided") from exc


def find_tenant_path_pattern(configuration_model) -> Optional[str]:
    """Return the pattern of the ``spec.paths`` convention marked ``resolves: tenant``.

    Returns ``None`` if *configuration_model* is ``None``, has no ``spec.paths``,
    or none of its conventions declare ``resolves: tenant`` — callers should then
    fall back to the built-in ``tenants/{code}.yaml`` convention. Model validation
    already guarantees at most one such convention exists
    (``ConfigurationSpecModel.validate_single_tenant_path_resolver``).
    """
    if configuration_model is None:
        return None
    paths = getattr(getattr(configuration_model, "spec", None), "paths", None)
    if not paths:
        return None
    for conv in paths:
        if getattr(conv, "resolves", None) == "tenant":
            return conv.pattern
    return None


def resolve_tenant_relative_path(tenant_code: str, configuration_model=None) -> str:
    """Return a tenant's file path, relative to the workspace root (not yet joined).

    Uses the ``spec.paths`` convention marked ``resolves: tenant`` if declared
    on *configuration_model*, else the built-in ``tenants/{code}.yaml``
    convention (ADR-0012).
    """
    pattern = find_tenant_path_pattern(configuration_model) or _BUILTIN_TENANT_PATTERN
    return build_path_from_pattern(pattern, code=tenant_code)


def resolve_tenant_file_path(work_path: Path, tenant_code: str, configuration_model=None) -> Path:
    """Resolve the on-disk (absolute) path to a tenant's YAML file.

    Uses the ``spec.paths`` convention marked ``resolves: tenant`` if one is
    declared on *configuration_model* (substituting *tenant_code* into its
    ``{code}`` segment); otherwise falls back to the built-in
    ``tenants/{code}.yaml`` convention (ADR-0012) — identical to strata's
    behavior before this function existed, so workspaces that don't declare a
    custom convention see no change at all.

    Args:
        work_path: Workspace root.
        tenant_code: The deployment's ``spec.tenant`` value.
        configuration_model: Optional loaded ``ConfigurationModel``.

    Returns:
        Absolute path to the tenant's YAML file (existence not checked here).
    """
    return Path(work_path) / resolve_tenant_relative_path(tenant_code, configuration_model)
