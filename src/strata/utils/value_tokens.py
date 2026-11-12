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

A 5th kind, ``${value:kind.name.path}``, is a cross-document reference into
another already-loaded document's own literal field (docs/design/
cross-document-value-references.md) — addressed by `(kind, name)` document
identity plus a dotted path from that document's root, not by an
Environment-declared key the way `var`/`secret`/`feature` are. This module
only recognizes its *syntax* (the `key` group already permits the extra
dots, no regex redesign needed); resolving `kind.name.path` against a real
`DocumentIndex` is `value_references.py`'s job (`resolve_document_value_references()`),
not this module's — same kind-agnostic split `output` already established.

A 6th kind, ``${deployment:KEY}`` (``KEY`` is ``name`` or ``tenant``), is a
built-in for the deployment being run (docs/design/deployment-tokens.md): a
workspace is shared by every deployment, so nothing declared in a document
can say which one is running. Its flat-map key is ``deployment:<KEY>``, not
``KEY``: the map is otherwise indifferent to kind, and a declared variable
named ``name`` would share one slot with it. The colon cannot occur in any
other token's key (the regex excludes it), so a variable, secret, feature or
step output can never collide with these entries. `deployment_token_values()`
builds them.

A 7th kind, ``${artifact:NAME.FIELD}`` (``FIELD`` is ``image_name``,
``image_tag`` or ``image_ref``), is a built-in for an artifact's image as the
deployment being run pins it (docs/work/artifact-gap-analysis.md, design 1).
It is stored the same way, under ``artifact:NAME.FIELD``; this module only
knows the key shape (`artifact_key()`), the controllers build the entries
(`controllers/artifact_tokens.py`), since a pin needs the document index.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure regex/string logic, no Pydantic dependency, reused across
many model files (dns, module, network, firewall...) — same reasoning as
`strata.utils.builtin_types`.
"""

import ipaddress
import re
from typing import Any

VALUE_TOKEN_KINDS = ("var", "secret", "feature", "output", "value", "deployment", "artifact")

VALUE_TOKEN_PATTERN = re.compile(
    r"\$\{(?P<kind>var|secret|feature|output|value|deployment|artifact):(?P<key>[A-Za-z0-9_.-]+)\}"
)

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
    r"|\$\{(?P<kind>var|secret|feature|output|value|deployment|artifact):(?P<key>[A-Za-z0-9_.-]+)\}"
)


#: The keys ``${deployment:KEY}`` accepts (docs/design/deployment-tokens.md).
DEPLOYMENT_TOKEN_KEYS = ("name", "tenant")

_DEPLOYMENT_PREFIX = "deployment"

#: The fields ``${artifact:NAME.FIELD}`` accepts, the same a `store: artifact` variable's `field` takes. `image_tag`
#: is the whole value as pinned (`1.4.2@sha256:...`); `image_tag_name` and `image_digest` are its two parts.
ARTIFACT_TOKEN_FIELDS = ("image_name", "image_tag", "image_ref", "image_tag_name", "image_digest")

_ARTIFACT_PREFIX = "artifact"


def artifact_key(name: str, field: str) -> str:
    """The flat-map key of one artifact entry, ``artifact:NAME.FIELD``; the colon keeps it apart from every other
    kind, as for a deployment entry."""
    return f"{_ARTIFACT_PREFIX}:{name}.{field}"


def split_artifact_token_key(key: str) -> tuple[str, str | None]:
    """``NAME.FIELD`` as written in a token, split at the first ``.`` (an artifact name has none); ``FIELD`` is
    `None` when the key has no ``.`` or nothing after it."""
    name, dot, field = key.partition(".")
    return name, (field if dot and field else None)


def referenced_variable_keys(node: object) -> set[str]:
    """Every ``${var:KEY}`` token's ``KEY`` found anywhere in `node`, with the same walk as
    `referenced_output_keys()`."""
    if isinstance(node, str):
        return {key for kind, key in extract_value_tokens(node) if kind == "var"}
    if isinstance(node, dict):
        keys: set[str] = set()
        for value in node.values():
            keys |= referenced_variable_keys(value)
        return keys
    if isinstance(node, list):
        keys = set()
        for item in node:
            keys |= referenced_variable_keys(item)
        return keys
    return set()


def referenced_artifact_keys(node: object) -> set[str]:
    """Every ``${artifact:NAME.FIELD}`` token's ``"NAME.FIELD"`` found anywhere in `node`, with the same walk as
    `referenced_output_keys()`."""
    if isinstance(node, str):
        return {key for kind, key in extract_value_tokens(node) if kind == _ARTIFACT_PREFIX}
    if isinstance(node, dict):
        keys: set[str] = set()
        for value in node.values():
            keys |= referenced_artifact_keys(value)
        return keys
    if isinstance(node, list):
        keys = set()
        for item in node:
            keys |= referenced_artifact_keys(item)
        return keys
    return set()


def _deployment_key(key: str) -> str:
    """The flat-map key of one deployment entry; the colon keeps it apart from every other kind."""
    return f"{_DEPLOYMENT_PREFIX}:{key}"


def deployment_token_values(name: str, tenant: str | None) -> dict[str, str]:
    """The ``deployment:<key>`` entries of the flat token map for one deployment.

    `tenant` is left out when the deployment has none, so ``${deployment:tenant}``
    then fails like any other unresolved token instead of substituting an empty
    string into, say, a state key. One function builds the entries so deploy-time
    resolution and `strata validate` cannot disagree about what exists.
    """
    values = {_deployment_key("name"): name}
    if tenant:
        values[_deployment_key("tenant")] = tenant
    return values


def is_deployment_entry(key: str) -> bool:
    """Whether a flat token-map key is one of `deployment_token_values()`'s entries."""
    return key.startswith(f"{_DEPLOYMENT_PREFIX}:")


def _unresolved_message(kind: str, key: str, lookup: str, values: dict[str, str]) -> str:
    """Why a token did not resolve; a deployment or artifact token says what is missing instead of naming the
    internal key."""
    token = f"${{{kind}:{key}}}"
    if kind == _DEPLOYMENT_PREFIX:
        if key in DEPLOYMENT_TOKEN_KEYS:
            return f"Value token '{token}' needs a {key}, but the deployment being run has none."
        return f"Value token '{token}' is not a deployment token. Available: {sorted(DEPLOYMENT_TOKEN_KEYS)}"
    if kind == _ARTIFACT_PREFIX:
        return artifact_token_problem(key, values) or f"Value token '{token}' did not resolve to a value."
    return f"Value token '{token}' references key '{lookup}', which did not resolve to a value."


def artifact_token_problem(key: str, values: dict[str, str]) -> str | None:
    """Why ``${artifact:KEY}`` does not resolve against `values` (a flat token map), or `None` when it does.

    Every artifact has an ``image_name`` entry, so a name without one is not an artifact; a known artifact without
    an ``image_tag`` entry has no tag and no pin.
    """
    token = f"${{artifact:{key}}}"
    name, field = split_artifact_token_key(key)
    if field not in ARTIFACT_TOKEN_FIELDS:
        return f"'{token}' is not NAME.FIELD with FIELD one of {list(ARTIFACT_TOKEN_FIELDS)}."
    if artifact_key(name, "image_name") not in values:
        prefix = f"{_ARTIFACT_PREFIX}:"
        known = sorted({k.removeprefix(prefix).partition(".")[0] for k in values if k.startswith(prefix)})
        return f"'{token}': there is no artifact named '{name}'. Artifacts: {known or 'none'}."
    if artifact_key(name, field) not in values:
        if field != "image_tag" and artifact_key(name, "image_tag") in values:
            part = "digest" if field == "image_digest" else "tag"
            return (
                f"'{token}': artifact '{name}' is deployed as '{values[artifact_key(name, 'image_tag')]}', which has "
                f"no {part}. Give `spec.image_tag` or the pin under `pins.artifacts.{name}` the form "
                "`TAG@sha256:<hex>`."
            )
        return (
            f"'{token}': artifact '{name}' has no image_tag, and the deployment's version pins none. Set "
            f"`spec.image_tag` on the artifact, or pin it under `pins.artifacts.{name}`."
        )
    return None


def _lookup_key(kind: str, key: str) -> str:
    """The flat-map key a token resolves against: `key` itself, except for ``deployment`` and ``artifact``."""
    if kind == _DEPLOYMENT_PREFIX:
        return _deployment_key(key)
    if kind == _ARTIFACT_PREFIX:
        return f"{_ARTIFACT_PREFIX}:{key}"
    return key


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


def referenced_output_keys(node: object) -> set[str]:
    """Return every ``${output:STEP.KEY}`` token's ``"STEP.KEY"`` string found
    anywhere in `node` (a raw dns/networks/firewalls/module-services payload,
    or any nested piece of one).

    Same recursive dict/list/str walk shape as `deploy_controller.py`'s
    `_contains_output_token()` (a presence test), but collects every key
    instead of just testing for one — the per-step output pre-flight check
    (docs/design/output-declarations.md) diffs this against a step's real,
    collected `tokens` before attempting resolution. Lives in `utils`, not
    next to `_contains_output_token()`, so both `strata.controllers` and
    `strata.integrations` (Helm/Compose's own `deploy_namespace()`) can use
    it without crossing this codebase's own layering (ADR-0003) — `utils`
    is the lowest layer both sit above.

    Malformed candidates are ignored here, same assumption
    `extract_value_tokens()` itself already makes — already rejected at
    Phase 1 schema validation, so anything reaching this point is either
    well-formed or belongs to a document that never validated.
    """
    if isinstance(node, str):
        return {key for kind, key in extract_value_tokens(node) if kind == "output"}
    if isinstance(node, dict):
        keys: set[str] = set()
        for value in node.values():
            keys |= referenced_output_keys(value)
        return keys
    if isinstance(node, list):
        keys = set()
        for item in node:
            keys |= referenced_output_keys(item)
        return keys
    return set()


def referenced_secret_keys(node: object) -> set[str]:
    """Return every ``${secret:KEY}`` token's ``KEY`` found anywhere in `node`, with the same walk as
    `referenced_output_keys()`. Used to record which secrets a step's real input uses
    (docs/design/deployment-documentation.md, section 1)."""
    if isinstance(node, str):
        return {key for kind, key in extract_value_tokens(node) if kind == "secret"}
    if isinstance(node, dict):
        keys: set[str] = set()
        for value in node.values():
            keys |= referenced_secret_keys(value)
        return keys
    if isinstance(node, list):
        keys = set()
        for item in node:
            keys |= referenced_secret_keys(item)
        return keys
    return set()


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

    A ``${value:kind.name.path}`` token (docs/design/
    cross-document-value-references.md) is resolved exactly like any other
    kind — `key` here is the exact `"kind.name.path"` string, and
    `resolve_values()` (`value_controller.py`) already merges every
    resolved cross-document reference into the same flat `values` mapping
    passed in here (Phase 4) before this function ever runs. A
    `${value:...}` token this function is called with directly (outside
    that flow — e.g. a bespoke caller that built its own `values` dict)
    still raises the same "did not resolve to a value" error below if its
    key is missing, exactly like an undeclared `var`/`secret` would — this
    function itself has no special knowledge of `value:`'s own
    `(kind, name)`-lookup semantics; that resolution already happened
    upstream, in `value_references.py`.

    `kind` (`var`/`secret`/`feature`/`output`/`value`) only matters to the
    token's author, not to resolution (``deployment`` is the one exception:
    its key is looked up as ``deployment:<key>``, see `_lookup_key()`):
    callers (`resolve_values()`/
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
        kind = match.group("kind")
        key = match.group("key")
        lookup = _lookup_key(kind, key)
        if lookup not in values:
            raise ValueError(_unresolved_message(kind, key, lookup, values))
        return values[lookup]

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


def escape_path_segment(segment: str) -> str:
    """Escape a single dict-key path segment for safe inclusion in a
    dotted path string (`resolve_value_tokens_tracking_secrets()`'s own
    convention) — a literal `.` inside a real key (e.g. a DNS zone name
    like `example.com`) would otherwise be indistinguishable from the `.`
    separator joining path segments themselves, corrupting
    `split_path()`'s reverse operation. Escapes `\\` first (so an
    already-escaped `\\.` round-trips correctly), then `.`.
    """
    return segment.replace("\\", "\\\\").replace(".", "\\.")


def split_path(path: str) -> list[str]:
    """Inverse of joining `escape_path_segment()`-escaped segments with
    `.` — splits a dotted path back into its original, unescaped segments,
    treating a backslash-escaped `\\.` as a literal `.` within a segment
    rather than a separator."""
    parts: list[str] = []
    current: list[str] = []
    escape = False
    for char in path:
        if escape:
            current.append(char)
            escape = False
        elif char == "\\":
            escape = True
        elif char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def resolve_value_tokens_tracking_secrets(
    data: dict[str, Any], values: dict[str, str], *, sensitive_output_keys: set[str] | None = None
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
    into the returned dict. An `${output:step.key}` token is secret-shaped
    too, but only conditionally: it is real Terraform state, not a store
    strata itself classifies, so whether `step.key` is sensitive is a fact
    the caller must supply (a collected Terraform output's own `sensitive`
    flag) — `sensitive_output_keys` is that set, keyed exactly like an
    `${output:...}` token's own `key` half (`"step.key"`). Found as a real
    gap (2026-10-10): without this, a workspace author who marks a
    Terraform output `sensitive = true` and references it elsewhere via
    `${output:...}` got that real value written straight into a plain
    on-disk file — never routed through the secret-safe delivery path.

    A secret-shaped leaf's original, still-unresolved literal
    (``"${secret:KEY}"``) is left untouched in the returned dict rather
    than deleted or blanked — safe, since Helm's `--set-string`/Compose's
    `env:` both override whatever a values/compose file already has at
    that path, so leaving the literal there costs nothing and keeps this
    function's "don't touch what you can't safely resolve in place"
    contract simple.

    Dotted paths join dict keys with `.`, each key escaped by
    `escape_path_segment()` so a literal `.` inside a real key (e.g. a DNS
    zone name like `example.com`) can't be confused with the separator; a
    list index is its plain integer, stringified (e.g. `services.0.env.KEY`)
    — no real list-shaped secret leaf has been evidenced yet, but kept
    generic for consistency with the recursion itself. Use `split_path()`
    to reverse this, never a plain `path.split(".")`.
    """
    secrets: dict[str, str] = {}
    sensitive_keys = sensitive_output_keys or set()

    def _is_secret_shaped(text: str) -> bool:
        return any(
            kind == "secret" or (kind == "output" and key in sensitive_keys) for kind, key in extract_value_tokens(text)
        )

    def _walk(node: Any, path: str) -> Any:
        if isinstance(node, str):
            if has_value_tokens(node) and _is_secret_shaped(node):
                secrets[path] = resolve_value_tokens(node, values)
                return node
            return resolve_value_tokens(node, values)
        if isinstance(node, dict):
            result: dict[str, Any] = {}
            for key, value in node.items():
                escaped = escape_path_segment(key)
                result[key] = _walk(value, f"{path}.{escaped}" if path else escaped)
            return result
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
        lookup = _lookup_key(kind, key)
        if lookup not in values:
            raise ValueError(_unresolved_message(kind, key, lookup, values))
        if kind == "secret":
            secrets[key] = values[key]
            return f"${{{key}}}"
        return values[lookup]

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
