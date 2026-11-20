#!/usr/bin/env python3
"""Why a failed deploy failed, as the metrics file's bounded `error_category` (docs/design/audit-trail.md,
"Why a run failed").

Only signals strata produces itself, or one tool message that does not change between versions, decide it; anything
else is `unknown` rather than a guess. In order, the first that matches wins:

1. a policy with `enforcement: deny` that did not pass (`policy_results`): the gates run before any step, so a denial
   is the cause;
2. the code of an error, in run order (`_BY_CODE`);
3. a marker in an error's text: strata's own "timed out and was killed" (`deploy_controller._failure_detail()`), and
   Terraform's "Error acquiring the state lock".

`quota`, `network` and `conflict` have no signal in a deploy yet: the clouds word them differently, inside tool
output.
"""

from collections.abc import Sequence

from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.audit_metrics_model import ErrorCategory
from strata.utils.diagnostics import Diagnostics

#: Only codes a deploy run raises: `integration_unreachable` and `integration_credentials_rejected` belong to
#: `strata integration test`, and `helm_release_conflict` to `validate`, which a deploy needs to pass first.
_BY_CODE: dict[str, ErrorCategory] = {
    "integration_credentials_unresolved": ErrorCategory.AUTH,
    "helm_registry_auth_failed": ErrorCategory.AUTH,
    "helm_registry_credentials_invalid": ErrorCategory.AUTH,
}

_BY_MARKER: tuple[tuple[str, ErrorCategory], ...] = (
    ("timed out and was killed", ErrorCategory.TIMEOUT),
    ("Error acquiring the state lock", ErrorCategory.STATE_LOCK),
)


def error_category(
    run_diagnostics: Diagnostics, policy_results: Sequence[ManifestPolicyResultModel] | None
) -> ErrorCategory | None:
    """The category of a failed run; `None` for a run without errors."""
    if run_diagnostics.ok:
        return None
    if any(result.enforcement == "deny" and not result.passed for result in policy_results or ()):
        return ErrorCategory.POLICY
    errors = run_diagnostics.errors
    for error in errors:
        if error.code in _BY_CODE:
            return _BY_CODE[error.code]
    for error in errors:
        for marker, category in _BY_MARKER:
            if marker in error.message:
                return category
    return ErrorCategory.UNKNOWN
