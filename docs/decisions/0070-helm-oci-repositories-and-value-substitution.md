# Fix Helm OCI Chart Repository Support and `${KEY}` Value Substitution

- Status: proposed
- Date: 2026-08-24

## Context and Problem Statement

Two independent bugs in the Helm builder/deployer path (`src/strata/builders/helm_builder.py`,
`src/strata/deployers/helm_deployer.py`) make `type: helm` modules unusable for any chart
distributed via an OCI registry, and make secret/variable injection silently produce
literal placeholder strings instead of real values in the deployed release. Both were
confirmed by reading the source directly (strata `1.6.1`, also present on `main`), not
inferred from behavior alone.

### Bug 1 — `oci://` chart repositories are not supported

`SourceModel.chart_repository` ([src/strata/models/common_models.py](../../src/strata/models/common_models.py#L277))
explicitly documents OCI refs as valid input: *"Helm chart repository URL or OCI
reference (e.g. `https://charts.goauthentik.io` or `oci://ghcr.io/org/charts`)"*. The
deployer never special-cases the scheme, though:

```python
# src/strata/deployers/helm_deployer.py
def _sanitize_repo_name(url: str) -> str:
    name = re.sub(r"^https?://", "", url)   # oci:// is never stripped
    name = re.sub(r"[^a-zA-Z0-9]", "-", name)
    name = name.strip("-")
    return name[:20]
```

`validate_workspace()` always does:

```python
repo_name = _sanitize_repo_name(chart_repository)
chart_ref = f"{repo_name}/{chart_name}"
```

and `setup()` always calls `helm repo add <repo_name> <chart_repository>` regardless of
scheme. `helm repo add` only understands classic `index.yaml`-based HTTP(S) repos — it
does not work against an OCI registry URL. Since `setup()` discards the `repo add` exit
code (`# Ignore failure — repo may already be registered`), the failure is swallowed
silently, and `plan()`/`apply()` then fail later with `chart_ref` pointing at a repo
alias that was never actually registered.

**Repro:** a module with
`source: {chart_repository: "oci://ghcr.io/some-org/charts", chart_name: "foo"}` →
`strata deploy run` fails at `apply` (`repo <alias> not found`), even though the exact
same `oci://...` ref works fine with a bare
`helm upgrade --install <name> oci://ghcr.io/some-org/charts/foo` on the CLI directly
(Helm 3.8+ supports OCI refs natively, no `repo add` needed).

**Real-world trigger:** immich-app's official Helm chart moved from an HTTP repo to
OCI-only distribution (`oci://ghcr.io/immich-app/immich-charts/immich`) — this is likely
to affect any recently-migrated chart, not just this one.

### Bug 2 — `${KEY}` secret/variable substitution never resolves for Helm modules

`helm_builder.py`'s module docstring claims: *"Secrets and variable/feature references
are emitted as `${KEY}` substitution tokens. The deployer injects real values via
`--set` flags at deploy time."* This does not happen.

`_render_module_artifacts()` writes `env.KEY = "${SECRET_NAME}"` literally into
`values.yaml` for any `ModuleServiceEnvironmentModel` with `secret:`/`var:`/`feature:`
set. At deploy time, `helm_deployer.py`'s `plan()`/`apply()`:

```python
with inject_compose_env(self.resolved_values):
    ...
    args = ["upgrade", "--install", ..., "-f", str(target.values_file), ...]
```

`inject_compose_env()` only sets plain OS environment variables around the subprocess
call — it never generates `--set KEY=value` args, and it never runs any substitution
pass over `values.yaml` before handing it to Helm. Unlike Docker Compose, Helm does
**not** interpolate `${VAR}` inside a values file against the process environment, so
the literal string `${SECRET_NAME}` gets deployed as the actual value.

**Repro:** module with
`services: [{name: x, environment: [{key: DB_PASSWORD, secret: DB_PASSWORD}]}]`,
`type: helm` → after `strata deploy run`, `helm get values <release>` shows
`DB_PASSWORD: ${DB_PASSWORD}` (the literal token), not the resolved secret.

## Considered Options

### Bug 1 — OCI support

- **A. Skip `repo add`/`repo update` for `oci://` sources.** When
  `chart_repository.startswith("oci://")`, don't register a repo alias at all;
  construct `chart_ref` as `f"{chart_repository.rstrip('/')}/{chart_name}"` (the full
  OCI ref) and pass it straight to `helm upgrade`/`helm lint`/`helm pull`. `check()`
  already special-cases registry charts by skipping `lint` (`repo_url is not None`) —
  OCI charts fall into the same bucket without extra code.
- **B. Attempt `helm registry login` + `repo add` translation.** Try to map OCI refs
  onto Helm's classic repo model via some shim. Rejected — Helm itself does not treat
  OCI registries as "repos" in the `repo add` sense; there is no clean 1:1 mapping, and
  this would just reintroduce the same fragility the bug report is about.

### Bug 2 — value substitution

- **A. Emit `--set-string <path>=<value>` per token at `plan`/`apply` time.** Walk the
  rendered `values.yaml` doc for `${KEY}` tokens (matching resolved variable/secret/
  feature names) and emit a corresponding `--set-string` flag per match, using values
  already available via `self.resolved_values`. Keeps the on-disk `values.yaml`
  secret-free (as the builder intends) since the real value only ever appears as a
  process argument passed to `helm`, not written to disk. `--set-string` also avoids
  Helm's YAML-type-coercion surprises (e.g. a secret value that looks like `"true"` or
  `"123"` being parsed as bool/int instead of string).
  - Trade-off: secret values become visible in the OS process list (`ps`/task manager)
    for the lifetime of the `helm` subprocess — same exposure class already accepted by
    `inject_compose_env`/`inject_tf_vars` for other deployers, so this does not
    introduce a new risk category, only extends secret exposure from process env to
    process args.
- **B. Render a temp, substituted copy of `values.yaml` at deploy time and delete it
  after `helm upgrade` completes.** Keeps `strata build run` output secret-free
  (substitution happens only at deploy time), but requires careful temp-file lifecycle
  handling (crash-safe cleanup, correct permissions, avoiding leaving secrets on disk if
  the process is killed mid-run) that the codebase doesn't currently have a pattern for
  in this deployer.
- **C. Do nothing / require chart-native secret handling (e.g. External Secrets
  Operator).** Rejected — contradicts the documented behavior in the builder's own
  docstring and leaves the feature silently broken for any user who takes the
  docstring at face value.

## Decision Outcome

Chosen for Bug 1: **Option A** — special-case `oci://` in `_sanitize_repo_name`'s
caller (or an equivalent scheme check in `validate_workspace()`/`setup()`), skipping
`repo add`/`repo update` entirely for OCI sources and building `chart_ref` directly
from the full OCI URL.

Chosen for Bug 2: **Option A** — emit `--set-string` flags derived from
`self.resolved_values` for each `${KEY}` token found in the rendered values file, at
`plan()`/`apply()` time only (never at build time), because it avoids introducing new
temp-file lifecycle/cleanup responsibility in the deployer and reuses the existing
argument-injection pattern (`inject_compose_env`/`inject_tf_vars`) already established
for other deployers in this codebase.

### Consequences

- Good: `type: helm` modules work against OCI-only chart distributions (e.g.
  immich-app) without any workaround.
- Good: `${KEY}` tokens in Helm values actually resolve to real secret/variable/feature
  values at deploy time, matching the behavior already documented (and already relied
  upon by users following the docstring).
- Good: `strata build run` output remains secret-free — substitution only happens as
  process arguments at deploy time, never written back to `values.yaml` on disk.
- Bad: `setup()`/`check()` need an explicit scheme branch, adding a small amount of
  branching complexity to `helm_deployer.py`.
- Bad: secret values become visible in OS process listings for the duration of each
  `helm` subprocess call (same exposure class as existing `inject_tf_vars` usage for
  Terraform, not a new risk category for this codebase).
- Bad: `--set-string` values must be matched against `${KEY}` tokens present in the
  rendered values doc — a token whose key doesn't resolve (typo, deleted secret) needs
  explicit handling (fail loudly rather than silently leaving the literal token in
  place, which is exactly today's silent failure mode).

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Not started — nothing in this ADR has been implemented yet.
- Bug 1: add an `oci://` scheme branch in `validate_workspace()` (chart_ref
  construction) and `setup()` (skip `repo add`/`repo update` for OCI repo URLs);
  `check()`'s existing `repo_url is not None` skip-lint branch should continue to cover
  OCI charts unchanged.
- Bug 2: walk the rendered `values.yaml` doc in `plan()`/`apply()` for `${KEY}` tokens,
  cross-reference against `self.resolved_values` (variables/secrets/features), and
  append `--set-string <dotted.path>=<value>` per match to the `helm upgrade` args;
  decide and implement the failure mode for tokens that don't resolve to any known key.
- Add regression tests: an OCI-repository module fixture (`setup()`/`chart_ref`
  resolution without `repo add`), and a values-substitution fixture asserting the
  final `helm upgrade` argv contains `--set-string` for each `${KEY}` token instead of
  the literal token reaching disk or Helm unresolved.
