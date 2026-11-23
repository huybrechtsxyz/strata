#!/usr/bin/env python3
"""`strata validate` on a deployment's `verify` checks (docs/design/post-deploy-verification.md, D12).

A check runs after its step with that step's outputs and those of the steps it depends on, so `${output:}` is
judged against the workspace's steps there, instead of being refused as unsupported like elsewhere in a
deployment. Every other token kind is checked as in any other field. The files of the solution a check needs (the
script it runs, its `ca_file`) must exist.
"""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(tmp_path: Path, check: str, *, stage_step: str = "apply_api", spec_extra: str = "") -> Path:
    """Steps `apply_db` <- `apply_api` (depends on it) and an unrelated `apply_web`; one check on `stage_step`."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: s\nspec: {}\n")
    _write(root, "infra/main.tf", "#\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n"
        "    - name: apply_db\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "    - name: apply_api\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      depends_on:\n        - apply_db\n"
        "    - name: apply_web\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      depends_on:\n        - apply_api\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: TOKEN\n      store: environment\n      value: TOKEN_VAR\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  workspace: main\n"
        f"{spec_extra}  environments:\n    - prd\n  stages:\n    - step: {stage_step}\n      verify:\n        checks:\n"
        f"          - name: api-answers\n{check}",
    )
    return root


def _findings(root: Path) -> list[tuple[str, str | None, str | None]]:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    diagnostics = context.resolve()
    return [(item.severity.value, item.code, item.location) for item in diagnostics.errors]


@pytest.mark.parametrize(
    "check",
    [
        "            run: [check, '${output:apply_api.url}']\n",  # the stage's own step
        "            run: [check, '${output:apply_db.host}']\n",  # a step it depends on
        "            run: [check]\n            env:\n              URL: ${output:apply_api.url}\n",  # in env
        "            run: [check, '${secret:TOKEN}', '${deployment:name}']\n",  # other kinds, declared
        "            type: http\n            url: 'https://${output:apply_api.host}/health'\n"
        "            headers:\n              Authorization: 'Bearer ${secret:TOKEN}'\n",  # an http check
        "            type: tcp\n            host: '${output:apply_db.host}'\n            port: 5432\n"
        "            when: [destroy]\n            expect: down\n",  # a tcp check after a destroy
    ],
)
def test_a_check_may_use_its_steps_outputs_and_declared_values(tmp_path, check):
    assert _findings(_solution(tmp_path, check)) == []


@pytest.mark.parametrize(
    ("check", "location", "why"),
    [
        (
            "            run: [check, '${output:apply_web.url}']\n",
            "run.1",
            "neither 'apply_api' nor a step it depends on",
        ),
        (
            "            run: [check, '${output:no_such_step.url}']\n",
            "run.1",
            "step 'no_such_step' is not in workspace",
        ),
        ("            run: [check]\n            env:\n              URL: ${output:apply_api}\n", "env.URL", "STEP.KEY"),
        (
            "            type: http\n            url: '${output:apply_web.url}'\n",
            "url",
            "neither 'apply_api' nor a step it depends on",
        ),
        (
            "            type: http\n            url: https://x\n            headers:\n"
            "              X-Key: '${output:apply_web.key}'\n",
            "headers.X-Key",
            "neither 'apply_api' nor a step it depends on",
        ),
        (
            "            type: tcp\n            host: '${output:apply_web.host}'\n            port: 5432\n",
            "host",
            "neither 'apply_api' nor a step it depends on",
        ),
    ],
)
def test_an_output_the_check_will_not_have_is_refused(tmp_path, check, location, why):
    root = _solution(tmp_path, check)
    context = open_solution(root)
    diagnostics = context.resolve()

    [finding] = [item for item in diagnostics.errors if item.code == "verify_output_unavailable"]
    assert finding.location == f"spec.stages.0.verify.checks.0.{location}"
    assert why in finding.message
    assert finding.hint is not None and "['apply_api', 'apply_db']" in finding.hint
    assert [item.code for item in diagnostics.errors] == ["verify_output_unavailable"]  # not also "unsupported"


def test_health_checks_fail_to_load_with_the_verify_checks_that_replace_them(tmp_path):
    """Removed in 2.9 (ADR-0068): `health_checks` never ran; `verify.checks` with `type: http`/`tcp` replaces it."""
    root = _solution(
        tmp_path,
        "            run: [check]\n"
        "      health_checks:\n        - name: site\n          type: http\n          url: https://web.example\n"
        "          expect_status: 204\n"
        "        - name: db\n          type: tcp\n          host: db.example\n          port: 5432\n          timeout: 5\n",
    )
    context = open_solution(root)

    assert not context.ok
    message = "\n".join(context.diagnostics.messages())
    assert "'health_checks' was removed in strata 2.9" in message
    assert (
        "checks: [{name: site, type: http, url: https://web.example, expect_status: 204}, "
        "{name: db, type: tcp, host: db.example, port: 5432, timeout: 5}]" in message
    )


@pytest.mark.parametrize(
    "check",
    [
        "            run: [check, '${output:apply_web.url}']\n            enabled: false\n",  # the check is off
        "            run: [check, '${output:no_such_step.url}']\n            enabled: false\n",
    ],
)
def test_a_disabled_checks_outputs_are_not_judged(tmp_path, check):
    """`enabled: false` is how a child drops a base's check; what that check would read does not matter."""
    assert _findings(_solution(tmp_path, check)) == []


def test_a_disabled_blocks_outputs_are_not_judged(tmp_path):
    root = _solution(tmp_path, "            run: [check, '${output:apply_web.url}']\n")
    deployment = root / "deployment.yaml"
    deployment.write_text(
        deployment.read_text(encoding="utf-8").replace("      verify:\n", "      verify:\n        enabled: false\n"),
        encoding="utf-8",
    )

    assert _findings(root) == []


@pytest.mark.parametrize(
    "run",
    [
        "[kubectl, --context, '${deployment:kube_context}', get, pods]",  # no kube_context on the deployment
        "[check, '${var:NOT_DECLARED}']",
        "[check, '${artifact:nope.image}']",
    ],
)
def test_a_disabled_checks_tokens_are_not_judged(tmp_path, run):
    """No token of a check that never runs needs to resolve; enabled, each of these is an error."""
    enabled = _findings(_solution(tmp_path / "on", f"            run: {run}\n"))
    disabled = _findings(_solution(tmp_path / "off", f"            run: {run}\n            enabled: false\n"))

    assert enabled != [] and disabled == []


def test_a_disabled_blocks_tokens_are_not_judged(tmp_path):
    root = _solution(tmp_path, "            run: [check, '${var:NOT_DECLARED}']\n")
    deployment = root / "deployment.yaml"
    deployment.write_text(
        deployment.read_text(encoding="utf-8").replace("      verify:\n", "      verify:\n        enabled: false\n"),
        encoding="utf-8",
    )

    assert _findings(root) == []


def test_only_the_disabled_check_is_spared(tmp_path):
    """`checks[1]` disabled spares `checks[1]`, not `checks[0]` (nor a `checks[10]`)."""
    check = (
        "            run: [check, '${var:NOT_DECLARED}']\n"
        "          - name: retired\n            run: [check, '${var:ALSO_NOT}']\n            enabled: false\n"
    )

    findings = _findings(_solution(tmp_path, check))

    assert findings == [("error", "undeclared_value_token", "spec.stages[0].verify.checks[0].run[1]")]


def test_a_child_that_drops_a_bases_check_is_not_judged_for_its_tokens(tmp_path):
    """The documented way to drop a base's check: the child sets no `kube_context`, so the base's kubectl check
    could not resolve, but the child turns it off."""
    root = _solution(tmp_path, "            run: [check]\n")
    _write(
        root,
        "base.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: base\nspec:\n  partial: true\n"
        "  workspace: main\n  stages:\n    - step: apply_api\n      verify:\n        checks:\n"
        "          - name: pods\n            run: [kubectl, --context, '${deployment:kube_context}', get, pods]\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  extends: base\n"
        "  environments:\n    - prd\n  stages:\n    - step: apply_api\n      verify:\n        checks:\n"
        "          - name: pods\n            enabled: false\n",
    )

    assert _findings(root) == []

    _write(root, "deployment.yaml", (root / "deployment.yaml").read_text().replace("enabled: false", "timeout: 5"))
    assert [code for _, code, _ in _findings(root)] == ["unavailable_deployment_token"]


_BASE_HTTP = (
    "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: base\nspec:\n  partial: true\n"
    "  workspace: main\n  stages:\n    - step: apply_api\n      verify:\n        initial_delay: 45\n"
    "        enforcement: warn\n        checks:\n"
    "          - name: web\n            type: http\n            url: https://api.example/health\n"
    "            expect_status: 204\n            method: HEAD\n"
)


def _child_of_the_http_base(tmp_path: Path, check: str) -> tuple[Path, object]:
    """`app` extends a base whose stage has an `http` check `web`; the child's stage writes `check` over it."""
    from strata.controllers.deployment_resolution import resolve_deployment_chains

    root = _solution(tmp_path, "            run: [check]\n")
    _write(root, "base.yaml", _BASE_HTTP)
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  extends: base\n"
        "  environments:\n    - prd\n  stages:\n    - step: apply_api\n      verify:\n        checks:\n"
        f"          - name: web\n{check}",
    )
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    resolved, problems = resolve_deployment_chains(context.controller.index)
    assert problems.ok, problems.messages()
    return root, resolved["app"]


def test_a_child_turns_off_a_bases_http_check_by_name_alone(tmp_path):
    """No `run`, no `type`: the child's defaults (`type: script`, `method: GET`) must not overwrite the base's."""
    root, app = _child_of_the_http_base(tmp_path, "            enabled: false\n")

    [check] = app.spec.stages[0].verify.checks
    assert (check.type, check.url, check.method, check.enabled) == ("http", "https://api.example/health", "HEAD", False)
    assert _findings(root) == []


def test_a_child_changes_one_field_of_a_bases_check_and_keeps_the_rest(tmp_path):
    root, app = _child_of_the_http_base(tmp_path, "            url: https://api.example/ready\n")

    block = app.spec.stages[0].verify
    [check] = block.checks
    assert (check.type, check.url, check.expect_status, check.method) == (
        "http",
        "https://api.example/ready",
        204,
        "HEAD",
    )
    assert (block.initial_delay, block.enforcement) == (45, "warn")  # the child's block wrote only `checks`
    assert _findings(root) == []


def test_a_merged_check_that_cannot_work_is_still_refused(tmp_path):
    """Exempting the child document moves the judgement to the merged deployment, it does not drop it."""
    root = _solution(tmp_path, "            run: [check]\n")
    _write(root, "base.yaml", _BASE_HTTP)
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  extends: base\n"
        "  environments:\n    - prd\n  stages:\n    - step: apply_api\n      verify:\n        checks:\n"
        "          - name: web\n            run: [curl, -f]\n",  # `run` on what is still an `http` check
    )

    messages = "\n".join(open_solution(root).resolve().messages())

    assert "'run' do not apply to type 'http'" in messages


def test_a_disabled_check_in_a_complete_deployment_needs_no_run(tmp_path):
    assert _findings(_solution(tmp_path, "            enabled: false\n")) == []


def test_undeclared_and_malformed_tokens_are_still_refused(tmp_path):
    root = _solution(tmp_path, "            run: [check, '${var:NOT_DECLARED}', '${NOT_A_TOKEN}']\n")

    codes = sorted(code for _, code, _ in _findings(root))

    assert codes == ["malformed_value_token", "undeclared_value_token"]


def test_an_output_outside_a_verify_block_is_still_unsupported_in_a_deployment(tmp_path):
    """Only a verify check reads step outputs; elsewhere in a deployment `${output:}` stays refused."""
    root = _solution(tmp_path, "            run: [check]\n")
    deployment = root / "deployment.yaml"
    deployment.write_text(
        deployment.read_text(encoding="utf-8") + "  properties:\n    url: ${output:apply_api.url}\n", encoding="utf-8"
    )

    assert ("error", "unsupported_value_token_kind", "spec.properties.url") in _findings(root)


# ---------------------------------------------------------------------------
# The deployment's own checks, `spec.verify` (phase 2): they run after every step
# ---------------------------------------------------------------------------


def _with_deployment_check(root: Path, run: str) -> Path:
    deployment = root / "deployment.yaml"
    deployment.write_text(
        deployment.read_text(encoding="utf-8")
        + f"  verify:\n    checks:\n      - name: e2e\n        run: [check, '{run}']\n",
        encoding="utf-8",
    )
    return root


def test_a_deployment_check_may_read_any_step_of_the_workspace(tmp_path):
    # `apply_web` is neither the stage's step nor upstream of it: only a deployment check may read it.
    root = _with_deployment_check(_solution(tmp_path, "            run: [check]\n"), "${output:apply_web.url}")

    assert _findings(root) == []


@pytest.mark.parametrize(
    ("run", "why"),
    [
        ("${output:no_such_step.url}", "step 'no_such_step' is not in workspace 'main'"),
        ("${output:apply_web}", "STEP.KEY"),
    ],
)
def test_a_deployment_check_output_must_name_a_step_and_a_key(tmp_path, run, why):
    root = _with_deployment_check(_solution(tmp_path, "            run: [check]\n"), run)

    diagnostics = open_solution(root).resolve()

    [finding] = diagnostics.errors
    assert (finding.code, finding.location) == ("verify_output_unavailable", "spec.verify.checks.0.run.1")
    assert why in finding.message and "of the deployment" in finding.message
    assert finding.hint is not None and "['apply_api', 'apply_db', 'apply_web']" in finding.hint


# ---------------------------------------------------------------------------
# `${deployment:kube_context}` / `docker_context`, and `ca_file`
# ---------------------------------------------------------------------------

_KUBECTL = "            run: [kubectl, --context, '${deployment:kube_context}', get, pods]\n"


def test_a_check_may_pass_the_deployments_kube_context_to_kubectl(tmp_path):
    assert _findings(_solution(tmp_path, _KUBECTL, spec_extra="  kube_context: aks-prd\n")) == []


def test_the_kube_context_token_needs_a_kube_context(tmp_path):
    findings = _findings(_solution(tmp_path, _KUBECTL))

    assert findings == [("error", "unavailable_deployment_token", "spec.stages[0].verify.checks[0].run[2]")]


def test_the_docker_context_token_resolves_from_the_deployment(tmp_path):
    check = "            run: [docker, --context, '${deployment:docker_context}', service, ls]\n"

    assert _findings(_solution(tmp_path, check, spec_extra="  docker_context: swarm-prd\n")) == []


def _http_with_ca(ca_file: str) -> str:
    return f"            type: http\n            url: https://api.internal/health\n            ca_file: {ca_file}\n"


def test_a_ca_file_that_does_not_exist_is_refused(tmp_path):
    findings = _findings(_solution(tmp_path, _http_with_ca("certs/ca.pem")))

    assert findings == [("error", "verify_ca_file_unusable", "spec.stages.0.verify.checks.0.ca_file")]


def test_a_ca_file_without_a_certificate_is_refused(tmp_path):
    root = _solution(tmp_path, _http_with_ca("certs/ca.pem"))
    _write(root, "certs/ca.pem", "not a certificate\n")

    diagnostics = open_solution(root).resolve()

    [finding] = diagnostics.errors
    assert finding.code == "verify_ca_file_unusable"
    assert "holds no certificate" in finding.message


def test_a_ca_file_with_a_certificate_passes(tmp_path):
    x509 = pytest.importorskip("cryptography.x509")
    import datetime

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "strata-test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    root = _solution(tmp_path, _http_with_ca("certs/ca.pem"))
    _write(root, "certs/ca.pem", certificate.public_bytes(serialization.Encoding.PEM).decode())

    assert _findings(root) == []


def test_a_disabled_check_with_a_missing_ca_file_is_not_judged(tmp_path):
    check = _http_with_ca("certs/ca.pem") + "            enabled: false\n"

    assert _findings(_solution(tmp_path, check)) == []


def test_an_absolute_ca_file_is_left_to_the_machine_that_deploys(tmp_path):
    assert _findings(_solution(tmp_path, _http_with_ca(str(tmp_path / "nowhere" / "ca.pem")))) == []


# ---------------------------------------------------------------------------
# The script a `script` check runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("run", "index"),
    [
        ("[checks/web.sh, '${deployment:name}']", 0),
        ("[pwsh, -NoProfile, -File, checks/web.ps1]", 3),
        ("[powershell.exe, -f, checks/web.ps1]", 2),
        ("[python, -u, checks/web.py, --verbose]", 2),
        ("[bash, -o, pipefail, checks/web.sh]", 3),  # past an option's value
        ("[python, -W, ignore, checks/web.py]", 3),
        ("[bash, web.sh]", 1),  # an interpreter's script needs no directory: the working directory is the root
    ],
)
def test_a_script_the_check_runs_must_exist_in_the_solution(tmp_path, run, index):
    root = _solution(tmp_path, f"            run: {run}\n")

    diagnostics = open_solution(root).resolve()

    [finding] = diagnostics.errors
    assert (finding.code, finding.location) == ("verify_script_missing", f"spec.stages.0.verify.checks.0.run.{index}")
    assert "does not exist in the solution" in finding.message


@pytest.mark.parametrize(
    "run",
    [
        "[checks/web.sh]",
        "[pwsh, -File, checks/web.ps1]",
        "[python, checks/web.py]",
    ],
)
def test_a_script_that_exists_passes(tmp_path, run):
    root = _solution(tmp_path, f"            run: {run}\n")
    for name in ("web.sh", "web.ps1", "web.py"):
        _write(root, f"checks/{name}", "#\n")

    assert _findings(root) == []


@pytest.mark.parametrize(
    "run",
    [
        "[curl, -f, https://web.example/health]",  # a bare name is found on PATH
        "[kubectl, wait, kustomization/apps]",  # a slash in an argument of another program is not a path
        "[/opt/checks/web.sh]",  # absolute: a file of the machine that deploys
        "[python, -m, checks.web]",  # a module, not a file
        "[pwsh, -Command, 'Test-Path checks/x']",  # an inline program
        "[bash, -c, 'checks/web.sh']",
        "[pwsh, -File, 'checks/${deployment:name}.ps1']",  # a token: resolved at deploy
        "[bash, -o, pipefail, -c, 'checks/web.sh']",  # an option's value is not the script
        "[python, -W, ignore, -m, checks.web]",
        "[bash, --norc, pipefail]",  # an option strata does not know: its value has no extension, not reported
    ],
)
def test_what_is_not_a_file_of_the_solution_is_not_judged(tmp_path, run):
    assert _findings(_solution(tmp_path, f"            run: {run}\n")) == []


def test_a_disabled_check_with_a_missing_script_is_not_judged(tmp_path):
    assert _findings(_solution(tmp_path, "            run: [checks/web.sh]\n            enabled: false\n")) == []


def test_a_deployment_check_script_must_exist(tmp_path):
    root = _with_deployment_check(_solution(tmp_path, "            run: [check]\n"), "x")
    deployment = root / "deployment.yaml"
    deployment.write_text(
        deployment.read_text(encoding="utf-8").replace("run: [check, 'x']", "run: [python, checks/e2e.py]"),
        encoding="utf-8",
    )

    assert _findings(root) == [("error", "verify_script_missing", "spec.verify.checks.0.run.1")]
