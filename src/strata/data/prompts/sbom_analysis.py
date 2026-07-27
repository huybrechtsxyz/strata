"""Built-in prompt for SBOM supply-chain risk analysis."""

from __future__ import annotations

import json
from typing import Any


class SbomAnalysisPrompt:
    """Supply-chain risk analysis from CycloneDX SBOM data."""

    VERSION = "1.0"

    SYSTEM = """\
You are a supply-chain security analyst. Analyse the provided SBOM component inventory
and respond with a JSON object only.

Required fields:
  "summary"         : 2-3 sentence overview of the component landscape.
  "risk"            : one of "low" | "medium" | "high" | "critical".
  "total_components": integer total component count.
  "concerns"        : list of objects, each with:
                        "component" : name@version,
                        "reason"    : why it is a concern (CVE id, deprecated, license, etc.).
  "recommendations" : list of strings — concrete next steps.
  "license_issues"  : list of strings — components with problematic licences (copyleft, unknown).

Focus on actionable findings. Do not fabricate CVE identifiers."""

    @staticmethod
    def build_user_prompt(sbom_json: dict[str, Any], policies: list[Any]) -> str:
        # Extract component list from CycloneDX format
        components = sbom_json.get("components", [])
        total = len(components)

        # Build a condensed list: name@version + licenses
        condensed = []
        for c in components[:200]:  # cap at 200 to stay within context window
            entry = f"{c.get('name', '?')}@{c.get('version', '?')}"
            licenses = [lic.get("id", "") for lic in c.get("licenses", []) if lic.get("id")]
            if licenses:
                entry += f" [{', '.join(licenses)}]"
            condensed.append(entry)

        truncation_note = f"\n[Truncated — showing 200 of {total} components]" if total > 200 else ""
        policy_text = json.dumps(policies, indent=2) if policies else "none"

        return (
            f"Total components: {total}{truncation_note}\n\n"
            f"Components:\n" + "\n".join(f"  - {c}" for c in condensed) + "\n\n"
            f"Configured policies:\n```json\n{policy_text}\n```"
        )
