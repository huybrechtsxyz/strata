"""Built-in prompt for execution log error summarisation."""

from __future__ import annotations

from typing import Any


class ExecutionLogSummaryPrompt:
    """Summarise errors and warnings from execution log entries."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps log analyst for a strata infrastructure workspace.
You have been given a batch of execution log entries, filtered to include errors, warnings,
and surrounding context. Identify patterns, root causes, and actionable next steps.
Respond with a JSON object only.

Required fields:
  "summary"       : 2-3 sentence plain-language overview of what happened.
  "severity"      : overall severity — one of "low" | "medium" | "high" | "critical".
  "error_groups"  : list of objects grouping related errors, each with:
                      "title"       : short label for this error group,
                      "count"       : number of related entries,
                      "description" : what this group means,
                      "likely_cause": probable root cause,
                      "suggestion"  : concrete next step to investigate or fix.
  "noise"         : brief description of any repeated/expected noise to ignore (may be empty string).
  "next_steps"    : list of strings — prioritised, actionable investigation or fix steps.

Rules:
- Group repeated/similar errors together rather than listing each one.
- Distinguish transient errors (timeouts, retries) from persistent failures.
- If logs look clean (no meaningful errors), say so in "summary" and return empty error_groups.
- Never fabricate errors that are not present in the input."""

    @staticmethod
    def build_user_prompt(
        log_entries: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str:
        workspace = context.get("workspace", "unknown")
        filters = context.get("filters", {})
        total = context.get("total_entries", len(log_entries))

        lines: list[str] = [f"Workspace: {workspace}"]

        if filters:
            filter_parts = []
            if filters.get("level"):
                filter_parts.append(f"level≥{filters['level']}")
            if filters.get("minutes"):
                filter_parts.append(f"last {filters['minutes']}min")
            if filters.get("execution_id"):
                eid = str(filters["execution_id"])[:12]
                filter_parts.append(f"execution={eid}…")
            if filter_parts:
                lines.append(f"Filters: {', '.join(filter_parts)}")

        lines.append(f"Total entries shown: {total}")
        lines.append("")
        lines.append("Log entries:")

        for entry in log_entries:
            ts = entry.get("timestamp", "")
            lvl = (entry.get("level") or entry.get("levelname") or "INFO").upper()
            msg = entry.get("event", entry.get("message", ""))

            # Include key structured fields but strip noise
            extras: list[str] = []
            skip_keys = {"timestamp", "level", "levelname", "event", "message", "logger", "pid", "thread"}
            for k, v in entry.items():
                if k not in skip_keys and v is not None and str(v).strip():
                    extras.append(f"{k}={v}")

            line = f"  [{ts}] {lvl:<8} {msg}"
            if extras:
                line += f"  ({', '.join(extras[:4])})"  # cap at 4 extra fields to limit tokens
            lines.append(line)

        return "\n".join(lines)
