#!/usr/bin/env python3
"""The `helm` verify check and the target contexts every command check gets (docs/design/post-deploy-verification.md,
D5, D11).

`run_command` is faked: the arguments, the environment and the answer read from `helm test`'s exit code are what is
checked, not Helm itself.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from strata.controllers import verification
from strata.controllers.deploy_controller import verify_environment
from strata.controllers.verification import CheckResult, run_verify_block
from strata.models.deployment_model import DeploymentSpecModel, DeploymentVerifyCheckModel, DeploymentVerifyModel
from strata.utils.transport import CommandResult


@pytest.fixture
def fake(monkeypatch):
    """`run_command` answering `state["code"]` with `state["stderr"]`; records every call."""
    state: dict = {"code": 0, "stdout": "", "stderr": "", "timed_out": False, "calls": []}

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        state["calls"].append({"args": list(args), "env": dict(env or {}), "timeout": timeout})
        return CommandResult(
            returncode=state["code"], stdout=state["stdout"], stderr=state["stderr"], timed_out=state["timed_out"]
        )

    monkeypatch.setattr(verification, "run_command", _run)
    monkeypatch.setattr(verification.shutil, "which", lambda name: f"/bin/{Path(name).name}")
    return state


def _run(check: dict, **kwargs) -> CheckResult:
    block = DeploymentVerifyModel.model_validate(
        {"initial_delay": 0, "timeout": 120, "failure_threshold": 5, "checks": [check]}
    )
    [result] = run_verify_block(
        block,
        deployment="app",
        step="apply_charts",
        tokens=kwargs.pop("tokens", {}),
        root=Path("/solution"),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        **kwargs,
    )
    return result


def test_a_helm_check_runs_the_charts_tests_with_the_deployments_context(fake):
    result = _run(
        {
            "name": "chart-tests",
            "type": "helm",
            "release": "${deployment:name}-web",
            "namespace": "apps",
            "tests": ["web-test-connection"],
        },
        tokens={"deployment:name": "app"},
        environment={"HELM_KUBECONTEXT": "aks-prd"},
    )

    assert (result.answer, result.attempts) == ("pass", 1)
    assert result.message == "helm test app-web: the chart's tests passed"
    [call] = fake["calls"]
    assert call["args"] == [
        "/bin/helm",
        "test",
        "app-web",
        "--namespace",
        "apps",
        "--timeout",
        "120s",
        "--filter",
        "name=web-test-connection",
    ]
    assert call["env"]["HELM_KUBECONTEXT"] == "aks-prd"
    assert call["env"]["STRATA_CHECK"] == "chart-tests"
    assert call["timeout"] > 120  # the process outlives `--timeout`, so Helm can report the tests


def test_a_failing_helm_test_fails_once_with_helms_error(fake):
    """Not retried: Helm waited for the release to be ready, and each attempt starts the test pods again."""
    fake.update(code=1, stderr="NAME: web\nError: 1 error occurred:\n\t* pod web-test-connection failed\n")

    result = _run({"name": "chart-tests", "type": "helm", "release": "web"})

    assert (result.answer, result.attempts) == ("fail", 1)
    assert result.message == "helm test web failed: Error: 1 error occurred:"


def test_a_helm_test_that_does_not_finish_is_unknown(fake):
    fake.update(code=-1, timed_out=True)

    result = _run({"name": "chart-tests", "type": "helm", "release": "web"})

    assert (result.answer, result.attempts) == ("unknown", 1)


def test_without_helm_on_path_the_check_cannot_answer(fake, monkeypatch):
    monkeypatch.setattr(verification.shutil, "which", lambda name: None)

    result = _run({"name": "chart-tests", "type": "helm", "release": "web"})

    assert (result.answer, result.attempts) == ("unknown", 1)
    assert fake["calls"] == []


def test_a_script_check_gets_the_contexts_and_its_own_env_wins(fake):
    _run(
        {"name": "pods", "run": ["kubectl", "get", "pods"], "env": {"DOCKER_CONTEXT": "mine"}},
        environment={"HELM_KUBECONTEXT": "aks-prd", "STRATA_KUBE_CONTEXT": "aks-prd", "DOCKER_CONTEXT": "swarm"},
    )

    [call] = fake["calls"]
    assert call["env"]["STRATA_KUBE_CONTEXT"] == "aks-prd"
    assert call["env"]["DOCKER_CONTEXT"] == "mine"


def test_a_passing_helm_test_runs_once_whatever_the_success_threshold(fake):
    """`helm test` starts the chart's test pods; a block's `success_threshold: 3` (for its other checks) must not
    run them three times."""
    block = DeploymentVerifyModel.model_validate(
        {"initial_delay": 0, "success_threshold": 3, "checks": [{"name": "t", "type": "helm", "release": "web"}]}
    )
    [result] = run_verify_block(
        block, deployment="app", step="s", tokens={}, root=Path("/solution"), sleep=lambda s: None, clock=lambda: 0.0
    )

    assert (result.answer, result.attempts, len(fake["calls"])) == ("pass", 1, 1)


def test_verify_environment_is_what_the_integrations_set_for_their_steps():
    from strata.integrations.compose import ComposeIntegration
    from strata.integrations.helm import HelmIntegration

    spec = DeploymentSpecModel.model_validate(
        {"workspace": "main", "environments": ["prd"], "kube_context": "aks-prd", "docker_context": "swarm-prd"}
    )

    environment = verify_environment(spec)

    assert HelmIntegration.target_context_env("aks-prd").items() <= environment.items()
    assert ComposeIntegration.target_context_env("swarm-prd").items() <= environment.items()


def test_verify_environment_names_the_deployments_contexts():
    spec = DeploymentSpecModel.model_validate(
        {"workspace": "main", "environments": ["prd"], "kube_context": "aks-prd", "docker_context": "swarm-prd"}
    )

    assert verify_environment(spec) == {
        "HELM_KUBECONTEXT": "aks-prd",
        "STRATA_KUBE_CONTEXT": "aks-prd",
        "DOCKER_CONTEXT": "swarm-prd",
        "STRATA_DOCKER_CONTEXT": "swarm-prd",
    }
    assert verify_environment(DeploymentSpecModel.model_validate({"workspace": "main", "environments": ["prd"]})) == {}


def _in_a_deployment(check: dict) -> DeploymentSpecModel:
    """A check is judged as part of a complete deployment (`DeploymentSpecModel.validate_verify_checks()`)."""
    return DeploymentSpecModel.model_validate(
        {"workspace": "main", "environments": ["prd"], "verify": {"checks": [check]}}
    )


@pytest.mark.parametrize(
    ("check", "message"),
    [
        ({"name": "c", "type": "helm"}, "needs 'release'"),
        ({"name": "c", "type": "helm", "release": "web", "when": ["destroy"]}, "cannot run after a destroy"),
        ({"name": "c", "type": "helm", "release": "web", "url": "https://x"}, "do not apply to type"),
        ({"name": "c", "run": ["check"], "release": "web"}, "do not apply to type"),
    ],
)
def test_a_helm_check_that_cannot_work_is_refused(check, message):
    with pytest.raises(ValidationError, match=message):
        _in_a_deployment(check)


def test_a_helm_checks_release_and_namespace_are_token_fields():
    check = DeploymentVerifyCheckModel.model_validate(
        {"name": "c", "type": "helm", "release": "${output:apply_charts.release}", "namespace": "${var:NS}"}
    )

    assert check.token_fields() == [("release", "${output:apply_charts.release}"), ("namespace", "${var:NS}")]
