# Workspace Overrides (per-deployment rebinding of workspace content) — Design

- Status: current
- Last updated: 2026-11-20

## Overview

A workspace is the shared platform recipe, and the documents it names are its defaults: which providers it targets,
which assets its steps consume. One workspace often has to serve deployments that differ in exactly that: one region
or account per deployment, or one set of API definitions per customer. A deployment can therefore **rebind** a
workspace slot to another document of the same kind.

Two categories are built, each in its own design doc:

| Category  | Workspace declares          | Deployment rebinds          | Design                                    |
| --------- | --------------------------- | --------------------------- | ----------------------------------------- |
| Providers | `spec.providers[]` slots    | `deployment.spec.providers` | [provider-slots.md](provider-slots.md)     |
| Assets    | `spec.assets[]` slots       | `deployment.spec.assets`    | [asset-management.md](asset-management.md) |

This doc holds what every category shares: where a rebinding lives, its shape, and how `extends` merges it.

## The rule

1. The **workspace** declares named **slots**, each with a default document.
2. The **deployment** rebinds slots by name, one top-level field per category (`providers`, `assets`), each entry
   `{slot, <kind>}` naming another document of that kind. Identity is by name
   ([ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)).
3. **Rebind only**, never field patches: per-deployment values already flow through environments.
4. Reuse through **`extends`/`partial`**: a `partial: true` base deployment carries the bindings once, every
   deployment that needs them extends it, and the child wins.
5. **Separate fields per category**, for readability and per-category validation.

### Why the deployment, not the environment

v2 splits the two kinds by responsibility, as `DeploymentSpecModel.extends` says: environments compose **values**,
`extends` composes **structure**.

| Kind            | Role                                                  | Holds                                                                                     |
| --------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Environment** | Value source for `${var:}`/`${secret:}`/`${feature:}` | `variables`, `secrets`, `features`, `properties`: nothing structural                      |
| **Deployment**  | Composition root: *which documents* run               | `workspace`, `environments`, `tenant`, `version`, `stages`; `extends`/`partial` for reuse |

Choosing which document fills a slot is a document selection, like `workspace:` and `version:`, which already live
on the deployment.
- A rebinding on the environment would turn a value document into a structural one; v1's
  `environment.spec.overrides.*` did that one level deeper and shipped with no tracked use.
- Environments stack (tenant environments, then the deployment's own), so a rebinding could come from any layer and
  would need its own precedence rule. On the deployment, precedence is the existing `extends` rule.

## How `extends` merges

`merge_deployment_specs()` ([deployment_service.py](../../src/strata/services/deployment_service.py)) merges a base
deployment's spec into a child's, on raw dicts before validation (a `partial` base lacks required fields). The rule a
DevOps engineer already expects from Kustomize, Compose override files and strata's own environments: **a keyed
collection merges by its key, a plain list replaces.**

| Shape                                          | Rule                                                                                      |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Maps (`locking`, `properties`, `custom`, ...)  | deep-merged per leaf key, child wins                                                      |
| `stages`, `providers`, `assets`                | by `step`, `slot`, `slot` (`LIST_MERGE_KEYS`): a child item deep-merges over the base's, new keys append |
| `verify.checks`, on the spec and in a merged stage | by `name` (`VERIFY_CHECK_MERGE_KEY`), the same way; `health_checks` was removed in 2.9 |
| `environments`, `configurations`               | the base's names, then the child's new ones (`APPEND_LISTS`); later environments win at value resolution |
| `version`                                      | the base's documents, then the child's, each once ([version-composition.md](version-composition.md)) |
| any other list                                 | replaced by the child's                                                                   |
| `extends`, `partial`                           | consumed, not part of the result                                                          |

The tables are declared once, so a new keyed list is one entry, not a special case. `strata render` reads the same
keys to address a named item by its name when it shows where each value came from.

## Not covered

- **Rebinding `resources` or `modules`** (v1's `overrides.resources`: count, configuration, enabled per resource;
  `overrides.modules`: enabled, slot type, configuration). No real deployment has needed it; a module's image is
  already covered by `version` pins. A new category follows the rule above when one does.
- **Remote references** (v1's `overrides.remotes`): `version.spec.pins.remotes` already sets a remote's ref per
  deployment.
- **Workspace properties** (v1's `overrides.properties`): environment `properties` already merge into the
  deployment.
- **`includes`, `output_files`** (v1's Terraform file merging and output shaping): build machinery, not ported.

## Related Decisions

- [provider-slots.md](provider-slots.md) and [asset-management.md](asset-management.md) — the two built categories.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — workspace/provisioning decoupling; removed
  v1's per-stage provider/topology binding.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — identity is `(kind, meta.name)`, never
  a file, which is why a rebinding needs a stable slot name.
- [parameter-sets.md](parameter-sets.md) — the merge rules of every kind with `extends`.
- v1 ADR-0036 (`strata/docs/decisions/0036-workspace-provider-environment-overrides.md`, in the v1 repository) — the
  original provider override: swap the provider file per environment.

## History

- The doc began as `environment-overrides.md`, after v1's `environment.spec.overrides` (`providers`, `remotes`,
  `resources`, `modules`, `properties`, `includes`, `output_files`). v2 had ported none of it: there were 0 uses of
  `overrides` across 26 real environment documents, and the provider override (v1 ADR-0036) had none in four consumer
  repositories. Two real needs reopened it: one workspace in several regions, and one APIM workspace serving about
  300 customers, each with its own API definitions.
- Rebinding was put on the deployment, not the environment, following the values-versus-structure split; the doc was
  renamed accordingly. Binding as maps (`providers: {azure: azure-north}`) was rejected: it would merge per key with
  no new code, but not match the list shape on the workspace side.
- An early draft justified separate fields with "`extends` replaces top-level fields wholesale". That was wrong:
  `merge_deployment_specs()` deep-merges maps, and the `extends` field description that said otherwise was corrected.
  The merge-key table came from the same check: every keyed list merges by its key.
- Providers were built first, then assets (with the asset `location` and `pins.assets`); each moved to its own
  design doc.
- The last keyed list, a stage's `health_checks`, was added to the table, merged by `name` inside a merged stage, and
  `configurations` now appends like `environments`. Both fields were accepted but not yet used, so nothing that ran
  changed. With that, nothing was open and the doc moved to design.
