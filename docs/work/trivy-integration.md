# Trivy Backend (`CveScannerIntegration`) — Detail Reference

- Status: implemented — one of the two backends `CveScannerIntegration`
  auto-detects (`src/strata/integrations/cve_scanner.py`), preferred over
  Grype when both are on `PATH`. This doc is the per-backend detail
  reference split out from the umbrella doc for clarity; it does not
  describe a separate class or a separate integration point
- Last updated: 2026-10-07
- Related: [cve-scanner-integration.md](cve-scanner-integration.md) (the
  framework/umbrella doc — design decisions, config shape
  (`cve_policy`), allowlist, `ScannerIntegration` capability, and the
  shared "what's left" tracking all three scanner integrations draw
  from), [grype-integration.md](grype-integration.md) (sibling backend,
  same class), [checkov-integration.md](checkov-integration.md) (the
  third scanner-type integration — not started, different capability
  shape)

## Overview

[Trivy](https://trivy.dev) (Aqua Security) is one of the two real,
evidenced CLI vulnerability scanners `CveScannerIntegration` auto-detects
and wraps — preferred over Grype when both are installed (v1's own real
backend preference order, confirmed and ported as-is;
[cve-scanner-integration.md](cve-scanner-integration.md)'s Open Question 2
has the full real-adoption evidence for why both are kept rather than
picking one).

## Real CLI invocation (shipped, `cve_scanner.py::_scan_trivy()`)

```
trivy sbom <sbom_path> --format json --severity <CSV> --exit-code 0
```

- Scans an **already-written CycloneDX `sbom.json`**, never a container
  image or filesystem path directly — the hard dependency on
  [sbom-generation.md](sbom-generation.md) existing first.
- `--severity` is a comma-separated list computed from
  `CVE_SEVERITY_ORDER[: index(severity_threshold) + 1]` — **severity
  filtering happens inside the tool itself** via this flag (the one real
  asymmetry with Grype, which has no equivalent flag — see
  [grype-integration.md](grype-integration.md)).
- `--exit-code 0` — the subprocess is never asked to fail on findings;
  `evaluate_cve_policy()` applies the real `max_count`/`max_severity` gate
  itself, not Trivy's own exit code.

## Real output parsing (shipped, `cve_scanner.py::_parse_trivy()`)

`data["Results"][]["Vulnerabilities"][]`, already pre-filtered by the
tool's own `--severity` flag:

```python
CveFindingModel(
    vulnerability_id=vuln["VulnerabilityID"],
    severity=vuln.get("Severity", "UNKNOWN"),
    package_name=vuln.get("PkgName", ""),
    installed_version=vuln.get("InstalledVersion", ""),
    fixed_version=vuln.get("FixedVersion"),
    title=vuln.get("Title"),
    purl=(vuln.get("PkgIdentifier") or {}).get("PURL"),
)
```

Malformed/unexpected-shape JSON (missing `VulnerabilityID`, unparseable
JSON entirely) is caught by the shared `_safe_parse()` wrapper and raised
as a clean `IntegrationError`, not an uncaught `KeyError`/
`JSONDecodeError` — covered by
`test_scan_sbom_raises_a_clean_integration_error_on_malformed_json`/
`..._on_unexpected_json_shape` in
`tests/strata/integrations/test_integrations_cve_scanner.py`.

## Version detection (shipped)

`trivy --version` (a flag, not a subcommand — unlike Grype's `version`),
parsed via a shared `re.search(r"(\d+\.\d+\.\d+)", raw)` regex
(`CveScannerIntegration.parse_version()`).

## Not ported / out of scope

- **Vulnerability DB freshness/update management** — v1 evidence not yet
  checked for whether `CveScannerIntegration` ever invoked `trivy image
  --download-db-only` or relied on Trivy's own auto-update-on-scan
  behavior. Current v2 behavior: whatever DB state the installed `trivy`
  binary already has — no explicit update step. Flag as an open question
  only if real-usage evidence ever surfaces a need (same "evidence over
  assumption" discipline as the rest of this feature).
- **Trivy's own native ignore file** (`.trivyignore`/`.trivyignore.yaml`)
  — confirmed incompatible with Grype's own `.grype.yaml` `ignore:` block
  (`cve-scanner-integration.md`'s Open Question 3), so neither is the v2
  allowlist mechanism; `ConfigurationSpecModel.cve_allowed` is.

## Open Questions

1. Does Trivy's own `--vex` flag matter for a future VEX-consumption
   feature (deferred per `cve-scanner-integration.md`'s own finding)?
   Left explicitly **unconfirmed** — Trivy's VEX docs page could not be
   fetched to confirm which flavor(s) (OpenVEX/CycloneDX VEX/CSAF) it
   accepts, unlike Grype's confirmed-OpenVEX-only support. Check directly
   before ever building VEX consumption, don't assume parity with Grype.

## Changelog

- 2026-10-07: Split out from
  [cve-scanner-integration.md](cve-scanner-integration.md) into its own
  per-backend detail doc, per direct request — no new design decisions,
  purely extracting and organizing already-shipped, already-tested facts
  from that doc and `cve_scanner.py` itself. Trivy/Grype remain one
  class, two backends (`CveScannerIntegration`) — this split is
  documentation-only, not a code architecture change (confirmed directly
  with the user before proceeding).
