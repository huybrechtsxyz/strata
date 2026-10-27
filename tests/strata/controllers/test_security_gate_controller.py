#!/usr/bin/env python3
"""Tests for the vendor-neutral security gate controller (docs/work/security-gates-design.md D5, D6, D9).

A fake integration implementing both capability ABCs stands in for any vendor (no Aikido code runs),
which is what proves the controller is vendor-neutral. The index is built directly, as
`test_sbom_controller.py` does; `resolve_values` and `run_command` are stubbed where used.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.controllers import security_gate_controller as gates_module
from strata.controllers.security_gate_controller import evaluate_security_gates, images_from_sbom
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.capabilities import CredentialedIntegration, ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import GateNotFoundError, GateTimeoutError, IntegrationError
from strata.integrations.resolved_context import ValueResolution
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome, SecurityIssue
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationMetaModel, ConfigurationModel, ConfigurationSpecModel
from strata.models.integration_model import IntegrationModel
from strata.models.sbom_model import CveAllowedEntryModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.transport import CommandResult

COMMIT = "a" * 40
TOKEN = "ci-token-value-123456"
_COMMIT_VARS = (
    "STRATA_COMMIT_SHA",
    "GITHUB_SHA",
    "BUILD_SOURCEVERSION",
    "CI_COMMIT_SHA",
    "BITBUCKET_COMMIT",
    "GIT_COMMIT",
)


class _FakeGate(ReleaseGateIntegration, ImageFindingsIntegration, CredentialedIntegration):
    """Answers from a shared script; records what the controller asked."""

    TYPE = "fake-gate"
    CAPABILITIES = frozenset({"release_gate", "image_findings"})
    TRANSPORTS = frozenset({"http"})

    def __init__(self, config, script):
        super().__init__(config)
        self.script = script

    def bind_credentials(self, values):
        self.script.bound.append(dict(values))

    def run_release_gate(self, request, *, on_poll=None):
        self.script.requests.append(request)
        if on_poll is not None:
            on_poll(1)
        if isinstance(self.script.release, Exception):
            raise self.script.release
        return self.script.release

    def find_image_issues(self, image, severities):
        self.script.lookups_asked.append((image, severities))
        answer = self.script.lookups.get(image.repository, ImageLookup(status="found", platform_name=image.repository))
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def script(monkeypatch):
    state = SimpleNamespace(
        release=ReleaseGateOutcome(passed=True, reference="22", open_issues=0),
        lookups={},
        requests=[],
        lookups_asked=[],
        bound=[],
        constructed=[],
    )

    def _get(integration_type, config=None):
        state.constructed.append(config.meta.name)
        if integration_type == "broken":
            raise IntegrationError("integration_configuration_invalid: broken: bad key")
        if integration_type == "webhook-like":
            from strata.integrations.webhook import WebhookIntegration

            return WebhookIntegration(None)
        return _FakeGate(config, state)

    monkeypatch.setattr(gates_module, "get_integration", _get)
    return state


@pytest.fixture(autouse=True)
def _no_commit_sources(monkeypatch):
    for name in _COMMIT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        gates_module, "run_command", lambda *a, **k: CommandResult(returncode=128, stdout="", stderr="")
    )


def _integration(
    name: str, *, integration_type="fake-gate", capabilities=("release_gate",), auth=None
) -> IntegrationModel:
    spec: dict = {"type": integration_type, "capabilities": list(capabilities)}
    if auth is not None:
        spec["authentication"] = auth
    return IntegrationModel.model_validate({"meta": {"name": name}, "spec": spec})


def _context(tmp_path: Path, integrations: list[IntegrationModel], cve_allowed=None):
    index = DocumentIndex()
    for document in integrations:
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.INTEGRATION, name=document.meta.name),
                model=document,
                source=Path(f"{document.meta.name}.yaml"),
            )
        )
    if cve_allowed is not None:
        configuration = ConfigurationModel(
            meta=ConfigurationMetaModel(name="config"), spec=ConfigurationSpecModel(cve_allowed=cve_allowed)
        )
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.CONFIGURATION, name="config"),
                model=configuration,
                source=Path("configuration.yaml"),
            )
        )
    return SimpleNamespace(root=tmp_path, controller=SimpleNamespace(index=index))


def _workspace(release=None, image=None) -> WorkspaceModel:
    spec: dict = {
        "providers": ["p1"],
        "provisioners": [{"name": "tf", "tool": "terraform", "source": {"source_path": "infra"}}],
    }
    if release is not None:
        spec["release_gate_policy"] = {"integration": "gate-ci", "repository": "org/infra", **release}
    if image is not None:
        spec["image_gate_policy"] = {"integration": "gate-api", **image}
    return WorkspaceModel.model_validate({"meta": {"name": "main"}, "spec": spec})


def _sbom(build_path: Path, *purls: str) -> None:
    components = [{"type": "container", "name": f"svc{n}", "purl": purl} for n, purl in enumerate(purls)]
    components.append({"type": "library", "name": "hashicorp/azurerm", "purl": "pkg:terraform/hashicorp/azurerm@4.0"})
    (build_path / "sbom.json").write_text(json.dumps({"bomFormat": "CycloneDX", "components": components}))


def _resolved(**values) -> ValueResolution:
    return ValueResolution(deployment="app", values=values)


def _codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


_RELEASE_DOCS = [_integration("gate-ci")]
_IMAGE_DOCS = [_integration("gate-api", capabilities=("image_findings",))]
_API_KEY = {"method": "api_key", "api_key": {"api_key": "CI_TOKEN_KEY"}}


def _release(tmp_path, script, release=None, *, docs=None, resolved=None, **kwargs):
    context = _context(tmp_path, docs or _RELEASE_DOCS)
    workspace = _workspace(release={"commit": COMMIT, **(release or {})})
    return evaluate_security_gates(
        context, "app", workspace, tmp_path, resolved=resolved if resolved is not None else _resolved(), **kwargs
    )


# ---------------------------------------------------------------------------
# Credentials (design D2)
# ---------------------------------------------------------------------------


def test_credentials_come_from_the_deploys_resolved_values(tmp_path, script):
    secrets: list[str] = []
    docs = [_integration("gate-ci", auth=_API_KEY)]
    evaluation = _release(tmp_path, script, docs=docs, resolved=_resolved(CI_TOKEN_KEY=TOKEN), on_secret=secrets.append)
    assert evaluation.diagnostics.ok
    assert script.bound == [{"CI_TOKEN_KEY": TOKEN}]
    assert secrets == [TOKEN]


def test_outside_a_deploy_credentials_are_resolved_here(tmp_path, script, monkeypatch):
    asked = []

    def _resolve(context, deployment, keys, *, version_pin=None):
        asked.append((deployment, keys, version_pin))
        return _resolved(CI_TOKEN_KEY=TOKEN)

    monkeypatch.setattr(gates_module, "resolve_values", _resolve)
    context = _context(tmp_path, [_integration("gate-ci", auth=_API_KEY)])
    evaluate_security_gates(
        context, "app", _workspace(release={"commit": COMMIT}), tmp_path, resolved=None, version_pin="prd"
    )
    assert asked == [("app", ["CI_TOKEN_KEY"], "prd")]
    assert script.bound == [{"CI_TOKEN_KEY": TOKEN}]


def test_a_declared_key_that_does_not_resolve_is_an_error_naming_the_key(tmp_path, script):
    docs = [_integration("gate-ci", auth=_API_KEY)]
    evaluation = _release(tmp_path, script, docs=docs, resolved=_resolved(OTHER=TOKEN))
    errors = evaluation.diagnostics.errors
    assert _codes(errors) == ["integration_credentials_unresolved"]
    assert "CI_TOKEN_KEY" in errors[0].message and TOKEN not in errors[0].message
    assert script.requests == []


def test_an_unresolved_key_carries_the_resolution_reason(tmp_path, script, monkeypatch):
    def _resolve(context, deployment, keys, *, version_pin=None):
        outcome = _resolved()
        outcome.diagnostics.error("'CI_TOKEN_KEY' is not declared in any environment.", location="CI_TOKEN_KEY")
        outcome.diagnostics.error("'UNRELATED' failed.", location="UNRELATED")
        return outcome

    monkeypatch.setattr(gates_module, "resolve_values", _resolve)
    context = _context(tmp_path, [_integration("gate-ci", auth=_API_KEY)])
    evaluation = evaluate_security_gates(
        context, "app", _workspace(release={"commit": COMMIT}), tmp_path, resolved=None
    )
    [error] = evaluation.diagnostics.errors
    assert "is not declared in any environment" in error.message and "UNRELATED" not in error.message


def test_an_undeclared_integration_is_an_unknown_reference(tmp_path, script):
    evaluation = _release(tmp_path, script, docs=[_integration("other")])
    [error] = evaluation.diagnostics.errors
    assert error.code == "unknown_reference"
    assert "'gate-ci' is not declared (declared: other)" in error.message


def test_without_authentication_an_empty_mapping_is_bound(tmp_path, script):
    _release(tmp_path, script)
    assert script.bound == [{}]


def test_an_integration_that_cannot_be_built_is_a_configuration_error(tmp_path, script):
    evaluation = _release(tmp_path, script, docs=[_integration("gate-ci", integration_type="broken")])
    assert _codes(evaluation.diagnostics.errors) == ["integration_configuration_invalid"]


def test_an_integration_lacking_the_capability_is_rejected(tmp_path, script):
    evaluation = _release(tmp_path, script, docs=[_integration("gate-ci", integration_type="webhook-like")])
    assert _codes(evaluation.diagnostics.errors) == ["gate_integration_type_unsupported"]


# ---------------------------------------------------------------------------
# Commit SHA (design D6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", _COMMIT_VARS)
def test_each_commit_variable_is_a_source(tmp_path, script, monkeypatch, variable):
    monkeypatch.setenv(variable, "B" * 40)
    _release(tmp_path, script, release={"commit": None})
    assert script.requests[0].commit == "b" * 40


def test_commit_order_override_then_policy_then_environment(tmp_path, script, monkeypatch):
    """Under `warn` the pin still wins over the CI variable (D6a only adds a warning)."""
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    _release(tmp_path, script, release={"commit": "d" * 40, "enforcement": "warn"}, commit_override="e" * 40)
    _release(tmp_path, script, release={"commit": "d" * 40, "enforcement": "warn"})
    assert [request.commit for request in script.requests] == ["e" * 40, "d" * 40]


def test_git_head_is_the_last_source(tmp_path, script, monkeypatch):
    seen = []

    def _git(args, *, cwd=None, timeout=60, **kwargs):
        seen.append((args, cwd))
        return CommandResult(returncode=0, stdout="F" * 40 + "\n", stderr="")

    monkeypatch.setattr(gates_module, "run_command", _git)
    _release(tmp_path, script, release={"commit": None})
    assert script.requests[0].commit == "f" * 40
    assert seen == [(["git", "rev-parse", "HEAD"], tmp_path)]


def test_an_invalid_commit_value_is_skipped_with_a_warning(tmp_path, script, monkeypatch):
    monkeypatch.setenv("STRATA_COMMIT_SHA", "abc1234")
    monkeypatch.setenv("GITHUB_SHA", "1" * 40)
    evaluation = _release(tmp_path, script, release={"commit": None})
    assert script.requests[0].commit == "1" * 40
    assert _codes(evaluation.diagnostics.warnings) == ["release_gate_commit_invalid"]


def test_no_commit_anywhere_is_missing_data(tmp_path, script):
    evaluation = _release(tmp_path, script, release={"commit": None})
    assert _codes(evaluation.diagnostics.errors) == ["release_gate_missing_data"]
    assert script.requests == []


# ---------------------------------------------------------------------------
# Pinned commit vs the run's commit (design D6a)
# ---------------------------------------------------------------------------


def test_a_pin_differing_from_ci_warns_under_warn_and_still_judges_the_pin(tmp_path, script, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    evaluation = _release(tmp_path, script, release={"enforcement": "warn"})
    [warning] = [d for d in evaluation.diagnostics.warnings if d.code == "release_gate_commit_mismatch"]
    assert (
        "pinned commit aaaaaaaaaaaa" in warning.message
        and "GITHUB_SHA says this run builds cccccccccccc" in warning.message
    )
    assert warning.location == "spec.release_gate_policy.commit"
    assert [request.commit for request in script.requests] == [COMMIT]


def test_a_pin_differing_from_ci_is_an_error_under_deny_and_asks_nothing(tmp_path, script, monkeypatch):
    monkeypatch.setenv("BUILD_SOURCEVERSION", "c" * 40)
    evaluation = _release(tmp_path, script)
    assert _codes(evaluation.diagnostics.errors) == ["release_gate_commit_mismatch"]
    assert script.requests == []
    [result] = evaluation.policy_results(_workspace(release={"commit": COMMIT}))
    assert result.passed is False and "pinned commit" in result.violations[0]


def test_a_pin_matching_ci_is_silent(tmp_path, script, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", COMMIT.upper())
    evaluation = _release(tmp_path, script)
    assert "release_gate_commit_mismatch" not in _codes(evaluation.diagnostics)
    assert evaluation.diagnostics.ok


def test_commit_override_is_never_compared(tmp_path, script, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    evaluation = _release(tmp_path, script, commit_override="e" * 40)
    assert evaluation.diagnostics.ok
    assert script.requests[0].commit == "e" * 40


def test_a_pin_outside_ci_is_not_compared_with_git_head(tmp_path, script, monkeypatch):
    monkeypatch.setattr(
        gates_module, "run_command", lambda *a, **k: CommandResult(returncode=0, stdout="f" * 40, stderr="")
    )
    evaluation = _release(tmp_path, script)
    assert evaluation.diagnostics.ok
    assert script.requests[0].commit == COMMIT


def test_an_invalid_ci_value_is_not_a_mismatch(tmp_path, script, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc1234")
    evaluation = _release(tmp_path, script)
    assert evaluation.diagnostics.ok


# ---------------------------------------------------------------------------
# Release gate verdicts
# ---------------------------------------------------------------------------


def test_a_passed_gate_is_an_info_carrying_the_reference(tmp_path, script):
    steps: list[str] = []
    evaluation = _release(tmp_path, script, on_step=steps.append)
    assert evaluation.diagnostics.ok
    info = [d for d in evaluation.diagnostics if d.code == "release_gate_passed"][0]
    assert "fake-gate 'gate-ci'" in info.message and "scan 22" in info.message
    assert any("asking fake-gate 'gate-ci'" in step for step in steps)
    assert any("check 1" in step for step in steps)


@pytest.mark.parametrize(("enforcement", "severity"), [("deny", "errors"), ("warn", "warnings")])
def test_a_failed_gate_reports_counts_and_the_first_link(tmp_path, script, enforcement, severity):
    script.release = ReleaseGateOutcome(
        passed=False,
        reference="22",
        open_issues=2,
        breakdown={"sca": {"critical": 1, "high": 0}, "secrets": {"critical": 1}},
        links=("https://example/issue/1", "https://example/issue/2"),
    )
    evaluation = _release(tmp_path, script, release={"enforcement": enforcement})
    finding = getattr(evaluation.diagnostics, severity)[0]
    assert finding.code == "release_gate_failed"
    assert "2 open issue(s)" in finding.message
    assert "sca: 1 critical; secrets: 1 critical" in finding.message
    assert finding.message.endswith("https://example/issue/1")


def test_an_unknown_repository_is_always_a_hard_error(tmp_path, script):
    script.release = GateNotFoundError("unknown repo", reason="nope")
    evaluation = _release(tmp_path, script, release={"on_missing_data": "skip"})
    assert _codes(evaluation.diagnostics.errors) == ["release_gate_repository_not_found"]


@pytest.mark.parametrize(
    ("on_missing_data", "severity"), [("block", "errors"), ("warn", "warnings"), ("skip", "infos")]
)
@pytest.mark.parametrize("failure", [GateTimeoutError("scan 22 timed out"), IntegrationError("unreachable")])
def test_platform_failures_follow_on_missing_data(tmp_path, script, on_missing_data, severity, failure):
    script.release = failure
    evaluation = _release(tmp_path, script, release={"on_missing_data": on_missing_data})
    findings = getattr(evaluation.diagnostics, severity)
    assert _codes(findings) == ["release_gate_missing_data"]
    assert str(failure) in findings[0].message


# ---------------------------------------------------------------------------
# Image gate
# ---------------------------------------------------------------------------


def test_images_are_read_from_container_purls(tmp_path):
    _sbom(
        tmp_path,
        "pkg:docker/ghcr.io/huybrechtsxyz/api@1.4.2",
        "pkg:docker/traefik@v3.0.1",
        "pkg:docker/registry:5000/img@latest",
        "pkg:docker/postgres@sha256:abc123",
        "pkg:docker/redis",
        "pkg:docker/ghcr.io/huybrechtsxyz/api@1.4.2",
    )
    images = images_from_sbom(tmp_path)
    assert [(i.registry, i.repository, i.tag, i.digest) for i in images] == [
        ("ghcr.io", "huybrechtsxyz/api", "1.4.2", None),
        (None, "traefik", "v3.0.1", None),
        ("registry:5000", "img", "latest", None),
        (None, "postgres", None, "sha256:abc123"),
        (None, "redis", None, None),
    ]
    assert images[0].service == "svc0"


def test_no_sbom_is_none_and_an_unreadable_one_raises(tmp_path):
    assert images_from_sbom(tmp_path) is None
    (tmp_path / "sbom.json").write_text("{not json")
    with pytest.raises(ValueError):
        images_from_sbom(tmp_path)


def _images(tmp_path, script, image=None, *, cve_allowed=None, **kwargs):
    context = _context(tmp_path, _IMAGE_DOCS, cve_allowed=cve_allowed)
    return evaluate_security_gates(
        context, "app", _workspace(image=image or {}), tmp_path, resolved=_resolved(), **kwargs
    )


def _issue(n, severity="HIGH", cve=None, package=None) -> SecurityIssue:
    return SecurityIssue(id=str(n), severity=severity, status="open", cve_id=cve, package=package)


def test_severities_at_or_above_the_threshold_are_asked_for(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    _images(tmp_path, script, {"max_severity": "MEDIUM"})
    assert script.lookups_asked[0][1] == frozenset({"CRITICAL", "HIGH", "MEDIUM"})


@pytest.mark.parametrize(("count", "ok"), [(2, True), (3, False)])
def test_max_count_boundary(tmp_path, script, count, ok):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    script.lookups["org/api"] = ImageLookup(
        status="found", platform_name="org/api", issues=tuple(_issue(n) for n in range(count))
    )
    evaluation = _images(tmp_path, script, {"max_count": 2})
    assert evaluation.diagnostics.ok is ok
    assert ("image_gate_passed" if ok else "image_gate_violation") in _codes(evaluation.diagnostics)


def test_counts_add_up_across_images_and_unknown_severity_never_counts(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0", "pkg:docker/org/worker@1.0")
    script.lookups["org/api"] = ImageLookup(status="found", issues=(_issue(1, cve="CVE-1", package="xz"),))
    script.lookups["org/worker"] = ImageLookup(status="found", issues=(_issue(2), _issue(3, severity="UNKNOWN")))
    evaluation = _images(tmp_path, script)
    violation = evaluation.diagnostics.errors[0]
    assert violation.code == "image_gate_violation"
    assert "2 open issue(s)" in violation.message
    assert "org/api:1.0 (1), org/worker:1.0 (1)" in violation.message
    assert "HIGH CVE-1 in xz" in violation.message


def test_the_allowlist_suppresses_only_active_matching_entries(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    yesterday, today = (date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()
    script.lookups["org/api"] = ImageLookup(
        status="found",
        issues=(
            _issue(1, cve="CVE-ACTIVE"),
            _issue(2, cve="CVE-EXPIRED"),
            _issue(3, cve="CVE-PKG", package="openssl"),
            _issue(4, cve="CVE-PKG", package="libwebp"),
            _issue(5, cve="CVE-TODAY"),
            _issue(6),
        ),
    )
    allowed = [
        CveAllowedEntryModel(id="CVE-ACTIVE", reason="accepted"),
        CveAllowedEntryModel(id="CVE-EXPIRED", reason="old", expires=yesterday),
        CveAllowedEntryModel(id="CVE-PKG", reason="scoped", package="openssl"),
        CveAllowedEntryModel(id="CVE-TODAY", reason="last day", expires=today),
    ]
    evaluation = _images(tmp_path, script, {"max_count": 0, "max_severity": "HIGH"}, cve_allowed=allowed)
    violation = evaluation.diagnostics.errors[0].message
    assert "3 open issue(s)" in violation
    assert "CVE-EXPIRED" in violation and "CVE-PKG in libwebp" in violation and "HIGH 6 in ?" in violation
    assert "CVE-ACTIVE" not in violation and "openssl" not in violation and "CVE-TODAY" not in violation


@pytest.mark.parametrize(
    ("on_unknown_image", "severity"), [("block", "errors"), ("warn", "warnings"), ("skip", "infos")]
)
def test_unknown_images_follow_on_unknown_image(tmp_path, script, on_unknown_image, severity):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    script.lookups["org/api"] = ImageLookup(status="unknown", platform_name="org/api")
    evaluation = _images(tmp_path, script, {"on_unknown_image": on_unknown_image})
    assert "image_unknown" in _codes(getattr(evaluation.diagnostics, severity))


def test_an_image_error_under_max_count_is_not_reported_as_passed(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    script.lookups["org/api"] = ImageLookup(status="unknown", platform_name="org/api")
    codes = _codes(_images(tmp_path, script, {"on_unknown_image": "block"}).diagnostics)
    assert "image_gate_counted" in codes and "image_gate_passed" not in codes


def test_an_ambiguous_image_is_an_error(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    script.lookups["org/api"] = ImageLookup(status="ambiguous", platform_name="org/api", detail="2 matches")
    evaluation = _images(tmp_path, script)
    assert _codes(evaluation.diagnostics.errors) == ["image_ambiguous"]


def test_no_sbom_is_missing_data_and_no_containers_passes(tmp_path, script):
    assert _codes(_images(tmp_path, script).diagnostics.errors) == ["image_gate_missing_data"]
    _sbom(tmp_path)
    evaluation = _images(tmp_path, script)
    assert evaluation.diagnostics.ok
    assert "image_gate_no_images" in _codes(evaluation.diagnostics)


def test_a_platform_failure_stops_the_image_gate_as_missing_data(tmp_path, script):
    _sbom(tmp_path, "pkg:docker/org/api@1.0", "pkg:docker/org/worker@1.0")
    script.lookups["org/api"] = IntegrationError("unreachable")
    evaluation = _images(tmp_path, script, {"on_missing_data": "warn"})
    assert _codes(evaluation.diagnostics.warnings) == ["image_gate_missing_data"]
    assert len(script.lookups_asked) == 1


# ---------------------------------------------------------------------------
# Both gates, `only`, and policy results (design D9)
# ---------------------------------------------------------------------------


def _both(tmp_path, script, release=None, image=None, **kwargs):
    context = _context(tmp_path, _RELEASE_DOCS + _IMAGE_DOCS)
    workspace = _workspace(release={"commit": COMMIT, **(release or {})}, image=image or {})
    return evaluate_security_gates(context, "app", workspace, tmp_path, resolved=_resolved(), **kwargs), workspace


def test_both_gates_run_even_when_the_first_fails(tmp_path, script):
    script.release = ReleaseGateOutcome(passed=False)
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    script.lookups["org/api"] = ImageLookup(status="found", issues=(_issue(1),))
    evaluation, workspace = _both(tmp_path, script)
    assert _codes(evaluation.diagnostics.errors) == ["release_gate_failed", "image_gate_violation"]

    results = evaluation.policy_results(workspace)
    assert [(r.policy_name, r.policy_type, r.phase, r.passed) for r in results] == [
        ("release_gate_policy", "fake-gate", "deploy", False),
        ("image_gate_policy", "fake-gate", "deploy", False),
    ]


def test_a_warn_breach_is_recorded_as_passed_with_violations(tmp_path, script):
    script.release = ReleaseGateOutcome(passed=False)
    _sbom(tmp_path)
    evaluation, workspace = _both(tmp_path, script, release={"enforcement": "warn"})
    release_result = evaluation.policy_results(workspace)[0]
    assert release_result.passed is True
    assert release_result.enforcement == "warn"
    assert any("FAILED" in violation for violation in release_result.violations)


@pytest.mark.parametrize(("only", "release_ran", "images_ran"), [("release", True, False), ("images", False, True)])
def test_only_runs_one_gate(tmp_path, script, only, release_ran, images_ran):
    _sbom(tmp_path, "pkg:docker/org/api@1.0")
    evaluation, workspace = _both(tmp_path, script, only=only)
    assert bool(script.requests) is release_ran
    assert bool(script.lookups_asked) is images_ran
    assert len(evaluation.policy_results(workspace)) == 1


def test_no_policy_constructs_nothing(tmp_path, script):
    evaluation = evaluate_security_gates(_context(tmp_path, []), "app", _workspace(), tmp_path, resolved=_resolved())
    assert evaluation.release is None and evaluation.images is None
    assert script.constructed == []
