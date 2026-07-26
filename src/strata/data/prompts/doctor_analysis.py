"""Built-in prompt for env doctor check failure analysis."""

from __future__ import annotations

import json
from typing import Any


class DoctorAnalysisPrompt:
    """Root-cause analysis and remediation for env doctor check failures."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps environment diagnostics assistant for a strata infrastructure workspace.
One or more environment health checks have failed. Analyse the failed checks and respond with a JSON object only.

Required fields:
  "summary"      : 1-2 sentence overview of what is wrong.
  "severity"     : one of "low" | "medium" | "high" | "critical".
  "root_cause"   : concise diagnosis of the underlying problem(s).
  "remediation"  : list of objects, each with:
                     "check"   : check name,
                     "steps"   : list of ordered, actionable fix steps.
  "references"   : list of strings — install docs or help links (may be empty).

Focus on actionable, numbered steps. Never fabricate tool versions."""

    @staticmethod
    def build_user_prompt(failed_checks: list[dict[str, Any]], context: dict[str, Any]) -> str:
        workspace = context.get("workspace", "unknown")
        platform = context.get("platform", "")

        lines = []
        for check in failed_checks:
            category = check.get("category", "unknown")
            name = check.get("name", "?")
            status = check.get("status", "fail")
            value = check.get("value") or ""
            hint = check.get("fix_hint") or ""
            line = f"  [{category}] {name}: {status}"
            if value:
                line += f" — {value}"
            if hint:
                line += f"\n    hint: {hint}"
            lines.append(line)

        checks_text = "\n".join(lines) if lines else "  (no details)"

        header = f"Workspace: {workspace}"
        if platform:
            header += f"\nPlatform: {platform}"

        return f"{header}\n\nFailed checks ({len(failed_checks)} total):\n{checks_text}"
