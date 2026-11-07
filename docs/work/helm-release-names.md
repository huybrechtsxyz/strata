# Helm release and namespace names - Work

- Status: in-progress (implemented; open questions remain)
- Last updated: 2026-11-07

## Overview

A Helm module is installed as a release with a name, into a Kubernetes namespace. Both names come from the
configuration, and strata never checks them. A name Helm or Kubernetes rejects passes `strata validate` and `build
run`, and fails only at `helm upgrade`, one module at a time. Two modules that end up with the same release in the
same Kubernetes namespace are not detected at all: the second deploy upgrades the first module's release with
another chart.

This doc designs a `strata validate` check for both, for `type: helm` modules. The rule is: if Helm or Kubernetes
would reject it, strata rejects it first, as an error. Argo CD application names (the same `release_name` field) are
out of scope here and follow later.

It is gap 7 of [helm-gap-analysis.md](helm-gap-analysis.md).

## Current state

Verified against the source on 2026-11-07.

### Where the names come from

`_render_meta()` (`integrations/helm.py`) writes them into `meta.yaml` at build time, and deploy reads them back:

| Name                 | Value                                                                         |
| -------------------- | ----------------------------------------------------------------------------- |
| Release name         | `module.spec.release_name`, else the module reference name in the namespace   |
| Kubernetes namespace | `module.spec.kubernetes_namespace`, else the strata Namespace document's name |

- Neither field has a format constraint (`str | None` in `models/module_model.py`), and no semantic check reads them.
- The defaults are `PlatformName`s (`^[a-z][a-z0-9_-]*$`, up to 64 characters). That allows `_` and lengths that Helm
  and Kubernetes reject, so a default is not safe either: a reference named `my_app` or a namespace named
  `apps_forge` fails at deploy without any explicit field.
- The values are written verbatim. A `${var:...}` token in either field is never resolved, so Helm receives the
  token text.
- Semantic errors already stop `build run` and every `deploy` command (`require_valid()`), so a validate error stops a
  bad name before anything runs.
- [module.md](../config/module.md) says `release_name` defaults to `meta.name`; the code uses the reference name.
  The field's "Design notes" in `module_model.py` say the same stale thing.

### The rules

| Name                 | Rule                                                                                       | Source                                                 |
| -------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| Release name         | `^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$`, at most 53 characters | Helm; verified with `helm template` 3.13.2             |
| Kubernetes namespace | `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` (DNS-1123 label), at most 63 characters                  | Kubernetes API server; not checkable without a cluster |

Probed with Helm 3.13.2: `my_app`, `My-app` and `app-` are rejected, `my.app` and a 53-character name pass, a
54-character name is rejected. `helm template` does not check the namespace; the API server does when
`--create-namespace` creates it, so that rule is Kubernetes' documented one.

## Design

### What is checked

Every enabled module reference (`ModuleReferenceModel.enabled`) whose module has `spec.type: helm`, in every
Namespace document. That is the set `build run` renders and a Helm step deploys from. Topology component modules are
not Helm releases (a Helm step deploys namespaces only), so they are not checked.

For each reference, the effective names are computed exactly as `_render_meta()` does. Both checks then run on what
Helm will really receive, defaults included. To keep one rule in one place, the effective-name function moves next to
`_render_meta()` and both use it.

### Check 1: name format (`_check_helm_release_names`, solution-wide)

Not tied to a workspace, because the names do not depend on one. Every finding is an error:

| Code                                | When                                                        |
| ----------------------------------- | ----------------------------------------------------------- |
| `helm_release_name_invalid`         | the effective release name breaks Helm's rule               |
| `helm_kubernetes_namespace_invalid` | the effective Kubernetes namespace breaks the DNS-1123 rule |

The message names the value, where it came from, the rule, and the fix, because the fix differs by origin:

```
error  Namespace 'apps', module reference 'my_app' (module 'authentik', type helm): release name 'my_app' is not
       a valid Helm release name: lowercase letters, digits, '-' and '.', starting and ending with a letter or
       digit, at most 53 characters. It is the reference name; rename the reference or set spec.release_name on
       module 'authentik'.  [helm_release_name_invalid]

error  Namespace 'apps_forge': it is the Kubernetes namespace of its Helm module references 'immich', 'jellyfin',
       and 'apps_forge' is not a valid Kubernetes namespace: a Kubernetes namespace has only lowercase letters,
       digits and '-', and starts and ends with a letter or digit. Rename the namespace, or set
       spec.kubernetes_namespace on module 'immich', 'jellyfin'.  [helm_kubernetes_namespace_invalid]
```

- When the value comes from the module's own field, the finding points at the module document
  (`spec.release_name` or `spec.kubernetes_namespace`) and is reported once per module, not once per reference.
- When it comes from a default, it points at the Namespace document: once per reference for a release name, and
  once per namespace for the namespace name, listing the references it applies to (one strata namespace with five
  charts would otherwise give five identical errors).
- A value containing a `${...}` token gets one extra sentence: tokens are not resolved in these fields.

### Check 2: one release per name and namespace (`_check_workspace_helm_releases`, per workspace)

Within one workspace, every Helm module reference across all the namespaces it renders
(`workspace_namespaces()`) must have a distinct (Kubernetes namespace, release name) pair. Two that share one are an
error, `helm_release_conflict`, naming both:

```
error  Workspace 'platform': release 'auth' in Kubernetes namespace 'apps' is installed by both namespace 'apps',
       reference 'auth' (module 'authentik') and namespace 'apps-extra', reference 'auth' (module 'authentik-v2').
       The second deploy would upgrade the first one's release. Set spec.release_name or
       spec.kubernetes_namespace on one of the modules.  [helm_release_conflict]
```

The workspace is the scope because it is the unit one deployment deploys. Two deployments of one workspace (dev and
prd) produce the same names by design and are not a conflict. The pair is only checked once both names are valid, so
a bad name gives one finding, not two.

### What does not change

- No schema pattern on the fields yet. `release_name` is shared with Argo CD, whose rule is different; a pattern belongs
  with the Argo CD work. The field descriptions do gain the Helm rules, because the schemas are the modelling
  reference (ADR-0053).
- No runtime check at deploy. Validation already runs before every build and deploy.
- No new configuration and no opt-out, in line with Helm's own behaviour: it refuses these names too.

## Implementation plan

- [x] 1. `release_identity(namespace, reference, module) -> ReleaseIdentity` in `integrations/helm.py`, used by
  `_render_meta()`; it also records whether each name came from the module's field
- [x] 2. The two rules as compiled patterns and a pure `release_name_problem()` / `kubernetes_namespace_problem()`
  that return the reason or `None`
- [x] 3. `_check_helm_release_names(index)` in `controllers/semantic_checks.py`, registered in
  `run_semantic_checks()`
- [x] 4. `_check_workspace_helm_releases(index, workspace)`, called from `_check_workspaces()`
- [x] 5. Tests: each rule's edges (the Helm probe values above, 53/54 and 63/64 characters, `_`, upper case, leading
  or trailing `-`, a dot in a namespace, a token); default versus explicit origin and where the finding points; a
  module with a bad explicit name referenced from two namespaces gives one finding; a disabled reference and a
  `compose` module are ignored; the conflict across namespaces and within one; no conflict across workspaces; an
  invalid name does not also produce a conflict
- [x] 6. Docs: [module.md](../config/module.md) (the real default and the rules), the field descriptions in
  `module_model.py` and the regenerated schema, [helm-chart-deployment.md](../design/helm-chart-deployment.md)
  (Validation and Diagnostics), the changelog, and gap 7 in [helm-gap-analysis.md](helm-gap-analysis.md)

## Related Decisions

- [ADR-0053](../decisions/0053-schemas-are-the-ai-readable-modelling-reference.md) - the rules go in the field
  descriptions
- [helm-chart-deployment.md](../design/helm-chart-deployment.md) - where the names are used
- [helm-gap-analysis.md](helm-gap-analysis.md) - gap 7

## Remaining Work / Open Questions

- **Existing configurations.** A config whose namespaces or references use `_` fails `validate`, `build run` and
  `deploy` once this lands, even if it never deployed a Helm step. That is intended (the deploy would fail), but haven
  should be checked before release: a `strata validate` run on it lists every name to change.
- **Two clusters in one workspace.** strata does not know which cluster a step deploys to (gap 6). A workspace whose
  Helm steps go to different clusters could legitimately reuse a pair, and this check would reject it. No consumer is
  known to do this. If one appears, scope the check to steps that share a cluster once gap 6 gives strata that fact.
- **Argo CD.** The same check for `type: argocd` (`release_name` as the Application name, a DNS-1123 subdomain of up
  to 253 characters) and a schema pattern on the shared field.

## Changelog

- 2026-11-07: implemented. A default namespace name is reported once per namespace instead of per reference.
  `test_release_name_problem_agrees_with_real_helm` runs the rule against `helm template` so it cannot drift.
- 2026-11-07: created. Release name rule verified with `helm template` 3.13.2; namespace rule from Kubernetes.
