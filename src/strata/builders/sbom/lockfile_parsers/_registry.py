"""LockfileParserRegistry — global registry for lockfile parsers."""

import fnmatch
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from strata.builders.sbom.lockfile_parsers._base import LockfileParser


class LockfileParserRegistry:
    """Maps filename glob patterns to ``LockfileParser`` instances.

    Parsers are matched by **filename** (not full path).  Last registered
    parser wins — custom parsers registered after the defaults shadow any
    earlier registration for the same pattern.
    """

    def __init__(self) -> None:
        self._parsers: List["LockfileParser"] = []

    def register(self, parser: "LockfileParser") -> None:
        """Append *parser* to the registry.  Later registrations take precedence."""
        self._parsers.append(parser)

    def find(self, filename: str) -> Optional["LockfileParser"]:
        """Return the last-registered parser whose pattern matches *filename*."""
        for parser in reversed(self._parsers):
            for pattern in parser.filename_patterns():
                if fnmatch.fnmatch(filename, pattern):
                    return parser
        return None

    def all_patterns(self) -> List[str]:
        """Return all registered filename patterns (preserving insertion order, de-duplicated)."""
        seen: set[str] = set()
        result: List[str] = []
        for parser in self._parsers:
            for pattern in parser.filename_patterns():
                if pattern not in seen:
                    seen.add(pattern)
                    result.append(pattern)
        return result

    def copy(self) -> "LockfileParserRegistry":
        """Return a shallow copy — base for custom registries."""
        new = LockfileParserRegistry()
        new._parsers = list(self._parsers)
        return new


# DEFAULT_REGISTRY must be defined BEFORE LockfileParser so that
# __init_subclass__ can reference it at class-body execution time.
DEFAULT_REGISTRY = LockfileParserRegistry()
