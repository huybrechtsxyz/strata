#!/usr/bin/env python3
"""Version constraints for `IntegrationModel.spec.version` (docs/work/integration-readiness.md D3).

PEP 440 specifiers, plus Terraform's `~>`: `~> 1.9` means `>= 1.9, < 2.0`, which is exactly PEP 440's
`~= 1.9`, so it is read as that.
"""

from packaging.specifiers import InvalidSpecifier, SpecifierSet


def parse_version_constraint(text: str) -> SpecifierSet:
    """Parse a version constraint such as `>= 1.7`, `~= 1.9` or `~> 1.9`.

    Raises:
        ValueError: `text` is not a valid constraint.
    """
    try:
        return SpecifierSet(text.replace("~>", "~="))
    except InvalidSpecifier:
        raise ValueError(
            f"'{text}' is not a version constraint (examples: '>= 1.7', '~= 1.9', '~> 1.9', '>= 1.7, < 2.0')"
        ) from None


def version_satisfies(installed: str, constraint: str) -> bool:
    """Whether `installed` meets `constraint`; a prerelease counts as its release, an unparseable version never does.

    Raises:
        ValueError: `constraint` is not a valid constraint.
    """
    return parse_version_constraint(constraint).contains(installed, prereleases=True)
