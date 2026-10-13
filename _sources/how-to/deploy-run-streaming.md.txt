# How To: Stream `deploy run` Output Live (`--follow`)

`strata deploy run` can show subprocess output (Terraform/Helm/Compose) live as
it happens instead of only at the very end. This guide covers both streaming
modes, how they interact with `--output`/`STRATA_OUTPUT`, and what's piped
where. See [docs/archive/cli-output-mode-interactions.md](../archive/cli-output-mode-interactions.md)
and [docs/archive/ndjson-output.md](../archive/ndjson-output.md) for the full design
rationale and evidence trail.

## The short answer

- **Want to watch a real apply as it happens, on your terminal?** Add `--follow`.
- **Want machine-parseable output *and* live progress in CI?** Use
  `--output json --follow ndjson`.
- **Want machine-parseable output with no live stream** (the common, default
  CI shape)? Use `--output json` alone — `--follow` is opt-in, never implied.
- `--follow` is a plain flag with an optional value: bare `--follow` means
  `console`; `--follow ndjson` means NDJSON. There's no `--output ndjson` —
  the format of the *stream* is always `--follow`'s own job, independent of
  `--output`.

## Mode 1 — `--follow` (console streaming)

```bash
strata deploy run my-deployment --follow
```

Every subprocess output line appears as it arrives, tool-prefixed:

```
    running step 'apply_infra' via terraform
      terraform │ azurerm_resource_group.main: Creating...
      terraform │ azurerm_resource_group.main: Creation complete after 2s
    deployed step 'apply_infra' via terraform
```

stdout lines print in cyan, stderr lines in yellow (`--no-color`/`NO_COLOR`
disables this like everywhere else in the CLI). This is the default, console
output shape — nothing new here if `--output` is left at its default.

### Under `--output json`

```bash
strata deploy run my-deployment --output json --follow
```

Lines **reroute to stderr** instead of disappearing — stdout still ends with
exactly one clean JSON document, safe to pipe to `jq`:

```bash
strata deploy run my-deployment --output json --follow | jq .
#   terraform │ ...   <- visible on your terminal (stderr), not piped
#   { "ok": true, ... }  <- the only thing jq actually receives (stdout)
```

This matches `--verbose`'s own "logs always go to stderr, stdout stays clean"
behavior — nothing about `--output json`'s single-document contract changes;
you just also get to watch progress on the terminal while it runs.

**Before 2026-10-07** this combination silently did nothing at all (no lines,
no warning) — if you're on an older build and see no output here, upgrade.

## Mode 2 — `--follow ndjson` (structured streaming)

For a pipeline that wants to *parse* the live progress too — not just watch
it — `--follow ndjson` streams one JSON object per line to stdout instead,
ending with the normal result as the final line:

```bash
strata deploy run my-deployment --output json --follow ndjson
```

**Requires `--output json` explicitly** — `--follow ndjson --output console`
(or no `--output` at all) is rejected:

```
Error: --follow ndjson requires --output json.
```

### Output shape

```json
{"event": "progress", "message": "running step 'apply_infra' via terraform", "ts": "2026-10-07T12:00:00+00:00"}
{"event": "line", "tool": "terraform", "stream": "stdout", "text": "azurerm_resource_group.main: Creating...", "ts": "2026-10-07T12:00:01+00:00"}
{"event": "line", "tool": "terraform", "stream": "stdout", "text": "azurerm_resource_group.main: Creation complete after 2s", "ts": "2026-10-07T12:00:03+00:00"}
{"event": "progress", "message": "deployed step 'apply_infra' via terraform", "ts": "2026-10-07T12:00:03+00:00"}
{"ok": true, "command": "deploy run", "version": "2.0.0a8", "context": {"solution": "my-solution", "deployment": "my-deployment"}, "summary": {"errors": 0, "warnings": 0, "info": 0}, "diagnostics": [], "data": {}}
```

Every line is independently parseable:

```bash
strata deploy run my-deployment --output json --follow ndjson | while IFS= read -r line; do
  event=$(echo "$line" | jq -r '.event // "result"')
  case "$event" in
    line)     echo "LIVE: $(echo "$line" | jq -r '.text')" ;;
    progress) echo "STEP: $(echo "$line" | jq -r '.message')" ;;
    result)   echo "$line" > result.json ;;
  esac
done
```

**The last line is always the normal result** — exactly the same
`{ok, command, version, context, summary, diagnostics, data}` shape plain
`--output json` (no `--follow`) produces, nothing bespoke. A consumer that
only cares about the final outcome can ignore every line except the last one.

### `stream` is metadata, not routing

Every event — including one with `"stream": "stderr"` — is written to v2's
own stdout. `stream` only records which stream the *subprocess* line came
from; it never means "this event went to your stderr." If you need
stdout/stderr-origin lines separated, filter on the `stream` field yourself:

```bash
... | jq 'select(.event == "line" and .stream == "stderr")'
```

## Interaction with other flags

| Flag                    | Effect on `--follow`                                                                                                                                                 |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--quiet`               | None. `--quiet` only suppresses the console header/step/summary chrome — it never touches `--follow`'s stream. `--quiet --follow` together still streams every line. |
| `--verbose`             | None (independent mechanism). `--verbose`'s own INFO-level logs go to stderr regardless, alongside whatever `--follow` is doing.                                     |
| `STRATA_OUTPUT` env var | Same precedence as every other `--output`-consuming flag — an explicit `--output console` on the command line beats a pipeline-wide `STRATA_OUTPUT=json`.            |
| `--dry-run`             | Still streams — a dry run still calls `init`/`validate`/`plan` for real, so there's real subprocess output to show.                                                  |
| `--smoke-test`          | `--follow` only emits `progress` events (which steps would run) — `--smoke-test` makes zero tool contact, so there's no `line` events to stream.                     |

## What's not covered yet

- **Other commands.** `--follow`/`--follow ndjson` only exist on `deploy run`
  today. `build run` has no subprocess/line-callback plumbing at all — there's
  nothing to stream. No other command has a `--follow` flag.
- **Structured per-item events for non-subprocess commands.** v1 used NDJSON
  for commands with no subprocess at all too (e.g. `repo sync`, emitting one
  event per synced repo) — no equivalent command exists in v2 yet, so this
  shape isn't designed or built.
- **Finer-grained lifecycle events.** `progress` events exist only at
  whole-step boundaries (step started/finished) — there's no separate
  "init started"/"plan finished"-style event within a step.
