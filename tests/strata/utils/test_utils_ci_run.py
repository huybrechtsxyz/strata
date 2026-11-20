#!/usr/bin/env python3
"""Tests for `resolve_pipeline_run()` (docs/design/audit-trail.md, "The audit files")."""

from strata.utils.ci_run import PipelineRun, resolve_pipeline_run

_AZURE = {
    "TF_BUILD": "True",
    "BUILD_BUILDID": "4711",
    "BUILD_BUILDNUMBER": "20261119.3",
    "BUILD_DEFINITIONNAME": "deploy-int",
    "BUILD_SOURCEBRANCH": "refs/heads/main",
    "SYSTEM_COLLECTIONURI": "https://dev.azure.com/acme/",
    "SYSTEM_TEAMPROJECT": "Platform Ops",
}

_GITHUB = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_RUN_ID": "9001",
    "GITHUB_RUN_NUMBER": "12",
    "GITHUB_WORKFLOW": "deploy",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "acme/platform",
}


def test_outside_ci_there_is_no_run():
    assert resolve_pipeline_run({}) is None
    assert resolve_pipeline_run({"BUILD_BUILDID": "1"}) is None  # without TF_BUILD it is not Azure Pipelines


def test_an_azure_pipelines_run_links_to_its_results_page():
    assert resolve_pipeline_run(_AZURE) == PipelineRun(
        system="azure_devops",
        run_id="4711",
        run_number="20261119.3",
        pipeline="deploy-int",
        branch="refs/heads/main",
        url="https://dev.azure.com/acme/Platform%20Ops/_build/results?buildId=4711",
    )


def test_an_azure_pipelines_run_without_a_collection_has_no_url():
    run = resolve_pipeline_run({"TF_BUILD": "True", "BUILD_BUILDID": "4711"})
    assert run is not None
    assert (run.run_id, run.url, run.pipeline) == ("4711", None, None)


def test_the_older_collection_variable_is_used_when_the_newer_is_missing():
    env = {k: v for k, v in _AZURE.items() if k != "SYSTEM_COLLECTIONURI"}
    env["SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"] = "https://acme.visualstudio.com"
    run = resolve_pipeline_run(env)
    assert run is not None
    assert run.url == "https://acme.visualstudio.com/Platform%20Ops/_build/results?buildId=4711"


def test_a_github_actions_run_links_to_its_run_page():
    assert resolve_pipeline_run(_GITHUB) == PipelineRun(
        system="github_actions",
        run_id="9001",
        run_number="12",
        pipeline="deploy",
        branch="refs/heads/main",
        url="https://github.com/acme/platform/actions/runs/9001",
    )
