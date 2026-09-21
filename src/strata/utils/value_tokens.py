#!/usr/bin/env python3
"""Value binding token syntax (ADR-0002).

A field may be a plain literal, or contain one or more embedded
``${var:KEY}``/``${secret:KEY}``/``${feature:KEY}`` tokens (e.g. a composite/
concatenated string like a connection string). Reused verbatim from v1
(ADR-0075) rather than ``{{ }}``-style templating, since ``{{`` collides
with strata's existing Jinja/Helm templating elsewhere.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure regex/string logic, no Pydantic dependency, reused across
many model files (dns, module, network, firewall...) — same reasoning as
`strata.utils.builtin_types`.
"""

import ipaddress
import re

VALUE_TOKEN_KINDS = ("var", "secret", "feature")

VALUE_TOKEN_PATTERN = re.compile(r"\$\{(?P<kind>var|secret|feature):(?P<key>[A-Za-z0-9_.-]+)\}")

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
