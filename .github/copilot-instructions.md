# strata-v2 — Agent Instructions

`strata-v2` is a ground-up rebuild of the `strata` CLI (an infrastructure-as-code
platform layered over Terraform/Helm/Compose). It is **not** a straight port of
v1 — every feature is rebuilt only after being justified by real evidence (v1's
own source, and real consumer CI in `haven`/`config-deploy`), not by
assumption. Read [docs/design/v2-schema-overview.md](../docs/design/v2-schema-overview.md)
for the current one-page status of every kind/feature.

## Architecture

Strict layering (ADR-0003), enforced by import-linter — a lower layer must
never import a higher one:

```
strata.commands > controllers > services > integrations > models > utils > logging
```

- **Identity, not paths.** Every cross-document reference is `(kind, meta.name)`
  (ADR-0015), never a file path. A field that references another document
  should be `Annotated[PlatformName, References(PlatformKind.X)]`
  (`strata/models/reference_fields.py`) so `references.py`'s generic walker
  checks it for free. A field that is only *conditionally* a reference (its
  meaning depends on a sibling field, e.g. `VariableStoreModel.value` when
  `store == artifact`) is **not** a `References()` candidate — write a
  dedicated service method instead (see `WorkspaceService.
  validate_topology_references()` for the established pattern).
- **Document discovery** walks up from a path to find `strata.yaml` (the
  solution manifest) and deliberately never descends into `.strata/`
  (runtime-only state, never a source of truth — see `layout.py`).
- **`docs/decisions/`** — ADRs, MADR format, one immutable decision per file.
  **`docs/design/`** — living docs for anything with ongoing build-out (append-only
  Changelog section). See each folder's own `README.md` for the exact conventions
  before writing either.

## Build, Lint, Test

```powershell
uv sync                                                          # install (creates ./.venv)
& .\.venv\Scripts\python.exe -m mypy src                         # strict mode
& .\.venv\Scripts\python.exe -m ruff check --fix src tests
& .\.venv\Scripts\lint-imports.exe                                # layering contract
& .\.venv\Scripts\python.exe -m pytest -q
```

Run all four after every change, in that order — this is the same sequence CI
runs (`nox -s lint` / `nox -s test`). A change isn't done until all four are clean.

**`uv sync`/`uv run` may fail with `No solution found... setuptools`** — an Acme
private-feed quirk, not a real dependency conflict. Add
`--index-strategy unsafe-best-match` to the command and retry.

## Conventions worth knowing

- **Evidence over assumption.** When porting or designing a v1-derived feature,
  ground it in the real, installed v1 source or a real consumer's actual CI
  workflow — not the documented/assumed behavior. Several "confirmed" v1
  behaviors turned out to be no-ops or version-drifted once actually traced to
  their real read site; cite what you verified, and flag what you couldn't.
- **Small, tested, documented phases.** Non-trivial features get a design doc
  with an Implementation Plan checklist and phased delivery — one phase, one
  focused change, full check suite, then the next.
- **`config/`** at the repo root is a real, working example solution (not test
  fixture) used to dogfood the CLI end to end — safe to `cd config && strata
  build run ...` against.
- Tests live under `tests/strata/...`, mirroring `src/strata/...` — one test
  file per module, `test_<area>_<name>.py`.
