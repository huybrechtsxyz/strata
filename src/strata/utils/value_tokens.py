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

#: Escapes a literal, non-strata ``${...}`` (docs/design/value-token-resolution.md's
#: "Escape syntax" section) — a doubled leading ``$`` matching the same escape
#: idiom Terraform (``$${``) and Compose (``$$``) already use for their own
#: native interpolation, e.g. a Helm chart's own ``${TOKEN}`` substitution
#: (Gatus's real, documented case in `.v2-haven`) that strata must not confuse
#: for one of its own tokens.
_ESCAPED_VALUE_TOKEN_PATTERN = re.compile(r"\$\$(\{[^}]*\})")

#: Combines the escaped-literal and real-token shapes into one pattern for
#: `resolve_value_tokens()`'s single substitution pass — `${escaped}` is
#: matched (and unescaped) *before* a real token would be, so an unanchored
#: real-token match can never reach inside an escaped span (the bug found
#: validating this design: fed "$${var:x}", the plain `VALUE_TOKEN_PATTERN`
#: alone would match the inner "${var:x}" and substitute it, leaving a
#: stray literal "$" prepended to the resolved value).
_RESOLVE_VALUE_TOKEN_PATTERN = re.compile(
    r"\$\$(?P<escaped>\{[^}]*\})"
    r"|\$\{(?P<kind>var|secret|feature|output):(?P<key>[A-Za-z0-9_.-]+)\}"
)


def strip_escaped_value_tokens(value: str) -> str:
    """Remove every escaped ``$${...}`` occurrence from `value`.

    Used by every Phase 1/2 syntax/lookup function below *before* they scan
    for candidates, so an escaped occurrence is invisible to both phases —
    not "always valid," simply not a candidate at all. Does not unescape
    (that's a resolution/rendering concern, deploy-time only, not yet
    built) — this only removes the span so validation never sees it.
    """
    return _ESCAPED_VALUE_TOKEN_PATTERN.sub("", value)


def find_malformed_value_tokens(value: str) -> list[str]:
    """Return every malformed ``${...}``-shaped candidate in `value`, escapes ignored.

    The non-raising counterpart to `validate_value_tokens()` (which wraps
    this and raises on the first hit) — for callers that collect
    `Diagnostics` across a whole document instead of raising on the first
    malformed token found (`environment_service.py`'s `unresolved_value_tokens()`,
    which walks every string in a document generically, `configuration`/
    `custom` passthrough dicts included).
    """
    stripped = strip_escaped_value_tokens(value)
    return [c for c in _VALUE_TOKEN_CANDIDATE_PATTERN.findall(stripped) if not VALUE_TOKEN_PATTERN.fullmatch(c)]


def validate_value_tokens(value: str) -> None:
    """Raise ``ValueError`` if `value` contains a malformed ``${...}`` token.

    Catches typos at schema time (Phase 1) — e.g. an unknown kind
    (``${vars:x}``), a missing key (``${var:}``), or a missing colon
    (``${var}``) — without needing an Environment to check keys against.
    Well-formed tokens (``${var:region}``, ``${secret:db_password}``,
    ``${feature:enable_x}``) and plain literals with no tokens are accepted.
    An escaped ``$${...}`` (a literal, non-strata placeholder — e.g. a
    third-party chart's own substitution syntax) is also accepted, never
    treated as a candidate at all. Whether a well-formed token's *key* is
    actually declared (e.g. `region` is real) is a Phase 2 check against a
    real Environment, not this function's job.
    """
    for candidate in find_malformed_value_tokens(value):
        kinds = "|".join(VALUE_TOKEN_KINDS)
        raise ValueError(f"Malformed Value token {candidate!r}. Expected '${{{kinds}:KEY}}', e.g. '${{var:region}}'.")


def has_value_tokens(value: str) -> bool:
    """Return True if `value` contains at least one ``${...}`` Value token.

    Used to decide whether a field's literal-only validation (e.g. CIDR format
    checking) should run at all — a token-bearing string can't be format-checked
    until it's resolved (build/deploy time), so callers should skip that
    validation when this returns True. An escaped ``$${...}`` doesn't count —
    it resolves to a fixed literal, not something deferred to build/deploy.
    """
    return bool(_VALUE_TOKEN_CANDIDATE_PATTERN.search(strip_escaped_value_tokens(value)))


def extract_value_tokens(value: str) -> list[tuple[str, str]]:
    """Return every well-formed ``(kind, key)`` pair in `value`.

    The Phase 2 counterpart to `validate_value_tokens()`: that function checks
    a token is *shaped* correctly without needing an Environment, this one
    pulls the keys out so they can be checked against a real one (ADR-0002).

    Malformed candidates are ignored here rather than raised — they are
    already rejected at Phase 1 by `validate_value_tokens()`, so anything
    reaching this point is either well-formed or belongs to a document that
    never validated. An escaped ``$${kind:key}`` is stripped before matching,
    so it is never mistaken for a real token — without this, an unanchored
    match would find the inner ``${kind:key}`` and treat an intentionally
    literal, escaped string as if it needed resolving.

        >>> extract_value_tokens("postgres://${var:HOST}/${secret:DB_PASS}")
        [('var', 'HOST'), ('secret', 'DB_PASS')]
    """
    stripped = strip_escaped_value_tokens(value)
    return [(m.group("kind"), m.group("key")) for m in VALUE_TOKEN_PATTERN.finditer(stripped)]


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
        escaped = match.group("escaped")
        if escaped is not None:
            # An escaped literal (docs/design/value-token-resolution.md's
            # "Escape syntax") — not a strata token at all, unescape to a
            # single '$' and stop, never look up `key` for this match.
            return f"${escaped}"
        key = match.group("key")
        if key not in values:
            raise ValueError(
                f"Value token '${{{match.group('kind')}:{key}}}' references key '{key}', "
                "which did not resolve to a value."
            )
        return values[key]

    return _RESOLVE_VALUE_TOKEN_PATTERN.sub(_substitute, value)


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


def resolve_value_tokens_tracking_secrets(
    data: dict[str, Any], values: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Like `resolve_value_tokens_in_mapping()`, except a secret-shaped leaf
    is never resolved in place — its dotted path and resolved value are
    reported separately instead (docs/design/value-token-resolution.md's
    Full Solution Phase 3), for a caller (Helm's `--set-string
    <path>=<value>`, Compose's `env:` kwarg) that must deliver a secret
    without ever writing it to disk. `resolve_value_tokens_in_mapping()`
    itself is unaffected — Terraform's use (Phase 2) delivers the whole
    payload via a `TF_VAR_<name>` env var either way, so it never needed
    this split.

    "Secret-shaped" reuses the rule this doc's Current Design section
    already states: a leaf containing any `${secret:...}` token is
    secret-shaped as a whole, even mixed with `${var:}`/`${feature:}` in the
    same string (e.g. a connection string) — such a leaf is reported
    *only* in the secrets map, fully resolved, never partially rewritten
    into the returned dict.

    A secret-shaped leaf's original, still-unresolved literal
    (``"${secret:KEY}"``) is left untouched in the returned dict rather
    than deleted or blanked — safe, since Helm's `--set-string`/Compose's
    `env:` both override whatever a values/compose file already has at
    that path, so leaving the literal there costs nothing and keeps this
    function's "don't touch what you can't safely resolve in place"
    contract simple.

    Dotted paths join dict keys with `.`; a list index is its plain
    integer, stringified (e.g. `services.0.env.KEY`) — no real
    list-shaped secret leaf has been evidenced yet, but kept generic for
    consistency with the recursion itself.
    """
    secrets: dict[str, str] = {}

    def _is_secret_shaped(text: str) -> bool:
        return any(kind == "secret" for kind, _ in extract_value_tokens(text))

    def _walk(node: Any, path: str) -> Any:
        if isinstance(node, str):
            if has_value_tokens(node) and _is_secret_shaped(node):
                secrets[path] = resolve_value_tokens(node, values)
                return node
            return resolve_value_tokens(node, values)
        if isinstance(node, dict):
            return {key: _walk(value, f"{path}.{key}" if path else key) for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(value, f"{path}.{index}" if path else str(index)) for index, value in enumerate(node)]
        return node

    resolved = {key: _walk(value, key) for key, value in data.items()}
    return resolved, secrets


def resolve_value_tokens_renaming_secrets(
    data: dict[str, Any], values: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Like `resolve_value_tokens_in_mapping()`, except a `${secret:KEY}`
    token is rewritten to Compose's own native, bare `${KEY}` interpolation
    syntax in place — never resolved to its literal value on disk
    (docs/design/value-token-resolution.md's Full Solution Phase 5).

    Unlike `resolve_value_tokens_tracking_secrets()` (Phase 3, Helm's
    `--set-string <path>=<value>`), which treats an ENTIRE leaf as one
    secret-shaped unit reported by dotted path — safe for Helm, since
    `--set-string` overrides a whole values.yaml path in one shot — Compose
    has no such per-path override mechanism. `docker stack deploy`/`stack
    config` substitute `${KEY}` occurrences *within* a string using the
    subprocess's own environment (`Integration.run()`'s `env` kwarg, merged
    onto `os.environ` per call — no `.env` file needed, simpler than v1's
    `inject_compose_env()` context-manager-mutates-os.environ approach),
    exactly like a shell variable — so this function rewrites **per
    token**, not per leaf: a mixed string like
    `"postgres://${var:HOST}/${secret:DB_PASSWORD}"` resolves the `var`
    token to its literal value in place while renaming only the `secret`
    token to `${DB_PASSWORD}`, leaving both halves independently correct.

    The returned `dict[str, str]` is keyed by the token's own `KEY` name
    (not a dotted path — Compose substitution has no path concept, it is a
    flat, deployment-wide `KEY -> value` namespace, matching v1's real
    `ResolvedValues.as_compose_env()` convention exactly) — pass it as
    `env=` to `ComposeIntegration.plan()`/`.deploy()`.

    Args:
        data: A raw (still-token-bearing) mapping — a whole
            `docker-compose.yml` document in practice, already merged by
            `prepare_namespace()` (Compose merges every module in a
            namespace into one file, unlike Helm).
        values: Every resolved `${kind:KEY}` value, keyed by `KEY` — same
            flat shape every other resolver in this module takes.

    Returns:
        `(resolved_document, secrets_env)` — `resolved_document` has every
        `var`/`feature`/`output` token substituted to its literal value and
        every `secret` token renamed to bare `${KEY}`; `secrets_env` is the
        `{KEY: value}` pairs to inject as subprocess environment.

    Raises:
        ValueError: a referenced key is not in `values` — same fail-loud
            contract `resolve_value_tokens()` already has.
    """
    secrets: dict[str, str] = {}

    def _substitute(match: re.Match[str]) -> str:
        escaped = match.group("escaped")
        if escaped is not None:
            return f"${escaped}"
        kind = match.group("kind")
        key = match.group("key")
        if key not in values:
            raise ValueError(
                f"Value token '${{{kind}:{key}}}' references key '{key}', which did not resolve to a value."
            )
        if kind == "secret":
            secrets[key] = values[key]
            return f"${{{key}}}"
        return values[key]

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _RESOLVE_VALUE_TOKEN_PATTERN.sub(_substitute, node)
        if isinstance(node, dict):
            return {key: _walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(value) for value in node]
        return node

    resolved = {key: _walk(value) for key, value in data.items()}
    return resolved, secrets
