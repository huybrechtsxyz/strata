"""Built-in prompt for deployment history / audit-changes summarisation."""

from __future__ import annotations

from typing import Any


class AuditHistorySummaryPrompt:
    """Summarise a window of deployment executions and surface trends/anomalies."""

    VERSION = "1.0"

    SYSTEM = """\
You are a deployment operations analyst for a strata infrastructure workspace.
You have been given a set of recent deployment execution records along with
pre-computed statistics. Identify trends, anomalies, and recurring failures.
Respond with a JSON object only.

Required fields:
  "summary"        : 2-3 sentence plain-language narrative of the deployment history window.
                     Include success rate, average duration, and overall health.
  "health"         : overall health — one of "healthy" | "degraded" | "failing" | "mixed".
  "anomalies"      : list of strings describing notable anomalies
                     (e.g. "duration spike in run 3 (+80%)", "stage networking failed 3/5 times").
                     Empty list if none.
  "failing_stages" : list of stage names that have recurring failures across multiple runs.
                     Empty list if none.
  "trends"         : 1-2 sentence description of trends over the window
                     (e.g. "Success rate declining: 5/5 → 3/5 over last 10 runs").
                     Use "Stable." if no significant trend.
  "recommendations": list of strings — actionable suggestions based on patterns found.
                     Empty list if everything looks healthy.

Rules:
- Reference specific deployment names, stage names, and timestamps when flagging issues.
- Distinguish a single isolated failure from a recurring pattern.
- If all runs succeeded with stable duration, say so briefly — do not fabricate concerns.
- Never fabricate execution data not present in the input."""

    @staticmethod
    def build_user_prompt(
        entries: list[dict[str, Any]],
        stats: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        workspace = context.get("workspace", "unknown")
        filters = context.get("filters", {})

        lines: list[str] = [f"Workspace: {workspace}"]

        if filters.get("stage"):
            lines.append(f"Stage filter: {filters['stage']}")
        if filters.get("since"):
            lines.append(f"Since: {filters['since']}")

        lines.append("")
        lines.append(f"Pre-computed statistics ({stats['total']} entries):")
        lines.append(
            f"  Succeeded: {stats['succeeded']}  Failed: {stats['failed']}  Success rate: {stats['success_rate']}"
        )
        lines.append(
            f"  Duration — avg: {stats['avg_duration_s']:.1f}s"
            f"  min: {stats['min_duration_s']:.1f}s"
            f"  max: {stats['max_duration_s']:.1f}s"
        )
        lines.append("")
        lines.append("Deployment entries (newest first):")

        for entry in entries:
            ts = entry.get("timestamp", "?")[:19]  # strip microseconds
            deployment = entry.get("deployment", "?")
            success = "✓" if entry.get("success") else "✗"
            duration = entry.get("duration_seconds", 0)
            errors = entry.get("errors") or []
            stages = entry.get("stages") or []

            stage_summary = ", ".join(
                f"{s.get('name', '?')}:{'✓' if s.get('success') else '✗'}({s.get('duration_seconds', 0):.0f}s)"
                for s in stages
            )
            line = f"  [{ts}] {success} {deployment} ({duration:.1f}s)"
            if stage_summary:
                line += f"  stages=[{stage_summary}]"
            if errors:
                line += f"  errors={errors[:2]}"  # cap at 2 to control tokens
            lines.append(line)

        return "\n".join(lines)
