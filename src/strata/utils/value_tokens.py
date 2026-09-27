#!/usr/bin/env python3
"""Value binding token syntax (ADR-0002).

A field may be a plain literal, or contain one or more embedded
``${var:KEY}``/``${secret:KEY}``/``${feature:KEY}`` tokens (e.g. a composite/
concatenated string like a connection string). Reused verbatim from v1
(ADR-0075) rather than ``{{ }}``-style templating, since ``{{`` collides
with strata's existing Jinja/Helm templating elsewhere.

A 4th kind, ``${output:STEP.KEY}``, references a prior `deploy run` step's
collected output (docs/design/deploy-command.md's "Cross-step output
context") — dependency-scoped resolution lives in `deploy_controller.py`,
not here; this module stays kind-agnostic (see `resolve_value_tokens()`).

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure regex/string logic, no Pydantic dependency, reused across
many model files (dns, module, network, firewall...) — same reasoning as
`strata.utils.builtin_types`.
"""

import ipaddress
import re
from typing import Any

VALUE_TOKEN_KINDS = ("var", "secret", "feature", "output")

VALUE_TOKEN_PATTERN = re.compile(r"\$\{(?P<kind>var|secret|feature|output):(?P<key>[A-Za-z0-9_.-]+)\}")

_VALUE_TOKEN_CANDIDATE_PATTERN = re.compile(r"\$\{[^}]*\}")


def validate_value_tokens(value: str) -> None:
    """Raise ``ValueError`` if `value` contains a malformed ``${...}`` token.

    Catches typos at schema time (Phase 1) — e.g. an unknown kind
    (``${vars:x}``), a missing key (``${var:}``), or a missing colon
    (``${var}``) — without needing an Environment to check keys against.
    Well-formed tokens (``${var:region}``, ``${secret:db_password}``,
    ``${feature:enable_x}``) and plain literals with no tokens are accepted.
    Whether the *key itself* is real (e.g. `region` is actually declared) is a
    Phase 2 check against a real Environment, not this function's job.
    """
    for candidate in _VALUE_TOKEN_CANDIDATE_PATTERN.findall(value):
        if not VALUE_TOKEN_PATTERN.fullmatch(candidate):
            kinds = "|".join(VALUE_TOKEN_KINDS)
            raise ValueError(f"Malformed Value token {candidate!r}. Expected '${{{kinds}:KEY}}', e.g. '${{var:region}}'.")


def has_value_tokens(value: str) -> bool:
    """Return True if `value` contains at least one ``${...}`` Value token.

    Used to decide whether a field's literal-only validation (e.g. CIDR format
    checking) should run at all — a token-bearing string can't be format-checked
    until it's resolved (build/deploy time), so callers should skip that
    validation when this returns True.
    """
    return bool(_VALUE_TOKEN_CANDIDATE_PATTERN.search(value))


def extract_value_tokens(value: str) -> list[tuple[str, str]]:
    """Return every well-formed ``(kind, key)`` pair in `value`.

    The Phase 2 counterpart to `validate_value_tokens()`: that function checks
    a token is *shaped* correctly without needing an Environment, this one
    pulls the keys out so they can be checked against a real one (ADR-0002).

    Malformed candidates are ignored here rather than raised — they are
    already rejected at Phase 1 by `validate_value_tokens()`, so anything
    reaching this point is either well-formed or belongs to a document that
    never validated.

        >>> extract_value_tokens("postgres://${var:HOST}/${secret:DB_PASS}")
        [('var', 'HOST'), ('secret', 'DB_PASS')]
    """
    return [(m.group("kind"), m.group("key")) for m in VALUE_TOKEN_PATTERN.finditer(value)]


def validate_cidr_or_token(value: str) -> None:
    """Validate a CIDR/IP-or-Value-binding string.

    Always checks Value-token syntax via `validate_value_tokens()`. If `value`
    has no tokens (a plain literal), also validates it's a well-formed IP
    network/address via `ipaddress.ip_network()` — a token-bearing value can't
    be format-checked until it's resolved (build/deploy time). Shared by
    `network_model.py` (subnet/address-space CIDRs) and `firewall_model.py`
    (rule source/destination IP or CIDR).
    """
    validate_value_tokens(value)
    if not has_value_tokens(value):
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid CIDR/IP: {value}") from e


def resolve_value_tokens(value: str, values: dict[str, str]) -> str:
    """Replace every ``${var:KEY}``/``${secret:KEY}``/``${feature:KEY}``/
    ``${output:STEP.KEY}`` token in `value` with its resolved value from
    `values` — the deploy-time resolver Mechanism B has always been missing
    (docs/design/value-token-resolution.md; `strata deploy run`, docs/design/
    deploy-command.md).

    `kind` (`var`/`secret`/`feature`/`output`) only matters to the token's
    author, not to resolution: callers (`resolve_values()`/
    `build_value_references()`/`deploy_controller.py`'s per-step output
    context) already merge every source into one flat `key -> value` mapping
    before this function ever runs — it reads `key` only, indifferent to
    which kind prefixed it. An `${output:STEP.KEY}` token's `key` is simply
    `"STEP.KEY"` — the regex's `key` group already permits `.`, so no special
    handling is needed here; dependency-scoping (a step may only reference a
    step it `depends_on`) is enforced by the caller building `values`, not by
    this function.

    A plain literal with no tokens (`has_value_tokens(value)` is `False`) is
    returned unchanged — this function is safe to call unconditionally on
    every string-typed field, tokens or not.

    Raises:
        ValueError: a referenced key is not in `values` — matches
            `output.template`'s own build-time precedent (`templater.py`):
            an unknown reference is always an error, never a silent
            empty-string substitution.
    """

    def _substitute(match: re.Match[str]) -> str:
        key = match.group("key")
        if key not in values:
            raise ValueError(
                f"Value token '${{{match.group('kind')}:{key}}}' references key '{key}', "
                "which did not resolve to a value."
            )
        return values[key]

    return VALUE_TOKEN_PATTERN.sub(_substitute, value)


def resolve_value_tokens_in_mapping(data: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """Apply `resolve_value_tokens()` to every string value in `data`.

    Recurses into nested `dict`/`list` structures so a real, deeply-nested
    `configuration`/`custom` payload resolves in one call; non-string leaves
    (`int`/`bool`/`None`/already-resolved values) pass through unchanged.
    Every real `provisioner.backend.configuration` value checked so far is a
    flat top-level string (`resource_group_name: ${var:tf_state_resource_group}`),
    but recursing costs nothing and avoids a silent gap if a deeper structure
    ever needs it.
    """

    def _resolve(node: Any) -> Any:
        if isinstance(node, str):
            return resolve_value_tokens(node, values)
        if isinstance(node, dict):
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(v) for v in node]
        return node

    return {k: _resolve(v) for k, v in data.items()}
