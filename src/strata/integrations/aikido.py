#!/usr/bin/env python3
"""Aikido Security: the first `release_gate` and `image_findings` integration.

docs/work/security-gates-design.md D11; Aikido's API is in docs/work/aikido-api-reference.md
Appendix A. Two Aikido APIs need two unrelated credentials, so a solution declares one Integration
document per credential (D2): `api_key` (the CI token) for `release_gate`, `oauth2` (client
credentials) for `image_findings`. Without an `authentication` block the class reads Aikido's
environment variables instead. Every vendor option lives in `spec.configuration` (D4) and is
validated here, at construction, so nothing is silently ignored.

Response shapes Aikido has not confirmed (reference section 13: OAuth body encoding, REST list
envelopes, container naming) are parsed tolerantly; Phase 7 of the implementation plan settles them.
"""

import base64
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, NoReturn
from urllib.parse import urlencode

from strata.integrations.capabilities import (
    ConnectionCheckIntegration,
    CredentialedIntegration,
    ImageFindingsIntegration,
    ReleaseGateIntegration,
)
from strata.integrations.errors import (
    GateNotFoundError,
    GateTimeoutError,
    IntegrationCredentialError,
    IntegrationError,
)
from strata.integrations.security_findings import (
    SECURITY_SEVERITY_ORDER,
    ImageLookup,
    ImageRef,
    ReleaseGateOutcome,
    ReleaseGateRequest,
    SecurityIssue,
)
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils.transport import NO_RESPONSE, HttpResult, http_request
from strata.utils.version import get_version

#: Aikido hosts per region (reference Appendix A.1). The same host serves the CI and REST APIs.
_HOSTS: dict[str, str] = {
    "eu": "https://app.aikido.dev",
    "us": "https://app.us.aikido.dev",
    "me": "https://app.me.aikido.dev",
    "au": "https://app.au.aikido.dev",
    "us-gov": "https://app.aikidogov.us",
}
#: A CI token's prefix names its region, as Aikido's own CI client reads it (Appendix A.1).
_TOKEN_PREFIX_REGIONS: tuple[tuple[str, str], ...] = (("AIK_CI_US_", "us"), ("AIK_CI_ME_", "me"), ("AIK_CI_AU_", "au"))

_SCAN_PATH = "/api/integrations/continuous_integration/scan/repository"
_FAIL_ON = ("dependency", "sast", "iac", "secrets", "malware")
_SEVERITIES = ("critical", "high", "medium", "low")
_DEFAULT_FAIL_ON = ("dependency", "iac", "secrets")

_CONFIGURATION_KEYS = frozenset({"region", "http_timeout", "max_retries", "release_gate", "images"})
_RELEASE_GATE_KEYS = frozenset({"minimum_severity", "fail_on", "sla_mode", "base_branch"})
_IMAGES_KEYS = frozenset({"name_strategy", "require_tag_match", "names"})
#: Authentication fields Aikido has no use for; set, they would be silently ignored, so they are rejected.
_UNUSED_AUTH_FIELDS: dict[str, tuple[str, ...]] = {
    "api_key": ("api_secret", "endpoint", "header_name"),
    "oauth2": ("tenant_id", "token_url", "authorization_url", "scope"),
}

_MAX_RETRY_AFTER = 60
_BODY_PREVIEW = 200

_CI_TOKEN_ENV_VARS = ("AIKIDO_CI_TOKEN", "AIKIDO_API_KEY")
_CLIENT_ID_ENV_VAR = "AIKIDO_CLIENT_ID"
_CLIENT_SECRET_ENV_VAR = "AIKIDO_CLIENT_SECRET"

_TOKEN_PATH = "/api/oauth/token"
_CONTAINERS_PATH = "/api/public/v1/containers"
_ISSUES_EXPORT_PATH = "/api/public/v1/issues/export"
#: Refresh this long before Aikido's stated expiry; and never cache for less than the floor.
_TOKEN_EXPIRY_MARGIN = 60
_TOKEN_MINIMUM_LIFETIME = 30
_DEFAULT_TOKEN_LIFETIME = 300
#: Paging (reference Appendix A.4): page size and a cap so a misbehaving API cannot loop forever.
_CONTAINERS_PAGE_SIZE = 100
_CONTAINERS_MAX_PAGES = 10
_ISSUES_PAGE_SIZE = 1000
_ISSUES_MAX_PAGES = 20
#: Keys a REST list may be wrapped under; the envelope is unconfirmed (reference R2).
_LIST_KEYS = ("containers", "issues", "repositories", "data", "items", "results")


@dataclass(frozen=True)
class _ReleaseGateSettings:
    minimum_severity: str = "high"
    fail_on: frozenset[str] = frozenset(_DEFAULT_FAIL_ON)
    sla_mode: bool = False
    base_branch: str | None = None


@dataclass(frozen=True)
class _ImageSettings:
    name_strategy: str = "repository"
    require_tag_match: bool = False
    names: Mapping[str, str] = field(default_factory=dict)


class AikidoIntegration(
    ReleaseGateIntegration, ImageFindingsIntegration, CredentialedIntegration, ConnectionCheckIntegration
):
    """Aikido's release gate (CI API) and image findings (REST API) behind strata's neutral capabilities."""

    TYPE: ClassVar[str] = "aikido"
    CAPABILITIES: ClassVar[frozenset[str]] = frozenset({Capability.RELEASE_GATE, Capability.IMAGE_FINDINGS})
    TRANSPORTS: ClassVar[frozenset[str]] = frozenset({"http"})
    COMMAND: ClassVar[str | None] = None

    def __init__(
        self,
        config: IntegrationModel | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            config: The Integration document; `None` means both capabilities from environment variables.
            sleep: Injected for tests; the registry passes only `config`.
            clock: Injected for tests.

        Raises:
            IntegrationError: `integration_configuration_invalid` - an unknown or invalid configuration key,
                or an authentication method, field, or capability combination Aikido cannot serve.
        """
        super().__init__(config)
        self._sleep = sleep
        self._clock = clock
        self._credentials: dict[str, str] = {}
        self._access_token_value: str | None = None
        self._access_token_expiry = 0.0
        self._oauth_form = False
        self._declared = frozenset(config.spec.capabilities) if config is not None else self.CAPABILITIES
        configuration: Mapping[str, Any] = (config.spec.configuration or {}) if config is not None else {}

        self._reject_unknown(configuration, _CONFIGURATION_KEYS, "configuration")
        region = configuration.get("region")
        if region is not None and region not in _HOSTS:
            self._invalid(f"configuration.region must be one of {', '.join(_HOSTS)}, not {region!r}")
        self._region: str | None = region
        self._http_timeout = self._int_option(configuration, "http_timeout", default=30, minimum=1, maximum=None)
        self._max_retries = self._int_option(configuration, "max_retries", default=3, minimum=0, maximum=10)
        self._release_gate = self._release_gate_settings(configuration.get("release_gate"))
        self._images = self._image_settings(configuration.get("images"))
        self._validate_authentication()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _invalid(self, detail: str) -> NoReturn:
        raise IntegrationError(f"integration_configuration_invalid: {self.name}: {detail}")

    def _reject_unknown(self, block: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
        unknown = sorted(set(block) - allowed)
        if unknown:
            self._invalid(f"unknown {where} key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(allowed))})")

    def _int_option(
        self, block: Mapping[str, Any], key: str, *, default: int, minimum: int, maximum: int | None
    ) -> int:
        value = block.get(key, default)
        too_big = maximum is not None and isinstance(value, int) and value > maximum
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or too_big:
            bounds = f"{minimum}-{maximum}" if maximum is not None else f">= {minimum}"
            self._invalid(f"configuration.{key} must be an integer {bounds}, not {value!r}")
        return int(value)

    def _block(self, raw: Any, key: str, allowed: frozenset[str], capability: str) -> Mapping[str, Any] | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            self._invalid(f"configuration.{key} must be a mapping")
        if capability not in self._declared:
            self._invalid(f"configuration.{key} is only allowed on a document declaring '{capability}'")
        self._reject_unknown(raw, allowed, f"configuration.{key}")
        return raw

    def _release_gate_settings(self, raw: Any) -> _ReleaseGateSettings:
        block = self._block(raw, "release_gate", _RELEASE_GATE_KEYS, Capability.RELEASE_GATE)
        if block is None:
            return _ReleaseGateSettings()
        severity = block.get("minimum_severity", "high")
        if severity not in _SEVERITIES:
            self._invalid(f"configuration.release_gate.minimum_severity must be one of {', '.join(_SEVERITIES)}")
        fail_on = block.get("fail_on", list(_DEFAULT_FAIL_ON))
        if not isinstance(fail_on, list) or any(item not in _FAIL_ON for item in fail_on):
            self._invalid(f"configuration.release_gate.fail_on must list only {', '.join(_FAIL_ON)}")
        if len(set(fail_on)) != len(fail_on):
            self._invalid("configuration.release_gate.fail_on has duplicates")
        sla_mode = block.get("sla_mode", False)
        if not isinstance(sla_mode, bool):
            self._invalid("configuration.release_gate.sla_mode must be true or false")
        base_branch = block.get("base_branch")
        if base_branch is not None and (not isinstance(base_branch, str) or not base_branch):
            self._invalid("configuration.release_gate.base_branch must be a non-empty string")
        return _ReleaseGateSettings(str(severity), frozenset(fail_on), sla_mode, base_branch)

    def _image_settings(self, raw: Any) -> _ImageSettings:
        block = self._block(raw, "images", _IMAGES_KEYS, Capability.IMAGE_FINDINGS)
        if block is None:
            return _ImageSettings()
        strategy = block.get("name_strategy", "repository")
        if strategy not in ("repository", "full"):
            self._invalid("configuration.images.name_strategy must be 'repository' or 'full'")
        require_tag_match = block.get("require_tag_match", False)
        if not isinstance(require_tag_match, bool):
            self._invalid("configuration.images.require_tag_match must be true or false")
        names = block.get("names", {})
        if not isinstance(names, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) and value for key, value in names.items()
        ):
            self._invalid("configuration.images.names must map image paths to Aikido container names")
        return _ImageSettings(str(strategy), require_tag_match, dict(names))

    def _validate_authentication(self) -> None:
        auth = self.config.spec.authentication if self.config is not None else None
        if auth is None:
            return
        if auth.method not in _UNUSED_AUTH_FIELDS:
            self._invalid(f"authentication method '{auth.method}' is not supported (use api_key or oauth2)")
        if auth.method == "api_key" and Capability.IMAGE_FINDINGS in self._declared:
            self._invalid("api_key (the CI token) serves release_gate only; declare image_findings with oauth2")
        if auth.method == "oauth2" and Capability.RELEASE_GATE in self._declared:
            self._invalid("oauth2 (client credentials) serves image_findings only; declare release_gate with api_key")
        block = getattr(auth, auth.method)
        unused = [name for name in _UNUSED_AUTH_FIELDS[auth.method] if getattr(block, name) is not None]
        if unused:
            self._invalid(f"authentication.{auth.method} field(s) {', '.join(unused)} are not used by Aikido")

    # ------------------------------------------------------------------
    # Credentials and transport
    # ------------------------------------------------------------------

    def bind_credentials(self, values: Mapping[str, str]) -> None:
        """Keep the resolved key-reference values; read lazily, so an unused credential is never required."""
        self._credentials = dict(values)

    def _ci_token(self) -> str:
        auth = self.config.spec.authentication if self.config is not None else None
        token: str | None
        if auth is None:
            token = next((os.environ[name] for name in _CI_TOKEN_ENV_VARS if os.environ.get(name)), None)
            source = " or ".join(_CI_TOKEN_ENV_VARS)
        elif auth.method == "api_key" and auth.api_key is not None:
            token = self._credentials.get(auth.api_key.api_key)
            source = f"key '{auth.api_key.api_key}'"
        else:
            token, source = None, f"authentication method '{auth.method}'"
        if not token:
            raise IntegrationCredentialError(f"{self.name}: no Aikido CI token (from {source}).")
        return token

    def _base_url(self, ci_token: str | None = None) -> str:
        endpoints = self.config.spec.endpoints if self.config is not None else None
        if endpoints is not None:
            return endpoints.address.rstrip("/")
        if self._region is not None:
            return _HOSTS[self._region]
        if ci_token is not None:
            for prefix, region in _TOKEN_PREFIX_REGIONS:
                if ci_token.startswith(prefix):
                    return _HOSTS[region]
        return _HOSTS["eu"]

    def _call(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
    ) -> HttpResult:
        """One request, retried on no response, 429 and 5xx; returns the final result without raising."""
        if params:
            url = f"{url}?{urlencode(params)}"
        attempt = 0
        while True:
            result = http_request(method, url, headers=headers, body=body, timeout=self._http_timeout)
            retryable = result.status in (NO_RESPONSE, 429) or result.status >= 500
            if not retryable or attempt >= self._max_retries:
                return result
            self._sleep(self._retry_delay(result, attempt))
            attempt += 1

    @staticmethod
    def _retry_delay(result: HttpResult, attempt: int) -> float:
        retry_after = next((value for key, value in result.headers.items() if key.lower() == "retry-after"), None)
        if retry_after is not None and retry_after.strip().isdigit():
            return float(min(int(retry_after), _MAX_RETRY_AFTER))
        return float(2**attempt)

    def _failure(self, result: HttpResult, method: str, path: str) -> IntegrationError:
        """An error for a final non-2xx result: truncated body, never a header (credentials live there)."""
        if result.status == NO_RESPONSE:
            return IntegrationError(f"{self.name}: {method} {path} - Aikido unreachable: {result.body[:_BODY_PREVIEW]}")
        message = f"{self.name}: {method} {path} - HTTP {result.status}: {result.body[:_BODY_PREVIEW]}."
        if result.status in (401, 403):
            return IntegrationCredentialError(f"{message} Check the credential and its scopes.")
        return IntegrationError(message)

    def _json_object(self, result: HttpResult, what: str) -> dict[str, Any]:
        try:
            body = result.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrationError(f"{self.name}: unparseable {what} response from Aikido ({error})") from None
        if not isinstance(body, dict):
            raise IntegrationError(f"{self.name}: unparseable {what} response from Aikido (not a JSON object)")
        return body

    # ------------------------------------------------------------------
    # release_gate (CI API, reference Appendix A.3)
    # ------------------------------------------------------------------

    def run_release_gate(
        self, request: ReleaseGateRequest, *, on_poll: Callable[[int], None] | None = None
    ) -> ReleaseGateOutcome:
        token = self._ci_token()
        url = f"{self._base_url(token)}{_SCAN_PATH}"
        headers = {"Content-Type": "application/json", "X-AIK-API-SECRET": token}
        scan_id = self._start_release_scan(request, url, headers)

        started = self._clock()
        attempt = 0
        while True:
            attempt += 1
            result = self._call("GET", url, headers=headers, params={"scan_id": scan_id})
            if not result.is_successful:
                raise self._failure(result, "GET", _SCAN_PATH)
            body = self._json_object(result, "scan status")
            if body.get("all_scans_completed") is True:
                return _outcome(scan_id, body)
            if self._clock() - started >= request.timeout:
                raise GateTimeoutError(
                    f"{self.name}: Aikido release scan {scan_id} did not finish within {request.timeout}s; "
                    "see the Aikido UI for its result."
                )
            if on_poll is not None:
                on_poll(attempt)
            self._sleep(request.poll_interval)

    def _start_release_scan(self, request: ReleaseGateRequest, url: str, headers: Mapping[str, str]) -> int:
        settings = self._release_gate
        payload: dict[str, Any] = {
            "version": f"strata-{get_version()}",
            "is_release_gating": True,
            "repository_id": request.repository,
            "head_commit_id": request.commit,
            **{f"fail_on_{category}_scan": category in settings.fail_on for category in _FAIL_ON},
            "minimum_severity": settings.minimum_severity,
        }
        if settings.sla_mode:
            payload["sla_mode"] = True
        if settings.base_branch is not None:
            payload["base_branch"] = settings.base_branch

        result = self._call("POST", url, headers=headers, body=json.dumps(payload).encode())
        if result.status == 404:
            reason = _reason_phrase(result)
            raise GateNotFoundError(
                f"{self.name}: Aikido does not know repository '{request.repository}' at commit {request.commit}"
                + (f": {reason}" if reason else "."),
                reason=reason,
            )
        if not result.is_successful:
            raise self._failure(result, "POST", _SCAN_PATH)
        scan_id = self._json_object(result, "scan start").get("scan_id")
        if isinstance(scan_id, bool) or not isinstance(scan_id, int):
            raise IntegrationError(f"{self.name}: unparseable scan start response from Aikido (no scan_id)")
        return scan_id

    # ------------------------------------------------------------------
    # image_findings (REST API, reference Appendix A.2 and A.4)
    # ------------------------------------------------------------------

    def _client_credentials(self) -> tuple[str, str]:
        auth = self.config.spec.authentication if self.config is not None else None
        if auth is None:
            client_id, client_secret = os.environ.get(_CLIENT_ID_ENV_VAR), os.environ.get(_CLIENT_SECRET_ENV_VAR)
            source = f"{_CLIENT_ID_ENV_VAR} / {_CLIENT_SECRET_ENV_VAR}"
        elif auth.method == "oauth2" and auth.oauth2 is not None:
            client_id = self._credentials.get(auth.oauth2.client_id)
            client_secret = self._credentials.get(auth.oauth2.client_secret)
            source = f"keys '{auth.oauth2.client_id}' / '{auth.oauth2.client_secret}'"
        else:
            client_id = client_secret = None
            source = f"authentication method '{auth.method}'"
        if not client_id or not client_secret:
            raise IntegrationCredentialError(f"{self.name}: no Aikido API client credentials (from {source}).")
        return client_id, client_secret

    def _access_token(self) -> str:
        """A cached OAuth access token; JSON body first, form-encoded if Aikido refuses JSON (reference R1)."""
        if self._access_token_value is not None and self._clock() < self._access_token_expiry:
            return self._access_token_value
        client_id, client_secret = self._client_credentials()
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        url = f"{self._base_url()}{_TOKEN_PATH}"

        result = self._request_token(url, basic, form=self._oauth_form)
        if not self._oauth_form and result.status in (400, 415):
            form_result = self._request_token(url, basic, form=True)
            if form_result.is_successful:
                self._oauth_form = True
            result = form_result
        if not result.is_successful:
            raise self._failure(result, "POST", _TOKEN_PATH)

        body = self._json_object(result, "OAuth token")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise IntegrationError(f"{self.name}: unparseable OAuth token response from Aikido (no access_token)")
        lifetime = _as_int(body.get("expires_in")) or _DEFAULT_TOKEN_LIFETIME
        self._access_token_value = token
        self._access_token_expiry = self._clock() + max(lifetime - _TOKEN_EXPIRY_MARGIN, _TOKEN_MINIMUM_LIFETIME)
        return token

    def _request_token(self, url: str, basic: str, *, form: bool) -> HttpResult:
        if form:
            content_type, body = "application/x-www-form-urlencoded", b"grant_type=client_credentials"
        else:
            content_type, body = "application/json", json.dumps({"grant_type": "client_credentials"}).encode()
        headers = {"Authorization": f"Basic {basic}", "Content-Type": content_type}
        return self._call("POST", url, headers=headers, body=body)

    def _rest_get(self, path: str, params: Mapping[str, Any]) -> Any:
        """GET a REST resource as JSON; a 401 gets one fresh token and one retry (an expired token is normal)."""
        url = f"{self._base_url()}{path}"
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
            result = self._call("GET", url, headers=headers, params=params)
            if result.status == 401 and attempt == 0:
                self._access_token_value = None
                continue
            if not result.is_successful:
                raise self._failure(result, "GET", path)
            try:
                return result.json()
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrationError(f"{self.name}: unparseable {path} response from Aikido ({error})") from None
        raise AssertionError("unreachable")

    def _items(self, body: Any, path: str) -> list[Any]:
        if isinstance(body, list):
            return body
        if isinstance(body, Mapping):
            for key in _LIST_KEYS:
                if isinstance(body.get(key), list):
                    return list(body[key])
        raise IntegrationError(f"{self.name}: unexpected {path} response shape from Aikido")

    def _paged(self, path: str, params: Mapping[str, Any], page_size: int, max_pages: int) -> list[Any]:
        items: list[Any] = []
        for page in range(max_pages):
            batch = self._items(self._rest_get(path, {**params, "per_page": page_size, "page": page}), path)
            items.extend(batch)
            if len(batch) < page_size:
                break
        return items

    def _aikido_name(self, image: ImageRef) -> str:
        override = self._images.names.get(image.path)
        if override is not None:
            return override
        return image.repository if self._images.name_strategy == "repository" else image.path

    def find_image_issues(self, image: ImageRef, severities: frozenset[str]) -> ImageLookup:
        name = self._aikido_name(image)
        if self._images.require_tag_match and image.tag is None:
            return ImageLookup(status="unknown", platform_name=name, detail="no tag to match (digest-pinned)")

        params: dict[str, Any] = {"filter_name": name, "filter_status": "active"}
        if self._images.require_tag_match:
            params["filter_tag"] = image.tag
        containers = [
            container
            for container in self._paged(_CONTAINERS_PATH, params, _CONTAINERS_PAGE_SIZE, _CONTAINERS_MAX_PAGES)
            if _container_name(container).lower() == name.lower()
            and (not self._images.require_tag_match or container.get("tag") == image.tag)
        ]
        if len(containers) > 1:
            same_tag = [container for container in containers if container.get("tag") == image.tag]
            containers = same_tag if len(same_tag) == 1 else containers
        if not containers:
            return ImageLookup(status="unknown", platform_name=name)
        if len(containers) > 1:
            tags = ", ".join(sorted(str(container.get("tag")) for container in containers))
            return ImageLookup(
                status="ambiguous", platform_name=name, detail=f"{len(containers)} matches (tags {tags})"
            )

        wanted = [severity.lower() for severity in SECURITY_SEVERITY_ORDER if severity in severities]
        wanted = [severity for severity in wanted if severity in _SEVERITIES]
        if not wanted:
            return ImageLookup(status="found", platform_name=name)
        export_params = {
            "filter_container_repo_id": containers[0]["id"],
            "filter_status": "open",
            "filter_severities": ",".join(wanted),
            "format": "json",
        }
        raw = self._paged(_ISSUES_EXPORT_PATH, export_params, _ISSUES_PAGE_SIZE, _ISSUES_MAX_PAGES)
        issues = tuple(issue for issue in (_issue(item) for item in raw) if issue is not None)
        return ImageLookup(status="found", platform_name=name, issues=issues)

    # ------------------------------------------------------------------
    # Connection check (`strata integration check`)
    # ------------------------------------------------------------------

    def check_connection(self) -> list[str]:
        """Verify the credential each declared capability needs, changing nothing.

        The CI token is probed with a status request for scan 0 (reference A.3.4, unconfirmed): 401/403
        means rejected; no response, 429 or 5xx (after retries) means Aikido could not confirm it; any
        other answer means Aikido accepted the token.
        """
        lines: list[str] = []
        if Capability.RELEASE_GATE in self._declared:
            token = self._ci_token()
            host = self._base_url(token)
            headers = {"Content-Type": "application/json", "X-AIK-API-SECRET": token}
            result = self._call("GET", f"{host}{_SCAN_PATH}", headers=headers, params={"scan_id": 0})
            if result.status in (NO_RESPONSE, 401, 403, 429) or result.status >= 500:
                raise self._failure(result, "GET", _SCAN_PATH)
            lines.append(f"release_gate: CI token accepted by {host}")
        if Capability.IMAGE_FINDINGS in self._declared:
            self._access_token()
            lines.append(f"image_findings: API client credentials accepted by {self._base_url()}")
        if not lines:
            raise IntegrationError(f"{self.name}: declares neither release_gate nor image_findings; nothing to check.")
        return lines


def _container_name(container: Any) -> str:
    name = container.get("name") if isinstance(container, Mapping) else None
    return name if isinstance(name, str) and _as_int(container.get("id")) is not None else ""


def _issue(item: Any) -> SecurityIssue | None:
    """Map one exported Aikido issue; one without an id cannot be reported or allowlisted, so it is dropped."""
    if not isinstance(item, Mapping):
        return None
    raw_id = item.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)) or raw_id == "":
        return None
    severity = item.get("severity")
    status = item.get("status")
    cve_id = item.get("cve_id")
    package = item.get("affected_package")
    return SecurityIssue(
        id=str(raw_id),
        severity=severity.upper() if isinstance(severity, str) and severity.lower() in _SEVERITIES else "UNKNOWN",
        status=status if isinstance(status, str) and status else "open",
        cve_id=cve_id if isinstance(cve_id, str) and cve_id else None,
        package=package if isinstance(package, str) and package else None,
    )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reason_phrase(result: HttpResult) -> str | None:
    try:
        body = result.json()
    except (json.JSONDecodeError, ValueError):
        return None
    reason = body.get("reason_phrase") if isinstance(body, dict) else None
    return reason if isinstance(reason, str) and reason else None


def _outcome(scan_id: int, body: Mapping[str, Any]) -> ReleaseGateOutcome:
    """Map a completed scan; anything but a literal `gate_passed: true` is a fail (Aikido's client does the same)."""
    raw_breakdown = body.get("issue_breakdown")
    breakdown: dict[str, dict[str, int]] = {}
    if isinstance(raw_breakdown, Mapping):
        for category, counts in raw_breakdown.items():
            if isinstance(counts, Mapping):
                breakdown[str(category)] = {
                    str(severity): count for severity, count in counts.items() if _as_int(count) is not None
                }
    raw_links = body.get("issue_links")
    links = tuple(link for link in raw_links if isinstance(link, str)) if isinstance(raw_links, list) else ()
    open_issues = _as_int(body.get("open_issues_found"))
    if open_issues is None:
        open_issues = _as_int(body.get("new_issues_found"))
    outside_sla = _as_int(body.get("issues_outside_sla"))
    return ReleaseGateOutcome(
        passed=body.get("gate_passed") is True,
        reference=str(scan_id),
        open_issues=open_issues,
        breakdown=breakdown,
        links=links,
        detail=f"{outside_sla} issue(s) outside SLA" if outside_sla else None,
    )
