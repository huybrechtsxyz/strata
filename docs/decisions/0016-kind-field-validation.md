# Enforcing `kind` Matches the Model It Is Validated As

- Status: implemented
- Date: 2026-09-21
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (the
  "validates fine, silently wrong" defect class this belongs to)

## Context and Problem Statement

Every root model declares its `kind` the same way:

```python
kind: PlatformKind = Field(
    default=PlatformKind.NETWORK,
    frozen=True,
    description="Platform kind (always 'network')",
)
```

The `frozen=True` reads as a guarantee that the value cannot be anything
else. It is not. `frozen=True` blocks *reassignment after construction*; it
places no constraint on the value supplied at parse time. Verified directly
against the real model rather than assumed:

```python
NetworkModel(meta={"name": "test"}, spec=spec, kind="firewall").kind
# -> PlatformKind.FIREWALL   (silently accepted)
```

So a YAML file declaring `kind: firewall` validated cleanly against
`NetworkModel` and quietly took on the wrong kind. This matters more once
documents are discovered and dispatched by their own `kind:` field
(ADR-0015), where the declared kind is the routing key.

v1 had noticed this, but only partially: `validate_kind_is_*` model
validators exist in exactly three of its ~15 model files
(`diagram_model.py`, `dns_model.py`, `network_model.py`) — inconsistent
there too, and v2 had initially ported the check to none of them.

## Decision

Add one shared helper to `common_models.py`:

```python
def validate_kind_matches(value: PlatformKind, expected: PlatformKind) -> PlatformKind:
    if value != expected:
        raise ValueError(f"Expected kind '{expected.value}', got '{value.value}'")
    return value
```

and call it from a `@field_validator("kind")` on **every** root model — not
the three v1 happened to cover. The `frozen=True`/`default=` declaration
stays (it still prevents post-construction mutation and supplies the
default); the validator supplies the parse-time guarantee people already
assumed was there.

Applied across all kinds: `solution`, `configuration`, `providerconfig`,
`topologyconfig`, `provider`, `resource`, `dns`, `network`, `firewall`,
`module`, `namespace`, `topology`, `workspace`, `integration`.

## Consequences

- Good: a mismatched `kind:` now fails loudly at Phase 1, in every kind,
  rather than silently producing a wrongly-typed document.
- Good: the discovery loader (ADR-0015) can trust `kind` as a dispatch key —
  a document that parses as `NetworkModel` really is a network.
- Good: consistency. The rule is "every root model calls this", so there is
  no per-file judgement about whether the check is worth having, which is
  how v1 ended up with three covered files and twelve uncovered ones.
- Neutral: the helper lives in `common_models.py` rather than `strata.utils`
  because it takes and returns `PlatformKind`, which is itself defined
  there — it is not a Pydantic-free pure-string utility like
  `check_unique_names`.
- Cost: one small validator repeated on 14 models. Rejected the alternative
  of a shared base class carrying the field, because each model needs a
  *different* default and the frozen default is part of each model's public
  schema.
