"""Built-in prompt for deploy health probe failure analysis."""

from __future__ import annotations

from typing import Any


class HealthAnalysisPrompt:
    """Explain why health probes failed and suggest remediation."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps service health analyst. One or more health probes have failed for a deployed infrastructure stage.
Analyse the failed checks and respond with a JSON object only.

Required fields:
  "summary"      : 1-2 sentence overview of what is unhealthy and the likely impact.
  "root_cause"   : most probable reason the probes failed.
  "checks"       : list of objects for each failed check, each with:
                     "name"           : check name,
                     "type"           : "http" or "tcp",
                     "target"         : URL or host:port that was checked,
                     "likely_cause"   : specific diagnosis for this check,
                     "remediation"    : ordered list of steps to fix it.
  "recommendations" : list of strings — broader advice (firewall rules, service restart, config fixes).

Common causes to consider: firewall blocking, service not started, wrong port, certificate error,
misconfigured Terraform output, health endpoint not yet ready after deploy.
Never fabricate IP addresses or specific configuration values."""

    @staticmethod
    def build_user_prompt(health_data: dict[str, Any], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")

        failed_stages = []
        stages = health_data.get("stages", {})
        for stage_name, stage_result in stages.items():
            if stage_result.get("passed"):
                continue
            checks = stage_result.get("checks", [])
            failed_checks = [c for c in checks if not c.get("passed")]
            if not failed_checks:
                continue

            check_lines = []
            for c in failed_checks:
                ctype = c.get("type", "?")
                name = c.get("name", "?")
                target = c.get("url") or f"{c.get('host', '?')}:{c.get('port', '?')}"
                error = c.get("error") or ""
                status = c.get("status_code") or c.get("error") or "no response"
                line = f"  [{ctype}] {name}: {target} → {status}"
                if error and error != status:
                    line += f" ({error})"
                check_lines.append(line)

            failed_stages.append(f"Stage: {stage_name}\n" + "\n".join(check_lines))

        summary = health_data.get("summary", {})
        total = summary.get("total_stages", 0) if isinstance(summary, dict) else 0
        passed = summary.get("passed", 0) if isinstance(summary, dict) else 0

        return (
            f"Deployment: {deployment}\n"
            f"Health summary: {passed}/{total} stages passed\n\n"
            f"Failed probes:\n" + "\n\n".join(failed_stages)
            if failed_stages
            else f"Deployment: {deployment}\n(no failed probes details available)"
        )
