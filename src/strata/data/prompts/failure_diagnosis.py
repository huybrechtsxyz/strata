"""Built-in prompt for deployer step failure diagnosis."""

from __future__ import annotations

from typing import Any


class FailureDiagnosisPrompt:
    """Root-cause analysis and remediation for deployer failures."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps troubleshooting assistant for an infrastructure deployment platform.
A deployer step has failed. Analyse the error output and respond with a JSON object only.

Required fields:
  "root_cause"      : concise 1-2 sentence diagnosis of the underlying problem.
  "category"        : one of "auth" | "network" | "config" | "state" | "resource" | "dependency" | "unknown".
  "remediation"     : list of strings — ordered, actionable steps to fix the problem.
  "references"      : list of strings — relevant docs or error code links (may be empty).

Focus on actionable remediation. Never guess credential values."""

    @staticmethod
    def build_user_prompt(error_output: str, step: str, context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        stage = context.get("stage", "unknown")
        provisioner = context.get("provisioner", "unknown")

        # Cap error output to ~100 lines to stay within token budget
        all_lines = error_output.splitlines()
        total = len(all_lines)

        if not all_lines:
            output_block = "*(empty output)*"
        elif total > 100:
            visible = all_lines[-100:]
            output_block = (
                f"[Output truncated — showing last 100 of {total} lines]\n```\n" + "\n".join(visible) + "\n```"
            )
        else:
            output_block = "```\n" + "\n".join(all_lines) + "\n```"

        return (
            f"Deployment: {deployment}\n"
            f"Stage: {stage}\n"
            f"Provisioner: {provisioner}\n"
            f"Failed step: {step}\n\n"
            f"Error output:\n{output_block}"
        )
