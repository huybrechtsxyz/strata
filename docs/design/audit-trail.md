# Audit Trail — Design

- Status: current
- Last updated: 2026-11-19

## Overview

Every real `deploy run`, `deploy apply` and `deploy destroy` leaves a record of what it did: a deployment manifest
(`_manifest.json`) and a metrics record (`_metrics.json`). Both are written once, locally, at the end of the run,
then sent to every configured audit sink: a git repository, where they are kept, or an integration (a webhook, OTel
or Sentinel), which receives a CloudEvent rendered from them. A dry run and a smoke test write nothing.

The record answers the questions a change auditor asks (NIS2, ISO 27001 A.12.1.2 and A.12.4, ISAE 3402 Type II):

| Question                     | Answered by                                                                                          |
| ---------------------------- | ---------------------------------------------------------------------------------------------------- |
| What changed                 | `artifacts`: the build's `resolved.yaml` by hash, each repository's commit, images, charts, providers; the SBOM by hash |
| Why                          | `change_reference`, from the `--change-*` flags                                                      |
| Who approved                 | `change_reference.approved_by`/`approved_at` (stated by the operator, not checked against a tracker) |
| Who ran it, and how it went  | `deployed_by`, `pipeline` (the CI run and its log), `status`, timings, findings, `policy_results`    |
| Every change in a period     | `strata audit changes`, reading the git sink ([audit-commands.md](audit-commands.md))                |

`spec.audit` is optional. With none, a deploy still writes a full local trail and sends it nowhere.

Older docs number the parts as v1 did: Layer 1 is pull request evidence, Layer 2 the deploy record, Layer 3 reading
the trail back, Layer 4 forwarding to other systems. Two neighbouring design docs cover parts in depth: [audit-sink-dispatch.md](audit-sink-dispatch.md) (the integration
sink and its event envelope) and [audit-commands.md](audit-commands.md) (`strata audit status` and `changes`).

## Current Design

### Configuration

`spec.audit` lives on the Configuration document ([audit_model.py](../../src/strata/models/audit_model.py)), not on
an environment or deployment: whoever edits the thing being deployed must not be able to switch off or reroute its
audit trail.

| Field                     | Meaning                                                                                                   |
| ------------------------- | --------------------------------------------------------------------------------------------------------- |
| `event_overrides`         | Switch event types on or off, for example `{deployment.measured: false}`. Keys must be known event types. |
| `sinks`                   | Where records are sent; see "Sinks". Names are unique.                                                    |
| `require_remote_commits`  | Fail a deploy whose build has no clean commit for every repository it used ([remote-commit-provenance.md](remote-commit-provenance.md)). |

There is no `path`, `structure` or `journal`. The local directory is a layout fact (`layout.audit_dir()`,
`.strata/audit`), and the directory shape under it is derived from the deployment (see "Path resolution").

### Events

A closed set of three event types, all on by default:

| Event                  | Sent for                 | Rendered from    |
| ---------------------- | ------------------------ | ---------------- |
| `deployment.completed` | A deploy, success or not | `_manifest.json` |
| `deployment.destroyed` | A destroy                | `_manifest.json` |
| `deployment.measured`  | A deploy or destroy      | `_metrics.json`  |

A sink receives an event when the event type is on (`EVENT_DEFAULTS` with `event_overrides` applied), the sink is
`enabled`, and its `events` list is empty or names the type. An unknown event type in either place, or a sink that
lists a type the overrides switched off, is a validation error. The set grows only when a new producer is built.

### The audit files

Both files sit in one directory per run and share an `execution_id`.

**`_manifest.json`** ([audit_manifest_model.py](../../src/strata/models/audit_manifest_model.py)) is the full
record:

- identity: `execution_id`, `action` (`deploy` or `destroy`), strata `version`, `deployment`, `workspace`,
  `environment`, and `file`, the deployment document's path relative to the solution root (absolute for a document
  from a repository outside it);
- timing and outcome: `started_at`, `completed_at`, `duration_seconds`, `status`, and the run's own findings by
  severity: `errors`, `warnings`, `messages`;
- who and from what: `deployed_by` (see "Actor"); `pipeline`, the CI run that deployed (Azure Pipelines or GitHub
  Actions: system, run id and number, pipeline, branch, and a link to the run), read from the job's own variables
  by `ci_run.resolve_pipeline_run()` and left out outside CI; `commit_sha` and `commit_dirty` of the solution, both
  copied from the build's record ([remote-commit-provenance.md](remote-commit-provenance.md));
- `artifacts`: `platform` (`resolved.yaml` by hash and path, never its content), `repositories` (each remote's url,
  ref, commit and dirty flag), `images`, `charts`, and `providers` (each provisioner's type, tool version and path);
- `stages`, one per provisioning step (see "Stages");
- `sbom` (path, format, sha256, component count), `change_reference` and `policy_results`.

Satellite files are referenced by path and hash, never embedded: the manifest stays small and vouches for each file
without copying it.

**`_metrics.json`** ([audit_metrics_model.py](../../src/strata/models/audit_metrics_model.py)) is the lean sibling,
meant to be shipped to dashboards and SIEMs more freely:

- `dimensions` (what to slice by: `outcome`, `action`, `environment`, the deployment's `tenant`, `actor`, the
  manifest's `commit_sha`, and `verification`: `verified`, `verification_failed` or `not_verified`, the manifest's
  `verified` as one word), `measures` (what to add up: `duration_seconds`, the stage counts,
  `policy_checks_total`/`policy_checks_failed`, counted from `policy_results`, a check that did not pass counting as
  failed whatever its enforcement, and left out when no policy was judged; and `checks_total`/`checks_failed`, the
  verify checks of the stages and of `spec.verify`, a check that failed or could not answer counting as failed
  whatever its enforcement, left out when no check ran). A measure that is left out was not measured, because
  `null` cannot tell "not measured" from "zero";
- `error_category` on a failed or partial run, a bounded enum (`auth`, `quota`, `timeout`, `conflict`, `policy`,
  `network`, `state_lock`, `verification`, `unknown`; see "Why a run failed"). Raw error text stays in the manifest;
  a failing Terraform run can print a connection string;
- `label_safe`, the dimensions bounded enough to use as metric labels (`execution_id`, `actor` and `commit_sha` are
  not).

It holds only facts known inside the deploying process. Deployment frequency, failure rate and time to recover need
history (or a future event) and are left to whoever aggregates the records.

**The event is rendered, never written.** An integration sink gets a CloudEvents 1.0 envelope with ECS fields,
rendered from the manifest or metrics at send time (`audit_event_rendering.py`). It points to the manifest by path
and hash instead of carrying it, and gets a fresh `id` on every send. Its `event.outcome` uses the ECS values:
`success`, or `failure` for a failed or partial run. `labels.verification` says what the verify checks said
(`verified`, `verification_failed`, `not_verified`), so a rule can tell a deploy that works from one that only
applied; a metrics record written before it existed has no such label.

### Why a run failed

`error_category` ([audit_error_category.py](../../src/strata/controllers/audit_error_category.py)) is decided only
by signals strata produces itself, or a tool message that does not change between versions; anything else is
`unknown`, never a guess. The first that matches wins:

1. `policy`: a policy result with `enforcement: deny` that did not pass. The gates run before any step, so a denial
   is the cause.
2. `verification`: every error is a verify check's answer (`verify_check_failed`, `verify_check_unknown`). The
   steps were applied and the deployed workload does not work, so the workload is the problem, not the deploy. A
   check that could not be set up (`verify_outputs_unavailable`) is not this.
3. An error code a deploy raises, in run order: `integration_credentials_unresolved`, `helm_registry_auth_failed` and
   `helm_registry_credentials_invalid` are `auth`.
4. A marker in an error's text: strata's own "timed out and was killed" is `timeout`; Terraform's "Error acquiring
   the state lock" is `state_lock`.

`quota`, `network` and `conflict` have no signal in a deploy yet: the clouds word them differently, inside tool
output. `integration_unreachable` and `integration_credentials_rejected` come only from `strata integration test`,
and `helm_release_conflict` from `validate`. A new deploy code that names a cause is added to the table in step 3.

### Stages

A stage is one provisioning step the run selected (after `--stage` and `--scope`), in run order; a deploy stops
at the first failing step, so the order tells what ran. Each holds:

- `name`, `provisioner`, `status` (`success`, `failed` or `skipped`, with a `skip_reason`), `started_at`,
  `completed_at`, `duration_seconds`;
- `steps`, one per phase that ran, with `success` and `duration_seconds`. The phases are named as a deployment
  stage's `timeouts` are: `setup` (`init`), `check` (`validate`, or a container step's check), `plan`, `apply`,
  `destroy`. `deploy apply` has no `check` or `plan`; a container step has no `setup`;
- `errors`, `warnings`, `messages`: the run's findings that name the step (their `location`). They are also in the
  manifest's own lists, which hold every finding.

`deploy_run()` fills a `StageLog` ([deploy_stage_log.py](../../src/strata/controllers/deploy_stage_log.py)) as
it goes and still returns only its findings. The loop says which steps it means to run, when each begins and which
phase it is in; the outcome is worked out from the findings once the run returned, so none of the loop's early
returns needs bookkeeping:

- a stage the loop returned out of failed, since the loop only returns after an error; so did a stage whose phase
  added an error, or one an error names;
- a step never begun is `skipped` ("an earlier step failed", or "the run stopped before this step" when a check
  before the first step failed), unless an error names it, as a failed tool preflight does: then it is `failed`;
- under `deploy destroy`, a step whose tool cannot destroy is skipped with that reason.

**`partial`**: a run that failed after a stage succeeded is `partial`, not `failed`: something changed before it
stopped, which is what an auditor needs to know. The metrics keep `outcome` to `success` and `failed` (a partial
run counts as failed in a failure rate), as does the event's `outcome`; `stages_total` counts the stages that ran,
`stages_failed` the failed ones.

**No output values.** A stage carries no step outputs and no reference to an outputs file: the values stay in the
workspace's `output_store`, where `deploy output` reads them, rather than in a file sent to every sink, where an
output not marked sensitive could still carry a secret.

### Policy results

Policies are judged in other commands than the one that writes the manifest: `cve_policy`, `checkov_policy` and
`image_tag_policy` in `build run`, `image_tag_policy` again and the security gates in `deploy run`, any of them in
`strata policy check`. Each writes a `ManifestPolicyResultModel` to `policy_results.json` in the build directory
([policy_results.py](../../src/strata/controllers/policy_results.py)); a later result replaces an earlier one with
the same `policy_name`. The audit step reads that file into `policy_results`. A destroy records none: it judges no
policy, and the file is left from the earlier deploy.

`passed` follows `Diagnostics.ok`, so a `warn` breach is `passed: true`, but its findings are still listed in
`violations`. A policy without an operator-chosen name is recorded under its field name (`cve_policy`).

Only deploy commands write a manifest. A denied `build run` keeps its results in `policy_results.json`, and the CI
log records the attempt; a denied `deploy run` writes a `failed` manifest with the denying result
([security-gates.md](security-gates.md), "6.5 Policy results").

### Write once locally, then distribute

`finalize_and_distribute_deploy_audit()` ([audit_run.py](../../src/strata/controllers/audit_run.py)) runs after
`deploy_run()` returns, whatever it returned:

1. `execution_id` and `started_at` were fixed at the top of the run.
2. The manifest and metrics are completed and written once to `.strata/audit/<relative path>/`.
3. One loop over the sinks sends them: a git sink copies the files and pushes, an integration sink sends a rendered
   event per admitted type.

Writing once, after everything is known, avoids v1's write-then-enrich-then-rewrite, which left an incomplete record
on disk between the two writes.

### Path resolution

`resolve_audit_relative_path()` ([audit_path_resolution.py](../../src/strata/controllers/audit_path_resolution.py))
gives each run's directory, used by the local write and by every git sink:

```text
<layers.segments in the convention's order, or the workspace>/<deployment>/<yyyymmdd-hhmmss>
```

- With `layers.follows`/`layers.segments`, the segment values in the order the `spec.paths` convention declares
  them, so the trail mirrors the deployment hierarchy (`hub1/spoke1/fabrikam/dev/...`). Otherwise the workspace name.
- The deployment name always follows. Deployments in one folder share their segments, and without the name two of
  them started in the same second would overwrite each other.
- The timestamp leaf is always added, from `started_at`, so a retry lands in the same place. It has no colons
  because Windows paths cannot hold them; the JSON keeps full ISO-8601.

### Sinks

A sink ([audit_model.py](../../src/strata/models/audit_model.py) `AuditSinkModel`) has `name`, `enabled`,
`required`, `events`, and exactly one target:

- **`integration`**: an Integration document with the `audit` capability. Each admitted event is rendered and sent
  through it ([audit-sink-dispatch.md](audit-sink-dispatch.md)).
- **`git`**: `remote` (a remote in `strata.yaml`), `branch`, `path` (a folder in the repository) and `action`
  (`push`, `commit` or `none`).

Neither target holds a URL or a credential. The git target's transport lives on the remote (`url`, `integration`),
and the integration target's on the Integration document.

```yaml
spec:
  audit:
    sinks:
      - name: prod-siem
        integration: splunk-prod
        events: [deployment.completed, deployment.destroyed]
      - name: config-repo
        git:
          remote: platform-config
          branch: main
          path: audit
```

### The git sink

The files are copied to `<path>/<relative path>/` in the repository, committed and pushed
([audit_push.py](../../src/strata/controllers/audit_push.py)).

- **A `reference` remote:** strata keeps its own clone under `.strata/audit-push/<remote>/<branch>`, keyed by branch
  rather than ref because a push target is a moving tip. It fetches and resets before every write, and pushes
  `HEAD:<branch>`, which works from a detached checkout.
- **A `path` remote:** strata writes in the pipeline's own checkout, commits only the files it wrote, never resets,
  and pushes with the checkout's credentials, rebasing and retrying when the branch moved. With `action: commit` or
  `none`, a later pipeline step pushes ([remote-commit-provenance.md](remote-commit-provenance.md), "Writing to a
  path remote").

`strata audit status` and `changes` read the same branch back through a separate checkout,
`.strata/audit-read/<remote>/<branch>`, so a read never runs git in a tree a deploy is pushing from
([audit-commands.md](audit-commands.md)).

### When delivery fails

A failed push or send is always a warning in the run's report. With the sink's `required: true` it is an error, and
the command exits with code 3. A deploy that changed infrastructure is not reported as failed over a lost audit
copy unless the operator said that copy is mandatory. A missing, disabled or non-`audit` integration goes through the
same `required` rule rather than raising.

### Actor

`deployed_by` comes from `resolve_actor()` ([actor.py](../../src/strata/utils/actor.py)): the CI's own variable
(`BUILD_REQUESTEDFOR`, then `BUILD_REQUESTEDFOREMAIL`), then the OS user, then `"unknown"`. It never fails a deploy.
It is best effort: anyone can set those variables. It is still a different and better answer than a commit author,
which names who wrote some code, not who ran the deploy.

### Change reference

Six `deploy run`/`deploy apply` flags fill `change_reference` (`ChangeReferenceModel`): `--change-system`,
`--change-id`, `--change-reason` (these three together or not at all; a partial set is a usage error before anything
runs), `--change-classification`, `--change-title` and `--change-url`. `--change-approved-by` and
`--change-approved-at` record an approval. `supplied_by` and `supplied_at` are derived, never passed.

For an Azure DevOps approval, the ticket and the reason come from queue-time pipeline parameters passed to these
flags. The approval step itself cannot supply them: an approval check has no per-run input, only an approver comment,
and a job cannot find out which approval let it run. Environment checks also only gate `deployment` jobs, so the job
that runs `strata deploy` has to be one.

### Pull request templates

`strata sln init` and `update` scaffold `.github/pull_request_template.md` and an issue template for a deployment
change request ([solution-scaffolding.md](solution-scaffolding.md)). The justification, risk and rollback plan live
in the pull request; nothing copies them into the manifest.

### Not covered

- **`audit diff`**, comparing the resolved values of two records. `commit_sha` and the artifact hashes say which
  versions ran; a value-level diff waits for a real request.
- **Pull request evidence** (the PR, its approvers). Deploys are not PR-triggered, the deployed HEAD is often not the
  change's merge commit, and a lookup would need a GitHub or Azure DevOps token.
- **Validate-phase policies** (`tenant_zone`, `path_convention`) in `policy_results`: `strata validate` has no build
  directory and no fixed relation to a later deploy.
- **Event types beyond the three** (`policy.violated`, `secret.accessed`, ...), added when a producer exists.
- **A journal of every command** and **a local append-only metrics series**. Most deploys run on throwaway CI agents,
  where neither would accumulate; aggregation reads the git sink instead.
- **A cloud-CLI or OIDC identity** for `deployed_by`; it would slot in above the CI variables.

## Related Decisions

- [audit-sink-dispatch.md](audit-sink-dispatch.md) — the integration sink, the `audit` capability and the event
  envelope.
- [audit-commands.md](audit-commands.md) — `strata audit status` and `changes`, reading the git sink.
- [remote-commit-provenance.md](remote-commit-provenance.md) — the commits in the manifest, and writing to a path
  remote.
- [security-gates.md](security-gates.md) and [image-tag-policy.md](image-tag-policy.md) — policies whose results the
  manifest carries.
- [deploy-destroy.md](deploy-destroy.md) — what a destroy records.
- [solution-scaffolding.md](solution-scaffolding.md) — the pull request and issue templates.

## History

- v2's audit trail started from a measured look at v1's: a real 18,853-line `.strata/audit.log` was 95% editor
  polling and test noise with no deploy events, and half its configuration was read by nothing. The lessons kept:
  no configuration that nothing reads, an explicit actor field, one sink-resolution path for sending and resending,
  and no field that can hold a credential.
- Scope came from what a real platform configuration used: pull request templates and a git-pushed deploy record
  were live; reporting commands, the journal and the SIEM sink were unused or disabled. Those two were built first,
  and the rest only when a consumer appeared.
- v1's three overlapping per-deploy files (deploy-log, deployment manifest, event) became two. The persisted event
  was dropped when it turned out nothing reads it in the default setup; rendering it at send time also gives each
  send a fresh CloudEvents `id`, which a stored copy could not.
- `artifacts.platform` is a reference, not an embed. v1 embedded the platform content but referenced the SBOM and
  outputs, without saying why; one rule for all satellites keeps the manifest small.
- `path`, `structure` and `journal` were removed from the configuration rather than defaulted. A templated
  `structure` would have been a second way to describe a shape `layers.segments` already declares.
- The timestamp leaf is mandatory because two of v1's built-in structures had none, so every run silently
  overwrote the last record.
- The git sink is its own target shape, not a reused remote with a pinned `reference`: a push needs a moving branch,
  refreshed before every write. `remote`, `branch` and `path` are routing, not transport, so v1's "no transport on
  a sink" rule still holds.
- v1's policy framework for event admission was reduced to a flat `event_overrides` table over a closed set scoped
  to what v2 emits, keeping v1's two validators. The word "policy" was dropped to avoid suggesting an engine.
- Delivery failure is a per-sink `required` flag, the same name and default as on an Integration, instead of a
  global choice between silent and fatal.
- The metrics file follows v1's never-built ADR-0064: only facts known inside the process, a redacted error enum and
  a `label_safe` list. A local append series was dropped because CI agents are thrown away after each run.
- Reporting first read `.strata/audit` and was stopped before any code: on CI the local files never survive. It was
  rebuilt to read the git sink ([audit-commands.md](audit-commands.md)).
- Policy results travel through `policy_results.json` in the build directory because the judging command and the
  manifest-writing command are separate invocations, the same hand-off `resolved.yaml` and `sbom.json` already use.
- A denied build is not recorded as a `build` manifest: nobody asked for denied attempts, and it would add a producer
  and a failed-build record in every sink.
- `commit_author` and `commit_message` were modeled and then removed. Deploys are queued by hand from whatever is at
  HEAD, often an unrelated commit, so they would have named the wrong author; `git show <commit_sha>` gives both. For
  the same reason, scoping the commit to the deployment file was rejected: a change in another file the deployment
  uses would be missed. No git-log query answers "what changed"; a value diff would.
- Five more fields ported from v1 were removed because nothing could fill them: `force` (`deploy run --force` has no
  effect), `pull_request` (see "Not covered"), `outputs` (step outputs go to the output store, not a file),
  `signatures` (nothing signs) and the free-form `metadata`, replaced by the typed `pipeline` block. The link to the
  CI run was the missing piece: the run log holds the full output and every denied attempt.
- `stages` waited on `deploy_run()`, which returns only findings. Changing its return value would touch every
  caller and its many early returns, so the loop reports to a `StageLog` instead, the way `on_tool` already reports
  tool versions, and the outcome is derived from the findings afterwards. The stage's `outputs`, `outputs_artifact`
  and `topology` were removed: outputs for the same reason as the top-level field, and `topology` because a step
  can target several. Until then the metrics counted every run as one stage and `status` was never `partial`.
- The metrics file had the same gap: fields modeled from v1's ADR-0064 that nothing filled. `tenant`, `commit_sha`
  and the policy counts were filled from data the run already had, and `error_category` from strata's own codes.
  `resource_changes` was removed (only Terraform's plan summary could give it; Helm, Compose and scripts have no such
  count), and so were `sections` (cost, CVE, drift), which nothing measured. The event's `outcome` said `failed`,
  which ECS does not allow, and became `failure`.
- `force` had defaulted to `false`, so unlike the other removed fields it is in every older manifest. The model drops
  it on read rather than refusing those records, which `audit status` and `changes` would otherwise skip.
- `strata audit status` and `changes` name the step a failed or partial run stopped at in the status cell
  (`partial (apply_apps)`) and as `failed_step` in JSON, rather than in a new column.
- An Azure DevOps approval gate was planned as a script that turns the approval into a change reference. The REST
  API offers no way for a job to find the approval that gated it, and an approval has no structured input, so the
  ticket and reason come from queue-time parameters instead and the gate only stops and waits. v1's pause-and-resume
  gate (`mode: enforce`) was not ported: Azure DevOps already does that.
- `approved_by`/`approved_at` are stated by the operator, not looked up in a tracker. That is weaker than a tracker
  lookup, but keeps the approver apart from the person who ran the deploy, and no tracker integration exists to
  check against.
