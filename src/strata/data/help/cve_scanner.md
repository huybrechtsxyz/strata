# CVE Scanner Integration

strata auto-detects either **Trivy** (preferred) or **Grype** in `PATH` for SBOM
vulnerability scanning. The scanner is used by the `cve_max_severity` policy and the
`strata build sbom --audit` command.

Installation — Trivy (preferred)
- macOS: `brew install trivy`
- Linux (script): `curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh`
- Windows (Scoop): `scoop install trivy`
- Docs: https://trivy.dev/latest/getting-started/installation/

Installation — Grype (alternative)
- macOS: `brew install anchore/grype/grype`
- Linux: `curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh`
- Docs: https://github.com/anchore/grype

Verify install
```
trivy --version   # or: grype version
```

No credentials required for scanning local SBOM files. For remote registry scans,
configure cloud provider credentials as for Terraform.

Activation
The scanner is used automatically when the `cve_max_severity` policy is declared:

```yaml
policies:
  - name: no_critical_cves
    type: cve_max_severity
    phase: build
    enforcement: deny
    configuration:
      max_severity: CRITICAL    # CRITICAL | HIGH | MEDIUM | LOW
      max_count: 0              # fail when count exceeds this
```

The scanner reads the `sbom.json` produced by `strata build sbom`. Run the SBOM step
before evaluating CVE policies:
```
strata build sbom -f deploy/deploy-prd.yaml
strata build run  -f deploy/deploy-prd.yaml   # policies evaluated here
```

Allowlist
Create `.strata/cve-allowed.yaml` to suppress known/accepted CVEs:
```yaml
allowed:
  - id: CVE-2024-12345
    reason: "Not applicable — we don't use this code path"
    expires: "2025-01-01"
```

Graceful degradation
- Neither Trivy nor Grype found → policy skips (passes), warning logged
- No `sbom.json` in build path → policy skips

Docs
- Trivy: https://trivy.dev
- Grype: https://github.com/anchore/grype
