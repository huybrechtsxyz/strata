#!/usr/bin/env python3
"""Directory-family naming convention matching (docs/design/path-conventions.md).

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure string/regex logic, no Pydantic dependency — mirrors
`path_safety.py`'s own placement. `semantic_checks.py` (a controller, above
both layers) owns turning a mismatch into a `Diagnostics` finding; this
module only answers "is this relative path in scope, and if so, does its
directory/filename match?".

`scope` is deliberately restricted to `"<relative-dir>/**"` — every real
`cfg-int-deployment` convention is this shape (`"customers/**"`,
`"providers/**"`, `"deploy/control/**"`, `"deploy/hubs/**"`), so a plain
string-prefix check covers it without a general glob engine (see the
design doc's Open Question #3, resolved: keep it simple until a real
counter-example appears).

A convention's directory shape (`pattern`) and filename shape
(`filename_pattern`) are two independent, both-optional matches against
two different substrings of a document's real relative path, split on the
final `/` — not one combined full-path match. v1's own real `hub-path`/
`control-path` conventions never mention a filename at all (they only
care about the containing directory); `provider-path`'s real capture
(`{region}`) lives entirely in the filename, with no directory capture at
all. One combined pattern can't cleanly express both shapes without
forcing every convention to spell out a filename it may not care about —
see the design doc's "Why pattern and filename_pattern are split".
"""

import re

_SEGMENT_TOKEN = re.compile(r"\{(\w+)\}")


def in_scope(relative_path: str, scope: str) -> bool:
    """True when `relative_path` falls under `scope`'s directory prefix.

    `scope` is always `"<dir>/**"` (see module docstring) — the prefix is
    everything before the trailing `**`, i.e. `"customers/**"` ->
    `"customers/"`. `relative_path` must already be normalized to
    forward-slash-separated, root-relative form (`IndexEntry.source`
    relative to the solution root, via `.as_posix()`).
    """
    prefix = scope.removesuffix("**")
    return relative_path.startswith(prefix)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a `{name}`-templated template into an anchored regex.

    Each `{name}` token becomes a named group matching one path segment
    (`[^/]+` — never crosses a `/`, so a pattern only ever describes one
    level of directory structure per token). Literal regex metacharacters
    in the template (e.g. `.` in `tenant.yaml`) are escaped via
    `re.escape()` on the literal spans between tokens. Shared by both
    `match_directory()` (a multi-segment directory template) and
    `match_filename()` (a single-segment filename template) — the same
    templating either way, just matched against a different substring.

    Example: `"customers/{code}"` -> `^customers/(?P<code>[^/]+)$`;
    `"{region}.yaml"` -> `^(?P<region>[^/]+)\\.yaml$`.
    """
    pieces: list[str] = []
    last_end = 0
    for match in _SEGMENT_TOKEN.finditer(pattern):
        pieces.append(re.escape(pattern[last_end : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        last_end = match.end()
    pieces.append(re.escape(pattern[last_end:]))
    return re.compile("^" + "".join(pieces) + "$")


def split_directory_and_filename(relative_path: str) -> tuple[str, str]:
    """Split `relative_path` into its containing directory and bare filename.

    A document with no `/` at all (filed directly at the solution root) has
    no directory component — returns `("", relative_path)`. No real
    `scope` (always `"<dir>/**"`) can ever match a root-level file anyway,
    so this only matters for a hypothetical future one; defined here so
    that case has a non-crashing, documented answer.
    """
    directory, sep, filename = relative_path.rpartition("/")
    return (directory, filename) if sep else ("", relative_path)


def match_directory(relative_path: str, pattern: str) -> re.Match[str] | None:
    """Match `pattern` against `relative_path`'s containing directory only.

    Recompiles `pattern` on every call — call sites checking many documents
    against one convention should compile once via `compile_pattern()` and
    call `.match()` directly instead, if this becomes a hot path.
    """
    directory, _filename = split_directory_and_filename(relative_path)
    return compile_pattern(pattern).match(directory)


def match_filename(relative_path: str, filename_pattern: str) -> re.Match[str] | None:
    """Match `filename_pattern` against `relative_path`'s bare filename only.

    Recompiles `filename_pattern` on every call — see `match_directory()`'s
    own note on compiling once instead, if this becomes a hot path.
    """
    _directory, filename = split_directory_and_filename(relative_path)
    return compile_pattern(filename_pattern).match(filename)


def matches_segment_pattern(pattern: str, value: str) -> bool:
    """True when `value` matches a `PathSegmentModel`'s own regex `pattern`.

    Kept here (not a bare `re.match()` call at the controller layer) so
    every regex operation this feature needs stays in one, pure-regex file.
    """
    return re.match(pattern, value) is not None
