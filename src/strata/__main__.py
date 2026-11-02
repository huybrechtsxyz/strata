#!/usr/bin/env python3
"""Enable `python -m strata`.

The console script is named `strata`, which is correct for release but is
shadowed during development by whatever `strata` is already on PATH — v1 is
installed globally as a uv tool while v2 is developed alongside it. Module
execution resolves through the *import* path instead, so
`python -m strata` always runs the strata you are pointing at.
"""

from strata.commands.cli import main

if __name__ == "__main__":
    main()
