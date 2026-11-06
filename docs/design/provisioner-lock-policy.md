# Provisioner lock policy - Design

- Status: current
- Last updated: 2026-11-06

## Overview

Terraform records the provider versions it chose in `.terraform.lock.hcl`, one file per root module. The SBOM
lists each provider at the version that file locks, and with no version when there is none, so a scanner cannot
check it ([sbom-generation.md](sbom-generation.md)). Two things make that gap visible:

1. **A notice, always on.** `build run` prints one `info` finding (`sbom_provider_unlocked`) per Terraform root
   whose declared providers end up without a version. `info` never fails a command, not even `validate --strict`.
2. **`provisioner_lock_policy`, opt-in.** A workspace policy that turns a missing or incomplete lock file into a
   warning (`enforcement: warn`) or an error (`deny`).

The user-facing reference is "Provisioner lock policy" in [workspace.md](../config/workspace.md). It follows the
policy framework of [ADR-0049](../decisions/0049-strata-policy-command-group.md): one workspace field, one
evaluator, one row in `strata policy`, a result in the audit manifest.

## Current Design

### The policy

```yaml
kind: workspace
spec:
  provisioner_lock_policy:
    enforcement: deny                # deny (default) | warn
    rings: [acc, prd]                # only deployments in these rings; default all
    allow:
      - provisioner: legacy-infra    # a provisioner name in this workspace
        reason: vendored root, providers pinned exactly in required_providers
        expires: 2026-12-31          # optional ISO date, honoured through that day
```

`ProvisionerLockPolicyModel` in `models/provisioner_lock_policy_model.py`. `provisioner_lock_policy: {}` already
requires a complete lock file for every Terraform/OpenTofu provisioner. `allow` entries have the shape of
`ImageTagAllowEntryModel`: a required `reason`, an optional `expires` (an unquoted YAML date is accepted), each
provisioner at most once. An entry that names no declared provisioner fails validation
(`WorkspaceSpecModel.validate_provisioner_lock_allow()`).

### Rule

`evaluate_provisioner_lock_policy(source_paths, workspace, ring, *, today=None)` in
`controllers/provisioner_lock_policy.py` is pure: it reads the policy from the workspace, the folder each
provisioner was materialised into, and the deployment's ring. For each `terraform` or `opentofu` provisioner, in
declaration order, that was materialised and is not exempt:

| Fails when                                                                                 | Code                       |
| ------------------------------------------------------------------------------------------ | -------------------------- |
| The source needs providers and its folder has no `.terraform.lock.hcl`                     | `provisioner_lock_missing` |
| A provider declared in `required_providers` anywhere in the source is not in the lock file | `provisioner_lock_missing` |

- **Needs providers**: any `*.tf` file declares `required_providers`, or a file in the root folder has a
  `provider`, `resource`, `data` or `module` block. A root with only outputs and locals passes.
- **Which files**: every `*.tf` file under the provisioner's folder, except inside `.terraform` (what
  `terraform init` downloaded during an earlier `deploy run` in the same build path) and below a nested folder
  with its own `.terraform.lock.hcl`, which is a separate root with its own lock.
- The message names the provisioner and the providers (`hashicorp/azurerm`; another registry keeps its host) and
  says to run `terraform init` and commit the lock file.
- `deny` makes the finding an error, `warn` a warning. There is no `on_missing_data`: the rule reads only files
  the build wrote.
- An expired `allow` entry no longer exempts the provisioner, and the finding says so.
- A deployment whose ring is not in `rings` gets one `info` finding (`provisioner_lock_policy_not_applied`) and is
  not judged. A deployment with no ring is judged.

The lock reading is shared with the SBOM: `read_provider_locks(root)` in
`integrations/sbom_collectors/terraform_collector.py` returns `ProviderLocks` (`lock_file`, `declared`, `locked`,
`needs_providers`, `unlocked`). The collector itself resolves, for every folder, the nearest lock file in that
folder or a parent within the build path, so a child module's providers take the root's locked version.

### Where it runs

| Command               | Behavior                                                                                                                                             |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata build run`    | After the SBOM, beside `cve_policy` and `checkov_policy`, over the `materialised` map. `deny` fails the build (exit 3). Not under `--dry-run`.       |
| `strata policy check` | Offline against `--build-path`, the paths re-derived by `expected_source_paths()`; recorded like the others. `policy list` shows `strata (offline)`. |
| `strata deploy run`   | Not evaluated: deploy uses the built sources, so the build's result stands.                                                                          |
| `strata validate`     | Not evaluated: remote sources are not materialised yet.                                                                                              |

`check_provisioner_lock_policy(context, deployment, workspace, source_paths)` is the entry point; it takes the
ring from `image_tag_policy.deployment_ring()`. The result is a `ManifestPolicyResultModel` (`policy_name:
provisioner_lock_policy`, `policy_type: provisioner_lock`, phase `build`). There is no `policy findings` row: the
finding already lists every provider.

### The notice

Without the policy, `TerraformCollector` adds one `info` diagnostic per root with a declared provider left without
a version: `main: no .terraform.lock.hcl; providers azurerm, random are listed in the SBOM without a version.` or
`main: .terraform.lock.hcl does not lock random; it is listed in the SBOM without a version.` A root with no lock
file that uses providers without declaring them (a root-level `provider`, `resource`, `data` or `module` block, no
`required_providers`) gets `main: no .terraform.lock.hcl; the providers it uses are not listed in the SBOM.`: such
a provider only reaches the SBOM through the lock file. An unlocked child
module folder is folded into the highest unlocked folder above it. With the policy set the notice is not printed,
so the policy's finding is the only report.

### Out of scope

- Other lock files: Helm `Chart.lock` (Helm is a module, not a provisioner, and a chart is pinned by
  `chart_version`), Ansible `requirements.yml`, Bicep (no lock file).
- Lock file hashes and platforms: `terraform init` checks them, and needs network access.
- Exempting a single provider: `allow` names a provisioner. A team with one provider it cannot lock locks the rest
  and allows the provisioner.

## Related Decisions

- [ADR-0049](../decisions/0049-strata-policy-command-group.md) - adding a policy is one field, one evaluator, one
  row in `strata policy`
- [ADR-0047](../decisions/0047-deploy-security-gates-as-vendor-neutral-capabilities.md) - the `deny | warn`
  convention
- [image-tag-policy.md](image-tag-policy.md) - the `rings` and `allow` shapes this copies
- [sbom-generation.md](sbom-generation.md) - why a missing lock file costs SBOM coverage

## History

- The default is a notice, not a warning, and enforcement is opt-in. A team may skip the lock file on purpose
  (cross-platform hash churn, providers pinned exactly), and 2.3.2 showed the cost of a default warning:
  `path_remote_outside_solution` broke every `validate --strict` pipeline using the layout it was made for.
- Named `provisioner_lock_policy`, not `terraform_lock_policy`, `iac_` or `infra_`: it is judged per
  `spec.provisioners` entry, `allow` names provisioners, and it covers OpenTofu, which uses the same file.
- An incomplete lock file is the same finding as a missing one: either way those providers have no version in the
  SBOM. The message says which case it is.
- Building the policy showed that the first lock-file collector read a lock only in the same folder as each `*.tf`
  file, so a child module's providers had no version even when the root locked them. Both the collector and the
  rule now use the nearest lock file, as Terraform does.
- Only root-level `provider`/`resource`/`data`/`module` blocks make a root need providers: a folder the root does
  not call is not run by `terraform init`. A declared provider anywhere always counts.
- The rule first read every `*.tf` file below the provisioner's folder. A nested root with its own lock file then
  failed the outer one, and `.terraform/` left by `deploy run` was read as source. Both are now skipped;
  `.terraform/` was already skipped by `checkov_policy`.
- An `allow` entry for an undeclared provisioner fails validation rather than warning: the workspace already
  rejects unknown names in `execution[].provisioner` and `depends_on`, and a typo would silently exempt nothing.
- Not evaluated in `strata validate` (remote sources are not there yet) nor at deploy (the built sources do not
  change); not in a dry run, which materialises nothing.
- Running `terraform init` during the build to produce a lock file was rejected in sbom-generation.md: it needs
  network access and credentials, and a lock the build made up is not the reviewed one.
- Dogfooded on a copy of `config/`, whose stand-in root uses `null_resource` without `required_providers`. The
  first notice only covered declared providers, so that root printed nothing although its provider was missing
  from the SBOM altogether; the notice now also covers a root that uses providers without declaring them. `warn`
  reports and passes, `deny` fails with exit 3, both record the result; with a lock file `deny` passes and the SBOM
  lists `pkg:terraform/hashicorp/null@3.2.1` from the lock alone.
