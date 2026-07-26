"""Built-in prompt for CVE vulnerability analysis from SBOM audit scans."""

from __future__ import annotations

from typing import Any


class CveAnalysisPrompt:
    """Explain CVE findings from a scanner audit and suggest remediation."""

    VERSION = "1.0"

    SYSTEM = """\
You are a supply-chain security analyst reviewing CVE vulnerability findings from an infrastructure build.
The findings come from a SBOM scanner (trivy or grype). Analyse the results and respond with a JSON object only.

Required fields:
  "summary"      : 2-3 sentence overview of the vulnerability landscape.
  "risk"         : one of "low" | "medium" | "high" | "critical".
  "findings"     : list of objects for each significant CVE, each with:
                     "id"             : CVE identifier,
                     "severity"       : severity level,
                     "package"        : affected package and version,
                     "fixed_in"       : fixed version or null if none,
                     "recommendation" : concise action (upgrade, patch, monitor, etc.).
  "priorities"   : list of strings — ordered list of packages to address first (most critical).
  "no_fix_count" : integer — number of findings with no available fix.
  "recommendations" : list of strings — broader advice (upgrade strategies, monitoring, etc.).

Focus on actionable findings. Group by urgency. Do not fabricate CVE identifiers or versions."""

    @staticmethod
    def build_user_prompt(cve_data: dict[str, Any], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        scanner = cve_data.get("scanner", "unknown")
        scanner_version = cve_data.get("scanner_version", "")
        total = cve_data.get("total_findings", 0)
        critical = cve_data.get("critical", 0)
        high = cve_data.get("high", 0)
        medium = cve_data.get("medium", 0)
        low = cve_data.get("low", 0)

        findings = cve_data.get("findings", [])
        # Cap at 50 findings to stay within token budget; prioritise by severity
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
        sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.get("severity", "UNKNOWN"), 4))
        truncated = ""
        if len(sorted_findings) > 50:
            sorted_findings = sorted_findings[:50]
            truncated = f"\n[Showing 50 of {total} findings, sorted by severity]"

        finding_lines = []
        for f in sorted_findings:
            fixed = f.get("fixed_version") or f.get("fixed_in") or "no fix available"
            title = f.get("title", "")
            line = (
                f"  [{f.get('severity', '?')}] {f.get('vulnerability_id', '?')}: "
                f"{f.get('package_name', '?')}@{f.get('installed_version', '?')} "
                f"→ fix: {fixed}"
            )
            if title:
                line += f" ({title})"
            finding_lines.append(line)

        findings_text = "\n".join(finding_lines) if finding_lines else "  (no findings)"

        return (
            f"Deployment: {deployment}\n"
            f"Scanner: {scanner} {scanner_version}\n"
            f"Total: {total}  CRITICAL={critical}  HIGH={high}  MEDIUM={medium}  LOW={low}\n"
            f"{truncated}\n\nFindings:\n{findings_text}"
        )
