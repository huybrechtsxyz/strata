#!/usr/bin/env python3
"""Parse HCL with `python-hcl2`, reusing the result for a text parsed before.

Parsing is slow (tens of milliseconds for a small file), and the same files are read more than once: `build run`
reads a root module's `.tf` files for the SBOM, `deploy run` reads them again for the declared outputs and
variables. The parse is cached on the file's text, so an edited file is parsed again and two copies of one module
are parsed once. Every caller gets its own copy, which it may change freely.
"""

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import hcl2

#: Distinct texts kept. A module's `.tf` files and lock file are a few kilobytes each.
_CACHED_TEXTS = 256


def load_hcl_file(path: Path) -> Any:
    """The parsed content of the HCL file at `path`.

    Raises:
        OSError: The file cannot be read.
        Exception: The text is not valid HCL (whatever `python-hcl2` raises).
    """
    return parse_hcl(path.read_text(encoding="utf-8"))


def parse_hcl(text: str) -> Any:
    """`hcl2.loads(text)`, from the cache when this text was parsed before. A text that fails is not cached."""
    return copy.deepcopy(_parse(text))


@lru_cache(maxsize=_CACHED_TEXTS)
def _parse(text: str) -> Any:
    return hcl2.loads(text)
