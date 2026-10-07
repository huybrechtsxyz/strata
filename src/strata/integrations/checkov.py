#!/usr/bin/env python3
"""Checkov integration (docs/work/checkov-integration.md) — wraps the real
Checkov CLI to scan rendered IaC output (Terraform/Bicep/Ansible/Helm) for
security misconfigurations. Framework-agnostic itself — resolving *which*
directory to scan (a provisioner's materialised source, or a Helm module's
namespace directory) is `strata.controllers.checkov_controller`'s job, not
this class's.

Deliberately its own `scan()` signature, not `CveScannerIntegration.
scan_sbom()`'s shape — `ScannerIntegration`'s own docstring already states
why: an IaC directory in, a completely different findings model out, is a
genuinely different input/output shape from an SBOM file in.

Never has an `IntegrationModel` document of its own — like
`CveScannerIntegration`, always built-in (`shutil.which("checkov")` is the
only resolution strata needs), even though it declares `Capability.SCANNER`.
The `"checkov"` string in `registry.py`'s `_KNOWN_V1_TYPES` is unrelated —
that list only improves the error message for someone who tries to declare
an `Integration` document of `type: checkov`, which this class is never
constructed from.
"""

import json
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import ScannerIntegration
from strata.integrations.errors import IntegrationError
from strata.models.checkov_model import CheckovFindingModel, CheckovScanResultModel
from strata.models.integration_model import Capability


class CheckovIntegration(ScannerIntegration):
    """Detects and runs the real `checkov` CLI, scanning a rendered IaC
    directory for security misconfigurations."""

    TYPE = "checkov"
    CAPABILITIES = frozenset({Capability.SCANNER})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "checkov"

    def ensure_available(self) -> None:
        """Raise when `checkov` is not on `PATH`.

        Raises:
            IntegrationError: `checkov` is not installed.
        """
        if not self.is_available():
            raise IntegrationError(
                "checkov not found on PATH — install it (https://www.checkov.io/) to enable "
                "static IaC security scanning."
            )

    def scan(
        self,
        directory: Path,
        *,
        framework: str = "terraform",
        skip_checks: list[str] | None = None,
        include_checks: list[str] | None = None,
        custom_checks_dir: str | None = None,
        timeout: int = 120,
    ) -> CheckovScanResultModel:
        """Scan `directory` (an already-materialised/rendered IaC output
        directory) with Checkov.

        Args:
            directory: The provisioner source directory to scan.
            framework: Checkov `--framework` value (terraform | bicep | ansible | helm).
            skip_checks: Check IDs to suppress (`--skip-check`). Ignored when
                `include_checks` is also set — Checkov's own CLI treats
                `--check`/`--skip-check` as mutually exclusive.
            include_checks: When set, run ONLY these check IDs (`--check`).
            custom_checks_dir: Extra custom-checks directory (`--external-checks-dir`).
            timeout: Subprocess timeout in seconds.

        Raises:
            IntegrationError: `checkov` is not available, the scan process
                itself failed, or it produced unparseable output.
        """
        self.ensure_available()
        args = [
            "--directory",
            str(directory),
            "--output",
            "json",
            "--framework",
            framework,
            "--compact",
            # Never let the subprocess's own exit code drive control flow —
            # strata applies the real severity gate downstream, matching
            # CveScannerIntegration's own Trivy `--exit-code 0` precedent.
            # Without this, checkov's default exit code is 1 whenever it
            # finds any failed check at all, indistinguishable from a
            # genuine scan error.
            "--soft-fail",
        ]
        if include_checks:
            args += ["--check", ",".join(include_checks)]
        elif skip_checks:
            args += ["--skip-check", ",".join(skip_checks)]
        if custom_checks_dir:
            args += ["--external-checks-dir", custom_checks_dir]

        result = self.run(*args, timeout=timeout)
        if not result.is_successful:
            raise IntegrationError(f"checkov scan of '{directory}' failed: {result.stderr.strip()}")
        return self._safe_parse(directory, framework, result.payload)

    def _safe_parse(self, directory: Path, framework: str, raw: str) -> CheckovScanResultModel:
        """Turn a malformed/unexpected-shape response into a clean
        `IntegrationError` instead of letting `json.JSONDecodeError`/
        `KeyError` escape `scan()` uncaught — matches
        `CveScannerIntegration._safe_parse()`'s own established fix for
        this exact failure class.
        """
        try:
            data = json.loads(raw)
            return self._parse(data, directory, framework)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise IntegrationError(f"checkov scan of '{directory}' produced unparseable output: {exc}") from exc

    def _parse(self, data: dict[str, Any], directory: Path, framework: str) -> CheckovScanResultModel:
        summary = data.get("summary") or {}
        findings: list[CheckovFindingModel] = []
        results = data.get("results") or {}
        for check in results.get("failed_checks") or []:
            findings.append(
                CheckovFindingModel(
                    check_id=check["check_id"],
                    check_name=check.get("check_name", ""),
                    resource=check.get("resource", ""),
                    file_path=check.get("file_path", ""),
                    file_line_range=check.get("file_line_range"),
                    severity=(check.get("severity") or "UNKNOWN").upper(),
                    guideline=check.get("guideline"),
                )
            )
        return CheckovScanResultModel(
            passed=summary.get("passed", 0),
            failed=summary.get("failed", 0),
            skipped=summary.get("skipped", 0),
            findings=findings,
            scanner_version=self.get_version() or "unknown",
            framework=framework,
            scanned_path=str(directory),
        )
