#!/usr/bin/env python3
"""`git` as a local tool (docs/work/integration-readiness.md R1).

Source sync, remotes, assets, audit pushes and GitOps all run the `git` CLI, so a `type: git`
Integration document (a `sources` credential holder) is also a statement about that tool: on PATH,
and at `spec.version` when set. Nothing dispatches through this class; it exists so readiness can
check the tool like any other.
"""

import re
from typing import ClassVar

from strata.integrations.base import Integration
from strata.models.integration_model import Capability


class GitIntegration(Integration):
    """The `git` CLI behind every `sources` fetch and push."""

    TYPE: ClassVar[str] = "git"
    CAPABILITIES: ClassVar[frozenset[str]] = frozenset({Capability.SOURCES})
    TRANSPORTS: ClassVar[frozenset[str]] = frozenset({"cli"})
    COMMAND: ClassVar[str | None] = "git"

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `git version 2.45.1.windows.1`."""
        match = re.search(r"(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()
