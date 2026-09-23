#!/usr/bin/env python3
"""Tests for `Integration` (ADR-0021 D1) — transport resolution, the command
fallback chain, and version checking. Exercised with fake subclasses; no
real tool required.
"""

import pytest

from strata.integrations.base import Integration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import IntegrationModel
from strata.utils.transport import CommandResult, HttpResult


class _CliOnly(Integration):
    """A class with exactly one transport."""

    TYPE = "widget"
    CAPABILITIES = frozenset({"infrastructure"})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "widget-cli"


class _NetworkedOnly(Integration):
    """A class with exactly one, non-cli, transport."""

    TYPE = "widget"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS = frozenset({"http"})


class _DualTransport(Integration):
    """The Consul-shaped case: cli or http, either configurable."""

    TYPE = "widget"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS = frozenset({"cli", "http"})
    COMMAND = "definitely-not-a-real-binary-xyz"


def _config(**spec_overrides: object) -> IntegrationModel:
    spec: dict[str, object] = {"type": "widget", "capabilities": []}
    spec.update(spec_overrides)
    return IntegrationModel.model_validate({"meta": {"name": "widget-main"}, "spec": spec})


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_construction_without_config_is_allowed():
    """A built-in works from PATH alone, zero configuration (ADR-0021 D4)."""
    integration = _CliOnly()
    assert integration.name == "widget"
    assert integration.config is None


def test_construction_rejects_mismatched_type():
    """Configuring a class with the wrong document's type is a caller bug, not an authoring error."""
    bad_config = IntegrationModel.model_validate({"meta": {"name": "x"}, "spec": {"type": "not-widget"}})
    with pytest.raises(IntegrationError, match="not-widget"):
        _CliOnly(bad_config)


def test_name_falls_back_to_type_without_a_document():
    assert _CliOnly().name == "widget"


def test_name_uses_the_document_when_given():
    assert _CliOnly(_config()).name == "widget-main"


# ---------------------------------------------------------------------------
# command resolution (ADR-0021 D6 fallback chain)
# ---------------------------------------------------------------------------


def test_command_uses_the_class_default_with_no_document():
    assert _CliOnly().command == "widget-cli"


def test_command_override_wins_over_the_class_default():
    integration = _CliOnly(_config(command="custom-binary"))
    assert integration.command == "custom-binary"


def test_command_falls_back_to_type_when_the_class_has_no_default():
    """A generic wrapper with no COMMAND of its own uses the document's `type`."""

    class _Generic(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})

    integration = _Generic(_config())
    assert integration.command == "widget"


def test_command_is_none_with_no_document_and_no_class_default():
    class _ApiOnlyNoCommand(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"http"})

    assert _ApiOnlyNoCommand().command is None


# ---------------------------------------------------------------------------
# transport resolution — all four cases (ADR-0021 D1)
# ---------------------------------------------------------------------------


def test_transport_explicit_choice_is_honoured():
    integration = _DualTransport(_config(transport="http"))
    assert integration.transport == "http"


def test_transport_rejects_a_choice_outside_the_class_transports():
    integration = _CliOnly(_config(transport="grpc"))
    with pytest.raises(IntegrationError, match="grpc"):
        _ = integration.transport


def test_transport_resolves_to_the_sole_supported_one():
    assert _CliOnly().transport == "cli"
    assert _NetworkedOnly().transport == "http"


def test_transport_falls_back_to_cli_when_available_and_unconfigured():
    import sys

    class _AlwaysAvailable(_DualTransport):
        COMMAND = sys.executable  # guaranteed present, unlike a bare "python"

    integration = _AlwaysAvailable()
    assert integration.is_available()
    assert integration.transport == "cli"


def test_transport_falls_back_to_the_remaining_option_when_cli_unavailable():
    """_DualTransport's COMMAND does not exist on PATH."""
    integration = _DualTransport()
    assert not integration.is_available()
    assert integration.transport == "http"


def test_transport_is_cached_after_first_resolution():
    integration = _CliOnly(_config(transport="cli"))
    first = integration.transport
    # Mutate the backing config; a cached property must not notice.
    integration.config.spec.transport = "grpc"  # type: ignore[union-attr]
    assert integration.transport == first == "cli"


# ---------------------------------------------------------------------------
# is_available / run / request
# ---------------------------------------------------------------------------


def test_is_available_false_for_an_unresolvable_command():
    assert not _DualTransport().is_available()


def test_is_available_true_for_a_real_command():
    import sys

    integration = _CliOnly(_config(command=sys.executable))
    assert integration.is_available()


def test_run_reports_a_missing_command_without_raising():
    class _NoCommand(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})

    result = _NoCommand().run("--version")
    assert isinstance(result, CommandResult)
    assert not result.is_successful


def test_run_delegates_to_run_command_with_the_resolved_executable():
    import sys

    integration = _CliOnly(_config(command=sys.executable))
    result = integration.run("-c", "print('hi')")
    assert result.is_successful
    assert result.payload.strip() == "hi"


def test_request_reports_a_missing_endpoint_without_raising():
    result = _NetworkedOnly().request("GET", "/v1/kv/x")
    assert isinstance(result, HttpResult)
    assert not result.is_successful


def test_request_joins_path_onto_the_configured_endpoint(monkeypatch):
    import strata.integrations.base as base_module

    captured: dict[str, object] = {}

    def _fake_http_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return HttpResult(status=200, body="ok")

    monkeypatch.setattr(base_module, "http_request", _fake_http_request)
    integration = _NetworkedOnly(_config(endpoints={"address": "https://example.test:8500/"}))
    result = integration.request("GET", "/v1/kv/x")
    assert result.is_successful
    assert captured["url"] == "https://example.test:8500/v1/kv/x"


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_get_version_is_none_for_a_networked_only_integration():
    assert _NetworkedOnly().get_version() is None


def test_get_version_is_none_when_the_command_is_unavailable():
    assert _DualTransport().get_version() is None


def test_get_version_uses_parse_version_hook():
    import sys

    class _FakeCli(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})
        COMMAND = sys.executable
        VERSION_ARGS = ("-c", "print('MyTool v9.9.9')")

        def parse_version(self, raw: str) -> str:
            return raw.strip().removeprefix("MyTool v")

    assert _FakeCli().get_version() == "9.9.9"


def test_parse_version_default_just_strips():
    assert _CliOnly().parse_version("  1.2.3  \n") == "1.2.3"


def test_ensure_version_no_op_when_expected_is_none():
    _DualTransport().ensure_version(None)  # must not raise, even though get_version() is None


def test_ensure_version_raises_when_the_version_cannot_be_determined():
    with pytest.raises(IntegrationError, match="could not determine"):
        _NetworkedOnly().ensure_version(">=1.0")


def test_ensure_version_passes_for_a_satisfied_constraint():
    import sys

    class _FakeCli(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})
        COMMAND = sys.executable
        VERSION_ARGS = ("-c", "print('9.9.9')")

    _FakeCli().ensure_version(">=1.0")  # must not raise


def test_ensure_version_raises_for_an_unsatisfied_constraint():
    import sys

    class _FakeCli(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset()
        TRANSPORTS = frozenset({"cli"})
        COMMAND = sys.executable
        VERSION_ARGS = ("-c", "print('0.1.0')")

    with pytest.raises(IntegrationError, match="does not satisfy"):
        _FakeCli().ensure_version(">=1.0")
