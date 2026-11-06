# How To: Use the SBOM (CycloneDX)

Every `strata build run` writes a CycloneDX 1.6 Software Bill of Materials, `sbom.json`, at the
root of the build path (next to `resolved.yaml`). There is nothing to configure. This guide covers
what it contains, how strata and other tools use it, and how to add a collector. The design is in
[docs/design/sbom-generation.md](../design/sbom-generation.md).

## What is in it

- **The deployment** (`metadata.component`): an application named after the deployment, with the
  workspace, `kind: version` and the commit being built (`strata:commit`) as properties. It depends
  on every component. The commit is `STRATA_COMMIT_SHA` or the CI's own variable (`GITHUB_SHA`,
  `BUILD_SOURCEVERSION`, `CI_COMMIT_SHA`, ...), else `git rev-parse HEAD`, the order the release
  gate uses.
- **The tool** (`metadata.tools`): `strata` and its version.
- **Components**, one per package URL (purl), each with the purl as its `bom-ref`, so two builds of
  the same deployment can be diffed component by component:

| Collector   | Reads                                                                                                                                  |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `image`     | Every resolved module's `spec.services[].image`, and artifact images                                                                   |
| `compose`   | `services.<name>.image` in every rendered `docker-compose.yml`/`.yaml`                                                                 |
| `helm`      | Chart modules (`chart_name`/`chart_version`, the repository or the remote's URL), every rendered `Chart.yaml` and its `dependencies[]` |
| `terraform` | `required_providers {}` and `module "x" {}` blocks in every rendered `*.tf`, and each root module's `.terraform.lock.hcl`              |

Versions are the deployed ones: `kind: version` pins and `--pin` are applied first. The SBOM is
validated against the CycloneDX schema before it is written, and is not written in a dry run.

A floating image tag (none, `latest`, `main`, `dev`, or a tag that is not a version number) is
listed with the property `strata:tag-stability: floating` and reported as a warning. It never
fails the build; use the [image tag policy](../design/image-tag-policy.md) for that.

An image or chart says where it is used in `strata:used-by`, for example
`service:authentik/server` or `variable:API_IMAGE, service:web/api` for an image and
`module:authentik` for a chart, so a CVE on a component points to the module that owns it. A
version that a [`kind: version`](../config/version.md) pin chose also has
`strata:pinned-by: <version document>`.

An image you reference by digest (`image: ghcr.io/acme/api:1.4.2@sha256:...`) also lists that
digest under `hashes` (`{"alg": "SHA-256", "content": "..."}`). An image referenced by tag has
none: strata does not ask the registry.

## Example

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "metadata": {
    "component": {
      "type": "application", "name": "prd-deployment", "bom-ref": "strata:deployment/prd-deployment",
      "properties": [{ "name": "strata:version", "value": "prd" }, { "name": "strata:workspace", "value": "main" },
                     { "name": "strata:commit", "value": "cf305ff..." }]
    },
    "tools": { "components": [{ "type": "application", "name": "strata", "version": "2.3.3" }] }
  },
  "components": [
    { "type": "container", "name": "server", "version": "2026.5.2",
      "bom-ref": "pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io",
      "purl": "pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io" },
    { "type": "container", "name": "db", "version": "16-alpine",
      "bom-ref": "pkg:docker/postgres@16-alpine", "purl": "pkg:docker/postgres@16-alpine" },
    { "type": "library", "name": "authentik", "version": "2026.5.2",
      "bom-ref": "pkg:helm/authentik@2026.5.2?repository_url=https://charts.example.io",
      "purl": "pkg:helm/authentik@2026.5.2?repository_url=https://charts.example.io" }
  ],
  "dependencies": [
    { "ref": "strata:deployment/prd-deployment",
      "dependsOn": ["pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io", "..."] }
  ]
}
```

Image purls follow the purl specification: the registry is the `repository_url` qualifier, and
Docker Hub images (`postgres`, `library/postgres`, `docker.io/library/postgres`) all become
`pkg:docker/postgres`.

Terraform providers are listed at the version locked in `.terraform.lock.hcl`, e.g.
`pkg:terraform/hashicorp/azurerm@4.12.0`. Commit the lock file next to your root module's `*.tf`
files, as Terraform recommends: without it a provider is listed with no version, so scanners
cannot check it. The `required_providers` constraint is always in the
`strata:version-constraint` property. `build run` prints an `info` line for each Terraform root whose
providers end up without a version (no lock file, or one that does not lock them); it never fails
the build. To make it fail, set the workspace's
[`provisioner_lock_policy`](../config/workspace.md#provisioner-lock-policy).

## How strata uses it

| Feature                                      | What it does with `sbom.json`                                                                  |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `cve_policy` (workspace)                     | `build run` scans it with Trivy or Grype right after writing it and fails or warns on findings |
| `image_gate_policy` (workspace)              | `deploy run` asks the security platform about every container image in it                      |
| `strata policy findings --policy cve_policy` | Lists every CVE in it, allowlisted ones marked                                                 |
| Deploy audit record                          | Stores its path, format, SHA-256 and component count                                           |

The gates are set up in [security-gates.md](security-gates.md).

## Using it in other tools

`sbom.json` is a standard CycloneDX document, so any CycloneDX tool reads it directly:

```bash
trivy sbom build/sbom.json --exit-code 1 --severity CRITICAL
grype sbom:build/sbom.json --fail-on critical
cyclonedx-cli validate --input-file build/sbom.json --input-format json
curl -X POST "$DTRACK_URL/api/v1/bom" -H "X-Api-Key: $DTRACK_API_KEY" \
  -F "project=$PROJECT_UUID" -F "bom=@build/sbom.json"
```

## Not covered

- Application lockfiles (`requirements.txt`, `package-lock.json`, `go.sum`, ...).
- Ansible collections.
- The version a Terraform module range resolves to: a registry module whose `version` is a range
  (`~> 5.0`) is listed without a version and with the range in `strata:version-constraint`. Pin
  an exact version (`version = "5.0.0"`) to have it scanned.

## Adding a collector

A collector is one class implementing `SbomCollector.collect(graph, modules, build_path)`;
`write_sbom()` picks up every registered one.

**Built-in**: add the class under `strata/integrations/sbom_collectors/` and one entry to
`registry.py`'s `_KNOWN` table:

```python
# strata/integrations/sbom_collectors/npm_collector.py
class NpmLockfileCollector(SbomCollector):
    def collect(self, graph, modules, build_path):
        ...

# registry.py
_KNOWN["npm"] = ("strata.integrations.sbom_collectors.npm_collector", "NpmLockfileCollector")
```

**Third-party**: a separate package declaring an entry point, with no change to strata:

```toml
[project.entry-points."strata.sbom_collectors"]
npm = "acme_strata_plugins.npm_collector:NpmLockfileCollector"
```

A component found by more than one collector is listed once (by purl), so a new collector may
overlap with an existing one.
