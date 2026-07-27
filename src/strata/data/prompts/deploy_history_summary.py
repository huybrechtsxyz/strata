"""Built-in prompt for deployment history trend analysis."""

from __future__ import annotations

from typing import Any


class DeployHistorySummaryPrompt:
    """Analyse deployment history for trends, recurring failures, and anomalies."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps deployment analyst. A deployment execution history is provided below.
Analyse the data for trends and patterns and respond with a JSON object only.

Required fields:
  "summary"         : 2-3 sentence overview of the deployment health trend.
  "success_rate"    : percentage of successful deployments (0-100).
  "trend"           : one of "improving" | "stable" | "degrading" | "insufficient_data".
  "recurring_issues": list of objects for recurring failure patterns, each with:
                        "pattern"  : description of what is failing repeatedly,
                        "count"    : number of occurrences,
                        "files"    : list of affected deployment files.
  "anomalies"       : list of strings — unusual patterns (e.g. sudden spike in failures).
  "recommendations" : list of strings — concrete next steps based on the trend.

Look for:
  - Consecutive failures (indicating a persistent problem)
  - Alternating success/fail (indicating flakiness)
  - Failure rate change over time (first half vs second half of history)
  - Same deployment file failing repeatedly
  - Operations that always fail vs succeed

Use "insufficient_data" for trend when fewer than 3 entries are present."""

    @staticmethod
    def build_user_prompt(history: list[dict[str, Any]], context: dict[str, Any]) -> str:
        workspace = context.get("workspace", "unknown")
        total = len(history)
        successes = sum(1 for e in history if e.get("success") is True)
        failures = sum(1 for e in history if e.get("success") is False)

        lines = []
        for e in history:
            status = "✅" if e.get("success") is True else "❌" if e.get("success") is False else "?"
            op = e.get("operation", "?")
            when = e.get("when", "?")
            file_ = e.get("file", "")
            stage = e.get("stage", "")
            line = f"  {status} {when}  {op}"
            if file_:
                line += f"  [{file_}]"
            if stage:
                line += f"  stage:{stage}"
            lines.append(line)

        return (
            f"Workspace: {workspace}\n"
            f"Total entries: {total}  Successes: {successes}  Failures: {failures}\n\n"
            f"History (newest first):\n" + "\n".join(lines)
        )
