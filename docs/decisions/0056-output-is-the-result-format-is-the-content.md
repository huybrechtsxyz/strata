# `--output` is the result, `--format` is the content

- Status: accepted
- Date: 2026-11-09
- Related: [ADR-0020](0020-v1-consumer-feature-priority.md) (`--output json` and `STRATA_OUTPUT` as the CI contract),
  [ADR-0033](0033-ndjson-streaming-follow-trigger.md) (why `--output` gained no `ndjson` value),
  [docs/design/deployment-documentation.md](../design/deployment-documentation.md) (`deploy report`, the first new command
  to need all three flags, and `deploy output`)

## Context and Problem Statement

Every command takes `-o/--output console|json`, also set by `STRATA_OUTPUT`. CI sets `STRATA_OUTPUT=json` once per job
and parses exactly one JSON envelope from stdout, with stable exit codes.

Some commands also produce content in more than one shape: `values get` prints a table, a bare value, `KEY=value`
lines or `export` lines, and the planned `deploy report` renders a table or a Markdown document. Some write that
content to a file: `promote view` and, planned, `deploy report`.

Two commands already solved this the same way, each on its own: `values get` added `--format` and ignores it under
`--output json`, and `promote view` added `--output-path` rather than reuse `--output`. Nothing states the rule, so
the next command could add `--output markdown` and break every pipeline that sets `STRATA_OUTPUT=json`.

## Considered Options

- **Add content shapes to `--output`** (`--output markdown`, `--output env`). One flag. But `STRATA_OUTPUT=json` would
  then pick the content as well as the envelope, and a value valid for one command would be invalid for the next.
- **A separate flag per command** (`--doc-format`, `--render`). Clear locally, inconsistent across the CLI.
- **Three flags, each with one meaning.** Chosen.

## Decision Outcome

1. **`-o/--output console|json` is the command's result**: the envelope, diagnostics and exit codes. Its values
   are the same on every command, and it never gains a value that describes content.
2. **`--format` is the content's shape**, for commands whose content has more than one. Its values belong to the
   command (`values get`: `table|raw|env|export`; `deploy output`: `table|raw`; `deploy report`:
   `table|markdown`). The default is the shape a person reads.
3. **Under `--output json`, stdout is one JSON document.** Content written to stdout goes into the envelope's `data`
   as structured data, and `--format` does not apply to it.
4. **`--output-path PATH` writes the content to a file**, in the shape `--format` picked. The envelope still goes to
   stdout, and with `--output json` its `data` confirms what was written instead of repeating it. Never `--out`,
   `--file` or `--output FILE`.

### Consequences

- Good: `STRATA_OUTPUT=json` keeps meaning one thing on every command, so a CI job that sets it never gets Markdown
  or `export` lines on stdout.
- Good: a command can add a content shape without touching the shared `--output` option.
- Good: the two existing commands already comply; nothing changes for users.
- Bad: three flags with similar names. Help text has to say which is which.
- Neutral: a command that writes no content beyond its result has no `--format` and no `--output-path`.
