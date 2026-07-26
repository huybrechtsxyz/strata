"""Built-in prompt for guide readiness phase assistance."""

from __future__ import annotations

import json
from typing import Any


class GuideAssistancePrompt:
    """Explains what is blocking a readiness phase and suggests the next action."""

    VERSION = "1.0"

    SYSTEM = """\
You are an onboarding assistant for a strata infrastructure workspace.
A workspace readiness phase is blocked. Analyse the blocking items and respond with a JSON object only.

Required fields:
  "summary"     : 1-2 sentence plain-language explanation of what is blocking the phase.
  "root_cause"  : the primary reason the phase cannot complete.
  "next_action" : the single most important command or step the operator should run now.
  "steps"       : list of strings — ordered, concrete steps to unblock the phase.
  "hint"        : optional brief tip (may be empty string).

Be concise and specific. Provide actual strata CLI commands where applicable."""

    @staticmethod
    def build_user_prompt(
        phase: int,
        phase_label: str,
        blocking_items: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str:
        workspace = context.get("workspace", "unknown")

        items_lines = []
        for item in blocking_items:
            status = item.get("status", "?")
            label = item.get("label", "?")
            detail = item.get("detail") or ""
            line = f"  [{status}] {label}"
            if detail:
                line += f": {detail}"
            items_lines.append(line)

        items_text = "\n".join(items_lines) if items_lines else "  (no details)"

        return (
            f"Workspace: {workspace}\n"
            f"Phase {phase}: {phase_label}\n\n"
            f"Blocking items:\n{items_text}"
        )
