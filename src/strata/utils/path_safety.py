#!/usr/bin/env python3
"""Path-traversal safety helpers for file/module/source reference fields.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure string/path logic, no Pydantic dependency, reused across
`common_models.py` (`SourceModel`, `ModuleReferenceModel`) and multiple model
files (`module_model.py`, `namespace_model.py`).
"""

from strata.utils.repo_refs import split_repo_ref


def validate_no_path_traversal(value: str) -> None:
    """Raise ``ValueError`` if `value` is an absolute path or contains '..'.

    Guards against path traversal escaping a build/deploy output directory.
    Validation only — does not normalize/mutate `value` (callers that also
    want normalization, e.g. stripping slashes, should use
    `validate_relative_path` instead; callers where a trailing '/' is
    semantically meaningful, e.g. `ModuleFileModel.target`, should use this
    directly so that marker isn't stripped).
    """
    path_str = str(value)

    if path_str.startswith("/") or path_str.startswith("\\"):
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if len(path_str) >= 2 and path_str[1] == ":":
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if ".." in path_str:
        raise ValueError(f"Path cannot contain parent directory references (..). Got: {path_str}")


def validate_relative_path(value: str) -> str:
    """Validate that a path is relative and secure, and normalize it.

    Runs `validate_no_path_traversal()`, then normalizes backslashes to
    forward slashes and strips leading/trailing slashes. Used by `SourceModel`
    (`source_path`/`target_path`), where no trailing-slash convention applies.
    """
    validate_no_path_traversal(value)
    return str(value).replace("\\", "/").strip("/")


def validate_file_ref_no_traversal(value: str) -> None:
    """Raise ``ValueError`` if a file-reference string escapes its base directory.

    Handles the ``@reponame/...`` cross-repo reference convention via the
    canonical `split_repo_ref()` (`strata.utils.repo_refs`) rather than
    reimplementing ``@``-detection/parsing here — the ``@reponame`` segment
    itself isn't a filesystem path, so only the part after it is checked
    (e.g. ``@infra/../../etc/passwd`` is still rejected). A bare relative
    reference with no ``@`` prefix is checked as-is. Shared by
    `module_model.py`'s `ModuleFileModel` (`source`/`target`) and
    `namespace_model.py`'s `NamespaceModuleModel` (`file`) — any field naming
    a file/module reference that will be resolved relative to a repo or
    build/work directory should use this.
    """
    split = split_repo_ref(value)
    path_to_check = split["rest"] if split is not None else value
    if path_to_check:
        validate_no_path_traversal(path_to_check)
