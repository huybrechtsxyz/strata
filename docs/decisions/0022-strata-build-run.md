# `strata build run` — Rendering Artifacts from Integrations

- Status: proposed - design not started
- Date: 2026-09-23
- Related: [ADR-0021](0021-integration-layer.md) (Phases 1-6, all done - the
  integration layer this consumes: registry, `Integration`/`InfraIntegration`,
  `TerraformIntegration`/`ComposeIntegration`/`HelmIntegration`)

## Context and Problem Statement

ADR-0021 built the integration layer up through its first two real
`InfraIntegration` classes (Terraform - Phase 5; Compose and Helm - Phase 6),
deliberately stopping short of anything that calls them. Quoting that ADR's
own Phase 7 placeholder, which this document replaces:

> The first actual consumer: wires provisioner -> integration resolution
> (including D4's auto-bind-or-error) into artifact rendering. Large enough
> to deserve its own ADR rather than being specified here.

`strata build run` is proven, critical-path v1 functionality (per
`/memories/repo/v1-consumer-usage.md`'s Tier 1 census: renders **both**
Terraform and Helm artifacts, depended on by every real haven/cfg-int-deployment
workflow) with no v2 design yet. This ADR is a placeholder for that design -
scope, decisions, and phasing are not yet written. Do not build against this
document until it has a real Decision section.

## Decision

Not yet designed.

## Consequences

Not yet designed.

## Remaining Work

- The design itself. Candidate topics known from ADR-0021's own deferred
  items and v1 evidence, to ground rather than invent when this is picked up:
  - Provisioner -> Integration resolution (D4's "auto-bind or error" -
    ADR-0021 Consequences flags this as still open).
  - Value substitution into rendered artifacts (`${var:}/${secret:}/${feature:}`
    typed-expression resolution, v1 ADR-0075) - deliberately kept out of
    Phases 5/6's integration classes, which take an already-resolved values
    file/chart ref.
  - Chart/stack reference resolution (`meta.yaml` parsing, OCI-vs-HTTP-vs-local
    chart source, repo-name aliasing) - same "kept out of the integration
    layer" reasoning.
  - `TF_VAR_`/compose env injection at deploy time (v1's
    `resolved_values.as_tf_vars()`/`as_compose_env()`) - the integration
    classes already accept `env`, this decides who builds that dict and when.
  - Helm's OCI `chart_repository` support and the "must not Jinja-render a
    local chart's own `templates/` dir" rule - both real v1 1.8.2 bug fixes
    (ADR-0020), requirements not discoveries to make again.
