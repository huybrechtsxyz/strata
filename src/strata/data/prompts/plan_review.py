"""Built-in prompt for Terraform plan analysis (ADR-0025 §6)."""

from __future__ import annotations

import json
from typing import Any


class PlanReviewPrompt:
    """Terraform plan analysis — summary, risk score, and recommendations."""

    VERSION = "1.0"

    SYSTEM = """\
You are an infrastructure change reviewer for a deployment platform.
Analyse the Terraform plan provided and respond with a JSON object only — no prose outside the JSON.

Required fields:
  "summary"         : 2-3 sentence plain-language summary of the changes.
  "risk"            : one of "low" | "medium" | "high" | "critical".
  "creates"         : integer count of resources being created.
  "updates"         : integer count of resources being updated in-place.
  "replaces"        : integer count of resources being destroyed and recreated.
  "deletes"         : integer count of resources being destroyed.
  "concerns"        : list of strings — specific risks (destructive ops, security groups, IAM, etc.).
  "recommendations" : list of strings — concrete next steps for the operator.

Treat any resource destruction or replacement as elevated risk.
Never include secrets or credential values in your response."""

    @staticmethod
    def build_user_prompt(plan_json: dict[str, Any], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        environment = context.get("environment", "unknown")

        # Summarise plan stages for the prompt (avoid sending the full raw JSON
        # for very large plans — keep within a reasonable token budget).
        stages_summary = _summarise_plan_stages(plan_json)

        return (
            f"Deployment: {deployment}\n"
            f"Environment: {environment}\n\n"
            f"Terraform plan output:\n"
            f"```json\n{stages_summary}\n```"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise_plan_stages(plan_data: dict[str, Any]) -> str:
    """Return a condensed JSON representation of plan stage results."""
    if not isinstance(plan_data, (dict, list)):
        return json.dumps(plan_data, indent=2)

    stages = plan_data if isinstance(plan_data, list) else plan_data.get("stages", [plan_data])
    condensed = []
    for stage in stages:
        if not isinstance(stage, dict):
            condensed.append(stage)
            continue
        entry: dict[str, Any] = {
            "stage": stage.get("stage", "unknown"),
            "ok": stage.get("ok", False),
        }
        if stage.get("error"):
            entry["error"] = stage["error"]
        # Include messages but cap at 20 lines to stay within token budget
        messages = stage.get("messages", [])
        entry["messages"] = messages[:20]
        if len(messages) > 20:
            entry["messages_truncated"] = len(messages) - 20
        condensed.append(entry)

    return json.dumps(condensed, indent=2, default=str)
