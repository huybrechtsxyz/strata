"""Built-in prompt for promotion status analysis."""

from __future__ import annotations

from typing import Any


class PromotionStatusPrompt:
    """Explain in-flight promotions and recommend next actions."""

    VERSION = "1.0"

    SYSTEM = """\
You are a release management assistant for a strata infrastructure platform.
In-flight version promotions are reported below. Analyse the promotion state and respond with a JSON object only.

Required fields:
  "summary"         : 1-2 sentence overview of what is currently in flight.
  "attention"       : list of strings — promotions that need operator action (stuck, overdue, conflicting).
  "promotions"      : list of objects for each promotion, each with:
                        "target"         : what is being promoted,
                        "ring"           : destination ring,
                        "status"         : current status,
                        "assessment"     : 1-sentence state assessment,
                        "next_action"    : specific next step for this promotion.
  "recommendations" : list of strings — broader advice (sequence, timing, safety checks).

Consider: promotions stuck "in-progress" for a long time may need investigation.
Completed promotions near the end of the list can indicate drift.
Never fabricate version numbers or ring names not in the input."""

    @staticmethod
    def build_user_prompt(promotions: list[dict[str, Any]], context: dict[str, Any]) -> str:
        workspace = context.get("workspace", "unknown")

        if not promotions:
            return f"Workspace: {workspace}\n\nNo in-flight promotions found."

        lines = []
        for p in promotions:
            target = p.get("target", "?")
            version = p.get("version", "?")
            prev = p.get("previous_version") or "none"
            ring = p.get("ring", "?")
            strategy = p.get("strategy", "?")
            status = p.get("status", "?")
            branch = p.get("branch") or ""
            events = p.get("event_count", 0)

            line = f"  [{status}] {target}: {prev} → {version}  ring={ring}  strategy={strategy}  events={events}"
            if branch:
                line += f"  branch={branch}"
            lines.append(line)

        return f"Workspace: {workspace}\nIn-flight promotions ({len(promotions)}):\n" + "\n".join(lines)
