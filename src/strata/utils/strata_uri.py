#!/usr/bin/env python3
"""The ``strata://`` URI scheme — durable identity for workspace objects (ADR-0034).

A diagram node's connection back to the workspace lives in the Mermaid text
itself, as a ``click`` directive pointing at one of these URIs. That is what
survives copy/paste out of an editor: an in-memory node map does not.

Shape, structural rather than positional::

    strata://<kind>/<name>[/<child-kind>/<child-name>]

No line numbers are encoded, so reformatting, reordering, or inserting YAML
above the target does not break the reference. Turning a URI into a concrete
``{file, line}`` is a separate, on-demand lookup.

``file`` is the one kind whose name is a path, so everything after
``strata://file/`` is taken verbatim::

    strata://file/deploy/deploy-prd.yaml
    strata://workspace/platform/resource/app_server
    strata://environment/env-prd/secret/DB_PASSWORD
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SCHEME = "strata://"
FILE_KIND = "file"

_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class UriError(ValueError):
    """Raised when a ``strata://`` URI is malformed."""


@dataclass(frozen=True)
class StrataUri:
    """A parsed ``strata://`` URI."""

    kind: str
    name: str
    child_kind: Optional[str] = None
    child_name: Optional[str] = None

    @property
    def is_file(self) -> bool:
        """True when this URI names a file rather than an object inside one."""
        return self.kind == FILE_KIND

    def __str__(self) -> str:
        return build_uri(self.kind, self.name, self.child_kind, self.child_name)


def build_uri(
    kind: str,
    name: str,
    child_kind: Optional[str] = None,
    child_name: Optional[str] = None,
) -> str:
    """Compose a ``strata://`` URI.

    Args:
        kind: Document kind, or ``file``.
        name: Document ``meta.name``, or the workspace-relative path for ``file``.
        child_kind: Kind of the object inside the document, if any.
        child_name: Name of that object.

    Returns:
        The URI string.
    """
    parts = [kind, name]
    if child_kind and child_name:
        parts += [child_kind, child_name]
    return SCHEME + "/".join(str(part).strip("/") for part in parts)


def parse_uri(uri: str) -> StrataUri:
    """Parse a ``strata://`` URI.

    Args:
        uri: URI string.

    Returns:
        The parsed :class:`StrataUri`.

    Raises:
        UriError: If the scheme, segment count, or segment format is wrong.
    """
    text = (uri or "").strip()
    if not text.startswith(SCHEME):
        raise UriError(f"'{uri}' is not a strata URI — expected it to start with '{SCHEME}'.")

    remainder = text[len(SCHEME) :]
    kind, _, rest = remainder.partition("/")

    if not kind or not _KIND_RE.match(kind):
        raise UriError(
            f"'{uri}' has no valid kind segment. Expected '{SCHEME}<kind>/<name>[/<child-kind>/<child-name>]'."
        )

    if kind == FILE_KIND:
        # A file's name is a path, so it keeps its slashes.
        if not rest:
            raise UriError(f"'{uri}' names no file. Expected '{SCHEME}file/<path>'.")
        return StrataUri(kind=FILE_KIND, name=rest)

    segments = [segment for segment in rest.split("/")] if rest else []
    if not segments or not segments[0]:
        raise UriError(f"'{uri}' names no {kind}. Expected '{SCHEME}{kind}/<name>'.")
    if any(not segment for segment in segments):
        raise UriError(f"'{uri}' has an empty segment.")

    if len(segments) == 1:
        return StrataUri(kind=kind, name=segments[0])
    if len(segments) == 3:
        if not _KIND_RE.match(segments[1]):
            raise UriError(f"'{uri}' has an invalid child kind '{segments[1]}'.")
        return StrataUri(kind=kind, name=segments[0], child_kind=segments[1], child_name=segments[2])

    raise UriError(
        f"'{uri}' has {len(segments) + 1} segments. Expected 2 "
        f"('{SCHEME}<kind>/<name>') or 4 ('{SCHEME}<kind>/<name>/<child-kind>/<child-name>')."
    )
