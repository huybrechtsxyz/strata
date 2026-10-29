#!/usr/bin/env python3
"""Tests for the Aikido integration (docs/design/security-gates.md D11; plan Phases 3 and 4).

Offline only: `strata.integrations.aikido.http_request` is replaced by a scripted fake, and
`sleep`/`clock` are injected. Fixtures are Appendix B of docs/archive/aikido-api-reference.md.
"""

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from strata.integrations import aikido as aikido_module
from strata.integrations.aikido import AikidoIntegration
from strata.integrations.errors import (
    GateNotFoundError,
    GateTimeoutError,
    IntegrationCredentialError,
    IntegrationError,
)
from strata.integrations.security_findings import ImageRef, ReleaseGateRequest
from strata.models.integration_model import IntegrationModel
from strata.utils.transport import NO_RESPONSE, HttpResult
from strata.utils.version import get_version

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "aikido"
CI_TOKEN = "AIK_CI_secret-token-value-1234567890"
SCAN_URL = "https://app.aikido.dev/api/integrations/continuous_integration/scan/repository"
COMMIT = "1c5a7dd" + "0" * 33


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _ok(name: str) -> HttpResult:
    return HttpResult(status=200, body=_fixture(name))


class _FakeHttp:
    """Answers each request with the next scripted result and records what was sent."""

    def __init__(self, *results: HttpResult) -> None:
        self.results = list(results)
        self.calls: list[SimpleNamespace] = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=30):
        self.calls.append(SimpleNamespace(method=method, url=url, headers=dict(headers or {}), body=body))
        if not self.results:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.results.pop(0)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _document(
    *,
    capabilities=("release_gate",),
    authentication=None,
    configuration=None,
    endpoints=None,
) -> IntegrationModel:
    spec: dict = {"type": "aikido", "capabilities": list(capabilities)}
    if authentication is not None:
        spec["authentication"] = authentication
    if configuration is not None:
        spec["configuration"] = configuration
    if endpoints is not None:
        spec["endpoints"] = {"address": endpoints}
    return IntegrationModel.model_validate({"meta": {"name": "aikido-ci"}, "spec": spec})


_API_KEY = {"method": "api_key", "api_key": {"api_key": "AIKIDO_CI_TOKEN"}}


def _integration(document: IntegrationModel | None, clock: _Clock | None = None, **values) -> AikidoIntegration:
    clock = clock or _Clock()
    integration = AikidoIntegration(document, sleep=clock.sleep, clock=clock)
    integration.bind_credentials(values or {"AIKIDO_CI_TOKEN": CI_TOKEN})
    return integration


def _request(**overrides) -> ReleaseGateRequest:
    return ReleaseGateRequest(
        **{
            "repository": "huybrechtsxyz/strata-infra",
            "commit": COMMIT,
            "timeout": 900,
            "poll_interval": 10,
            **overrides,
        }
    )


@pytest.fixture
def http(monkeypatch):
    def install(*results: HttpResult) -> _FakeHttp:
        fake = _FakeHttp(*results)
        monkeypatch.setattr(aikido_module, "http_request", fake)
        return fake

    return install


@pytest.fixture(autouse=True)
def _no_aikido_env(monkeypatch):
    for name in ("AIKIDO_CI_TOKEN", "AIKIDO_API_KEY", "AIKIDO_CLIENT_ID", "AIKIDO_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Capabilities and construction
# ---------------------------------------------------------------------------


def test_declares_both_gate_capabilities_and_constructs_without_a_document():
    from strata.integrations.capabilities import find_capability_mismatches

    assert find_capability_mismatches(AikidoIntegration) == []
    assert AikidoIntegration().name == "aikido"


def test_both_reference_documents_construct():
    """The two documents of design section 5.1, minus credentials values."""
    AikidoIntegration(
        _document(
            authentication=_API_KEY,
            configuration={
                "region": "eu",
                "http_timeout": 30,
                "max_retries": 3,
                "release_gate": {"minimum_severity": "high", "fail_on": ["dependency", "iac"], "base_branch": "main"},
            },
        )
    )
    AikidoIntegration(
        _document(
            capabilities=("image_findings",),
            authentication={"method": "oauth2", "oauth2": {"client_id": "ID", "client_secret": "SECRET"}},
            configuration={"images": {"name_strategy": "full", "names": {"ghcr.io/org/api": "org/api"}}},
        )
    )


@pytest.mark.parametrize(
    ("configuration", "fragment"),
    [
        ({"regoin": "eu"}, "regoin"),
        ({"region": "mars"}, "configuration.region"),
        ({"http_timeout": 0}, "configuration.http_timeout"),
        ({"http_timeout": True}, "configuration.http_timeout"),
        ({"max_retries": 11}, "configuration.max_retries"),
        ({"release_gate": {"minimum_severity": "HIGH"}}, "minimum_severity"),
        ({"release_gate": {"fail_on": ["iac", "docs"]}}, "fail_on"),
        ({"release_gate": {"fail_on": ["iac", "iac"]}}, "duplicates"),
        ({"release_gate": {"sla_mode": "yes"}}, "sla_mode"),
        ({"release_gate": {"base_branch": ""}}, "base_branch"),
        ({"release_gate": {"unknown": 1}}, "configuration.release_gate"),
        ({"images": {"name_strategy": "repository"}}, "only allowed on a document declaring 'image_findings'"),
    ],
)
def test_invalid_configuration_is_rejected_naming_the_key(configuration, fragment):
    with pytest.raises(IntegrationError, match="integration_configuration_invalid") as raised:
        AikidoIntegration(_document(configuration=configuration))
    assert fragment in str(raised.value)


@pytest.mark.parametrize(
    ("capabilities", "authentication", "fragment"),
    [
        (("image_findings",), _API_KEY, "api_key (the CI token) serves release_gate only"),
        (
            ("release_gate",),
            {"method": "oauth2", "oauth2": {"client_id": "ID", "client_secret": "SECRET"}},
            "oauth2 (client credentials) serves image_findings only",
        ),
        (("release_gate",), {"method": "token", "token": {"token": "T"}}, "not supported"),
        (
            ("release_gate",),
            {"method": "api_key", "api_key": {"api_key": "K", "header_name": "H"}},
            "header_name",
        ),
        (
            ("image_findings",),
            {"method": "oauth2", "oauth2": {"client_id": "ID", "client_secret": "S", "scope": "X"}},
            "scope",
        ),
    ],
)
def test_authentication_aikido_cannot_serve_is_rejected(capabilities, authentication, fragment):
    with pytest.raises(IntegrationError, match="integration_configuration_invalid") as raised:
        AikidoIntegration(_document(capabilities=capabilities, authentication=authentication))
    assert fragment in str(raised.value)


# ---------------------------------------------------------------------------
# Credentials and host
# ---------------------------------------------------------------------------


def test_ci_token_comes_from_the_bound_key_reference(http):
    fake = http(_ok("ci_start_ok.json"), _ok("ci_poll_release_passed.json"))
    _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert fake.calls[0].headers["X-AIK-API-SECRET"] == CI_TOKEN


@pytest.mark.parametrize("variable", ["AIKIDO_CI_TOKEN", "AIKIDO_API_KEY"])
def test_without_authentication_the_ci_token_comes_from_the_environment(http, monkeypatch, variable):
    monkeypatch.setenv(variable, "AIK_CI_from-env")
    fake = http(_ok("ci_start_ok.json"), _ok("ci_poll_release_passed.json"))
    _integration(_document()).run_release_gate(_request())
    assert fake.calls[0].headers["X-AIK-API-SECRET"] == "AIK_CI_from-env"


def test_missing_ci_token_raises_naming_the_key_and_never_calls_aikido(http):
    fake = http()
    integration = _integration(_document(authentication=_API_KEY), OTHER_KEY="x")
    with pytest.raises(IntegrationError, match="no Aikido CI token .*AIKIDO_CI_TOKEN"):
        integration.run_release_gate(_request())
    assert fake.calls == []


@pytest.mark.parametrize(
    ("configuration", "endpoints", "token", "host"),
    [
        (None, "https://aikido.internal/", CI_TOKEN, "https://aikido.internal"),
        ({"region": "us"}, None, "AIK_CI_AU_x", "https://app.us.aikido.dev"),
        (None, None, "AIK_CI_US_x", "https://app.us.aikido.dev"),
        (None, None, "AIK_CI_ME_x", "https://app.me.aikido.dev"),
        (None, None, "AIK_CI_AU_x", "https://app.au.aikido.dev"),
        (None, None, "AIK_CI_abc", "https://app.aikido.dev"),
    ],
)
def test_host_precedence(http, configuration, endpoints, token, host):
    fake = http(_ok("ci_start_ok.json"), _ok("ci_poll_release_passed.json"))
    document = _document(authentication=_API_KEY, configuration=configuration, endpoints=endpoints)
    _integration(document, AIKIDO_CI_TOKEN=token).run_release_gate(_request())
    assert fake.calls[0].url == f"{host}/api/integrations/continuous_integration/scan/repository"


# ---------------------------------------------------------------------------
# Release gate: request, polling, outcome
# ---------------------------------------------------------------------------


def test_start_request_sends_every_flag_explicitly(http):
    fake = http(_ok("ci_start_ok.json"), _ok("ci_poll_release_passed.json"))
    _integration(_document(authentication=_API_KEY)).run_release_gate(_request())

    start = fake.calls[0]
    assert (start.method, start.url) == ("POST", SCAN_URL)
    assert start.headers["Content-Type"] == "application/json"
    assert json.loads(start.body) == {
        "version": f"strata-{get_version()}",
        "is_release_gating": True,
        "repository_id": "huybrechtsxyz/strata-infra",
        "head_commit_id": COMMIT,
        "fail_on_dependency_scan": True,
        "fail_on_sast_scan": False,
        "fail_on_iac_scan": True,
        "fail_on_secrets_scan": True,
        "fail_on_malware_scan": False,
        "minimum_severity": "high",
    }


def test_start_request_carries_sla_mode_and_base_branch_only_when_set(http):
    fake = http(_ok("ci_start_ok.json"), _ok("ci_poll_release_passed.json"))
    document = _document(
        authentication=_API_KEY,
        configuration={"release_gate": {"sla_mode": True, "base_branch": "main", "minimum_severity": "critical"}},
    )
    _integration(document).run_release_gate(_request())
    payload = json.loads(fake.calls[0].body)
    assert payload["sla_mode"] is True
    assert payload["base_branch"] == "main"
    assert payload["minimum_severity"] == "critical"


def test_polls_until_complete_then_reports_the_verdict(http):
    fake = http(
        _ok("ci_start_ok.json"),
        _ok("ci_poll_running.json"),
        _ok("ci_poll_running.json"),
        _ok("ci_poll_release_passed.json"),
    )
    clock, polls = _Clock(), []
    outcome = _integration(_document(authentication=_API_KEY), clock).run_release_gate(_request(), on_poll=polls.append)

    assert outcome.passed is True
    assert outcome.reference == "22"
    assert outcome.open_issues == 0
    assert clock.sleeps == [10, 10]
    assert polls == [1, 2]
    assert [call.url for call in fake.calls[1:]] == [f"{SCAN_URL}?scan_id=22"] * 3


def test_failed_gate_maps_counts_links_and_breakdown(http):
    http(_ok("ci_start_ok.json"), _ok("ci_poll_release_failed.json"))
    outcome = _integration(_document(authentication=_API_KEY)).run_release_gate(_request())

    assert outcome.passed is False
    assert outcome.open_issues == 2
    assert outcome.links == (
        "https://app.aikido.dev/queue?sidebarIssue=1",
        "https://app.aikido.dev/queue?sidebarIssue=2",
    )
    assert outcome.breakdown["sca"]["critical"] == 1
    assert outcome.breakdown["secrets"]["critical"] == 1


def test_a_status_without_all_scans_completed_keeps_polling(http):
    http(
        _ok("ci_start_ok.json"),
        HttpResult(status=200, body='{"gate_passed": true}'),
        _ok("ci_poll_release_passed.json"),
    )
    outcome = _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert outcome.passed is True


def test_gate_passed_must_be_literally_true(http):
    body = json.dumps({"all_scans_completed": True, "gate_passed": "true"})
    http(_ok("ci_start_ok.json"), HttpResult(status=200, body=body))
    assert _integration(_document(authentication=_API_KEY)).run_release_gate(_request()).passed is False


def test_timeout_raises_with_the_scan_id(http):
    http(_ok("ci_start_ok.json"), *[_ok("ci_poll_running.json")] * 4)
    integration = _integration(_document(authentication=_API_KEY))
    with pytest.raises(GateTimeoutError, match="scan 22 did not finish within 30s"):
        integration.run_release_gate(_request(timeout=30, poll_interval=10))


def test_unknown_repository_or_commit_raises_gate_not_found_with_the_reason(http):
    http(HttpResult(status=404, body=_fixture("ci_start_404.json")))
    with pytest.raises(GateNotFoundError) as raised:
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert raised.value.reason == "Repository not found or commit does not exist"


def test_a_404_without_a_body_still_raises_gate_not_found(http):
    http(HttpResult(status=404, body=""))
    with pytest.raises(GateNotFoundError) as raised:
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert raised.value.reason is None


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '{"scan_id": "22"}', '{"scan_id": true}', "{}"])
def test_unusable_start_response_raises(http, body):
    http(HttpResult(status=200, body=body))
    with pytest.raises(IntegrationError, match="unparseable scan start"):
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())


def test_unparseable_poll_response_raises(http):
    http(_ok("ci_start_ok.json"), HttpResult(status=200, body="<html>"))
    with pytest.raises(IntegrationError, match="unparseable scan status"):
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_server_errors_are_retried_with_backoff(http):
    http(
        HttpResult(status=503, body="busy"),
        HttpResult(status=503, body="busy"),
        _ok("ci_start_ok.json"),
        _ok("ci_poll_release_passed.json"),
    )
    clock = _Clock()
    assert _integration(_document(authentication=_API_KEY), clock).run_release_gate(_request()).passed
    assert clock.sleeps == [1, 2]


@pytest.mark.parametrize(("retry_after", "expected"), [("7", 7), ("999", 60), ("soon", 1)])
def test_rate_limit_honours_retry_after(http, retry_after, expected):
    http(
        HttpResult(status=429, body="", headers={"Retry-After": retry_after}),
        _ok("ci_start_ok.json"),
        _ok("ci_poll_release_passed.json"),
    )
    clock = _Clock()
    _integration(_document(authentication=_API_KEY), clock).run_release_gate(_request())
    assert clock.sleeps == [expected]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_are_not_retried(http, status):
    fake = http(HttpResult(status=status, body="nope"))
    with pytest.raises(IntegrationError, match=f"HTTP {status}"):
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert len(fake.calls) == 1


def test_auth_failures_hint_at_the_credential(http):
    http(HttpResult(status=401, body="unauthorized"))
    with pytest.raises(IntegrationError, match="Check the credential and its scopes"):
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())


def test_retries_are_exhausted_after_max_retries(http):
    fake = http(*[HttpResult(status=502, body="bad gateway")] * 3)
    clock = _Clock()
    document = _document(authentication=_API_KEY, configuration={"max_retries": 2})
    with pytest.raises(IntegrationError, match="HTTP 502"):
        _integration(document, clock).run_release_gate(_request())
    assert len(fake.calls) == 3
    assert clock.sleeps == [1, 2]


def test_no_response_is_retried_then_reported_unreachable(http):
    http(*[HttpResult(status=NO_RESPONSE, body="connection refused")] * 4)
    with pytest.raises(IntegrationError, match="Aikido unreachable"):
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())


# ---------------------------------------------------------------------------
# Secrets never leak into errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "results",
    [
        (HttpResult(status=401, body="denied"),),
        (HttpResult(status=404, body=""),),
        (HttpResult(status=200, body="garbage"),),
        (_ok("ci_start_ok.json"), HttpResult(status=500, body="boom"), *[HttpResult(status=500, body="boom")] * 3),
        (HttpResult(status=NO_RESPONSE, body="down"),) * 4,
    ],
)
def test_no_error_message_contains_the_ci_token(http, results):
    http(*results)
    with pytest.raises(IntegrationError) as raised:
        _integration(_document(authentication=_API_KEY)).run_release_gate(_request())
    assert CI_TOKEN not in str(raised.value)


# ---------------------------------------------------------------------------
# image_findings: OAuth (Phase 4, reference Appendix A.2)
# ---------------------------------------------------------------------------

CLIENT_ID = "aik_client_id"
CLIENT_SECRET = "client-secret-value-1234567890"
ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiJ9.test.sig"
BASIC = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
_OAUTH = {"method": "oauth2", "oauth2": {"client_id": "AIKIDO_CLIENT_ID", "client_secret": "AIKIDO_CLIENT_SECRET"}}
API_IMAGE = ImageRef(service="api", registry="ghcr.io", repository="huybrechtsxyz/api", tag="1.4.2")
HIGH_AND_UP = frozenset({"CRITICAL", "HIGH"})


def _image_document(images=None, authentication=_OAUTH) -> IntegrationModel:
    return _document(
        capabilities=("image_findings",),
        authentication=authentication,
        configuration={"images": images} if images is not None else None,
    )


def _image_integration(images=None, clock: _Clock | None = None) -> AikidoIntegration:
    return _integration(_image_document(images), clock, AIKIDO_CLIENT_ID=CLIENT_ID, AIKIDO_CLIENT_SECRET=CLIENT_SECRET)


def _json(status: int, body) -> HttpResult:
    return HttpResult(status=status, body=json.dumps(body))


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def test_token_request_uses_basic_auth_and_a_json_body(http):
    fake = http(_ok("oauth_token_ok.json"), _ok("containers_api.json"), _ok("issues_export_api.json"))
    _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)

    token = fake.calls[0]
    assert (token.method, token.url) == ("POST", "https://app.aikido.dev/api/oauth/token")
    assert token.headers["Authorization"] == f"Basic {BASIC}"
    assert json.loads(token.body) == {"grant_type": "client_credentials"}
    assert fake.calls[1].headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


def test_token_is_cached_until_shortly_before_it_expires(http):
    fake = http(
        _ok("oauth_token_ok.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
        _ok("oauth_token_ok.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
    )
    clock = _Clock()
    integration = _image_integration(clock=clock)
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    clock.now += 3600 - 60
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)

    token_calls = [call for call in fake.calls if call.url.endswith("/api/oauth/token")]
    assert len(token_calls) == 2


def test_a_refused_json_token_request_falls_back_to_form_and_remembers_it(http):
    fake = http(
        HttpResult(status=415, body="unsupported"),
        _ok("oauth_token_ok.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
        _ok("oauth_token_ok.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
    )
    clock = _Clock()
    integration = _image_integration(clock=clock)
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    clock.now += 4000
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)

    token_calls = [call for call in fake.calls if call.url.endswith("/api/oauth/token")]
    assert [call.headers["Content-Type"] for call in token_calls] == [
        "application/json",
        "application/x-www-form-urlencoded",
        "application/x-www-form-urlencoded",
    ]
    assert token_calls[1].body == b"grant_type=client_credentials"


def test_missing_access_token_raises(http):
    http(_json(200, {"token_type": "bearer"}))
    with pytest.raises(IntegrationError, match="no access_token"):
        _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)


def test_missing_expires_in_defaults_to_five_minutes(http):
    fake = http(
        _json(200, {"access_token": ACCESS_TOKEN}),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
        _json(200, {"access_token": ACCESS_TOKEN}),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
    )
    clock = _Clock()
    integration = _image_integration(clock=clock)
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    clock.now += 300 - 60
    integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert len([call for call in fake.calls if call.url.endswith("/api/oauth/token")]) == 2


def test_a_rest_401_gets_one_fresh_token_and_one_retry(http):
    fake = http(
        _ok("oauth_token_ok.json"),
        HttpResult(status=401, body="expired"),
        _ok("oauth_token_ok.json"),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
    )
    lookup = _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert lookup.status == "found"
    assert len([call for call in fake.calls if call.url.endswith("/api/oauth/token")]) == 2


def test_a_second_rest_401_raises(http):
    http(
        _ok("oauth_token_ok.json"),
        HttpResult(status=401, body="expired"),
        _ok("oauth_token_ok.json"),
        HttpResult(status=401, body="still no"),
    )
    with pytest.raises(IntegrationError, match="HTTP 401"):
        _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)


def test_missing_client_credentials_raise_naming_the_keys(http):
    fake = http()
    integration = _integration(_image_document(), OTHER="x")
    with pytest.raises(IntegrationError, match="no Aikido API client credentials .*AIKIDO_CLIENT_ID"):
        integration.find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert fake.calls == []


def test_without_authentication_client_credentials_come_from_the_environment(http, monkeypatch):
    monkeypatch.setenv("AIKIDO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AIKIDO_CLIENT_SECRET", CLIENT_SECRET)
    fake = http(_ok("oauth_token_ok.json"), _ok("containers_api.json"), _ok("issues_export_api.json"))
    _integration(_image_document(authentication=None)).find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert fake.calls[0].headers["Authorization"] == f"Basic {BASIC}"


# ---------------------------------------------------------------------------
# image_findings: containers lookup and naming (reference Appendix A.4.2, R3)
# ---------------------------------------------------------------------------


def test_containers_lookup_query(http):
    fake = http(_ok("oauth_token_ok.json"), _ok("containers_api.json"), _ok("issues_export_api.json"))
    _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)

    lookup = fake.calls[1]
    assert lookup.url.startswith("https://app.aikido.dev/api/public/v1/containers?")
    assert _query(lookup.url) == {
        "filter_name": "huybrechtsxyz/api",
        "filter_status": "active",
        "per_page": "100",
        "page": "0",
    }


def test_containers_accept_a_wrapped_list(http):
    http(
        _ok("oauth_token_ok.json"),
        _json(200, {"containers": [{"id": 101, "name": "huybrechtsxyz/api", "tag": "1.4.2"}]}),
        _ok("issues_export_api.json"),
    )
    assert _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP).status == "found"


def test_an_unrecognised_list_shape_raises(http):
    http(_ok("oauth_token_ok.json"), _json(200, {"unexpected": True}))
    with pytest.raises(IntegrationError, match="unexpected .* response shape"):
        _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)


def test_containers_page_until_a_short_page(http):
    full_page = [{"id": n, "name": f"other/{n}", "tag": "1"} for n in range(100)]
    fake = http(
        _ok("oauth_token_ok.json"),
        _json(200, full_page),
        _ok("containers_api.json"),
        _ok("issues_export_api.json"),
    )
    assert _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP).status == "found"
    assert [_query(call.url)["page"] for call in fake.calls[1:3]] == ["0", "1"]


def test_containers_paging_is_capped(http):
    full_page = [{"id": n, "name": f"other/{n}", "tag": "1"} for n in range(100)]
    fake = http(_ok("oauth_token_ok.json"), *[_json(200, full_page)] * 10)
    assert _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP).status == "unknown"
    assert len(fake.calls) == 11


@pytest.mark.parametrize(
    ("image", "images", "expected"),
    [
        (API_IMAGE, None, "huybrechtsxyz/api"),
        (API_IMAGE, {"name_strategy": "full"}, "ghcr.io/huybrechtsxyz/api"),
        (ImageRef(service="proxy", registry=None, repository="traefik", tag="v3.0.1"), None, "traefik"),
        (API_IMAGE, {"names": {"ghcr.io/huybrechtsxyz/api": "team/api-renamed"}}, "team/api-renamed"),
    ],
)
def test_image_naming(http, image, images, expected):
    fake = http(_ok("oauth_token_ok.json"), _json(200, []))
    lookup = _image_integration(images).find_image_issues(image, HIGH_AND_UP)
    assert _query(fake.calls[1].url)["filter_name"] == expected
    assert (lookup.status, lookup.platform_name) == ("unknown", expected)


def test_name_match_is_exact_and_case_insensitive(http):
    """filter_name is a search; huybrechtsxyz/api-gateway must not match huybrechtsxyz/api."""
    http(
        _ok("oauth_token_ok.json"),
        _json(200, [{"id": 102, "name": "huybrechtsxyz/api-gateway"}, {"id": 101, "name": "HuybrechtsXYZ/API"}]),
        _ok("issues_export_api.json"),
    )
    lookup = _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert lookup.status == "found"


def test_several_matches_prefer_the_deployed_tag_else_are_ambiguous(http):
    two = [
        {"id": 101, "name": "huybrechtsxyz/api", "tag": "1.4.2"},
        {"id": 103, "name": "huybrechtsxyz/api", "tag": "1.3.0"},
    ]
    fake = http(_ok("oauth_token_ok.json"), _json(200, two), _ok("issues_export_api.json"))
    assert _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP).status == "found"
    assert _query(fake.calls[2].url)["filter_container_repo_id"] == "101"

    other_tags = [{**two[0], "tag": "2.0.0"}, two[1]]
    http(_ok("oauth_token_ok.json"), _json(200, other_tags))
    lookup = _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert lookup.status == "ambiguous"
    assert "1.3.0, 2.0.0" in lookup.detail


def test_require_tag_match_filters_by_tag(http):
    fake = http(_ok("oauth_token_ok.json"), _json(200, [{"id": 101, "name": "huybrechtsxyz/api", "tag": "1.4.1"}]))
    lookup = _image_integration({"require_tag_match": True}).find_image_issues(API_IMAGE, HIGH_AND_UP)
    assert _query(fake.calls[1].url)["filter_tag"] == "1.4.2"
    assert lookup.status == "unknown"


def test_require_tag_match_cannot_match_a_digest_pinned_image(http):
    fake = http()
    digest_only = ImageRef(service="db", registry=None, repository="postgres", digest="sha256:abc123")
    lookup = _image_integration({"require_tag_match": True}).find_image_issues(digest_only, HIGH_AND_UP)
    assert lookup.status == "unknown"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# image_findings: issue export (reference Appendix A.4.3)
# ---------------------------------------------------------------------------


def test_issue_export_query_and_mapping(http):
    fake = http(_ok("oauth_token_ok.json"), _ok("containers_api.json"), _ok("issues_export_api.json"))
    lookup = _image_integration().find_image_issues(API_IMAGE, frozenset({"HIGH", "CRITICAL", "UNKNOWN"}))

    export = fake.calls[2]
    assert export.url.startswith("https://app.aikido.dev/api/public/v1/issues/export?")
    assert _query(export.url) == {
        "filter_container_repo_id": "101",
        "filter_status": "open",
        "filter_severities": "critical,high",
        "format": "json",
        "per_page": "1000",
        "page": "0",
    }
    assert lookup.platform_name == "huybrechtsxyz/api"
    assert [(i.id, i.severity, i.cve_id, i.package) for i in lookup.issues] == [
        ("9001", "CRITICAL", "CVE-2024-3094", "xz-utils"),
        ("9002", "HIGH", "CVE-2023-4863", "libwebp"),
    ]


def test_issue_mapping_is_defensive(http):
    raw = [
        {"id": 1, "severity": "informational", "status": "open"},
        {"severity": "critical", "status": "open"},
        {"id": "", "severity": "critical"},
        {"id": 2, "severity": "LOW"},
        "not an object",
    ]
    http(_ok("oauth_token_ok.json"), _ok("containers_api.json"), _json(200, raw))
    lookup = _image_integration().find_image_issues(API_IMAGE, frozenset({"LOW"}))
    assert [(i.id, i.severity, i.status) for i in lookup.issues] == [("1", "UNKNOWN", "open"), ("2", "LOW", "open")]


def test_only_unknown_severity_asks_aikido_for_nothing(http):
    fake = http(_ok("oauth_token_ok.json"), _ok("containers_api.json"))
    lookup = _image_integration().find_image_issues(API_IMAGE, frozenset({"UNKNOWN"}))
    assert (lookup.status, lookup.issues) == ("found", ())
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# check_connection (`strata integration check`)
# ---------------------------------------------------------------------------


def test_check_connection_probes_the_ci_token(http):
    fake = http(HttpResult(status=404, body="no such scan"))
    lines = _integration(_document(authentication=_API_KEY)).check_connection()
    assert lines == ["release_gate: CI token accepted by https://app.aikido.dev"]
    assert _query(fake.calls[0].url) == {"scan_id": "0"}


@pytest.mark.parametrize("status", [401, 403])
def test_check_connection_reports_a_rejected_ci_token(http, status):
    http(HttpResult(status=status, body="denied"))
    with pytest.raises(IntegrationError, match=f"HTTP {status}"):
        _integration(_document(authentication=_API_KEY)).check_connection()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_check_connection_does_not_accept_a_token_aikido_could_not_check(http, status):
    http(*[HttpResult(status=status, body="busy")] * 4)
    with pytest.raises(IntegrationError, match=f"HTTP {status}") as raised:
        _integration(_document(authentication=_API_KEY)).check_connection()
    assert not isinstance(raised.value, IntegrationCredentialError)


def test_check_connection_gets_an_oauth_token(http):
    http(_ok("oauth_token_ok.json"))
    assert _image_integration().check_connection() == [
        "image_findings: API client credentials accepted by https://app.aikido.dev"
    ]


def test_check_connection_with_nothing_bound_raises(http):
    http()
    with pytest.raises(IntegrationError, match="no Aikido CI token"):
        _integration(_document(authentication=_API_KEY), OTHER="x").check_connection()


def test_check_connection_with_no_capability_declared_raises():
    with pytest.raises(IntegrationError, match="declares neither"):
        _integration(_document(capabilities=())).check_connection()


# ---------------------------------------------------------------------------
# Registry and secrets
# ---------------------------------------------------------------------------


def test_aikido_is_registered():
    from strata.integrations.registry import get, get_class

    assert get_class("aikido") is AikidoIntegration
    assert isinstance(get("aikido"), AikidoIntegration)


@pytest.mark.parametrize(
    "results",
    [
        (HttpResult(status=401, body="denied"),),
        (_ok("oauth_token_ok.json"), HttpResult(status=403, body="forbidden")),
        (_ok("oauth_token_ok.json"), HttpResult(status=200, body="garbage")),
    ],
)
def test_no_error_message_contains_client_secrets(http, results):
    http(*results)
    with pytest.raises(IntegrationError) as raised:
        _image_integration().find_image_issues(API_IMAGE, HIGH_AND_UP)
    for secret in (CLIENT_SECRET, BASIC, ACCESS_TOKEN):
        assert secret not in str(raised.value)
