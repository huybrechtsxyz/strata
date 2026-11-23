#!/usr/bin/env python3
"""`http` and `tcp` verify checks (docs/design/post-deploy-verification.md, D10), and a check's `when`.

Against a real HTTP server on localhost, whose answer each test sets, and a real listening socket; a closed port
stands in for a workload that is gone. The clock is faked, so retries cost no time.
"""

import datetime
import ipaddress
import json
import socket
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from strata.controllers.verification import CheckResult, has_checks, output_steps, run_verify_block, would_verify
from strata.models.deployment_model import (
    DeploymentSpecModel,
    DeploymentVerifyCheckModel,
    DeploymentVerifyModel,
)

# Real sockets: a refused connection to localhost takes about 2 seconds on Windows.
pytestmark = pytest.mark.slow


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def server() -> Iterator[dict]:
    """A local HTTP server answering `state["status"]` with `state["body"]` as `state["type"]`; records requests."""
    state: dict = {"status": 200, "body": b"ok", "type": "text/plain", "requests": []}

    yield from _serve(state)


def _serve(state: dict, tls: ssl.SSLContext | None = None) -> Iterator[dict]:
    class _Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            sent = self.rfile.read(length) if length else b""
            state["requests"].append(
                {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": sent}
            )
            self.send_response(state["status"])
            self.send_header("Content-Type", state["type"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(state["body"])

        do_GET = do_HEAD = do_OPTIONS = do_POST = _answer

        def log_message(self, *args) -> None:  # quiet
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    if tls is not None:
        httpd.socket = tls.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"{'https' if tls else 'http'}://127.0.0.1:{httpd.server_address[1]}"
    state["port"] = httpd.server_address[1]
    yield state
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def tls_server(tmp_path: Path) -> Iterator[dict]:
    """The same server over HTTPS, with a self-signed certificate for 127.0.0.1 written to `state["ca_file"]`."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "strata-test")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    state: dict = {"status": 200, "body": b"ok", "type": "text/plain", "requests": [], "ca_file": str(cert_file)}
    yield from _serve(state, tls)


def _closed_port() -> int:
    """A port nothing listens on: bound, then released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _run(check: dict, *, action: str = "apply", tokens: dict | None = None, **block) -> CheckResult:
    model = DeploymentVerifyModel.model_validate(
        {"initial_delay": 0, "period": 1, "timeout": 5, "failure_threshold": 2, **block, "checks": [check]}
    )
    clock = _Clock()
    results = run_verify_block(
        model,
        deployment="app",
        step="apply_web",
        tokens=tokens or {},
        root=Path("."),
        redact=lambda text: text.replace("s3cret-token", "***"),
        action=action,  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock,
    )
    [result] = results
    return result


# ---------------------------------------------------------------------------
# http
# ---------------------------------------------------------------------------


def test_http_passes_on_the_expected_status(server):
    result = _run({"name": "web", "type": "http", "url": server["url"] + "/health"})

    assert (result.answer, result.attempts) == ("pass", 1)
    assert result.message == f"HTTP 200 from {server['url']}/health"
    assert server["requests"][0]["method"] == "GET"


def test_http_fails_on_another_status_and_retries(server):
    server["status"] = 503

    result = _run({"name": "web", "type": "http", "url": server["url"]})

    assert (result.answer, result.attempts) == ("fail", 2)
    assert result.message == f"HTTP 503 from {server['url']}, expected 200"


def test_http_accepts_any_of_several_statuses(server):
    server["status"] = 401

    result = _run({"name": "web", "type": "http", "url": server["url"], "expect_status": [200, 401]})

    assert result.answer == "pass"


def test_http_without_an_answer_fails(server):
    result = _run({"name": "web", "type": "http", "url": f"http://127.0.0.1:{_closed_port()}/"}, failure_threshold=1)

    assert (result.answer, result.attempts) == ("fail", 1)
    assert result.message is not None and result.message.startswith("no answer from http://127.0.0.1:")


@pytest.mark.parametrize(
    ("health", "status", "answer"),
    [
        ({"status": "pass"}, 200, "pass"),
        ({"status": "warn", "output": "disk 91% full"}, 200, "warn"),
        ({"status": "fail", "output": "database unreachable"}, 503, "fail"),
        ({"status": "fail"}, 200, "fail"),  # the body decides, not the status
        ({"status": "up"}, 200, "pass"),  # the draft's alias
    ],
)
def test_a_health_json_body_decides_the_answer(server, health, status, answer):
    server.update(status=status, body=json.dumps(health).encode(), type="application/health+json; charset=utf-8")

    result = _run({"name": "web", "type": "http", "url": server["url"]})

    assert result.answer == answer
    assert f"health {health['status']}" in (result.message or "")
    if "output" in health:
        assert health["output"] in (result.message or "")


def test_a_health_json_body_without_a_known_status_falls_back_to_the_status_code(server):
    server.update(body=b'{"status": "maybe"}', type="application/health+json")

    assert _run({"name": "web", "type": "http", "url": server["url"]}).answer == "pass"


def test_plain_json_is_not_read_as_health(server):
    server.update(body=b'{"status": "fail"}', type="application/json")

    assert _run({"name": "web", "type": "http", "url": server["url"]}).answer == "pass"


def test_http_resolves_tokens_in_the_url_and_headers_and_masks_secrets(server):
    result = _run(
        {
            "name": "web",
            "type": "http",
            "url": "${output:apply_web.base}/health?key=${secret:KEY}",
            "headers": {"Authorization": "Bearer ${secret:KEY}"},
            "method": "HEAD",
        },
        tokens={"apply_web.base": server["url"], "KEY": "s3cret-token"},
    )

    assert result.answer == "pass"
    [request] = server["requests"]
    assert (request["method"], request["path"]) == ("HEAD", "/health?key=s3cret-token")
    assert request["headers"]["Authorization"] == "Bearer s3cret-token"
    assert "s3cret-token" not in (result.message or "")
    assert result.message == f"HTTP 200 from {server['url']}/health?key=***"


@pytest.mark.parametrize("url", ["ftp://example.test/", "not a url", "${output:apply_web.missing}"])
def test_a_url_that_cannot_be_requested_is_unknown_and_not_retried(url):
    result = _run({"name": "web", "type": "http", "url": url})

    assert (result.answer, result.attempts) == ("unknown", 1)


def test_http_expect_down_passes_without_an_answer():
    result = _run({"name": "gone", "type": "http", "url": f"http://127.0.0.1:{_closed_port()}/", "expect": "down"})

    assert (result.answer, result.attempts) == ("pass", 1)


def test_http_expect_down_passes_on_another_status_and_fails_while_it_still_answers(server):
    check = {"name": "gone", "type": "http", "url": server["url"], "expect": "down"}
    assert _run(check).answer == "fail"
    assert "it still answers" in (_run(check).message or "")

    server["status"] = 404
    assert _run(check).answer == "pass"


def test_http_posts_a_body_with_tokens_resolved(server):
    result = _run(
        {
            "name": "graphql",
            "type": "http",
            "url": server["url"] + "/graphql",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": '{"query": "{ health }", "key": "${secret:KEY}"}',
        },
        tokens={"KEY": "s3cret-token"},
    )

    assert result.answer == "pass"
    [request] = server["requests"]
    assert request["method"] == "POST"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["body"] == b'{"query": "{ health }", "key": "s3cret-token"}'


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


def test_https_with_an_untrusted_certificate_fails_and_says_why(tls_server):
    result = _run({"name": "web", "type": "http", "url": tls_server["url"]}, failure_threshold=1)

    assert result.answer == "fail"
    assert "is not trusted" in (result.message or "") and "ca_file" in (result.message or "")


def test_an_untrusted_certificate_also_fails_expect_down(tls_server):
    """Something still answers, so the workload is not gone."""
    result = _run({"name": "gone", "type": "http", "url": tls_server["url"], "expect": "down"}, failure_threshold=1)

    assert result.answer == "fail"


def test_ca_file_trusts_a_self_signed_certificate(tls_server):
    result = _run({"name": "web", "type": "http", "url": tls_server["url"], "ca_file": tls_server["ca_file"]})

    assert (result.answer, result.message) == ("pass", f"HTTP 200 from {tls_server['url']}")


def test_ca_file_is_relative_to_the_solution_root(tls_server):
    """`_run` uses the working directory as the root: a relative path from there is found."""
    relative = Path(tls_server["ca_file"]).relative_to(Path.cwd(), walk_up=True)

    result = _run({"name": "web", "type": "http", "url": tls_server["url"], "ca_file": str(relative)})

    assert result.answer == "pass"


def test_a_ca_file_that_cannot_be_read_is_unknown_and_not_retried(tls_server):
    result = _run({"name": "web", "type": "http", "url": tls_server["url"], "ca_file": "missing/ca.pem"})

    assert (result.answer, result.attempts) == ("unknown", 1)
    assert "ca_file 'missing/ca.pem' cannot be read" in (result.message or "")


def test_insecure_skips_the_certificate_check_and_says_so(tls_server):
    result = _run({"name": "web", "type": "http", "url": tls_server["url"], "insecure": True})

    assert result.answer == "pass"
    assert result.message == f"HTTP 200 from {tls_server['url']} (certificate not checked: insecure)"


# ---------------------------------------------------------------------------
# tcp
# ---------------------------------------------------------------------------


def test_tcp_passes_when_the_connection_opens(server):
    result = _run({"name": "port", "type": "tcp", "host": "127.0.0.1", "port": server["port"]})

    assert (result.answer, result.message) == ("pass", f"connected to 127.0.0.1:{server['port']}")


def test_tcp_fails_when_nothing_listens():
    port = _closed_port()

    result = _run({"name": "port", "type": "tcp", "host": "127.0.0.1", "port": port}, failure_threshold=1)

    assert (result.answer, result.attempts) == ("fail", 1)
    assert (result.message or "").startswith(f"no connection to 127.0.0.1:{port}")


def test_tcp_expect_down_inverts_the_answer(server):
    gone = {"name": "port", "type": "tcp", "host": "${output:apply_db.host}", "port": _closed_port(), "expect": "down"}
    assert _run(gone, tokens={"apply_db.host": "127.0.0.1"}).answer == "pass"

    still_there = {"name": "port", "type": "tcp", "host": "127.0.0.1", "port": server["port"], "expect": "down"}
    assert _run(still_there).answer == "fail"


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def _in_a_deployment(check: dict) -> DeploymentSpecModel:
    """A check is judged as part of a complete deployment (`DeploymentSpecModel.validate_verify_checks()`)."""
    return DeploymentSpecModel.model_validate(
        {"workspace": "main", "environments": ["prd"], "verify": {"checks": [check]}}
    )


@pytest.mark.parametrize(
    ("check", "needs"),
    [
        ({"name": "c"}, "'run'"),
        ({"name": "c", "type": "http"}, "'url'"),
        ({"name": "c", "type": "tcp", "host": "db"}, "'port'"),
    ],
)
def test_each_type_needs_its_fields(check, needs):
    with pytest.raises(ValidationError, match=needs):
        _in_a_deployment(check)


@pytest.mark.parametrize(
    "check",
    [
        {"name": "c", "type": "http", "url": "https://x", "run": ["curl"]},
        {"name": "c", "type": "tcp", "host": "db", "port": 5432, "url": "https://x"},
        {"name": "c", "run": ["check"], "expect": "down"},
        {"name": "c", "type": "tcp", "host": "db", "port": 5432, "method": "HEAD"},
        {"name": "c", "type": "tcp", "host": "db", "port": 5432, "insecure": True},
        {"name": "c", "run": ["check"], "body": "{}"},
    ],
)
def test_a_field_of_another_type_is_refused(check):
    with pytest.raises(ValidationError, match="do not apply to type"):
        _in_a_deployment(check)


@pytest.mark.parametrize(
    ("check", "message"),
    [
        ({"body": "{}"}, "'body' is sent only with method 'POST'"),
        ({"ca_file": "ca.pem", "insecure": True}, "exclude each other"),
    ],
)
def test_http_option_combinations_that_cannot_work_are_refused(check, message):
    with pytest.raises(ValidationError, match=message):
        _in_a_deployment({"name": "c", "type": "http", "url": "https://x", **check})


@pytest.mark.parametrize("expect_status", [[], 0, 99, 600, [200, 1000]])
def test_expect_status_needs_at_least_one_real_status(expect_status):
    """With none, `expect: up` could never pass and `expect: down` never fail."""
    with pytest.raises(ValidationError, match="expect_status"):
        DeploymentVerifyCheckModel.model_validate(
            {"name": "c", "type": "http", "url": "https://x", "expect_status": expect_status}
        )


def test_a_check_survives_a_dump_and_a_second_validation():
    """A resolved deployment is dumped with its defaults and validated again."""
    check = DeploymentVerifyCheckModel.model_validate({"name": "c", "type": "tcp", "host": "db", "port": 5432})

    assert DeploymentVerifyCheckModel.model_validate(check.model_dump()) == check


def test_on_is_not_a_field_since_yaml_reads_it_as_true():
    """`when`, not `on`: PyYAML (YAML 1.1) loads an `on:` key as the boolean `True`."""
    with pytest.raises(ValidationError):
        DeploymentVerifyCheckModel.model_validate({"name": "c", "run": ["check"], True: ["destroy"]})


# ---------------------------------------------------------------------------
# `when`
# ---------------------------------------------------------------------------

_BOTH = DeploymentVerifyModel.model_validate(
    {
        "checks": [
            {"name": "up", "run": ["check", "${output:apply_web.url}"]},
            {"name": "gone", "when": ["destroy"], "type": "tcp", "host": "${output:apply_db.host}", "port": 1},
            {"name": "always", "when": ["apply", "destroy", "apply"], "run": ["check"]},
        ]
    }
)


def test_when_selects_the_checks_per_action():
    assert [line.rsplit("'", 2)[1] for line in would_verify(_BOTH, "s", "apply")] == ["up", "always"]
    assert [line.rsplit("'", 2)[1] for line in would_verify(_BOTH, "s", "destroy")] == ["gone", "always"]
    assert _BOTH.checks[2].when == ["apply", "destroy"]  # each once


def test_output_steps_follow_when_and_read_every_token_field():
    assert output_steps([_BOTH], "verify") == {"apply_web"}
    assert output_steps([_BOTH], "destroy") == {"apply_db"}


def test_has_checks_needs_one_for_the_action():
    apply_only = DeploymentVerifyModel.model_validate({"checks": [{"name": "up", "run": ["check"]}]})

    assert has_checks(apply_only, "apply") and has_checks(apply_only, "verify")
    assert not has_checks(apply_only, "destroy")
    assert not has_checks(None, "apply")
