"""
Custom lockfile parser template for strata.

Copy / rename this file and implement the class to teach DependencyFileCollector
how to read a new dependency manifest format.

Two ways to activate:

  Option A — zero config (recommended for quick additions):
    Save this file as .strata/lockfile_parsers/my_parser.py.
    It will be auto-imported on the next build — no collectors.yaml entry needed.

  Option B — explicit config:
    Declare it in .strata/collectors.yaml:

        collectors:
          - name: cargo-lock
            path: .strata/plugins/my_lockfile_parser.py
            type: lockfile_parser   # 'class' is optional — see How it works below

No changes to strata core code are required.

Minimal checklist
-----------------
1. Rename the class.
2. Set ecosystem to the correct purl type string (e.g. "cargo", "maven",
   "nuget", "gem", "pub", "hex").
   Full list: https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst
3. Set filename_patterns to glob(s) matching the target file name(s).
   Patterns are matched against the filename only, not the full path.
4. Implement parse() — return RawDependency(name, version) pairs.
   Return version=None for unpinned dependencies (still valid per purl spec).
5. Raise ValueError (only) for parse errors — DependencyFileCollector catches
   these, logs a warning, and continues.  Never raise any other exception.
6. Activate via Option A (drop folder) or Option B (collectors.yaml).

How it works
------------
Whether loaded via the drop folder or collectors.yaml, importing the file causes
this class body to execute, which triggers LockfileParser.__init_subclass__ — that
hook automatically calls DEFAULT_REGISTRY.register(MyLockfileParser()) without any
explicit register() call.  The 'class' key in collectors.yaml is therefore optional.

DependencyFileCollector uses DEFAULT_REGISTRY.all_patterns() to know which
filenames to look for, then calls the matched parser's parse() method.  It
handles purl construction, SbomComponentModel creation, and deduplication.

Test isolation
--------------
Because __init_subclass__ fires at class-definition time, test parsers MUST
use register=False to avoid polluting the global registry across test runs:

    class FakeParser(LockfileParser, register=False):
        ...

Or inject a fresh registry into DependencyFileCollector:

    from strata.builders.sbom.lockfile_parsers import LockfileParserRegistry
    from strata.builders.sbom.deps_collector import DependencyFileCollector

    registry = LockfileParserRegistry()
    registry.register(MyTestParser())
    collector = DependencyFileCollector(registry=registry)

Example: Cargo.lock parser (Rust)
----------------------------------
Replace the stub below with your implementation.  The Cargo.lock example is
left as inline comments to illustrate the pattern.

Example YAML (after renaming this file and choosing a name)
------------------------------------------------------------
  collectors:
    - name: cargo-lock
      path: .strata/plugins/my_lockfile_parser.py
      type: lockfile_parser
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers import LockfileParser, RawDependency


class MyLockfileParser(LockfileParser):
    """Replace this docstring with a description of the format this parser handles."""

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    @property
    def ecosystem(self) -> str:
        """Purl ecosystem identifier.

        Examples: "cargo", "maven", "nuget", "gem", "pub", "hex", "pypi",
        "npm", "golang", "composer".
        Full list: https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst
        """
        return "cargo"  # TODO: replace with the correct ecosystem

    def filename_patterns(self) -> List[str]:
        """Glob patterns matched against the filename (not the full path).

        Use fnmatch-style patterns.  Examples:
          ["Cargo.lock"]           — exact filename
          ["requirements*.txt"]    — prefix wildcard
          ["*.csproj"]             — extension wildcard
        """
        return ["Cargo.lock"]  # TODO: replace with the correct pattern(s)

    # ------------------------------------------------------------------ #
    # Parsing                                                              #
    # ------------------------------------------------------------------ #

    def parse(self, path: Path) -> List[RawDependency]:
        """Extract dependency (name, version) pairs from *path*.

        Rules:
        - Return version=None for unpinned dependencies (valid per purl spec).
        - Raise ValueError for unrecoverable parse errors — DependencyFileCollector
          catches these, logs a warning, and continues.
        - Never raise any other exception type.

        The example below parses Cargo.lock (TOML format).  Replace it with
        your own implementation.
        """
        # ----------------------------------------------------------------
        # Example: Cargo.lock  (TOML, [[package]] entries)
        # ----------------------------------------------------------------
        # import tomllib
        # try:
        #     with path.open("rb") as fh:
        #         data = tomllib.load(fh)
        # except Exception as exc:
        #     raise ValueError(f"Could not parse {path.name}: {exc}") from exc
        #
        # deps: List[RawDependency] = []
        # for pkg in data.get("package", []):
        #     name = pkg.get("name", "").strip()
        #     if not name:
        #         continue
        #     version = pkg.get("version")  # None if absent (workspace crates)
        #     deps.append(RawDependency(name=name, version=version))
        # return deps

        # TODO: implement and remove this placeholder
        raise NotImplementedError(
            f"{type(self).__name__}.parse() is not implemented yet. See the comments in this file for guidance."
        )
