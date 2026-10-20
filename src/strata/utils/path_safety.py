#!/usr/bin/env python3
"""Path-traversal safety helpers for file/module/source reference fields.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure string/path logic, no Pydantic dependency, reused across
`common_models.py` (`SourceModel`, `ModuleReferenceModel`) and multiple model
files (`module_model.py`, `namespace_model.py`).

Most helpers here validate a *declared string* before it ever becomes a real
path. `resolve_within()` is the exception and the complement: it checks an
already-built `Path` after the fact, for the cases a string check cannot
cover — a path assembled from runtime-supplied parts, or a glob match
strata never saw as a literal string at all.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

from strata.utils.repo_refs import split_repo_ref

#: A username on an http(s) URL that is really a token: a well-known token prefix, or a long
#: unbroken run of token characters. A plain account or organisation name (`git@`, an Azure DevOps
#: `myorg@`) is neither, so it passes.
_TOKEN_USERNAME = re.compile(r"(?:gh[pousr]_|github_pat_|glpat-|xox[abp]-)\S+|[A-Za-z0-9_\-]{32,}")


def validate_no_url_credentials(value: str, what: str) -> None:
    """Raise ``ValueError`` if `value` is a URL embedding a password, or a token as its http(s) username.

    Credentials belong in an Integration, never in a document or a process list. An `scp`-style
    `git@host:org/repo.git`, `ssh://git@host/...` and an ordinary `user@` carry no secret and pass; a
    personal access token is often pasted as `https://<token>@host/...` with no password, which the
    username check catches (heuristically: known prefixes, or 32+ token characters). The message never
    repeats `value`, which would print the secret.
    """
    if "://" not in value:
        return
    try:
        parts = urlsplit(value)
        password, username = parts.password, parts.username
    except ValueError:
        return  # not parseable as a URL, so there is no userinfo to find
    if password:
        raise ValueError(f"{what} must not embed a password or token; configure credentials through an Integration.")
    if parts.scheme in ("http", "https") and username and _TOKEN_USERNAME.fullmatch(username):
        raise ValueError(f"{what} must not embed a password or token; configure credentials through an Integration.")


def validate_no_path_traversal(value: str) -> None:
    """Raise ``ValueError`` if `value` is an absolute path or contains a `..`
    path segment.

    Guards against path traversal escaping a build/deploy output directory.
    Validation only — does not normalize/mutate `value` (callers that also
    want normalization, e.g. stripping slashes, should use
    `validate_relative_path` instead; callers where a trailing '/' is
    semantically meaningful, e.g. `ModuleFileModel.target`, should use this
    directly so that marker isn't stripped).

    `..` is matched per *segment*, not as a substring: `a/../b` is traversal
    and rejected, while a filename that merely contains two dots
    (`archive..json`, `v1..2`) is an ordinary name and is allowed.
    """
    path_str = str(value)

    if path_str.startswith("/") or path_str.startswith("\\"):
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if len(path_str) >= 2 and path_str[1] == ":":
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if ".." in path_str.replace("\\", "/").split("/"):
        raise ValueError(f"Path cannot contain parent directory references (..). Got: {path_str}")


def validate_not_option_like(value: str, what: str) -> None:
    """Raise ``ValueError`` if `value` starts with `-`.

    A URL or ref handed to `git` as a positional argument would otherwise be read as an option
    (`--upload-pack=...` runs a command), so it is rejected at the schema level.
    """
    if str(value).startswith("-"):
        raise ValueError(f"{what} must not start with '-' (it would be read as a command-line option). Got: {value}")


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


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and return it, raising ``ValueError`` if it lands
    outside `root`.

    The runtime complement to this module's string validators. Those reject a
    bad path at *declaration* time, which cannot cover two real cases:
    a path built from values only known at build time (e.g. an asset's
    `source_path` after its `{name}` tokens are filled from a deployment's
    `properties`), and a glob match, which is produced by the filesystem
    rather than written down anywhere.

    Resolves both sides before comparing, so `..` segments and symlinks are
    collapsed first — a purely lexical check would miss
    `root/subdir/../../outside` entirely.
    """
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"'{resolved}' resolves outside '{resolved_root}'")
    return resolved
