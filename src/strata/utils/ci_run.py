#!/usr/bin/env python3
"""Which CI run is executing this process, for the audit manifest's `pipeline` (docs/design/audit-trail.md,
"The audit files").

Read from the variables Azure Pipelines and GitHub Actions set on every job. Pure: no network, never raises, and
`None` outside CI. None of these values is a secret. The link to the run is the point: the run log holds the full
output, and denied builds, which the manifest does not.
"""

import os
from collections.abc import Mapping
from typing import Literal, NamedTuple
from urllib.parse import quote


class PipelineRun(NamedTuple):
    """One CI run."""

    system: Literal["azure_devops", "github_actions"]
    run_id: str
    run_number: str | None
    pipeline: str | None
    branch: str | None
    url: str | None


def resolve_pipeline_run(env: Mapping[str, str] | None = None) -> PipelineRun | None:
    """The CI run this process belongs to, or `None` when it is not running in Azure Pipelines or GitHub Actions."""
    env = os.environ if env is None else env
    if env.get("TF_BUILD", "").lower() == "true" and env.get("BUILD_BUILDID"):
        return _azure_devops(env)
    if env.get("GITHUB_ACTIONS", "").lower() == "true" and env.get("GITHUB_RUN_ID"):
        return _github_actions(env)
    return None


def _azure_devops(env: Mapping[str, str]) -> PipelineRun:
    run_id = env["BUILD_BUILDID"]
    collection = env.get("SYSTEM_COLLECTIONURI") or env.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI")
    project = env.get("SYSTEM_TEAMPROJECT")
    url = (
        f"{collection.rstrip('/')}/{quote(project)}/_build/results?buildId={quote(run_id)}"
        if collection and project
        else None
    )
    return PipelineRun(
        system="azure_devops",
        run_id=run_id,
        run_number=env.get("BUILD_BUILDNUMBER") or None,
        pipeline=env.get("BUILD_DEFINITIONNAME") or None,
        branch=env.get("BUILD_SOURCEBRANCH") or None,
        url=url,
    )


def _github_actions(env: Mapping[str, str]) -> PipelineRun:
    run_id = env["GITHUB_RUN_ID"]
    server = env.get("GITHUB_SERVER_URL")
    repository = env.get("GITHUB_REPOSITORY")
    url = f"{server.rstrip('/')}/{repository}/actions/runs/{quote(run_id)}" if server and repository else None
    return PipelineRun(
        system="github_actions",
        run_id=run_id,
        run_number=env.get("GITHUB_RUN_NUMBER") or None,
        pipeline=env.get("GITHUB_WORKFLOW") or None,
        branch=env.get("GITHUB_REF") or None,
        url=url,
    )
