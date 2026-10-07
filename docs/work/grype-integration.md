# Grype Backend (`CveScannerIntegration`) — Detail Reference

- Status: implemented — one of the two backends `CveScannerIntegration`
  auto-detects (`src/strata/integrations/cve_scanner.py`), used as the
  fallback when Trivy is not on `PATH`. This doc is the per-backend
  detail reference split out from the umbrella doc for clarity; it does
  not describe a separate class or a separate integration point
- Last updated: 2026-10-07
- Related: [cve-scanner-integration.md](cve-scanner-integration.md) (the
  framework/umbrella doc — design decisions, config shape
  (`cve_policy`), allowlist, `ScannerIntegration` capability, and the
  shared "what's left" tracking all three scanner integrations draw
  from), [trivy-integration.md](trivy-integration.md) (sibling backend,
  same class), [checkov-integration.md](checkov-integration.md) (the
  third scanner-type integration — not started, different capability
  shape)

## Overview

[Grype](https://github.com/anchore/grype) (Anchore) is one of the two
real, evidenced CLI vulnerability scanners `CveScannerIntegration`
auto-detects and wraps — the fallback when Trivy isn't installed (v1's
own real backend preference order, confirmed and ported as-is;
[cve-scanner-integration.md](cve-scanner-integration.md)'s Open Question 2
has the full real-adoption evidence for why both are kept rather than
picking one). Commonly paired with Anchore's own Syft SBOM generator, but
works against any CycloneDX/SPDX SBOM regardless of which tool produced
it — confirmed directly in this repo's own
[sbom-generation.md how-to](../how-to/sbom-generation.md).

## Real CLI invocation (shipped, `cve_scanner.py::_scan_grype()`)

```
grype sbom:<sbom_path> --output json
```

- Scans an **already-written CycloneDX `sbom.json`**, same hard
  dependency on [sbom-generation.md](sbom-generation.md) as Trivy.
- **No `--severity`-equivalent flag exists** — the one real asymmetry
  with Trivy. Grype always returns every finding; severity filtering
  happens entirely in Python afterward, in `_scan_grype()` itself:
  `[f for f in findings if f.severity in allowed]`, where `allowed` is
  the same `CVE_SEVERITY_ORDER`-derived set `_scan_trivy()` passes to the
  tool directly.

## Real output parsing (shipped, `cve_scanner.py::_parse_grype()`)

`data["matches"][]`, **not** pre-filtered by severity (see above):

```python
severity = (vulnerability.get("severity") or "UNKNOWN").upper()
severity = _NEGLIGIBLE_TO_LOW.get(severity, severity)  # Grype-only remap
CveFindingModel(
    vulnerability_id=vulnerability.get("id", ""),
    severity=severity,
    package_name=artifact.get("name", ""),
    installed_version=artifact.get("version", ""),
    fixed_version=fixed_versions[0] if fixed_versions else None,  # vulnerability["fix"]["versions"]
    title=vulnerability.get("description"),
    purl=artifact.get("purl"),
)
```

**The `NEGLIGIBLE` → `LOW` severity remap is Grype-only** — Trivy's own
vocabulary never produces a `NEGLIGIBLE` severity at all, confirmed v1
evidence (`_NEGLIGIBLE_TO_LOW` in `cve_scanner.py`). Covered by
`test_scan_sbom_with_grype_parses_and_remaps_negligible_to_low`.

Malformed/unexpected-shape JSON is caught by the same shared
`_safe_parse()` wrapper Trivy's parser uses, raised as a clean
`IntegrationError`.

## Version detection (shipped)

`grype version` (a subcommand, not a flag — unlike Trivy's `--version`),
parsed via the same shared `re.search(r"(\d+\.\d+\.\d+)", raw)` regex.

## Not ported / out of scope

- **Grype's own native ignore file** (`.grype.yaml`'s `ignore:` block) —
  confirmed incompatible with Trivy's own `.trivyignore`/
  `.trivyignore.yaml` (`cve-scanner-integration.md`'s Open Question 3), so
  neither is the v2 allowlist mechanism; `ConfigurationSpecModel.
  cve_allowed` is.
- **Vulnerability DB update management** — same "not yet checked, not
  assumed" stance as Trivy's own doc; current v2 behavior relies on
  whatever DB state the installed `grype` binary already has.

## Open Questions

1. None currently open specific to this backend — Grype's real,
   shipped-and-confirmed OpenVEX support (its own README: "OpenVEX
   support for filtering and augmenting scan results") is the reason
   OpenVEX was recommended as the starting flavor *if* VEX consumption is
   ever pursued (deferred, [cve-scanner-integration.md](
   cve-scanner-integration.md)'s own finding) — already fully captured
   there, not a gap needing tracking here.

## Changelog

- 2026-10-07: Split out from
  [cve-scanner-integration.md](cve-scanner-integration.md) into its own
  per-backend detail doc, per direct request — no new design decisions,
  purely extracting and organizing already-shipped, already-tested facts
  from that doc and `cve_scanner.py` itself. Trivy/Grype remain one
  class, two backends (`CveScannerIntegration`) — this split is
  documentation-only, not a code architecture change (confirmed directly
  with the user before proceeding).
