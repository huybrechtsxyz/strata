# How To: Migrate a v1 `workspace.yaml` (Topology/Provisioning Decoupling)

You have a real v1 `workspace.yaml` whose `spec.topology[]` entries carry
`provider`/`provisioner` fields directly, and a `deployment.yaml` whose
`stages[]` re-declare `topology`/`provisioner` bindings per stage. This is
the one part of a v1 → v2 migration that isn't a mechanical field rename
([docs/design/gap_fit_v1.md](../design/gap_fit_v1.md) gap #5) — one v1 document becomes several
v2 documents, and the *binding* between "what infrastructure" and "which
tool builds it" moves to a new place entirely. This guide walks through
converting one real example end to end.

See also: [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md)
(the full from-first-principles reasoning this migration follows),
[docs/design/gap_fit_v1.md](../design/gap_fit_v1.md) gap #5 (where this migration effort is
tracked), and the real worked example this guide is built from:
[`.v2-cfg/workspaces/spoke.yaml`](../../.v2-cfg/workspaces/spoke.yaml) /
[`.v2-cfg/topologies/spoke-cluster.yaml`](../../.v2-cfg/topologies/spoke-cluster.yaml)
— a real, hand-migrated `config-deploy` stack, chosen for that ADR's
own coverage-check pass specifically because it exercises this exact gap.

## The short answer

**v1 mixed three separate concepts into one `topology[]` entry. v2 splits
them into three separate places:**

| Concept                                                       | v1                                                                                                                                                  | v2                                                                                                                                    |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| "What belongs together" (grouping)                            | `workspace.yaml`'s `spec.topology[]` entry                                                                                                          | A standalone `kind: topology` document — pure grouping, no provider/provisioner field at all                                          |
| "What tool builds it, and where's its code"                   | `workspace.yaml`'s `spec.workspace.provisioners[]` (already mostly independent in v1)                                                               | `workspace.yaml`'s `spec.provisioners[]` — same idea, now the *only* place tool bindings live                                         |
| "Run this tool, targeting this infrastructure, in this order" | `workspace.yaml`'s `topology[].provider`/`.provisioner` **and** `deployment.yaml`'s per-stage `topology`/`provisioner` (re-declared at deploy time) | `workspace.yaml`'s `spec.execution[]` — one explicit, ordered recipe, baked into the workspace once, never re-declared per deployment |

The deployment no longer needs to know which tool builds which piece of
infrastructure at all — that knowledge is baked into the workspace once, the
same way a container image bakes in its own build recipe rather than making
every `docker run` re-specify it (ADR-0011's own framing).

## Step by step, using the real spoke stack

### 1. Find the `provider`/`provisioner` binding on the topology entry

Real v1 `stacks/spoke/workspace.yaml`:

```yaml
spec:
  topology:
    - name: spoke-cluster
      provider: azure
      provisioner: core_iac
      type: standalone
      components:
        - resource: spoke_resx
```

`provider: azure` and `provisioner: core_iac` are the two fields that don't
survive the move — everything else on this entry (`type`, `components`)
does.

### 2. Split the topology entry into a standalone `kind: topology` document

Everything **except** `provider`/`provisioner` becomes the new document's
`spec` — same field names, same values:

```yaml
# topologies/spoke-cluster.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: spoke-cluster
spec:
  type: standalone
  components:
    - resource: spoke_resx
```

The workspace now references it by name, not by embedding it:

```yaml
# workspaces/spoke.yaml
spec:
  topology:
    - spoke-cluster
```

### 3. Confirm the provisioner already exists (it almost always does)

v1's `workspace.yaml` already declared `core_iac` independently under
`spec.workspace.provisioners[]` (or `spec.provisioners[]`, depending on how
deep your v1 solution nested it) — the topology entry's `provisioner:
core_iac` was always just a *second, redundant reference* to a provisioner
declared elsewhere in the same file. Nothing new needs authoring here; the
provisioner block itself ports over field-for-field (`name`/`tool`/`source`/
`backend`/`configuration` — see the real spoke example's full provisioner
block for the field-name changes unrelated to this gap, e.g. v1's
`repository:` → v2's `remote:`, v1's `provisioner: terraform` → v2's `tool:
terraform`).

### 4. Write the `execution[]` step that carries the old binding forward

This is the one genuinely new piece of authoring. One `ProvisioningStepModel`
entry per binding v1 had scattered across `topology[].provisioner` and every
deployment stage that referenced it:

```yaml
# workspaces/spoke.yaml
spec:
  execution:
    - name: provision-spoke
      provisioner: core_iac
      scope: infra
      targets:
        - spoke_resx
```

Field-by-field, translated from what v1 had:

- **`name`** — new; a free-form label for this step (not present in v1 at
  all — v1 had no addressable "step" concept, just per-stage
  `topology`/`provisioner` fields). Pick something that describes the
  action, not the topology (`provision-spoke`, not `spoke-cluster` — the
  topology name is still available separately via `targets`/grouping).
- **`provisioner`** — the exact same value that was on the topology entry's
  `provisioner:` field (`core_iac`) — this is the direct carry-over.
- **`targets`** — **not** the topology's name. List every `Resource`/
  `Namespace`/`Dns`/`Network`/`Firewall` **name** the topology's
  `components[]`/`namespaces[]` reference (here, just `spoke_resx`, matching
  the topology's own single component). "This step realizes topology X" is
  a *derived* fact in v2 (the intersection of a step's `targets` with a
  topology's resources) — never declared directly (ADR-0011's own "no
  direct relationship" decision, see below).
- **`scope`** — carries over from whatever v1's deployment stage used for
  this binding (`infra`/`apps` is v1's own free-form vocabulary,
  unchanged) — not a new concept, just relocated from the deployment's
  stage onto the workspace's own step (see gap #6 in
  [docs/design/gap_fit_v1.md](../design/gap_fit_v1.md), already resolved: `scope` was always
  meant to live here, not on `DeploymentStageModel`).
- **`depends_on`** — only needed if this step must run after another named
  step (v1 had no equivalent field here at all; if v1's real ordering came
  from stage list order, preserve that same order using `depends_on` now
  that steps are addressable by name instead of positional).

### 5. Delete the per-stage `topology`/`provisioner` fields from `deployment.yaml`

Nothing replaces them — a v2 `Deployment` runs whatever `execution[]` recipe
its `Workspace` already declares. If your v1 `deployment.yaml`'s stages
carried nothing else useful (no per-stage timeout/lifecycle override), the
whole `stages[]` block may disappear entirely.

## Why the topology entry and the execution step are two separate documents, not one

Worth understanding before migrating a second, more complex workspace: v1's
1:1 `topology.provisioner` binding doesn't survive contact with a real
multi-tool pipeline. A real deployment routinely has Terraform provision a
cluster, then Ansible configure part of it, then Helm deploy workloads onto
it — three provisioners, one topology, none of them "the" provisioner for
it. ADR-0011 resolves this with a **many-to-many relationship, never
declared directly**: a `Topology` and a `ProvisioningStep` independently
reference the same underlying pool of `Resource`/`Namespace` names; "this
step realizes topology X" is *derived* (intersect the step's `targets` with
the topology's resources), never a field on either document. The spoke
example above is the simple 1:1 case (one topology, one step) — it still
goes through the same split, since a workspace's *second* topology or
*second* tool is exactly the case v1's shape couldn't represent at all.

## Checklist for your own workspace

1. For every `topology[]` entry with a `provider`/`provisioner` field:
   create a standalone `topologies/<name>.yaml` document carrying
   everything **except** those two fields.
2. Confirm every provisioner referenced this way is already declared under
   the workspace's own `spec.provisioners[]` (it will be, in every real v1
   workspace — the topology entry was always a second reference to it, not
   its only declaration).
3. Add one `spec.execution[]` step per distinct topology-entry-to-provisioner
   binding you removed in step 1, with `targets` listing the underlying
   resource/namespace names (not the topology's own name).
4. Carry `scope` over from whichever deployment stage used to reference this
   binding.
5. Add `depends_on` between steps only where v1's stage *order* actually
   mattered — most real v1 workspaces have exactly one topology and one
   provisioner, so this is usually empty.
6. Delete the now-empty `topology`/`provisioner` fields from every
   `deployment.yaml` stage that referenced this workspace — nothing
   replaces them at the deployment level.
7. Run `strata validate` — `WorkspaceService`'s dynamic validation
   cross-checks `execution[].targets` against real resource/namespace names
   the same way v1 cross-checked `topology[].components[].resource`, just
   moved to Phase 2 (ADR-0011's own "syntax-checked, not existence-checked"
   note — `Topology` alone can no longer confirm a resource exists, since
   it's no longer nested inside the same file as the resource list).
