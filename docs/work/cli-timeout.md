# External CLI timeouts (Terraform, Helm, Compose) — Work

- Status: draft
- Last updated: 2026-10-07

## Overview

An inventory of every timeout applied to an external CLI subprocess
(`terraform`, `helm`, `docker compose`) run by `strata`, taken from the
real source rather than assumed. The headline finding: every one of these
is a hardcoded Python default on the relevant `Integration` method — none
are configurable today via CLI flag, YAML config field, or environment
variable. A real `terraform apply` that legitimately runs longer than the
hardcoded 30-minute default (large cloud deployments aren't unusual) has no
way to get more time short of editing source.

This doc exists to track that gap and the design questions around closing
it — not to propose a specific fix yet.

## Current Design / Progress

### Terraform (`src/strata/integrations/terraform.py`)

| Method                                                   | Timeout        | Notes                                                                                                         |
| -------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------- |
| `version` (via `Integration.get_version()` in `base.py`) | 10s            |                                                                                                               |
| `init`                                                   | 300s (5 min)   |                                                                                                               |
| `validate`                                               | 60s            |                                                                                                               |
| `output`                                                 | 60s            |                                                                                                               |
| `show`                                                   | 60s            |                                                                                                               |
| `plan`                                                   | 600s (10 min)  | also backs v1's `plan_destroy` (`destroy=True`) and `drift` (`detailed_exitcode=True`) — not separate methods |
| `deploy` (`terraform apply`)                             | 1800s (30 min) |                                                                                                               |
| `destroy`                                                | 1800s (30 min) |                                                                                                               |

### Other CLI integrations, for comparison

- **Helm** (`src/strata/integrations/helm.py`): `deploy_namespace`/`uninstall`
  use a Python `timeout=600`/`300` *in addition to* Helm's own `--timeout 5m`
  CLI flag (a separate `deploy_timeout: str` param baked into argv) — two
  independent timeout layers that can disagree. `lint`/`repo_update` = 60s.
- **Compose** (`src/strata/integrations/compose.py`): `plan` (`stack config`)
  = 60s, `deploy`/`destroy` = 300s, `services` = 60s.
- **Generic `Integration.run()`/`.request()`** (`src/strata/integrations/base.py`):
  base-class defaults are `run()` = 300s, `request()` = 30s — only matter
  when a subclass doesn't override (the cli-transport default and the
  networked-transport default, respectively).

### Enforcement mechanics (`src/strata/utils/transport.py`)

`run_command()` dispatches to one of two paths depending on whether a
`line_callback` was given:

- `_run_buffered()` (no streaming) — `subprocess.communicate(timeout=...)`,
  catches `TimeoutExpired`.
- `_run_streaming()` (the real deploy path, `line_callback` set) — polls
  reader-thread joins against a `time.monotonic()` deadline, since
  `communicate()` isn't in play while lines are being drained live.

Either way, on expiry the child is `.kill()`ed (never left orphaned) and the
result comes back as `CommandResult(returncode=124, timed_out=True)` —
`run_command()` never raises for a timeout, matching this module's general
"failure is an outcome, not an exception" contract (see the module
docstring). `CommandResult.is_successful` checks both `returncode == 0` and
`not timed_out`, since a killed process can still report exit 0 on some
platforms.

### Call sites

`deploy_controller.py`'s real deploy path (`plan_or_warn()` → `.deploy()`)
never passes a `timeout=` override — every call just accepts whichever
default the method declares. Confirmed by grep: no CLI flag
(`src/strata/commands/**`), no config model field, and no environment
variable anywhere in the codebase currently feeds a timeout value into any
of these calls. The one superficially-similar field,
`DeploymentModel.wait_timeout` (`src/strata/models/deployment_model.py`),
is unrelated — it bounds how long to wait for a held *lock*, not a
subprocess.

## Related Decisions

- None yet — no ADR covers CLI subprocess timeout configurability.

## Remaining Work / Open Questions

- Should timeout be configurable at all, and if so, at what granularity?
  Candidates: a per-provisioner/per-step field (e.g. on `ProvisionerModel`
  or the deployment step model), a global CLI `--timeout` flag threaded
  through `deploy run`, an environment variable (`STRATA_CLI_TIMEOUT`), or
  some combination with step-level overriding global.
- If configurable, do `plan`/`deploy`/`destroy` get independent values, or
  one knob for all three? v1 precedent (if any) hasn't been checked yet —
  needs evidence before designing, not assumption (repo convention: ground
  in real v1 source/consumer CI, not documented/assumed behavior).
- Helm's dual-timeout (Python subprocess timeout + Helm's own `--timeout`
  flag) raising/lowering independently is a latent footgun worth resolving
  as part of any redesign — today the two can disagree with confusing
  results (e.g. the CLI's own `--timeout 5m` fires first, process exits
  cleanly, Python's larger timeout never triggers — or vice versa).
- No test coverage was reviewed yet for the timeout-expiry path itself
  (`timed_out=True`, returncode 124) on any real integration — only the
  `transport.py` unit-level behavior was traced from source.

## Changelog

- 2026-10-07: Created from an inventory of every `timeout=` default across
  `terraform.py`/`helm.py`/`compose.py`/`base.py`/`transport.py`, done in
  response to a direct ask to look at external CLI timeouts.
