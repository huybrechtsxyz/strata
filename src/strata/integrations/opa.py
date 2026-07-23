"""OPA (Open Policy Agent) integration.

Evaluates Rego policies against strata's deployment context using two modes:

1. **HTTP mode** — if an OPA server is reachable at ``endpoint`` (from policy
   ``configuration.endpoint`` or the ``OPA_ENDPOINT`` environment variable),
   strata POSTs the input document to ``POST /v1/data/{rule}``.

2. **CLI fallback** — if no server is reachable but the ``opa`` binary is in
   PATH, strata runs ``opa eval`` as a stateless subprocess with the input
   document on stdin.

HTTP mode is faster (no process spawn per evaluation) and enables state sharing
when the same OPA server is used across multiple policy evaluations.  CLI mode
requires no server and works in any environment where ``opa`` is installed.

Install OPA: https://www.openpolicyagent.org/docs/latest/#1-download-opa

Configuration YAML example::

    policies:
      - name: zone_enforcement
        type: opa
        phase: build
        enforcement: deny
        configuration:
          rule: "data.strata.zones.deny"
          policy_dir: ".strata/policies/"   # directory with .rego files (CLI mode)
          endpoint: "localhost:8181"        # OPA server URL (HTTP mode, optional)
          timeout: 30
"""

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:8181"
_HTTP_TIMEOUT = 10  # seconds for health / connectivity check


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class OPAResult:
    """Result of an OPA policy evaluation."""

    passed: bool
    violations: List[str] = field(default_factory=list)
    raw: Any = None  # raw OPA response, for debugging


# ---------------------------------------------------------------------------
# Integration class
# ---------------------------------------------------------------------------


class OPAIntegration(BaseIntegration):
    """OPA policy evaluation integration.

    Supports two modes: HTTP REST API (preferred) and ``opa eval`` CLI fallback.
    """

    COMMAND = "opa"
    CAPABILITIES: list = []

    def get_version_command(self) -> List[str]:
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        m = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return m.group(1) if m else version_output.strip()

    def ensure_available(self) -> Tuple[bool, str]:
        if not self.is_available():
            return False, (
                "OPA CLI is not installed or not in PATH. "
                "Install: https://www.openpolicyagent.org/docs/latest/#1-download-opa"
            )
        self._info = f"opa {self.get_version()} is available"
        return True, ""

    def get_setup_info(self) -> Dict[str, Any]:
        return {
            "name": "opa",
            "command": "opa",
            "install_url": "https://www.openpolicyagent.org/docs/latest/#1-download-opa",
            "env_vars": [
                {
                    "name": "OPA_ENDPOINT",
                    "purpose": "OPA server URL (e.g. http://localhost:8181). Used when OPA runs as a server.",
                    "required": False,
                },
            ],
            "auth_methods": [],
            "yaml_example": (
                "- name: opa\n"
                "  type: opa\n"
                "  capabilities: [iac_security]\n"
                "  required: false\n"
                "  validation:\n"
                "    command: opa version\n"
                '    min_version: "0.50.0"'
            ),
        }

    # ------------------------------------------------------------------
    # Public evaluation entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        rule: str,
        input_data: Dict[str, Any],
        *,
        endpoint: Optional[str] = None,
        policy_dir: Optional[str] = None,
        timeout: int = 30,
    ) -> OPAResult:
        """Evaluate a Rego rule against input_data.

        Tries HTTP mode first (if endpoint reachable), falls back to CLI.

        Args:
            rule: OPA rule path, e.g. ``"data.strata.zones.deny"``.
            input_data: The OPA input document (serializable to JSON).
            endpoint: OPA server URL. Falls back to ``OPA_ENDPOINT`` env var
                      or ``http://localhost:8181``.
            policy_dir: Directory with ``.rego`` files (CLI mode only).
            timeout: Evaluation timeout in seconds.

        Returns:
            ``OPAResult`` with ``passed`` and ``violations``.
        """
        resolved_endpoint = endpoint or os.environ.get("OPA_ENDPOINT") or _DEFAULT_ENDPOINT

        # Try HTTP mode if endpoint is explicitly configured or env var is set
        http_configured = bool(endpoint or os.environ.get("OPA_ENDPOINT"))
        if http_configured:
            try:
                return self.evaluate_http(rule, resolved_endpoint, input_data, timeout=timeout)
            except (urllib.error.URLError, OSError) as exc:
                logger.debug(
                    "opa_http_failed_trying_cli",
                    endpoint=resolved_endpoint,
                    error=str(exc),
                )

        # CLI fallback
        available, reason = self.ensure_available()
        if not available:
            logger.debug("opa_unavailable", reason=reason)
            raise RuntimeError(reason)

        return self.evaluate_cli(rule, input_data, policy_dir=policy_dir, timeout=timeout)

    # ------------------------------------------------------------------
    # HTTP mode
    # ------------------------------------------------------------------

    def evaluate_http(
        self,
        rule: str,
        endpoint: str,
        input_data: Dict[str, Any],
        *,
        timeout: int = 30,
    ) -> OPAResult:
        """POST input to OPA REST API and return violations.

        Endpoint example: ``http://localhost:8181``
        Rule example: ``data.strata.zones.deny``

        OPA REST URL: ``POST {endpoint}/v1/data/{rule_path}``
        where ``rule_path`` replaces dots with slashes.
        """
        # Convert rule path: "data.strata.zones.deny" → "strata/zones/deny"
        # Strip leading "data." — OPA REST uses /v1/data/{package/rule}
        rule_path = rule
        if rule_path.startswith("data."):
            rule_path = rule_path[len("data.") :]
        rule_path = rule_path.replace(".", "/")

        url = endpoint.rstrip("/") + f"/v1/data/{rule_path}"
        body = json.dumps({"input": input_data}).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        return self._parse_http_result(raw)

    def _parse_http_result(self, raw: Any) -> OPAResult:
        """Parse OPA HTTP API response into OPAResult."""
        result = raw.get("result")

        if result is None:
            # Rule not defined — treat as no violations
            return OPAResult(passed=True, violations=[], raw=raw)

        violations = self._extract_violations(result)
        return OPAResult(passed=len(violations) == 0, violations=violations, raw=raw)

    # ------------------------------------------------------------------
    # CLI mode
    # ------------------------------------------------------------------

    def evaluate_cli(
        self,
        rule: str,
        input_data: Dict[str, Any],
        *,
        policy_dir: Optional[str] = None,
        timeout: int = 30,
    ) -> OPAResult:
        """Run ``opa eval`` with input on stdin and return violations.

        Args:
            rule: OPA rule expression, e.g. ``"data.strata.zones.deny"``.
            input_data: Input document sent on stdin.
            policy_dir: Directory with ``.rego`` files.
            timeout: Subprocess timeout.
        """
        import subprocess

        cmd = [self.command, "eval", "--format", "json", "--stdin-input"]
        if policy_dir:
            cmd += ["--data", policy_dir]
        cmd.append(rule)

        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(input_data).encode("utf-8"),
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"OPA eval timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError("OPA binary not found in PATH")

        if proc.returncode not in (0, 1) and not proc.stdout:
            raise RuntimeError(f"OPA eval failed (exit {proc.returncode}): {proc.stderr.decode()[:300]}")

        return self._parse_cli_result(proc.stdout.decode("utf-8", errors="replace"))

    def _parse_cli_result(self, raw: str) -> OPAResult:
        """Parse ``opa eval --format json`` output into OPAResult."""
        if not raw or not raw.strip():
            return OPAResult(passed=True, violations=[], raw=None)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("opa_cli_parse_error", raw=raw[:200])
            return OPAResult(passed=True, violations=[], raw=raw)

        # opa eval output: {"result": [{"expressions": [{"value": <result>, ...}]}]}
        try:
            result_value = data["result"][0]["expressions"][0]["value"]
        except (KeyError, IndexError, TypeError):
            return OPAResult(passed=True, violations=[], raw=data)

        violations = self._extract_violations(result_value)
        return OPAResult(passed=len(violations) == 0, violations=violations, raw=data)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _extract_violations(self, result: Any) -> List[str]:
        """Extract violation strings from an OPA rule result.

        Handles three common patterns:
        - ``False`` / ``None`` → no violations (rule passed)
        - ``True`` → single violation (rule failed, no message)
        - ``["msg1", "msg2", ...]`` → list of violation messages
        - ``[{"msg": "...", ...}, ...]`` → list of dicts with msg field
        """
        if result is None or result is False:
            return []

        if result is True:
            return ["Policy violation (no message provided)"]

        if isinstance(result, list):
            violations: List[str] = []
            for item in result:
                if isinstance(item, str):
                    violations.append(item)
                elif isinstance(item, dict):
                    msg = item.get("msg") or item.get("message") or str(item)
                    violations.append(msg)
                else:
                    violations.append(str(item))
            return violations

        if isinstance(result, dict):
            # Single violation as dict
            msg = result.get("msg") or result.get("message") or str(result)
            return [msg]

        # Unexpected type — treat as violation if truthy
        return [str(result)] if result else []
