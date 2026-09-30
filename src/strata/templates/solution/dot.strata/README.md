# `.strata/`

This directory is managed by the `strata` CLI. Files under it are
refreshed automatically by `strata sln update` (rerun after upgrading the
installed `strata` package) — don't hand-edit anything here, since your
changes will be silently overwritten the next time someone runs it.

Anything you *do* want to customize belongs outside `.strata/` — for
example, `.github/pull_request_template.md` and
`.github/ISSUE_TEMPLATE/deployment-change-request.yml` (scaffolded by
`strata sln init`) are yours to edit freely; `strata sln update` never
touches them.
