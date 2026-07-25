"""Built-in prompt for post-deployment summary generation."""

from __future__ import annotations

import json
from typing import Any


class DeploymentSummaryPrompt:
    """Human-readable summary of a completed deployment."""

    VERSION = "1.0"

    SYSTEM = """\
You are a deployment reporter. A deployment has just completed. Analyse the manifest
and execution history and respond with a JSON object only.

Required fields:
  "headline"        : one-sentence deployment summary (what, where, outcome).
  "outcome"         : one of "success" | "partial" | "failure".
  "stages_summary"  : list of objects, each with "stage", "outcome", "duration_s".
  "highlights"      : list of strings — notable changes, new resources, or anomalies.
  "anomalies"       : list of strings — anything unexpected vs. previous runs (may be empty).
  "next_steps"      : list of strings — follow-up actions or monitoring recommendations."""

    @staticmethod
    def build_user_prompt(manifest: dict[str, Any], history: list[Any]) -> str:
        manifest_text = json.dumps(manifest, indent=2, default=str)
        if len(manifest_text) > 6000:
            manifest_text = manifest_text[:6000] + "\n... [truncated]"

        # Include up to 3 most recent history entries for comparison
        recent = history[-3:] if history else []
        history_text = json.dumps(recent, indent=2, default=str)

        return (
            f"Deployment manifest:\n```json\n{manifest_text}\n```\n\n"
            f"Recent deployment history (last {len(recent)} runs):\n```json\n{history_text}\n```"
        )
