#!/usr/bin/env python3
"""Security gates at deploy preflight (docs/design/security-gates.md D5, D6, section 6.3).

Vendor-neutral: everything here talks to `ReleaseGateIntegration` / `ImageFindingsIntegration`
(`integrations/capabilities.py`) and the neutral types in `integrations/security_findings.py`;
nothing names a vendor. Each gate resolves its Integration document, binds the credentials its
`spec.authentication` references (D2), asks the platform, and turns the answer into
`Diagnostics` under the policy's `enforcement` / `on_missing_data` rules, mirroring
`sbom_controller.evaluate_cve_policy()`. Both gates always run, so one deploy shows every problem.

Progress is reported through `on_step` and results through `Diagnostics`, like the rest of the
deploy path; this module does not log.
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, cast

from packageurl import PackageURL

from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.value_controller import resolve_values
from strata.integrations.base import Integration
from strata.integrations.capabilities import CredentialedIntegration, ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import GateNotFoundError, IntegrationError
from strata.integrations.registry import get as get_integration
from strata.integrations.resolved_context import ValueResolution
from strata.integrations.security_findings import (
    SECURITY_SEVERITY_ORDER,
    ImageRef,
    ReleaseGateOutcome,
    ReleaseGateRequest,
    SecurityIssue,
)
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.integration_model import IntegrationModel
from strata.models.sbom_model import CveAllowedEntryModel
from strata.models.security_gate_model import ImageGatePolicyModel, ReleaseGatePolicyModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics, Severity
from strata.utils.errors import SystemError, UsageError, ValidationError
from strata.utils.sbom_utils import split_registry
from strata.utils.transport import run_command

#: Same file `sbom_controller.write_sbom()` writes during `build run`.
_SBOM_FILENAME = "sbom.json"

#: Commit sources after the policy pin, first match wins (design D6): strata's own, then each CI's.
_COMMIT_ENV_VARS = (
    "STRATA_COMMIT_SHA",
    "GITHUB_SHA",
    "BUILD_SOURCEVERSION",
    "CI_COMMIT_SHA",
    "BITBUCKET_COMMIT",
    "GIT_COMMIT",
)
_COMMIT_LENGTH = 40
_GIT_TIMEOUT = 10
_MAX_LISTED_ISSUES = 5

Gate = Literal["release", "images"]


@dataclass
class SecurityGateEvaluation:
    """Each gate's findings kept apart, so the audit record gets one result per gate (design D9)."""

    release: Diagnostics | None = None
    images: Diagnostics | None = None
    _integration_types: dict[str, str] = field(default_factory=dict)

    @property
    def diagnostics(self) -> Diagnostics:
        combined = Diagnostics()
        for gate in (self.release, self.images):
            if gate is not None:
                combined.extend(gate)
        return combined

    def policy_results(self, workspace: WorkspaceModel) -> list[ManifestPolicyResultModel]:
        """One entry per evaluated gate, shaped like `policy_results.cve_policy_result()`."""
        results = []
        gates: tuple[tuple[str, Diagnostics | None, ReleaseGatePolicyModel | ImageGatePolicyModel | None], ...] = (
            ("release_gate_policy", self.release, workspace.spec.release_gate_policy),
            ("image_gate_policy", self.images, workspace.spec.image_gate_policy),
        )
        for name, gate_diagnostics, policy in gates:
            if gate_diagnostics is None or policy is None:
                continue
            results.append(
                ManifestPolicyResultModel(
                    policy_name=name,
                    policy_type=self._integration_types.get(name, "unknown"),
                    phase="deploy",
                    enforcement=policy.enforcement,
                    passed=gate_diagnostics.ok,
                    violations=gate_diagnostics.messages(Severity.ERROR) + gate_diagnostics.messages(Severity.WARNING),
                )
            )
        return results


def evaluate_security_gates(
    context: SolutionContext,
    deployment_name: str,
    workspace: WorkspaceModel,
    build_path: Path,
    *,
    resolved: ValueResolution | None,
    version_pin: str | None = None,
    commit_override: str | None = None,
    only: Gate | None = None,
    on_step: Callable[[str], None] | None = None,
    on_secret: Callable[[str], None] | None = None,
) -> SecurityGateEvaluation:
    """Run the workspace's `release_gate_policy` and/or `image_gate_policy` (design D5).

    Never raises for platform failures: an outage, a timeout or a missing credential goes through
    the policy's `on_missing_data`; configuration mistakes (unknown repository, an integration that
    cannot be constructed or whose declared credential does not resolve) are always errors.

    Args:
        resolved: The deploy's already-resolved values, or `None` outside a deploy; credentials are
            then resolved here.
        commit_override: A commit to judge instead of the D6 resolution order (`--commit`).
        only: Run just one gate.
        on_step: Progress lines.
        on_secret: Receives every resolved credential value, for the caller's redaction set.
    """
    evaluation = SecurityGateEvaluation()
    index = context.controller.index
    spec = workspace.spec
    if spec.release_gate_policy is not None and only != "images":
        evaluation._integration_types["release_gate_policy"] = _integration_type(index, spec.release_gate_policy)
        evaluation.release = _release_gate(
            context,
            deployment_name,
            spec.release_gate_policy,
            resolved,
            version_pin,
            commit_override,
            on_step,
            on_secret,
        )
    if spec.image_gate_policy is not None and only != "release":
        evaluation._integration_types["image_gate_policy"] = _integration_type(index, spec.image_gate_policy)
        evaluation.images = _image_gate(
            context, deployment_name, spec.image_gate_policy, build_path, resolved, version_pin, on_step, on_secret
        )
    return evaluation


# ---------------------------------------------------------------------------
# Integration and credentials
# ---------------------------------------------------------------------------


def _integration_type(index: DocumentIndex, policy: ReleaseGatePolicyModel | ImageGatePolicyModel) -> str:
    entry = index.get(PlatformKind.INTEGRATION, policy.integration)
    return cast(IntegrationModel, entry.model).spec.type if entry is not None else "unknown"


def _resolve_integration(
    index: DocumentIndex, name: str, capability_abc: type[Integration], field_name: str, diagnostics: Diagnostics
) -> Integration | None:
    """The named document's integration, if it can be built and implements the capability.

    Mirrors `resolve_output_store_integration()` / `_require_key_value_integration()`, but reports a
    diagnostic instead of raising: the gate's own policy decides how a deploy reacts.
    """
    location = f"spec.{field_name}.integration"
    entry = index.get(PlatformKind.INTEGRATION, name)
    if entry is None:
        known = ", ".join(sorted(index.names_of(PlatformKind.INTEGRATION))) or "none"
        diagnostics.error(
            f"{field_name}: integration '{name}' is not declared (declared: {known}).",
            location=location,
            code="unknown_reference",
        )
        return None
    config = cast(IntegrationModel, entry.model)
    try:
        integration = get_integration(config.spec.type, config=config)
    except IntegrationError as exc:
        diagnostics.error(f"{field_name}: {exc}", location=location, code="integration_configuration_invalid")
        return None
    if not isinstance(integration, capability_abc):
        diagnostics.error(
            f"{field_name}: integration '{name}' (type '{config.spec.type}') does not implement {capability_abc.__name__}.",
            location=location,
            code="gate_integration_type_unsupported",
        )
        return None
    return integration


def bind_resolved_credentials(
    context: SolutionContext,
    integration: Integration,
    resolved: ValueResolution | None,
    deployment_name: str,
    *,
    version_pin: str | None = None,
    on_secret: Callable[[str], None] | None = None,
) -> Diagnostics:
    """Resolve the keys `spec.authentication` references and hand them to the integration (design D2).

    Same steps `remote_credentials.py` takes for a git remote. A declared key that does not resolve
    is an error naming the key, never a value; no `authentication` block binds an empty mapping, so
    the integration may use its own environment variables.
    """
    diagnostics = Diagnostics()
    if not isinstance(integration, CredentialedIntegration):
        return diagnostics
    auth = integration.config.spec.authentication if integration.config is not None else None
    keys = list(auth.key_references()) if auth is not None else []
    values: dict[str, str] = {}
    reasons: list[str] = []
    if keys:
        if resolved is not None:
            values = {key: resolved.values[key] for key in keys if resolved.values.get(key)}
        else:
            outcome = resolve_values(context, deployment_name, keys, version_pin=version_pin)
            values = {key: value for key, value in outcome.values.items() if key in keys and value}
            reasons = [d.message for d in outcome.diagnostics.errors if d.location in keys]
        missing = [key for key in keys if key not in values]
        if missing:
            because = f" ({'; '.join(reasons)})" if reasons else ""
            diagnostics.error(
                f"Integration '{integration.name}': credential key(s) {', '.join(missing)} did not resolve for "
                f"deployment '{deployment_name}'{because}.",
                code="integration_credentials_unresolved",
            )
            return diagnostics
    if on_secret is not None:
        for value in values.values():
            on_secret(value)
    integration.bind_credentials(values)
    return diagnostics


# ---------------------------------------------------------------------------
# Release gate
# ---------------------------------------------------------------------------


def resolve_commit_sha(
    context: SolutionContext,
    policy: ReleaseGatePolicyModel,
    diagnostics: Diagnostics,
    override: str | None = None,
) -> tuple[str | None, str]:
    """The commit to judge and where it came from (design D6); first valid source wins.

    An invalid value (not 40 hex characters) is reported as a warning and the next source tried,
    so a mistyped variable cannot silently gate the wrong commit.
    """
    candidates: list[tuple[str, str | None]] = [("--commit", override), ("policy commit", policy.commit)]
    candidates += [(name, os.environ.get(name)) for name in _COMMIT_ENV_VARS]
    for source, value in candidates:
        if not value:
            continue
        sha = value.strip().lower()
        if _is_commit(sha):
            return sha, source
        diagnostics.warning(
            f"release_gate_policy: {source} value '{value}' is not a full 40-character commit SHA; ignored.",
            code="release_gate_commit_invalid",
        )
    result = run_command(["git", "rev-parse", "HEAD"], cwd=context.root, timeout=_GIT_TIMEOUT)
    sha = result.stdout.strip().lower()
    if result.is_successful and _is_commit(sha):
        return sha, "git rev-parse HEAD"
    return None, "none"


def _is_commit(value: str) -> bool:
    return len(value) == _COMMIT_LENGTH and all(char in "0123456789abcdef" for char in value)


def _pin_matches_run(commit: str, policy: ReleaseGatePolicyModel, diagnostics: Diagnostics) -> bool:
    """Report a policy `commit` pin that differs from the CI variable (design D6a); False stops the gate."""
    run = next(
        ((name, sha) for name in _COMMIT_ENV_VARS if _is_commit(sha := os.environ.get(name, "").strip().lower())),
        None,
    )
    if run is None or run[1] == commit:
        return True
    deny = policy.enforcement == "deny"
    report = diagnostics.error if deny else diagnostics.warning
    report(
        f"release_gate_policy: judging the pinned commit {commit[:12]} (spec.release_gate_policy.commit), "
        f"but {run[0]} says this run builds {run[1][:12]}. Remove the pin, or pass --commit to judge "
        "another commit on purpose.",
        location="spec.release_gate_policy.commit",
        code="release_gate_commit_mismatch",
    )
    return not deny


def _release_gate(
    context: SolutionContext,
    deployment_name: str,
    policy: ReleaseGatePolicyModel,
    resolved: ValueResolution | None,
    version_pin: str | None,
    commit_override: str | None,
    on_step: Callable[[str], None] | None,
    on_secret: Callable[[str], None] | None,
) -> Diagnostics:
    diagnostics = Diagnostics()
    field_name = "release_gate_policy"
    integration = _resolve_integration(
        context.controller.index, policy.integration, ReleaseGateIntegration, field_name, diagnostics
    )
    if integration is None:
        return diagnostics
    diagnostics.extend(
        bind_resolved_credentials(
            context, integration, resolved, deployment_name, version_pin=version_pin, on_secret=on_secret
        )
    )
    if not diagnostics.ok:
        return diagnostics

    commit, source = resolve_commit_sha(context, policy, diagnostics, commit_override)
    if commit is None:
        _report_missing_data(
            diagnostics,
            "release_gate",
            policy.on_missing_data,
            "no commit SHA (set --commit, STRATA_COMMIT_SHA or a CI commit variable, or run inside a git checkout)",
        )
        return diagnostics

    via = f"{integration.TYPE} '{integration.name}'"
    if source == "policy commit" and not _pin_matches_run(commit, policy, diagnostics):
        return diagnostics
    _emit(on_step, f"release gate: asking {via} about {policy.repository} @ {commit[:12]} ({source})")
    request = ReleaseGateRequest(
        repository=policy.repository, commit=commit, timeout=policy.timeout, poll_interval=policy.poll_interval
    )
    try:
        outcome = cast(ReleaseGateIntegration, integration).run_release_gate(
            request, on_poll=lambda attempt: _emit(on_step, f"release gate: waiting for {via} (check {attempt})")
        )
    except GateNotFoundError as exc:
        diagnostics.error(f"{field_name}: {exc}", code="release_gate_repository_not_found")
        return diagnostics
    except IntegrationError as exc:
        _report_missing_data(diagnostics, "release_gate", policy.on_missing_data, str(exc))
        return diagnostics

    target = f"'{policy.repository}' @ {commit[:12]}"
    if outcome.passed:
        diagnostics.info(
            f"{field_name}: passed via {via} for {target}{_outcome_summary(outcome)}.", code="release_gate_passed"
        )
        return diagnostics
    report = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning
    first_link = f" {outcome.links[0]}" if outcome.links else ""
    report(
        f"{field_name}: FAILED via {via} for {target}{_outcome_summary(outcome)}.{first_link}",
        code="release_gate_failed",
    )
    return diagnostics


def _outcome_summary(outcome: ReleaseGateOutcome) -> str:
    parts = []
    if outcome.reference:
        parts.append(f"scan {outcome.reference}")
    if outcome.open_issues is not None:
        parts.append(f"{outcome.open_issues} open issue(s)")
    categories = []
    for category, counts in outcome.breakdown.items():
        found = [f"{count} {severity}" for severity, count in counts.items() if count]
        if found:
            categories.append(f"{category}: {', '.join(found)}")
    if categories:
        parts.append("; ".join(categories))
    if outcome.detail:
        parts.append(outcome.detail)
    return f" ({' - '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------
# Image gate
# ---------------------------------------------------------------------------


def images_from_sbom(build_path: Path) -> list[ImageRef] | None:
    """The container images `build run` recorded in `sbom.json`, or `None` when there is no SBOM.

    The image comes from each container component's purl (`image_to_purl()`): its version is a
    digest when it contains `:` (a tag cannot), else a tag. Duplicates (one image used by several
    services) are kept once, in SBOM order.

    Raises:
        ValueError: `sbom.json` exists but cannot be read as an SBOM.
    """
    path = build_path / _SBOM_FILENAME
    if not path.is_file():
        return None
    try:
        sbom = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{path}' is not valid JSON ({exc})") from None
    components = sbom.get("components") if isinstance(sbom, Mapping) else None
    if not isinstance(components, list):
        raise ValueError(f"'{path}' has no components list")

    images: list[ImageRef] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for component in components:
        if not isinstance(component, Mapping) or component.get("type") != "container":
            continue
        purl = component.get("purl")
        if not isinstance(purl, str) or not purl.startswith("pkg:docker/"):
            continue
        parsed = PackageURL.from_string(purl)
        name = f"{parsed.namespace}/{parsed.name}" if parsed.namespace else parsed.name
        version = parsed.version or None
        tag, digest = (None, version) if version and ":" in version else (version, None)
        registry, repository = split_registry(name)
        key = (name, tag, digest)
        if key in seen:
            continue
        seen.add(key)
        service = component.get("name")
        images.append(
            ImageRef(
                service=service if isinstance(service, str) else repository,
                registry=registry,
                repository=repository,
                tag=tag,
                digest=digest,
            )
        )
    return images


def _image_gate(
    context: SolutionContext,
    deployment_name: str,
    policy: ImageGatePolicyModel,
    build_path: Path,
    resolved: ValueResolution | None,
    version_pin: str | None,
    on_step: Callable[[str], None] | None,
    on_secret: Callable[[str], None] | None,
) -> Diagnostics:
    diagnostics = Diagnostics()
    field_name = "image_gate_policy"
    index = context.controller.index
    integration = _resolve_integration(index, policy.integration, ImageFindingsIntegration, field_name, diagnostics)
    if integration is None:
        return diagnostics
    diagnostics.extend(
        bind_resolved_credentials(
            context, integration, resolved, deployment_name, version_pin=version_pin, on_secret=on_secret
        )
    )
    if not diagnostics.ok:
        return diagnostics

    try:
        images = images_from_sbom(build_path)
    except ValueError as exc:
        _report_missing_data(diagnostics, "image_gate", policy.on_missing_data, str(exc))
        return diagnostics
    if images is None:
        _report_missing_data(
            diagnostics,
            "image_gate",
            policy.on_missing_data,
            f"no {_SBOM_FILENAME} in '{build_path}' (run 'strata build run' first)",
        )
        return diagnostics
    if not images:
        diagnostics.info(
            f"{field_name}: no container images in {_SBOM_FILENAME}; nothing to check.", code="image_gate_no_images"
        )
        return diagnostics

    severities = frozenset(SECURITY_SEVERITY_ORDER[: SECURITY_SEVERITY_ORDER.index(policy.max_severity) + 1])
    allowed = _active_allowlist(index)
    finder = cast(ImageFindingsIntegration, integration)
    via = f"{integration.TYPE} '{integration.name}'"
    counted: list[tuple[str, list[SecurityIssue]]] = []
    for image in images:
        label = _image_label(image)
        _emit(on_step, f"image gate: asking {via} about {label}")
        try:
            lookup = finder.find_image_issues(image, severities)
        except IntegrationError as exc:
            _report_missing_data(diagnostics, "image_gate", policy.on_missing_data, str(exc))
            return diagnostics
        if lookup.status == "unknown":
            _report_unknown_image(diagnostics, policy, via, label, lookup.platform_name, lookup.detail)
            continue
        if lookup.status == "ambiguous":
            detail = f": {lookup.detail}" if lookup.detail else ""
            diagnostics.error(
                f"{field_name}: {via} has more than one container named '{lookup.platform_name}' for {label}{detail}. "
                "Map it explicitly in the integration's configuration.",
                code="image_ambiguous",
            )
            continue
        issues = [issue for issue in lookup.issues if issue.severity in severities and not _allowlisted(issue, allowed)]
        counted.append((label, issues))

    total = sum(len(issues) for _, issues in counted)
    if total <= policy.max_count:
        counts = (
            f"{total} open issue(s) at or above {policy.max_severity} across {len(counted)} image(s) "
            f"via {via} (max_count {policy.max_count})"
        )
        if diagnostics.ok:
            diagnostics.info(f"{field_name}: {counts}.", code="image_gate_passed")
        else:
            diagnostics.info(
                f"{field_name}: {counts}; the gate fails on the image error(s) above.", code="image_gate_counted"
            )
        return diagnostics
    per_image = ", ".join(f"{label} ({len(issues)})" for label, issues in counted if issues)
    listed = [issue for _, issues in counted for issue in issues][:_MAX_LISTED_ISSUES]
    examples = "; ".join(f"{issue.severity} {issue.cve_id or issue.id} in {issue.package or '?'}" for issue in listed)
    report = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning
    report(
        f"{field_name}: {total} open issue(s) at or above {policy.max_severity} exceed max_count "
        f"({policy.max_count}) via {via}: {per_image}. First: {examples}.",
        code="image_gate_violation",
    )
    return diagnostics


@dataclass(frozen=True)
class ImageFindings:
    """What the platform reports for one SBOM image; `allowlisted` holds the ids `cve_allowed` would suppress."""

    image: ImageRef
    label: str
    status: Literal["found", "unknown", "ambiguous"]
    platform_name: str | None
    detail: str | None
    issues: tuple[SecurityIssue, ...]
    allowlisted: frozenset[str]


@dataclass(frozen=True)
class ImageFindingsReport:
    """`strata policy findings` for the image gate (design D7)."""

    integration: str
    severities: tuple[str, ...]
    images: tuple[ImageFindings, ...]


def list_image_findings(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    integration_name: str,
    max_severity: str,
    *,
    version_pin: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> ImageFindingsReport:
    """Every SBOM image's open issues at or above `max_severity`, read-only (design D7).

    Same integration resolution, credential binding and allowlist as the image gate, but allowlisted
    issues are marked instead of dropped and nothing is judged or recorded.

    Raises:
        UsageError: The integration is not declared or lacks `image_findings`; no or unreadable `sbom.json`.
        ValidationError: The integration cannot be constructed or its credentials do not resolve.
        SystemError: The platform could not be reached or rejected the request.
    """
    index = context.controller.index
    diagnostics = Diagnostics()
    integration = _resolve_integration(
        index, integration_name, ImageFindingsIntegration, "image_gate_policy", diagnostics
    )
    if integration is None:
        error = diagnostics.errors[0]
        if error.code == "integration_configuration_invalid":
            raise ValidationError(diagnostics, error.message)
        raise UsageError(error.message)
    credentials = bind_resolved_credentials(context, integration, None, deployment_name, version_pin=version_pin)
    if not credentials.ok:
        raise ValidationError(credentials, credentials.errors[0].message)
    try:
        images = images_from_sbom(build_path)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    if images is None:
        raise UsageError(f"No {_SBOM_FILENAME} in '{build_path}'; run 'strata build run {deployment_name}' first.")

    severities = SECURITY_SEVERITY_ORDER[: SECURITY_SEVERITY_ORDER.index(max_severity) + 1]
    allowed = _active_allowlist(index)
    finder = cast(ImageFindingsIntegration, integration)
    via = f"{integration.TYPE} '{integration.name}'"
    found: list[ImageFindings] = []
    for image in images:
        label = _image_label(image)
        _emit(on_step, f"findings: asking {via} about {label}")
        try:
            lookup = finder.find_image_issues(image, frozenset(severities))
        except IntegrationError as exc:
            raise SystemError(str(exc)) from None
        issues = tuple(issue for issue in lookup.issues if issue.severity in severities)
        found.append(
            ImageFindings(
                image=image,
                label=label,
                status=lookup.status,
                platform_name=lookup.platform_name,
                detail=lookup.detail,
                issues=issues,
                allowlisted=frozenset(issue.id for issue in issues if _allowlisted(issue, allowed)),
            )
        )
    return ImageFindingsReport(integration=via, severities=severities, images=tuple(found))


def _image_label(image: ImageRef) -> str:
    if image.tag:
        return f"{image.path}:{image.tag}"
    if image.digest:
        return f"{image.path}@{image.digest}"
    return image.path


def _report_unknown_image(
    diagnostics: Diagnostics,
    policy: ImageGatePolicyModel,
    via: str,
    label: str,
    platform_name: str | None,
    detail: str | None,
) -> None:
    suffix = f" ({detail})" if detail else ""
    message = (
        f"image_gate_policy: {via} does not know {label} (looked up as '{platform_name}'){suffix}; it was not checked."
    )
    if policy.on_unknown_image == "block":
        diagnostics.error(message, code="image_unknown")
    elif policy.on_unknown_image == "warn":
        diagnostics.warning(message, code="image_unknown")
    else:
        diagnostics.info(message, code="image_unknown")


def _active_allowlist(index: DocumentIndex) -> list[CveAllowedEntryModel]:
    """The solution's `cve_allowed` entries still in force (`expires` today counts), as the CVE gate reads them."""
    entries = index.all_of(PlatformKind.CONFIGURATION)
    if len(entries) != 1:
        return []
    allowed = cast(ConfigurationModel, entries[0].model).spec.cve_allowed or []
    today = date.today()
    return [entry for entry in allowed if entry.expires is None or date.fromisoformat(entry.expires) >= today]


def _allowlisted(issue: SecurityIssue, allowed: list[CveAllowedEntryModel]) -> bool:
    """`sbom_controller._apply_allowlist()` semantics; an issue without a CVE id cannot be allowlisted."""
    if issue.cve_id is None:
        return False
    return any(
        entry.id == issue.cve_id and (entry.package is None or entry.package == issue.package) for entry in allowed
    )


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _report_missing_data(diagnostics: Diagnostics, gate: str, on_missing_data: str, reason: str) -> None:
    """Local copy of `sbom_controller._report_missing_data()`: `skip` must still leave a trace."""
    message = f"{gate}_policy: cannot evaluate - {reason}"
    code = f"{gate}_missing_data"
    if on_missing_data == "block":
        diagnostics.error(message, code=code)
    elif on_missing_data == "warn":
        diagnostics.warning(message, code=code)
    else:
        diagnostics.info(message, code=code)


def _emit(on_step: Callable[[str], None] | None, message: str) -> None:
    if on_step is not None:
        on_step(message)
