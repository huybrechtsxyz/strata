# config/

A single, real, working solution used to dogfood `strata` end to end — not a
gallery of per-cloud example workspaces (that was v1's `config/`). Every
document here is exercised by the test suite and by hand via the CLI, so it
must always be valid.

## Entry points

- [`strata.yaml`](strata.yaml) — the solution manifest (`kind: solution`).
  Document discovery starts here: everything under this directory (except
  what's excluded below) is walked and indexed.
- [`configuration.yaml`](configuration.yaml) — platform-wide policy
  (`kind: configuration`): which providers/topologies this solution allows.

## Structure

Documents are addressed by `(kind, meta.name)` identity (ADR-0015), never by
file path — the loader walks up to `strata.yaml` and indexes every document
it finds by its `kind:` field, regardless of which directory it lives in or
what the file is named.

By convention, documents are grouped one directory per kind (pluralized,
e.g. `providers/`, `deployments/`, `environments/`) purely for human
readability. Nothing enforces this and nothing here reads directory names —
add, remove, rename, split, or merge any of these directories freely as the
example solution evolves; this file never needs to change to match.

Two directories are special:

- [`templates/`](templates/) — scaffolding: strata-shaped YAML with
  placeholders that would otherwise index as bogus documents. Explicitly
  excluded via `strata.yaml`'s `discovery.exclude`.
- [`vendor/`](vendor/) — local stand-ins for the remote module sources
  declared under `strata.yaml`'s `remotes:`, so the example builds end to
  end with no network access.

`.strata/` (if present) is runtime-only state, never a source of truth —
document discovery deliberately never descends into it.

## Using this solution

```bash
# Validate every document in the solution
strata validate

# Render a deployment's workspace into on-disk artifacts (name, not a file path)
strata build run prd-deployment
```

