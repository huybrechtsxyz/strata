#!/usr/bin/env python3
"""Built-in policy: external script runner.

Evaluates at any phase.  Runs an external script (shell, OPA, Checkov, etc.)
and passes a serialised context as JSON on stdin.  The script is considered
passing when it exits with code 0; any non-zero exit code is treated as a
policy violation whose details come from the script's stdout/stderr.

Graceful degradation
--------------------
- ``command`` missing from policy configuration → pass (skip)
- Script not found (FileNotFoundError) → violation
- Script times out → violation
- Any other exception → violation
"""

import json
import shlex
import subprocess
from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_DEFAULT_TIMEOUT = 30


class ScriptPolicy(BasePolicy):
    """Run an external script as a policy check."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        command: str = configuration.get("command", "")
        if not command:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no command configured"},
            )

        timeout_sec: int = int(configuration.get("timeout", _DEFAULT_TIMEOUT))
        context_data: Dict[str, Any] = {
            "phase": context.phase,
            "work_path": str(context.work_path) if context.work_path else None,
        }

        cmd_parts = shlex.split(command)
        cwd_str = str(context.work_path) if context.work_path else None

        violations: List[str] = []
        try:
            result = subprocess.run(
                cmd_parts,
                input=json.dumps(context_data),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=cwd_str,
            )
            if result.returncode == 0:
                return PolicyResult(
                    passed=True,
                    policy_name=self.name,
                    enforcement=self.enforcement,
                )

            # Collect non-empty lines from stdout and stderr as individual violations
            output_lines = [
                line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()
            ]
            if output_lines:
                violations.extend(output_lines)
            else:
                violations.append(f"Script exited with code {result.returncode}")

        except subprocess.TimeoutExpired:
            violations.append(f"Script timed out after {timeout_sec}s")
        except FileNotFoundError:
            violations.append(f"Script not found: {command}")
        except Exception as e:
            violations.append(f"Script execution failed: {e}")

        return PolicyResult(
            passed=False,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
