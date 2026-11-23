#!/usr/bin/env python3
"""The handover document says whether the deploy was verified (docs/design/post-deploy-verification.md, D9)."""

import pytest

from strata.controllers.deployment_document import render_document
from strata.controllers.deployment_report_view import DeploymentReport
from strata.utils.diagnostics import Diagnostics


def _render(audit: dict | None) -> str:
    report = DeploymentReport(
        deployment="app",
        workspace="main",
        environments=["prd"],
        steps=[],
        secrets=[],
        images=[],
        diagnostics=Diagnostics(),
    )
    return render_document(report, audit=audit)


def _audit(verification: str | None) -> dict:
    return {
        "deployed_by": "ci",
        "completed_at": "2026-11-23T10:00:00+00:00",
        "status": "success",
        "action": "deploy",
        "version": "2.9.0",
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "commit_sha": "abcdef0123",
        "verification": verification,
    }


@pytest.mark.parametrize(
    ("verification", "line"),
    [
        ("verified", "Verified: the verify checks said the deployment works."),
        ("verification_failed", "Not verified: a verify check failed or could not answer"),
        ("not_verified", "Not verified: no verify check ran."),
    ],
)
def test_the_handover_says_whether_the_deploy_was_verified(verification, line):
    assert line in _render(_audit(verification))


def test_an_audit_summary_recorded_before_verification_says_nothing_about_it():
    """A summary an earlier strata stored has no `verification`: the document makes no claim either way."""
    document = _render(_audit(None))

    assert "Deployed 2026-11-23" in document
    assert "erified" not in document


def test_without_an_audit_summary_the_document_says_nothing_about_verification():
    assert "erified" not in _render(None)
