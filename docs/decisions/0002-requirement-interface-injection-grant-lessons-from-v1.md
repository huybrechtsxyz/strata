# Requirement, Interface, Injection, and Grant — Lessons from v1's References Model

- Status: proposed
- Date: 2026-09-20
- Related: [v2 ADR-0001](0001-v1-schema-analysis-findings-for-v2.md), v1 ADR-0078
  (scoping-variables-and-features-to-provisioners), v1 ADR-0084
  (variables-secrets-features-delivery-model)

## Context and Problem Statement

v2 already ported `spec.references` (`ProviderReferencesModel` — `variables`,
`secrets`, `features` key-name lists) onto `ProviderModel`, copied from v1's
schema. v1 designed this field **before** it had been exercised against real
multi-provisioner, multi-root platforms. Once it was used for real (a workspace
with one Terraform root that owns everything, vs. a workspace composing five-plus
independent, pre-existing Terraform roots each with its own input interface), v1
discovered the model was incomplete, and documented the findings in ADR-0078
(2026-09-11 → revised through 2026-09-15) and ADR-0084 (2026-09-20).

v2 has not yet built the kinds where this bites hardest (`resource`, `network`,
`module`, `dns`, and the provisioner/build/deploy layer), so this is the point to
design it correctly rather than discover the same gap later by repeating v1's
path.

## What v1 learned, after real implementation

### Four concepts; v1 modeled one and a half

| # | Concept | Question it answers | v1 status |
|---|---|---|---|
| 1 | **Requirement** | "What does this component need?" | Modeled (`spec.references`) |
| 2 | **Interface** | "What can this provisioner/root accept?" | Parsed (e.g. Terraform `variables.tf`), but only used to reject, never to shape delivery |
| 3 | **Injection** | "What does it actually get?" | **Never defined — defaults to "everything," unconditionally** |
| 4 | **Grant** | "What is this run allowed to see?" | Partially modeled, secrets-only (`stage.secrets` allowlist), and only at deploy time |

(3) is the structural gap. Because it was never decided, `stage.secrets` ended up
silently standing in for it — "the secret allowlist is compensating for the
absence of a definition of injection" (v1 ADR-0084).

### Requirements compose by union; grants compose by restriction

- **Requirements compose by union.** If component A needs `x` and component B
  needs `y`, both must be satisfied — adding a component can only add needs.
- **Grants compose by restriction.** The narrowest applicable permission wins —
  adding a constraint can only remove.

These are opposite operations. v1's build-time injected set was derived as a
**union of requirements**, which structurally cannot narrow — there is no
subtraction in that formula. The observed symptom ("every provisioner sees every
environment key, and fails validation against its own narrower interface") was not
a bug to patch; it is what a union-only formula must produce. Any narrowing
mechanism needs its own, separate, restriction-composed field — it cannot be
retrofitted onto the requirements list.

### `references` ended up doing two unrelated jobs

v1 audited every consumer of `.references` and found it serves two jobs that
happen to share one field, and that this is load-bearing (not incidental) for
three kinds:

1. **Job 1 — provisioner input scoping.** `resource`/`module`/`provider`
   references are unioned into the provisioner's validation scope (this is the
   "Requirement" concept above). Optional; absence means unscoped.
2. **Job 2 — document-local value-binding namespace.** `dns`, `module`, and
   `network` documents let sibling fields reference a key *by name* (e.g. a DNS
   record's `var: api_endpoint`), and `spec.references` is the namespace those
   names resolve against. This is **mandatory** — used at build time to validate
   that every `var:`/`secret:`/`feature:` reference actually appears in
   `references`, and the build errors if it doesn't.

`module` documents do **both jobs with the same field**. Any future v2 kind that
lets sibling fields bind to a variable/secret/feature by name (following the DNS
flat-union or Network nested-object patterns already flagged in
[v2 ADR-0001](0001-v1-schema-analysis-findings-for-v2.md)) must decide up front
whether it reuses `references` for both jobs (as v1's `module` does) or keeps them
as two separate fields — silently drifting into "same field, two jobs" the way
v1 did makes the field's contract harder to reason about later.

### Requirement is not the same as Authority

A resource does not read a variable; the provisioner that materializes it does —
so a requirement declared on a resource is a statement about code one step
removed from the declaring object. The useful test for where a concern belongs:
**who is the authority, and who would be wrong to override them?**

| Role | Authority on | Artifact |
|---|---|---|
| Component author | what the code consumes | the IaC/module source |
| Platform engineer | what exists and how it composes | workspace, resources |
| Operator / SRE | values, and who may see them | environment, deployment |

### Two independent input channels

A consumer (provisioner, module, etc.) receives values through two channels that
should not be conflated:

| Channel | Contents | Source |
|---|---|---|
| Structural | the platform's own composed model (resources, topologies, modules, …) | the platform artifact |
| Environmental | `variables`, `features`, and `secrets` | the environment |

`references` (Requirement/Injection/Grant) only ever governs the **environmental**
channel. Keeping that boundary explicit avoids scope creep of `references` into
structural composition.

## Decision Outcome

Adopt these as explicit design constraints for v2, applied as each relevant kind
is built (not implemented all at once now — `ProviderReferencesModel` as it
exists today is unaffected since a provider has no sibling fields to bind
against, i.e. it only ever does Job 1):

1. **Model Requirement, Injection, and Grant as separate fields/mechanisms**,
   never derive one from a union of another. `references` (Requirement) stays a
   union-composed "what do I need" list. Injection and Grant, when they are
   designed (provisioner/build and deploy/stage layers, not yet built in v2),
   must each be their own restriction-composed construct, not layered onto
   `references`.
2. **Decide Job 1 vs Job 2 explicitly per kind**, before adding `var:`/`secret:`/
   `feature:`-style sibling-field bindings to any new kind (`dns`, `network`,
   `module`, or successors). Default position: keep them as one field
   (`spec.references` as the binding namespace) only when the kind has no
   provisioner-scoping use for it separately — document the choice in that
   kind's own ADR or model docstring, not left implicit.
3. **Preserve the Requirement/Authority/Channel distinctions** as terminology
   when writing future ADRs or docstrings for resource/module/network/provisioner
   models, so the "who declares this and why" question has a standard vocabulary
   instead of re-deriving it per kind.

## Remaining Work

- No code changes required yet — `ProviderReferencesModel` is correct as-is
  (Job 1 only, no sibling bindings exist on `provider`).
- When designing `resource`, `network`, `module`, or `dns` kinds: explicitly
  record the Job 1/Job 2 decision for that kind's `references` field.
- When designing the provisioner/build layer: define Injection as its own
  restriction-composed mechanism, not derived from a union of `references`.
- When designing the deploy/stage layer: define Grant as its own mechanism
  covering variables and features, not just secrets (v1's `stage.secrets` only
  covered secrets — v2 should decide up front whether Grant applies uniformly to
  all three).
