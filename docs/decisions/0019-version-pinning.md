# Version Pinning — One Kind, Rationale as Schema, Pin Only What Strata Materialises

- Status: partially-implemented — model, service and binding built; pin overlay and resolution-time checks not wired
- Date: 2026-09-22
- Related: [ADR-0015](0015-solution-manifest-and-document-discovery.md)
  (discovery is local-only, which is why there is no bootstrap cycle here;
  also introduced `spec.remotes` and the `fetch` field this ADR leans on),
  [ADR-0018](0018-source-model-unified-remote-reference.md)
  (declared `reference` the single place a ref is set — this ADR qualifies that),
  [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Value tokens, considered and rejected as the delivery mechanism),
  [ADR-0016](0016-kind-field-validation.md) (`kind` validation applied here)

## Context and Problem Statement

A `version` document is the one file an operator edits to perform an upgrade.
If it is awkward, upgrades are awkward — and the target scale is roughly 250
customers across 4 rings, where nothing can be hand-reviewed.

**v1 built an enormous amount of machinery for it.** Two kinds
(`version_manifest_model.py` 123 lines + `version_lock_model.py` 209 lines),
two thin services, a 424-line `version_service.py`, a 454-line
`version_controller.py`, and seven command modules (`add`, `apply`, `export`,
`init`, `lock`, `refresh`, plus a shared base). The lock model supported
pointer-vs-inline locks, `previous` rollback snapshots, canary `scope`/
`scope_selector` overlays, `wave` numbering, floating `track: latest` pins,
and `resolved`/`resolved_at`/`resolved_sha` callbacks.

A census of every repository on disk (`alderwyn`, `haven`, `loom`, `strata`,
case-sensitive, build artifacts excluded) found:

| artifact | documents |
| --- | --- |
| `kind: version` | **1** (`haven/versions/prd.yaml`, 2.7 KB) |
| `kind: version-lock` | **0** |
| `kind: version-manifest` | **0** |

and **zero** uses of `track: latest`, `scope_selector`, `wave`, `previous`,
`resolved_sha` or `pins.tools`.

**The real usability defect is not in the schema.** All 14 pins in that one
production file carry a hand-written rationale — why a version is held, what
upstream has, what migration it would need:

```yaml
db: docker.io/library/postgres:16-alpine   # HELD — 18.6-alpine is latest upstream, but
                                           # postgres majors need pg_upgrade/dump-restore
gatus: 1.0.0                               # UNVERIFIED — could not confirm the chart version
                                           # from its repo; do not trust this pin blindly
```

and the file's own header says:

> `NOTE:` `strata versions lock`/`refresh` **rewrite this file and strip
> comments** — re-add them after running those commands.

The most valuable content in the file lives in comments that the tooling
destroys on every run, with the operator manually restoring it afterwards.
That does not survive contact with 1000 rollouts.

**The v1 delivery mechanism is also gone.** v1 applied pins by rewriting
`Environment.spec.overrides` — a subtree v2 did not port (0/26 real usage).

## Decision

**1. One kind, not two.** `version-lock` and `version-manifest` are not
ported; `kind: version` is the only version document. The manifest/lock split
existed to separate hand-edited intent from machine-generated state, but with
zero lock files ever written the split only ever added a second shape to
learn. `spec.hash` (which *is* used in the real file) carries the tamper
detection that motivated the lock.

**2. Rationale is schema, not comments.** A pin is:

```yaml
db:
  version: docker.io/library/postgres:16-alpine
  status: held                  # current | held | unverified
  available: 18.6-alpine
  reason: "postgres majors need pg_upgrade/dump-restore"
  reviewed: 2026-09-08
```

`status`/`available`/`reason`/`reviewed` are fields, so refresh tooling may
rewrite `version` and `available` freely while `reason` survives. The status
vocabulary is not invented — the real file already used exactly these three
states in prose (`HELD —`, `UNVERIFIED —`, plain bumps).

**A `held` or `unverified` pin without a `reason` is rejected.** This is the
load-bearing rule: it makes it structurally impossible for a machine rewrite
to produce a file that has forgotten why something is pinned back.

**3. Scalar shorthand.** `caddy: caddy:2-alpine` and the structured form
produce an identical model, so the common case stays one line and consumers
never branch on shape.

**4. Pin only what strata itself materialises.** Everything else is already on
disk before strata's process starts, so a pin could only misreport it. From
haven's 11 real workflows:

| target | materialised by | pinnable |
| --- | --- | --- |
| strata CLI | CI (`setup-strata@v1.9.3`, 10 uses) | no |
| solution repo checkout | CI (`actions/checkout@v6`, 14 uses) | no |
| terraform / helm binaries | CI (`setup-terraform@v4`, `setup-helm@v5`) | no |
| remote with `fetch: external` | CI | no |
| remote with `fetch: strata` | **strata** | **yes** |
| container images | **strata** (renders into compose/values) | **yes** |
| chart versions | **strata** (fetches the chart) | **yes** |

So the categories are `images`, `charts`, `remotes` — and for remotes, the
already-existing `fetch` field is the discriminator, not a judgement call.

**5. `pins.tools` is dropped.** It had zero real use *and* fails rule 4: CI
installs the binaries. The version a recipe expects stays on
`ProvisionerModel.version`, redefined as an **assertion** that preflight
verifies against what is present. Strata does not install software.

**6. `spec.ring` is dropped; binding is an ordinary reference.** `ring`
duplicated `meta.name` in the only real document (both `"prd"`) — the same
redundancy removed from `tenant.spec.code` — and it resolved against a
progression registry v2 has not ported. A deployment selects a version
document by name like it selects everything else:

```yaml
spec:
  workspace: main
  environments: [prd]
  version: prd
```

Ring/promotion is a planned follow-up for rollout automation. It attaches
*around* this model (which version document a ring selects), so nothing here
has to change when it lands.

**Not yet justified by evidence (2026-09-23 review): full Ring/Progression/
Wave/Strategy/PromotionRecord machinery.** v1 built all of it (`ProgressionRingModel`
with `require: any_one|all` quorum gates, `PromotionStrategyModel` waves,
`PromotionRecordModel` git-commit-per-wave audit trail with rollback) — the
same census pattern this ADR already applied elsewhere: **zero** real
`kind: version` documents beyond the one, so zero real second rings, so
zero real promotions ever exercised. Sanity-checked the "attaches around,
nothing here changes" claim rather than trusting it: the minimal ring
design is just one `version` document per ring, with different deployments
already able to point at different ones via the `spec.version` binding
above — no schema change needed for that part. Gating/quorum ordering and
an audit trail would be new, additive kinds if they're ever built, not
modifications to `version`/`deployment`. Real trigger to revisit: a second
`kind: version` document actually appearing in a real repo.

**Recommended starting shape, when a real per-run override is needed:** a
plain CLI flag on the future `build run`/`deploy run` (Phase 7, not yet
built) — `strata deploy run app --pin prd` — resolving a name against
`kind: version` and overriding `Deployment.spec.version` for that
invocation only. Deliberately not named `--version`: `strata --version` and
`strata version` (`commands/cli.py`) already both mean "print the CLI's own
version and exit," the near-universal CLI convention; reusing the word for
"which pinned version document" would make "version" mean three different
things in one CLI. `--pin` reads naturally against `spec.pins` and avoids
the collision. This needs no new model — it is sugar over the
`Deployment.spec.version` binding already built above — and is trivially
deprecatable (a bare CLI flag, no schema, nothing committed to 250
customers' YAML to migrate) if a real Ring/Promotion need ever displaces it.

**7. Overlay, not tokens.** When a pinned target is declared elsewhere, the
pin wins. Value tokens (`${version:db}`, reusing ADR-0002 machinery) were
considered and rejected: they would require rewriting every module to be
token-based, would make modules unloadable standalone, and cannot express a
`remotes` pin at all, since `SolutionRemoteModel.reference` is read during
bootstrap. Every application must be logged — target category, name, the
declared value, the pin value, and the document it came from — and a pin that
matches nothing must be reported, since a stale pin for a deleted module is
the likeliest real failure and is otherwise invisible.

**8. Resolution order — there is no bootstrap cycle.** It looked like there
was one: pins can change which ref a remote is fetched at, but the version
document is itself discovered. ADR-0015 already resolves it — *"strata
documents must live in the solution repo. Remotes supply artifacts, not
documents."* Discovery never reads remote content (verified: `repo_refs` is
used only to parse `@name/path` strings), so discovery never needs a fetch:

```
1. find solution root        (local)
2. load strata.yaml          (local)  -> remotes + default refs
3. discover + validate ALL   (local)  -> version documents in hand
4. select deployment, resolve `extends`
5. effective refs = manifest reference, overlaid by pins
6. materialise remotes       <- first fetch
7. build / deploy
```

## Consequences

- Good: the reasoning behind every held version is machine-readable, survives
  tooling rewrites, and can be surfaced in a rollout report rather than
  reconstructed from git history.
- Good: one kind, one shape, ~200 lines replacing v1's ~1200 across models,
  services and a controller — while keeping every feature that had a user.
- Good: no new override subtree. All three pin categories resolve against
  fields that already exist.
- Good: `fetch` doing double duty as the pinnability discriminator means the
  "can this pin take effect?" question has a schema answer.
- Bad — **remote materialisation becomes deployment-scoped.** Two deployments
  in one solution, selecting different version documents, can need the same
  remote at two refs. This qualifies ADR-0018's claim that a remote resolves
  to *"exactly one tree per solution"*: it is now one tree per resolved
  deployment. Checkout paths must be keyed by resolved ref
  (`.strata/remotes/<name>/<ref>/`), or two deployments will silently fight
  over one directory.
- Bad: the overlay is action-at-a-distance — reading a module no longer tells
  you the effective image. Mitigated only by the logging in decision 7, which
  is therefore not optional.
- Neutral: solution-repo provenance ("deployed from commit abc64fe", which
  haven records as `pins.remotes.haven`) has no home here. It is an output,
  not configuration, and belongs in a build record.
- Neutral: ~~ADR-0015's Decision 3 justifies putting remotes on the manifest
  with *"Configuration itself can live in a remote"*, which contradicts that
  same ADR's Consequences.~~ Corrected 2026-09-23 — see ADR-0015 decision 3.

## Remaining Work

- Wire the pin overlay into the controller's resolution pass, with the
  logging required by decision 7. `VersionService.resolve()` is the intended
  single entry point and is currently unused.
- Reject a `remotes` pin naming an unknown remote or one with
  `fetch: external` (needs the loaded manifest — a Phase 2 check).
- Ref-keyed remote checkout layout, before any fetch code is written.
- Verify `spec.hash` against the canonical pins payload during resolution;
  nothing computes or checks it yet.
- `ProvisionerModel.version` preflight assertion (exe presence / env var /
  endpoint reachability); currently declarative only.
- Ring/promotion for rollout automation, layered around this model.
