#!/usr/bin/env python3
"""Version constraints for `IntegrationModel.spec.version` (docs/design/integration-readiness.md, "Version constraints")
and a remote's `requires` (docs/design/remotes.md, "Which versions a solution accepts").

PEP 440 specifiers, plus Terraform's `~>`: `~> 1.9` means `>= 1.9, < 2.0`, which is exactly PEP 440's
`~= 1.9`, so it is read as that. Clauses may be separated by a comma or, as in npm and semver ranges, by a space:
`>=1.4.0 <2.0.0` is `>=1.4.0, <2.0.0`.
"""

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

#: Whitespace between the end of one clause's version and the next clause's operator.
_SPACE_BETWEEN_CLAUSES = re.compile(r"(?<=[\w.*+])\s+(?=[<>=!~])")


def parse_version_constraint(text: str) -> SpecifierSet:
    """Parse a version constraint such as `>= 1.7`, `~= 1.9`, `~> 1.9` or `>=1.4.0 <2.0.0`.

    Raises:
        ValueError: `text` is not a valid constraint.
    """
    try:
        return SpecifierSet(_SPACE_BETWEEN_CLAUSES.sub(", ", text.replace("~>", "~=")))
    except InvalidSpecifier:
        raise ValueError(
            f"'{text}' is not a version constraint (examples: '>= 1.7', '~= 1.9', '~> 1.9', '>= 1.7, < 2.0', "
            "'>=1.4.0 <2.0.0')"
        ) from None


def version_satisfies(installed: str, constraint: str) -> bool:
    """Whether `installed` meets `constraint`; a prerelease counts as its release, an unparseable version never does.

    Raises:
        ValueError: `constraint` is not a valid constraint.
    """
    return parse_version_constraint(constraint).contains(installed, prereleases=True)


def is_version(text: str) -> bool:
    """Whether `text` is a version (`1.9.2`, `v3.13.2`), not tool output that `parse_version()` could not pick apart."""
    try:
        Version(text)
    except InvalidVersion:
        return False
    return True
