#!/usr/bin/env python3
"""Version resolution — the one place the running version is determined.

`VERSION.txt` at the repository root is the single source. It feeds
`pyproject.toml` (`[tool.setuptools.dynamic]`), which feeds installed package
metadata, which is what this module reads. Nothing declares a version literal
in code, so there is no second copy to drift — the same rule
`strata.utils.layout` applies to paths.

**Known sharp edge, inherited from the same design in v1:** the runtime
version comes from *installed metadata*, not from `VERSION.txt` directly. In
an editable install, editing `VERSION.txt` does not change what
`get_version()` reports until the package is reinstalled. v1 hid this behind a
docstring claiming it read the file, and its own tests carried a
"Package metadata out of sync with VERSION.txt" warning for the dev-mode case.
Stating it plainly here instead.

The *distribution* name is resolved from the import package rather than
hardcoded. The system is called strata; the distribution is currently
published as `strata-v2` only so v2 can be developed alongside v1. Naming it
here would bake a temporary packaging artifact into permanent code and break
silently at rename time.
"""

from functools import lru_cache
from importlib.metadata import packages_distributions
from importlib.metadata import version as metadata_version

#: Import package name — the permanent identity of the system, unaffected by
#: whatever the distribution is currently published as.
PACKAGE_NAME = "strata"

#: Reported when the package is not installed (running straight from a source
#: checkout). Deliberately not a hardcoded version number: a fallback literal
#: would be a second source of truth that silently disagrees with the real one.
UNKNOWN_VERSION = "unknown (not installed)"


def get_distribution_name() -> str | None:
    """Return the distribution providing the `strata` package, if installed.

    Returns:
        The distribution name, or None when the package is not installed.
        Editable installs can list the same distribution more than once, so
        the first entry is taken — they are all the same distribution.
    """
    distributions = packages_distributions().get(PACKAGE_NAME)
    return distributions[0] if distributions else None


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the installed strata version.

    Cached: `packages_distributions()` scans every distribution in the
    environment, which is far too expensive to repeat per call.

    Returns:
        The version from package metadata, or `UNKNOWN_VERSION` when strata is
        not installed in the active environment.
    """
    distribution = get_distribution_name()
    if distribution is None:
        return UNKNOWN_VERSION
    return metadata_version(distribution)
