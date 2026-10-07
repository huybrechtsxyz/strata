#!/usr/bin/env python3
"""CVE scanner integration (docs/work/cve-scanner-integration.md) — scans
the SBOM `build run` just generated for known vulnerabilities, via either
of the two real, evidenced v1 backends (Trivy or Grype).

Both backends take an already-written SBOM file, not a container image or
filesystem path — matches v1's real `_scan_trivy()`/`_scan_grype()` CLI
invocations exactly:

    trivy sbom <path> --format json --severity <CSV> --exit-code 0
    grype sbom:<path> --output json

Trivy filters severities via its own `--severity` flag; Grype has none —
filtering happens here in Python instead, including the `NEGLIGIBLE` ->
`LOW` remap Trivy's own vocabulary never needs (v1 evidence, docs/work/
cve-scanner-integration.md).

Doesn't fit `Integration.command`'s normal one-executable-per-class
resolution (ADR-0021 D6): this integration picks between two entirely
different CLIs at runtime (detected once per instance), not declared on
the class — so `command` is overridden below rather than relying on
`COMMAND`/`config.spec.command`. It also never has an `IntegrationModel`
document of its own — it is always built-in, matching how the three store
resolvers work from `PATH`/environment alone — even though it now
declares `Capability.SCANNER` (`ScannerIntegration`,
`integrations/capabilities.py`): declaring a capability and being
document-configurable are independent; nothing today ever constructs this
class from an `IntegrationModel`.
"""

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from strata.integrations.capabilities import ScannerIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability
from strata.models.sbom_model import CVE_SEVERITY_ORDER, CveAuditResultModel, CveFindingModel
from strata.utils.transport import CommandResult

#: Grype reports this; Trivy's own vocabulary never produces it (v1 evidence).
_NEGLIGIBLE_TO_LOW = {"NEGLIGIBLE": "LOW"}


class CveScannerIntegration(ScannerIntegration):
    """Detects and runs whichever of Trivy/Grype is on `PATH` (Trivy
    preferred when both are present — matches v1's own backend preference
    order), scanning an already-written SBOM file for known
    vulnerabilities.
    """

    TYPE = "cve_scanner"
    CAPABILITIES = frozenset({Capability.SCANNER})
    TRANSPORTS = frozenset({"cli"})

    #: Preference order when both are on PATH.
    _BACKENDS = ("trivy", "grype")

    def __init__(self) -> None:
        super().__init__(config=None)
        self._backend = next((b for b in self._BACKENDS if shutil.which(b) is not None), None)

    @property
    def command(self) -> str | None:
        """The detected backend executable — `None` when neither is on `PATH`.

        Overrides `Integration.command` (normally resolved from `COMMAND`/
        `config.spec.command`): this integration picks between two
        entirely different CLIs at runtime instead of one fixed executable
        per class, and is never configured via an `IntegrationModel`
        document.
        """
        return self._backend

    def get_version(self) -> str | None:
        """Overrides the base implementation: `VERSION_ARGS` differs per
        backend (Trivy: `--version`; Grype: `version`), so it can't be a
        single `ClassVar` shared by both."""
        if self._backend is None:
            return None
        args = ("--version",) if self._backend == "trivy" else ("version",)
        result = self.run(*args, timeout=10)
        if not result.is_successful:
            return None
        return self.parse_version(result.payload)

    def parse_version(self, raw: str) -> str:
        match = re.search(r"(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    def ensure_available(self) -> None:
        """Raise when neither Trivy nor Grype is on `PATH`.

        Raises:
            IntegrationError: Neither backend is installed.
        """
        if self._backend is None:
            raise IntegrationError(
                "no CVE scanner found on PATH — install Trivy (https://trivy.dev) or "
                "Grype (https://github.com/anchore/grype) to enable CVE scanning."
            )

    def scan_sbom(self, sbom_path: Path, *, severity_threshold: str = "MEDIUM") -> CveAuditResultModel:
        """Scan `sbom_path` (a CycloneDX JSON file) with the detected backend.

        Args:
            sbom_path: Path to the already-written `sbom.json`.
            severity_threshold: Minimum severity to report — forwarded to
                Trivy's own `--severity` flag; applied here in Python for
                Grype, which has no equivalent flag (v1 evidence).

        Raises:
            IntegrationError: Neither backend is available, or the scan
                process itself failed (non-zero exit).
        """
        self.ensure_available()
        backend = self._backend
        assert backend is not None  # ensure_available() just confirmed this

        if backend == "trivy":
            result, findings = self._scan_trivy(sbom_path, severity_threshold)
        else:
            result, findings = self._scan_grype(sbom_path, severity_threshold)

        if not result.is_successful:
            raise IntegrationError(f"{backend} scan of '{sbom_path}' failed: {result.stderr.strip()}")

        return self._summarize(findings, sbom_path)

    def _scan_trivy(self, sbom_path: Path, severity_threshold: str) -> tuple[CommandResult, list[CveFindingModel]]:
        severities = CVE_SEVERITY_ORDER[: CVE_SEVERITY_ORDER.index(severity_threshold) + 1]
        result = self.run(
            "sbom", str(sbom_path), "--format", "json", "--severity", ",".join(severities), "--exit-code", "0"
        )
        if not result.is_successful:
            return result, []
        return result, self._safe_parse("trivy", sbom_path, self._parse_trivy, result.payload)

    def _scan_grype(self, sbom_path: Path, severity_threshold: str) -> tuple[CommandResult, list[CveFindingModel]]:
        result = self.run("sbom:" + str(sbom_path), "--output", "json")
        if not result.is_successful:
            return result, []
        findings = self._safe_parse("grype", sbom_path, self._parse_grype, result.payload)
        allowed = set(CVE_SEVERITY_ORDER[: CVE_SEVERITY_ORDER.index(severity_threshold) + 1])
        return result, [f for f in findings if f.severity in allowed]

    @staticmethod
    def _safe_parse(
        backend: str, sbom_path: Path, parser: Callable[[str], list[CveFindingModel]], raw: str
    ) -> list[CveFindingModel]:
        """Run `parser(raw)`, turning a malformed/unexpected-shape response
        into a clean `IntegrationError` instead of letting
        `json.JSONDecodeError`/`KeyError` escape `scan_sbom()` uncaught —
        `evaluate_cve_policy()` only catches `IntegrationError`, and a raw
        parser exception would crash `build run` entirely rather than
        degrading through `on_missing_data`, same as a real scan failure.
        """
        try:
            return parser(raw)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise IntegrationError(f"{backend} scan of '{sbom_path}' produced unparseable output: {exc}") from exc

    @staticmethod
    def _parse_trivy(raw: str) -> list[CveFindingModel]:
        data = json.loads(raw)
        findings: list[CveFindingModel] = []
        for scan_result in data.get("Results") or []:
            for vuln in scan_result.get("Vulnerabilities") or []:
                findings.append(
                    CveFindingModel(
                        vulnerability_id=vuln["VulnerabilityID"],
                        severity=vuln.get("Severity", "UNKNOWN"),
                        package_name=vuln.get("PkgName", ""),
                        installed_version=vuln.get("InstalledVersion", ""),
                        fixed_version=vuln.get("FixedVersion"),
                        title=vuln.get("Title"),
                        purl=(vuln.get("PkgIdentifier") or {}).get("PURL"),
                    )
                )
        return findings

    @staticmethod
    def _parse_grype(raw: str) -> list[CveFindingModel]:
        data = json.loads(raw)
        findings: list[CveFindingModel] = []
        for match in data.get("matches") or []:
            vulnerability = match.get("vulnerability") or {}
            artifact = match.get("artifact") or {}
            severity = (vulnerability.get("severity") or "UNKNOWN").upper()
            severity = _NEGLIGIBLE_TO_LOW.get(severity, severity)
            fixed_versions = (vulnerability.get("fix") or {}).get("versions") or []
            findings.append(
                CveFindingModel(
                    vulnerability_id=vulnerability.get("id", ""),
                    severity=severity,
                    package_name=artifact.get("name", ""),
                    installed_version=artifact.get("version", ""),
                    fixed_version=fixed_versions[0] if fixed_versions else None,
                    title=vulnerability.get("description"),
                    purl=artifact.get("purl"),
                )
            )
        return findings

    def _summarize(self, findings: list[CveFindingModel], sbom_path: Path) -> CveAuditResultModel:
        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        for finding in findings:
            counts[finding.severity if finding.severity in counts else "UNKNOWN"] += 1
        return CveAuditResultModel(
            scanner=self._backend or "unknown",
            scanner_version=self.get_version() or "unknown",
            sbom_path=str(sbom_path),
            total_findings=len(findings),
            critical=counts["CRITICAL"],
            high=counts["HIGH"],
            medium=counts["MEDIUM"],
            low=counts["LOW"],
            unknown=counts["UNKNOWN"],
            findings=findings,
        )
