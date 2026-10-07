#!/usr/bin/env python3
"""SBOM generation — `write_sbom()` (docs/design/sbom-generation.md Phase 1).

Corrects ADR-0022's "SBOM has no confirmed `build run` consumer" framing:
v1 generates a CycloneDX SBOM unconditionally on every build, not as an
opt-in feature (see the design doc for the full evidence trail). This is
the v2 orchestration half — collector plugins themselves live in
`strata.integrations.sbom_collectors` (ADR-0003 layering: this module may
import that one, never the reverse).

Called once per `build_run()` invocation, after every provisioner and
workload module has been materialised/rendered — matching v1's own
"sbom runs dead last" ordering (`platform -> terraform -> ... -> sync ->
sbom`). Writes `build_path / "sbom.json"` directly; no deployment manifest
exists yet to embed a `SbomReferenceModel` into (gap_fit_v1.md gap #7) —
see the design doc's "write now, reference later" conclusion.
"""

import hashlib
from datetime import date
from pathlib import Path
from typing import cast

from cyclonedx.model import Property
from cyclonedx.model.bom import Bom
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.output.json import JsonV1Dot6
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packageurl import PackageURL

from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.workload_controller import resolve_module
from strata.integrations.cve_scanner import CveScannerIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors import registry
from strata.logging.config import get_logger
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import (
    CVE_SEVERITY_ORDER,
    CveAllowedEntryModel,
    CveAuditResultModel,
    SbomComponentModel,
    SbomReferenceModel,
)
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics

log = get_logger(__name__)

_SBOM_FILENAME = "sbom.json"

#: Public — `audit_run.py`'s `_sbom_reference()` reuses this as the single
#: source of truth rather than hardcoding a second copy of the format string
#: when re-deriving the manifest's `sbom` reference at deploy time.
SBOM_FORMAT = "cyclonedx-1.6"

_TYPE_MAP: dict[str, ComponentType] = {
    "container": ComponentType.CONTAINER,
    "library": ComponentType.LIBRARY,
    "framework": ComponentType.FRAMEWORK,
}


def write_sbom(build_path: Path, graph: ResolvedWorkspaceGraph, index: DocumentIndex) -> Diagnostics:
    """Collect every registered SBOM collector's components, serialise to
    CycloneDX 1.6 JSON, validate, and write `build_path / "sbom.json"`.

    Args:
        build_path: The build output root for this `build run` invocation —
            already-rendered artifacts (compose files, materialised Helm
            charts, synced Terraform roots) live under here.
        graph: The deployment's already-resolved workspace graph
            (`build_resolved_workspace_graph()`).
        index: The loaded, already-`require_valid()`-ed `DocumentIndex` —
            used once, here, to resolve every namespace's module
            references into real `ModuleModel`s before any collector runs
            (collectors themselves never see `index` — ADR-0003 layering,
            `integrations` must not import `controllers`).

    Returns:
        `Diagnostics` carrying every collector's warnings (floating image
        tags, unparseable files) as `Severity.WARNING` findings — never
        `Severity.ERROR`; an invalid component or a bad file is survivable,
        not a reason to fail the whole build.

    Raises:
        RuntimeError: the CycloneDX library rejects the BOM it was just
            asked to build (schema-version mismatch, malformed `purl`) —
            should be unreachable in practice, since the library's own
            object model only produces valid output; this is insurance,
            not expected validation. Also raised if `sbom.json` does not
            exist on disk immediately after the write (the exact class of
            defect this design's Remaining Work traces to a historical v1
            bug — verified here as a hard failure, not a silent trust).
    """
    diagnostics = Diagnostics()
    modules = _resolve_all_modules(graph, index)

    components: list[SbomComponentModel] = []
    for name in registry.list_collectors():
        collector = registry.get(name)
        result = collector.collect(graph, modules, build_path)
        components.extend(result.components)
        diagnostics.items.extend(result.diagnostics)

    bom_json = _build_cyclonedx_json(components)

    validation_error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(bom_json)
    if validation_error is not None:
        raise RuntimeError(f"generated SBOM failed CycloneDX schema validation: {validation_error}")

    sbom_path = build_path / _SBOM_FILENAME
    sbom_bytes = bom_json.encode("utf-8")
    build_path.mkdir(parents=True, exist_ok=True)
    sbom_path.write_bytes(sbom_bytes)

    if not sbom_path.is_file():
        raise RuntimeError(f"SBOM file not found after write: {sbom_path}")

    reference = SbomReferenceModel(
        path=str(sbom_path),
        format=SBOM_FORMAT,
        sha256=f"sha256:{hashlib.sha256(sbom_bytes).hexdigest()}",
        component_count=len(components),
    )
    log.info(
        "sbom written",
        path=reference.path,
        component_count=reference.component_count,
        sha256=reference.sha256,
    )

    return diagnostics


def _resolve_all_modules(graph: ResolvedWorkspaceGraph, index: DocumentIndex) -> list[ModuleModel]:
    """Resolve every enabled module reference, across every namespace the
    workspace references, into a real `ModuleModel` — the same "resolve
    before any collector/integration sees it" step
    `workload_controller.build_workload_modules()` already does, done once
    here so every collector works from a flat list and never touches
    `DocumentIndex` itself."""
    modules: list[ModuleModel] = []
    for namespace in graph.namespaces.values():
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            modules.append(resolve_module(index, reference))
    return modules


def _build_cyclonedx_json(components: list[SbomComponentModel]) -> str:
    """Convert collected components to a CycloneDX 1.6 JSON string.

    The only function in this module that imports `cyclonedx-python-lib`'s
    model-building classes — every collector stays free of this dependency
    (docs/design/sbom-generation.md's own isolation convention, matching
    v1's).
    """
    bom = Bom()

    for comp in components:
        cdx_type = _TYPE_MAP.get(comp.component_type, ComponentType.LIBRARY)
        try:
            purl = PackageURL.from_string(comp.purl)
        except ValueError:
            purl = None
        properties = [Property(name=k, value=v) for k, v in comp.properties.items()]
        bom.components.add(
            Component(name=comp.name, type=cdx_type, version=comp.version, purl=purl, properties=properties)
        )

    output = JsonV1Dot6(bom)
    return output.output_as_string()


def evaluate_cve_policy(build_path: Path, workspace: WorkspaceModel, index: DocumentIndex) -> Diagnostics:
    """Gate `build run` on CVE findings in the SBOM `write_sbom()` just
    wrote, when `workspace.spec.cve_policy` is set (docs/work/
    cve-scanner-integration.md).

    No-op (empty `Diagnostics`) with no policy configured — the common
    case, matching every other Configuration-backed check's own "nothing
    declared, nothing to check" rule (`semantic_checks.py`). Unlike those
    checks, this one is NOT run during `strata validate` — it depends on
    the SBOM `write_sbom()` has just rendered to `build_path`, so it only
    ever runs as part of `build_controller.build_run()`, after that write.

    Args:
        build_path: The same build output root `write_sbom()` was called
            with — `sbom.json` is read from here.
        workspace: The deployment's resolved workspace
            (`ResolvedWorkspaceGraph.workspace`) — the sole owner of
            `spec.cve_policy`.
        index: The loaded `DocumentIndex` — used to find the solution's
            one `Configuration` document, if any, for its
            `spec.cve_allowed` suppression list (Phase 3).

    Returns:
        One `Severity.ERROR`/`Severity.WARNING` finding when the policy's
        thresholds are breached, or when `on_missing_data` requires one and
        no scanner/SBOM is available — never raises.
    """
    diagnostics = Diagnostics()
    policy = workspace.spec.cve_policy
    if policy is None:
        return diagnostics

    severity = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning
    sbom_path = build_path / _SBOM_FILENAME
    scanner = CveScannerIntegration()

    missing_reason: str | None = None
    if not scanner.is_available():
        missing_reason = "no CVE scanner (Trivy or Grype) found on PATH"
    elif not sbom_path.is_file():
        missing_reason = f"no SBOM found at '{sbom_path}'"

    if missing_reason is not None:
        _report_missing_data(diagnostics, policy.on_missing_data, missing_reason)
        return diagnostics

    effective_threshold = policy.severity_threshold or policy.max_severity
    try:
        result = scanner.scan_sbom(sbom_path, severity_threshold=effective_threshold)
    except IntegrationError as exc:
        _report_missing_data(diagnostics, policy.on_missing_data, f"scan failed — {exc}")
        return diagnostics

    allowed = _configured_allowlist(index)
    if allowed:
        result = _apply_allowlist(result, allowed)

    severity_counts = {"CRITICAL": result.critical, "HIGH": result.high, "MEDIUM": result.medium, "LOW": result.low}
    max_index = CVE_SEVERITY_ORDER.index(policy.max_severity)
    breaching = sum(count for level, count in severity_counts.items() if CVE_SEVERITY_ORDER.index(level) <= max_index)

    if breaching > policy.max_count:
        severity(
            f"cve_policy: {breaching} finding(s) at or above severity '{policy.max_severity}' exceed "
            f"max_count ({policy.max_count}) — scanned with {result.scanner} {result.scanner_version}.",
            code="cve_policy_violation",
        )
    return diagnostics


def _configured_allowlist(index: DocumentIndex) -> list[CveAllowedEntryModel]:
    """The solution's one `Configuration` document's `cve_allowed` list, or
    `[]` when no `Configuration` document is declared (or it declares
    none) — same "nothing declared, nothing to check" rule
    `semantic_checks.py`'s own `_single_configuration()` already applies
    for an ambiguous/absent `Configuration`. A small, local equivalent
    rather than a cross-module import of that module-private helper —
    matches this repo's existing pattern of small per-module private
    helpers over sharing a five-line function across controller files.
    """
    entries = index.all_of(PlatformKind.CONFIGURATION)
    if len(entries) != 1:
        return []
    configuration = cast(ConfigurationModel, entries[0].model)
    return configuration.spec.cve_allowed or []


def _apply_allowlist(result: CveAuditResultModel, allowed: list[CveAllowedEntryModel]) -> CveAuditResultModel:
    """Suppress findings matching an active allowlist entry, recomputing
    every severity count from the remaining findings (never decremented —
    matches v1's real behavior and avoids a whole class of
    'decremented past zero' bugs an incremental approach would risk).

    An entry is active only when `expires` is unset or not yet in the
    past (`>=` today — an entry expiring *today* is still honored, the
    plain-English reading of "valid through this date"). An active entry
    with no `package` set suppresses its CVE id everywhere it appears;
    one with `package` set only suppresses findings naming that exact
    package.
    """
    today = date.today()
    active = [entry for entry in allowed if entry.expires is None or date.fromisoformat(entry.expires) >= today]
    remaining = [
        finding
        for finding in result.findings
        if not any(
            entry.id == finding.vulnerability_id and (entry.package is None or entry.package == finding.package_name)
            for entry in active
        )
    ]
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    for finding in remaining:
        counts[finding.severity if finding.severity in counts else "UNKNOWN"] += 1
    return result.model_copy(
        update={
            "findings": remaining,
            "total_findings": len(remaining),
            "critical": counts["CRITICAL"],
            "high": counts["HIGH"],
            "medium": counts["MEDIUM"],
            "low": counts["LOW"],
            "unknown": counts["UNKNOWN"],
        }
    )


def _report_missing_data(diagnostics: Diagnostics, on_missing_data: str, reason: str) -> None:
    """Shared by both of `evaluate_cve_policy()`'s "nothing to evaluate"
    branches (no scanner/SBOM, and a scan that raised) — `on_missing_data`
    defaults to `"block"` (not the generic `"skip"`): v1's own docstring
    warns that `"skip"` makes "never scanned" indistinguishable from
    "scanned, found nothing", a real risk for a security-flavored
    guardrail (`CveMaxSeverityPolicyModel`'s own docstring).
    """
    message = f"cve_policy: cannot evaluate — {reason}."
    if on_missing_data == "block":
        diagnostics.error(message, code="cve_policy_missing_data")
    elif on_missing_data == "warn":
        diagnostics.warning(message, code="cve_policy_missing_data")
