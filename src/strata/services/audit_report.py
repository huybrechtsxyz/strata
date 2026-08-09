"""Audit report writers — CycloneDX VEX 1.6 and SARIF 2.1.0.

Pure functions that convert a ``CveAuditResultModel`` into standard
file formats.  No external dependencies beyond ``json`` and ``strata.models``.

Lives in ``services/`` (not ``utils/``) because it operates directly on domain
models (``CveAuditResultModel``) — per ADR-0003, ``utils/`` must not depend on
``models/``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from strata.models.sbom_model import CveAuditResultModel

# ---------------------------------------------------------------------------
# Severity → SARIF level mapping
# ---------------------------------------------------------------------------

_SARIF_LEVEL = {
    "CRITICAL": "error",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
    "UNKNOWN": "note",
}

# ---------------------------------------------------------------------------
# CycloneDX VEX 1.6
# ---------------------------------------------------------------------------


def write_vex(
    result: CveAuditResultModel,
    output_path: Path,
    sbom_path: Optional[Path] = None,
    strata_version: str = "0.0.0",
) -> Path:
    """Write a CycloneDX VEX 1.6 JSON document.

    Args:
        result: Filtered audit findings.
        output_path: Directory to write ``vex.json`` into.
        sbom_path: Optional path to the related SBOM (for metadata).
        strata_version: strata CLI version string.

    Returns:
        Path to the written file.
    """
    now = datetime.now(timezone.utc).isoformat()

    tools = [{"name": "strata", "version": strata_version}]
    if result.scanner:
        tools.append({"name": result.scanner, "version": result.scanner_version})

    vulnerabilities = []
    for f in result.findings:
        vuln: dict = {
            "id": f.vulnerability_id,
            "source": {"name": result.scanner},
            "ratings": [{"severity": f.severity.lower()}],
        }
        if f.title:
            vuln["description"] = f.title

        affects = []
        affect_entry: dict = {}
        if f.purl:
            affect_entry["ref"] = f.purl
        if f.installed_version:
            affect_entry["versions"] = [{"version": f.installed_version, "status": "affected"}]
        if affect_entry:
            affects.append(affect_entry)
        if affects:
            vuln["affects"] = affects

        if f.fixed_version:
            vuln["recommendation"] = f"Upgrade to {f.fixed_version}"

        vulnerabilities.append(vuln)

    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": tools,
        },
        "vulnerabilities": vulnerabilities,
    }

    file_path = output_path / "vex.json"
    output_path.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# SARIF 2.1.0
# ---------------------------------------------------------------------------


def write_sarif(
    result: CveAuditResultModel,
    output_path: Path,
    sbom_path: Optional[Path] = None,
    strata_version: str = "0.0.0",
) -> Path:
    """Write a SARIF 2.1.0 JSON document.

    Args:
        result: Filtered audit findings.
        output_path: Directory to write ``audit.sarif`` into.
        sbom_path: Optional path to the SBOM artifact (used in location URIs).
        strata_version: strata CLI version string.

    Returns:
        Path to the written file.
    """
    sbom_uri = sbom_path.name if sbom_path else "sbom.json"

    # Build unique rules from findings
    rules_map: dict[str, dict] = {}
    for f in result.findings:
        if f.vulnerability_id not in rules_map:
            rule: dict = {
                "id": f.vulnerability_id,
                "shortDescription": {"text": f.title or f.vulnerability_id},
                "defaultConfiguration": {"level": _SARIF_LEVEL.get(f.severity, "note")},
            }
            rules_map[f.vulnerability_id] = rule

    # Build results
    sarif_results = []
    for f in result.findings:
        msg_parts = [f"{f.package_name}@{f.installed_version}"]
        if f.fixed_version:
            msg_parts.append(f"upgrade to {f.fixed_version}")
        if f.title:
            msg_parts.append(f.title)

        sarif_results.append(
            {
                "ruleId": f.vulnerability_id,
                "level": _SARIF_LEVEL.get(f.severity, "note"),
                "message": {"text": " — ".join(msg_parts)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": sbom_uri},
                        }
                    }
                ],
            }
        )

    doc = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "strata-cve-audit",
                        "version": strata_version,
                        "informationUri": "https://github.com/huybrechtsxyz/strata",
                        "rules": list(rules_map.values()),
                    }
                },
                "results": sarif_results,
            }
        ],
    }

    file_path = output_path / "audit.sarif"
    output_path.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return file_path
