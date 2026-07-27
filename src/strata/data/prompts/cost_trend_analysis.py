"""Built-in prompt for cost history trend analysis."""

from __future__ import annotations

from typing import Any


class CostTrendAnalysisPrompt:
    """Analyse cost history snapshots for trends, spikes, and anomalies."""

    VERSION = "1.0"

    SYSTEM = """\
You are a cloud cost analyst for a strata infrastructure deployment.
You have been given a series of cost snapshots along with pre-computed statistics.
Identify trends, cost spikes, and provide actionable cost-reduction recommendations.
Respond with a JSON object only.

Required fields:
  "summary"       : 2-3 sentence narrative covering overall cost movement,
                    trend direction, and the biggest cost driver in the window.
  "trend"         : overall direction — one of "stable" | "rising" | "falling" | "volatile".
  "total_change"  : object with:
                      "from_cost"     : earliest snapshot total (number),
                      "to_cost"       : latest snapshot total (number),
                      "delta"         : absolute change (number, positive=increase),
                      "delta_pct"     : percentage change (number),
                      "currency"      : currency code string.
  "spikes"        : list of spike objects (empty if none), each with:
                      "recorded_at"         : snapshot timestamp,
                      "version"             : version at spike time (may be empty),
                      "delta"               : cost increase at this step,
                      "delta_pct"           : percentage increase at this step,
                      "contributing_provisioners": list of provisioner names with large increases,
                      "likely_cause"        : plain-language hypothesis for the spike.
  "recommendations": list of strings — actionable cost-reduction or investigation steps.
                     Empty list if costs are stable and healthy.

Rules:
- A spike is a single-step delta ≥ 10% of the previous snapshot total.
- For the likely_cause, consider: new resources added, VM size changes, region changes,
  version bumps introducing new provisioners. Be specific about what you observe.
- If only one snapshot exists, note that trend analysis requires at least two snapshots.
- Never fabricate cost figures not present in the input."""

    @staticmethod
    def build_user_prompt(
        snapshots: list[dict[str, Any]],
        stats: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        deployment = context.get("deployment", "unknown")
        currency = stats.get("currency", "USD")

        lines: list[str] = [
            f"Deployment: {deployment}",
            f"Currency: {currency}",
            "",
            f"Pre-computed statistics ({stats['total']} snapshots):",
            f"  Earliest cost : {stats['earliest_cost']:.2f}",
            f"  Latest cost   : {stats['latest_cost']:.2f}",
            f"  Min cost      : {stats['min_cost']:.2f}",
            f"  Max cost      : {stats['max_cost']:.2f}",
            f"  Avg cost      : {stats['avg_cost']:.2f}",
            f"  Net change    : {stats['net_delta']:+.2f} ({stats['net_delta_pct']:+.1f}%)",
            f"  Largest spike : {stats['max_spike_delta']:+.2f} at {stats['max_spike_at']}",
            "",
            "Snapshots (oldest → newest):",
        ]

        for snap in reversed(snapshots):  # list_snapshots returns newest-first; reverse for chronological
            ts = snap.get("recorded_at", "?")[:19]
            version = snap.get("version", "—")
            total = snap.get("total_monthly")
            delta = snap.get("delta_from_previous")
            provisioners = snap.get("provisioners") or {}

            total_str = f"{total:.2f}" if total is not None else "—"
            delta_str = f"{delta:+.2f}" if delta is not None else "first"

            prov_parts = [
                f"{name}:{data.get('total_monthly', 0):.2f}"
                for name, data in provisioners.items()
                if isinstance(data, dict)
            ]
            prov_str = f"  provisioners=[{', '.join(prov_parts)}]" if prov_parts else ""

            lines.append(f"  [{ts}] v{version}  total={total_str}  delta={delta_str}{prov_str}")

        return "\n".join(lines)
