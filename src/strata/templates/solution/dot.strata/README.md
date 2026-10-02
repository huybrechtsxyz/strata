# `.strata/`

This directory is managed by the `strata` CLI. Files under it are
refreshed automatically by `strata sln update` (rerun after upgrading the
installed `strata` package) — don't hand-edit anything here, since your
changes will be silently overwritten the next time someone runs it.

`schemas/` holds one JSON Schema per document kind, plus a `strata.json`
umbrella schema that dispatches to the right one based on each document's
own `kind:` field — regenerated from the installed `strata` package's own
models every time, so it always matches the version you have installed.
`.vscode/settings.json` (scaffolded once by `strata sln init`, yours to
customize afterwards) points VS Code's YAML extension at `strata.json`, so
every `.yaml`/`.yml` file in this solution gets real-time schema validation
and autocomplete out of the box.

Anything you *do* want to customize belongs outside `.strata/` — for
example, `.github/pull_request_template.md` and
`.github/ISSUE_TEMPLATE/deployment-change-request.yml` (scaffolded by
`strata sln init`) are yours to edit freely; `strata sln update` never
touches them.
