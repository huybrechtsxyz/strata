# How To: Use the SBOM Feature (CycloneDX)

Every `strata build run` now also generates a CycloneDX 1.6 Software Bill of Materials
(SBOM) — automatically, with no flag to opt in or out. This guide covers what gets
scanned, where the output lands, how to feed it into an external scanner/registry, and
how to add a collector for a technology this doesn't cover yet. See
[docs/design/sbom-generation.md](../design/sbom-generation.md) for the full design
rationale and evidence trail.

## The short answer

- **Nothing to configure.** `strata build run` writes `sbom.json` at the root of
  `--build-path` (next to `resolved.yaml`), every time, right after every provisioner
  and workload module has been rendered.
- **It's a real, spec-compliant CycloneDX 1.6 document**, validated in-process before
  being written — any CycloneDX-consuming tool (`cyclonedx-cli`, trivy, grype, OWASP
  Dependency-Track, Anchore Syft) can read it directly, today, with zero strata-specific
  tooling.
- **4 component sources today**: container images, Helm charts, and Terraform
  providers/modules. Application-level dependencies (lockfiles) and CVE scanning are not
  built yet — see [What's not covered yet](#whats-not-covered-yet).

## What gets scanned

| Collector   | What it reads                                                                                                             | Where that data comes from                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `image`     | Every resolved module's `spec.services[].image`                                                                           | The in-memory, already-resolved `ModuleModel` (no file scan needed)             |
| `compose`   | `services.<name>.image` in every rendered `docker-compose.yml`/`.yaml`                                                    | Files `ComposeIntegration.prepare_namespace()` already wrote under `build_path` |
| `helm`      | Chart-based modules' `spec.source.{chart_name,chart_version}`, **and** every rendered `Chart.yaml` + its `dependencies[]` | Both the resolved module and files `sync_module_source()` already materialised  |
| `terraform` | `required_providers {}` and `module "x" {}` blocks in every `*.tf` file                                                   | The synced provisioner source under `build_path`, parsed via `python-hcl2`      |

A floating/mutable tag (`:latest`, `:main`, a tag that isn't semver-shaped) is still
included as a component — it just gets a `strata:tag-stability: floating` property and a
`Severity.WARNING` finding in the command's diagnostics output. This never fails the
build; it's informational.

## Example output

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "components": [
    { "type": "container", "name": "server", "version": "2024.1.0",
      "purl": "pkg:docker/ghcr.io/goauthentik/server@2024.1.0" },
    { "type": "library", "name": "authentik", "version": "2024.1.0",
      "purl": "pkg:helm/authentik@2024.1.0" },
    { "type": "library", "name": "azurerm", "version": "~>3.90",
      "purl": "pkg:terraform/hashicorp/azurerm@~>3.90" }
  ]
}
```

## Feeding it into an external scanner or registry

Since `sbom.json` is a standard CycloneDX document, wire it into whatever your pipeline
already uses — strata doesn't need to know about any of these:

```bash
# Fail the pipeline on a CRITICAL finding (trivy)
trivy sbom build/sbom.json --exit-code 1 --severity CRITICAL

# Same idea with grype
grype sbom:build/sbom.json --fail-on critical

# Validate against the official CycloneDX schema with the canonical CLI
cyclonedx-cli validate --input-file build/sbom.json --input-format json

# Upload to OWASP Dependency-Track for trend tracking
curl -X POST "$DTRACK_URL/api/v1/bom" -H "X-Api-Key: $DTRACK_API_KEY" \
  -F "project=$PROJECT_UUID" -F "bom=@build/sbom.json"
```

**strata itself does not gate a build or deploy on scan results today.** It only
generates the artifact. v1 had an in-process CVE scanner + policy engine that *could*
block a build — v2 doesn't have that yet (see
[What's not covered yet](#whats-not-covered-yet)). Until it does, the pattern above (a
separate pipeline step reading `sbom.json`) is the way to gate on vulnerability findings.

## What's not covered yet

- **Application-level dependencies** (`requirements.txt`, `package-lock.json`, `go.sum`,
  …) — no lockfile collector exists yet; no trigger defined for when to build one.
- **Ansible collections** — no v2 Ansible integration exists to collect from at all.
- **CVE scanning** — no in-process `trivy`/`grype` integration; use the external-tool
  pattern above instead.
- **`sbom_*` policies / blocking a build on severity** — would need the CVE scanner
  integration above plus a scoped policy check; not designed yet.
- **Deployment manifest embedding** — `sbom.json`'s hash isn't recorded anywhere else
  yet (no v2 deployment manifest feature exists). The file itself is still written and
  verified; nothing currently reads a reference to it.

## Adding a collector for a new technology

A collector is one small class implementing `SbomCollector.collect()` — no changes to
`write_sbom()` or any existing collector are ever needed.

**Built-in** (ships with strata): add the class under
`strata/integrations/sbom_collectors/`, then one entry in `registry.py`'s `_KNOWN` dict:

```python
# strata/integrations/sbom_collectors/npm_collector.py
class NpmLockfileCollector(SbomCollector):
    def collect(self, graph, modules, build_path):
        ...

# registry.py
_KNOWN["npm"] = ("strata.integrations.sbom_collectors.npm_collector", "NpmLockfileCollector")
```

**Third-party / private** (an org-specific source strata will never ship): a separate
installable package registering an entry point — zero changes to this repo:

```toml
# some-other-package's pyproject.toml
[project.entry-points."strata.sbom_collectors"]
npm = "acme_strata_plugins.npm_collector:NpmLockfileCollector"
```

Either way, `write_sbom()` picks it up automatically via
`registry.list_collectors()` — see
[docs/design/sbom-generation.md](../design/sbom-generation.md#extensibility--a-new-collector-is-one-small-class-zero-core-repo-changes-for-a-third-party)
for the full extensibility design.
