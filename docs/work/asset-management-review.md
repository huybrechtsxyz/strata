# Asset Management — Code Review Findings — Work

- Status: **complete (2026-10-13)** — all five findings from the
  2026-10-13 code review are fixed and verified. Both open decisions were
  taken: finding 2's containment helper lives in `path_safety.py`, and
  finding 3's glob matches **preserve structure** rather than flatten.
  Full suite **2474 passed** (18 new tests), mypy/ruff/lint-imports clean.
  [asset-management.md](../design/asset-management.md) updated to match —
  finding 3 was a design change, not just a defect fix.
- Last updated: 2026-10-13
- Reviews: [asset-management.md](../design/asset-management.md)
  (`Status: current` — the feature itself is built and tested; this doc
  tracks only what the review found wrong with it)

## Overview

Code review of the full asset-management implementation (`asset_model.py`,
`source_sync.py`'s `sync_asset()`, `build_controller.py`'s asset loop,
`terraform_projection.py`'s `_strata_assets()`, `path_conventions.py`'s
`fill_pattern()`/`segment_tokens()`).

**Every finding below was verified empirically** (throwaway scripts run
against the real models/helpers, since deleted), not read-and-assumed —
the exact observed output is quoted per finding.

**What the review found solid**, for the record: design/implementation
agreement is genuinely good — the static up-front asset scan (not lazy
per-step), `dataclasses.replace()` on the frozen graph, `strata.assets`
keyed only by *referenced* names, `dry_run` skipping, the dedicated
unresolved-`${...}`-token diagnostic, and the two-layer pool/selection
validation all match the design doc exactly. Test coverage spans all six
layers (model, workspace wiring, sync, projection, controller, path
utils) — the one real gap is no traversal test, matching findings 1/2.

## Prior-art check (2026-10-13)

Before proposing any new helper, searched the codebase for an existing
one. The answer splits cleanly between the two security findings — worth
recording, since it changes what each fix actually *is*.

**Finding 1 — prior art exists, and is already the proposed fix.**
`strata/utils/path_safety.py` is exactly this: `validate_relative_path()`,
`validate_no_path_traversal()`, `validate_file_ref_no_traversal()`.
Already used by `SourceModel` (`source_path`/`target_path`),
`ModuleFileModel` (`source`/`target`), `NamespaceModuleModel` (`file`),
`ProvisionerModel`, and `solution_model.py` (`reference`/`url`).
**`AssetSpecModel` is the only path-bearing field in the schema that
skipped it.** So finding 1 is not "add a validator" — it is "apply the
existing, repo-wide convention that was missed," which is the strongest
form that fix can take.

**`references.py` is unrelated** (checked, since it was the first guess):
it resolves `(kind, meta.name)` *document* references — `validate_references()`,
`references_in()`, `_unresolved()` — with nothing filesystem-related in
it at all. That is ADR-0015's identity-not-paths rule working as
intended, not a gap.

**Finding 2 — no prior art, confirmed by search, not assumed:**

- `Path.is_relative_to()` — **zero occurrences** anywhere in `src/`.
- All 11 `relative_to()` call sites are display/shortening, never a
  containment check. The closest, `layout.py`'s `display_path()`,
  deliberately *swallows* the `ValueError` and falls back to the original
  string ("a long path is better than a crash") — the exact opposite of
  a security check.
- No `resolve_path`/`safe_join`/`within_root`/`ensure_within`-style
  helper exists under any of those names.

So finding 2 genuinely needs something new — which raises a placement
question (below) that finding 1 does not have.

### Placement question for finding 2's new helper

Surfaced by the prior-art search. `layout.py`'s own module docstring
states a hard rule:

> **Every derived path belongs here.** Nothing outside this module should
> join a path segment, hardcode a directory name, or decide where
> something lands on disk.

`sync_asset()` does `repo_root / filled_path` **and**
`build_path / "assets" / asset.meta.name` — both joins outside
`layout.py`, technically against that rule. (Pre-existing and not caused
by assets: `sync_source()` does the same thing.)

Two candidate homes for the containment helper:

- **`path_safety.py`** — its stated job is traversal safety on
  *user-supplied* input, which is exactly what a filled `{token}` is.
  Sits in `strata.utils`, same layer, no new import direction.
- **`layout.py`** — owns path *derivation*, and would let the
  `repo_root / filled_path` join itself move inside the module that
  claims to own every join.

Leaning `path_safety.py`: this validates untrusted input rather than
deriving a strata-owned location, and the helper is useful to any caller
joining an untrusted segment, not only asset sync. Worth deciding
deliberately rather than by default, though — and worth noting that
neither choice fixes `sync_source()`'s own identical, pre-existing join,
which is out of scope here.

## Findings

### 1. Path traversal: `AssetSpecModel.source_path` has no validation at all — FIXED (2026-10-13)

**Severity: high.** Every sibling path field in this repo validates;
this one doesn't.

| Field                                          | `../../etc/passwd` | `/etc/passwd` |
| ---------------------------------------------- | ------------------ | ------------- |
| `SourceModel.source_path` (`common_models.py`) | rejected           | rejected      |
| `ModuleFileModel.source` (`module_model.py`)   | rejected           | rejected      |
| **`AssetSpecModel.source_path`**               | **accepted**       | **accepted**  |

Verified output:

```
=== 1. SourceModel.source_path with '..' ===
  REJECTED: ValidationError
=== 2. AssetSpecModel.source_path with '..' ===
  ACCEPTED: '../../etc/passwd'
=== 3. AssetSpecModel.source_path absolute ===
  ACCEPTED: '/etc/passwd'
```

The absolute case is the worse of the two. `sync_asset()` does
`origin = repo_root / filled_path`, and pathlib **discards the left
operand entirely** when the right side is absolute:

```
Path('/repo/root') / '/etc/passwd'  ->  /etc/passwd
Path('C:/repo')    / 'C:/Windows'   ->  C:/Windows
```

So `source_path: /etc/passwd` reads straight from the filesystem root,
ignoring the solution root / resolved remote completely.

**Proposed fix:** one `@field_validator("source_path")` on
`AssetSpecModel` calling `validate_relative_path()`
(`strata.utils.path_safety`) — the exact same helper, called the exact
same way, `SourceModel.validate_source_target_path()` already uses. No
new mechanism, just applying the existing one consistently. See the
Prior-art check above: this field is the *only* path-bearing field in
the whole schema that skipped an otherwise repo-wide convention.

**FIXED as proposed.** 5 new parametrised tests in `test_models_asset.py`
cover parent traversal, embedded traversal, POSIX-absolute,
Windows-drive-absolute and UNC. A second set confirms glob wildcards
(`*`, `**`, `?`, `[abc]`, and a wildcard alongside a `{name}` token) all
survive the validator untouched — the validator rejects traversal
without constraining finding 3's syntax.

### 2. Path traversal via a `{token}` value — static validation cannot catch this — FIXED (2026-10-13)

**Severity: high.** Independent of finding 1 — fixing 1 does *not* fix
this. `source_path`'s `{name}` tokens are filled from the deployment's
merged `properties`, and those values are validated nowhere:

```
fill_pattern("assets/{deploy_tier}/openapi", {"deploy_tier": "../../../../etc"})
  -> 'assets/../../../../etc/openapi'
  -> resolves to  E:/etc/openapi       (outside repo_root)
```

A `properties` value is merged from tenant/environment/deployment YAML,
so this is reachable without ever touching the asset document itself.

**Proposed fix:** a post-fill containment check in `sync_asset()`, before
the `origin.exists()` check — assert the resolved `origin` is still under
the resolved `repo_root` (`Path.is_relative_to()`), raising
`SourceSyncError` with a clear message otherwise. Alternatively (or
additionally) validate each fill *value* before substituting, the same
shape as the existing `has_value_tokens()` guard right above it.

**No prior art for this one** — confirmed by search, see the Prior-art
check above: `is_relative_to()` appears nowhere in `src/`, and every
existing `relative_to()` use is display-only. This is the one finding
that needs a genuinely new helper, and therefore the one with a
placement question (`path_safety.py` vs `layout.py`, see above).

**Note:** the design doc already established the precedent of validating
fill *inputs* (that `has_value_tokens()` check). This is the same class
of concern, just never covered — so the fix fits the existing design
rather than introducing a new idea.

**FIXED. Decision taken: the helper lives in `path_safety.py`** — new
`resolve_within(root, candidate)`, which resolves both sides before
comparing (a purely lexical check would miss
`root/subdir/../../outside`) and raises `ValueError`, matching every
other validator in that module; `sync_asset()` wraps it into
`SourceSyncError`, exactly as it already wraps `fill_pattern()`'s.
`path_safety.py`'s module docstring now states the split explicitly: its
other helpers validate a *declared string* before it becomes a path,
while `resolve_within()` checks an already-built `Path` for the cases a
string check structurally cannot cover.

It runs in both branches of the now-glob-aware `sync_asset()`, and
**per match** in the glob branch — 3 new tests cover a `..` token value,
an absolute token value, and a glob whose *matches* escape even though
the pattern never names the escaping path literally.

### 3. `files` lists directories as though they were files — FIXED (2026-10-13) via glob-aware `source_path`

**Severity: medium.** `build_controller.py`:

```python
files=sorted(p.name for p in asset_path.iterdir()),
```

Non-recursive and unfiltered, so a nested asset tree yields:

```
  iterdir() names    = ['nested', 'openapi.json']     <- 'nested' is a DIRECTORY
  recursive rglob    = ['nested/deep.xml', 'openapi.json']
```

The design doc's own example (`["openapi.json", "openapi.xml"]`) implies
real filenames, and a consumer doing `for_each =
local.strata.assets.openapi.files` would silently get a directory name
mixed into the set.

#### Preferred direction (2026-10-13): make `source_path` glob-aware, and the question dissolves

Raised during review: *"this should just use the glob style — people will
expect the Linux glob to work, `openapi/*.json` for instance, or
`openapi/api.json`."* This is a better answer than any of the three
options below, because it removes the guess entirely — **the author
selects what they want; strata stops inferring it.** `files` then just
reports what the glob actually matched, with no shallow-vs-recursive
judgement call left for strata to make.

**Verified feasible, not assumed** — tested directly against the real
`_SEGMENT_TOKEN` regex, `fill_pattern()`, and `pathlib`:

- **No syntax collision with the existing `{name}` tokens.** Strata's
  token regex is `\{(\w+)\}`; every glob metacharacter produces zero
  tokens, and the two compose cleanly:

  ```
  'assets/{deploy_tier}/*.json'  tokens=['deploy_tier']   <- both work together
  'assets/**/*.json'             tokens=[]
  'assets/{json,xml}'            tokens=[]   <- brace expansion is NOT mistaken
  'assets/file?.json'            tokens=[]      for a token (',' isn't \w)
  'assets/[ab].json'             tokens=[]
  ```

  (Note: `{json,xml}` is safely *ignored* as a token, but is also not
  *supported* — brace expansion is a shell feature, `pathlib.glob()` has
  no equivalent. Worth documenting so nobody expects it.)

- **`fill_pattern()` already survives glob metacharacters untouched** —
  tokens fill, globs pass straight through, no change needed to it:

  ```
  fill_pattern('assets/{deploy_tier}/**/*.json', {'deploy_tier': 'prod'})
    -> 'assets/prod/**/*.json'
  ```

- **Glob semantics are exactly what an operator would expect**, including
  the user's own two examples:

  ```
  'openapi/*.json'     -> ['openapi/api.json']
  'openapi/**/*.json'  -> ['openapi/api.json', 'openapi/nested/deep.json']
  'openapi/api.json'   -> ['openapi/api.json']          <- single file
  'openapi'            -> ['openapi']                   <- the directory itself
  'openapi/*'          -> ['openapi/api.json', 'openapi/api.xml', 'openapi/nested']
  ```

- **Backward compatible by construction.** A `source_path` with no glob
  metacharacter (`openapi`) still resolves to exactly the directory it
  does today — today's behaviour is simply the no-metacharacter case, so
  no existing asset document changes meaning.

**Interaction with finding 2 — important, and it does *not* go away:**
a glob can still escape the root via `..`, confirmed directly:

```
root.glob('../*.txt')       -> ['.../repo/../secret.txt']    <- ESCAPED
root.glob('a/../../*.txt')  -> ['.../repo/a/../../secret.txt'] <- ESCAPED
root.glob('/etc/*')         -> NotImplementedError: Non-relative patterns are unsupported
```

So finding 2's containment check is still required, and under a glob it
must run **per match**, not once on a single joined path. One upside:
an *absolute* pattern raises `NotImplementedError` rather than silently
escaping — loud by default, though finding 1's validator should reject
it before it ever reaches `glob()` anyway. (Re the review comment's
`e:\openapi\*.json`: `source_path` is relative to the **resolved
remote's** root by design — an absolute path is what the `remote:` field
exists to express, and is rejected by findings 1 and 2 both.)

**New sub-question this direction introduces** (replacing the old
shallow-vs-recursive one): when a glob matches files across several
directories, what structure does the copy preserve? For
`openapi/**/*.json` matching `openapi/api.json` + `openapi/nested/deep.json`:

- **Flatten** into `assets/openapi/` — simple, but two same-named files
  in different directories collide silently.
- **Preserve the path relative to the glob's non-magic prefix** (what
  `rsync`/`tar` do) — `assets/openapi/api.json` +
  `assets/openapi/nested/deep.json`. No collisions, slightly more work to
  compute.

Preserving structure looks correct, but it is a real decision, not an
obvious default.

**DECIDED: preserve structure. FIXED as designed.** `sync_asset()` now
branches on whether the filled `source_path` contains a wildcard
(`*?[` — deliberately not `{}`, which is strata's own token syntax):

- **No wildcard** — byte-for-byte today's behaviour, unchanged.
- **Wildcard** — `repo_root.glob(...)`, every match copied to
  `destination / match.relative_to(base)`, where `base` is the pattern's
  last wildcard-free directory (`_glob_base()`). A directory match is
  copied recursively, matching `cp -r`. An empty match set is a clear
  error rather than a silently empty asset.

`build_controller.py`'s `files=` listing became **recursive and
files-only** (`rglob("*")` + `is_file()`, relative posix paths) to match
— which is also what fixed the original directories-as-files defect.

6 new tests: wildcard selection, recursive structure preservation
(asserting the flattened path is *absent*), glob combined with a `{name}`
token, empty match set, and backward compatibility for a wildcard-free
directory. [asset-management.md](../design/asset-management.md) updated
— schema, sync mechanism, the `files` example, and a History entry.

#### Original three options (superseded by the above, kept for the record)

These were the options before the glob direction was raised — all three
are strata *guessing* what the author wanted, which is exactly what the
glob direction removes:

1. **Filter to files only** (`if p.is_file()`) — smallest change, keeps
   the shallow listing, silently drops anything nested.
2. **Recurse** (`rglob("*")`, relative posix paths) — `files` becomes a
   genuine complete file list; changes the value shape for any existing
   consumer (none known yet, so the window to change this is now).
3. **Keep as-is, rename/document** — it's a shallow *directory entry*
   listing, not a file listing; document that explicitly in
   `ResolvedAssetInfo` and the design doc.

Option 2 is probably the most useful and the shape a consumer would
expect from the name `files`, but it is the only one of the three that
changes already-shipped output.

### 4. Non-scalar properties get stringified into the path — FIXED (2026-10-13)

**Severity: medium.** The unresolved-token guard in `sync_asset()` is
`if isinstance(value, str)`, so a non-string property skips the check
entirely and goes straight into `str(v)` at fill time:

```
{deploy_tier} (dict) -> "assets/{'size': 'large'}/openapi"
{region}      (list) -> "assets/['eu', 'us']/openapi"
{count}       (int)  -> 'assets/3/openapi'        <- fine, genuinely useful
{flag}        (bool) -> 'assets/True/openapi'     <- probably not intended
```

`int` is reasonable and worth keeping. `dict`/`list` produce garbage
paths that fail later as a confusing "path does not exist" error —
exactly the failure mode the `has_value_tokens()` check was added to
prevent in the first place.

**Decision: accept `str`/`int`/`float`, reject everything else including
`bool`.** A numeric tier/ring/version is a legitimate path segment; a
`dict`/`list` never is; and `assets/True/openapi` is never what anyone
meant.

**Implemented in `sync_asset()`** — one check added to the loop that was
already walking `segment_tokens()`, so no new traversal:

```python
if value is None:
    continue  # `fill_pattern()` below raises the clearer "Unknown token" error for this
# `bool` is checked first and rejected on purpose: `isinstance(True, int)` is True in
# Python, so it would otherwise pass the scalar check and fill a literal 'True' segment.
if isinstance(value, bool) or not isinstance(value, (str, int, float)):
    raise SourceSyncError(...)
```

**The one real subtlety, worth recording:** `bool` is a *subclass of*
`int` in Python, so the obvious `isinstance(value, (str, int, float))`
would have silently **accepted** `True`/`False` — the exact case the
decision was to reject. The explicit `isinstance(value, bool)` must come
first. The regression test asserts on the specific "cannot be a path
segment" message rather than merely that *some* error is raised, so a
future refactor that loses the bool branch fails loudly instead of
passing on the downstream "path does not exist" error.

`None` deliberately falls through rather than raising here — a missing
key and a `null` value both reach `fill_pattern()`'s existing, clearer
`"Unknown token '{x}' ... no matching value provided"` error, preserving
that behaviour and its existing test unchanged.

**Verified:** 7 new parametrised tests in `test_source_sync.py` (dict,
list, `True`, `False` rejected; `str`, `int`, `float` accepted end-to-end
through a real copy), 27 passing in that file, full suite **2456 passed**,
mypy/ruff/lint-imports clean.

### 5. Stale docstrings in `asset_model.py` — FIXED (2026-10-13)

**Severity: low, but one is user-visible.** Both claim the feature is
unbuilt, while [asset-management.md](../design/asset-management.md) is
`Status: current` and every phase has shipped:

- Module docstring: *"This is Phase 1 of that doc's Implementation Plan
  (schema only): no `WorkspaceSpecModel.assets` pool, no `sync_asset()`,
  no `strata.assets` projection yet — those are Phases 2-4."*
- `AssetSpecModel.source_path`'s own `Field(description=...)`: *"— not
  yet wired up, Phase 3"*. **This one is user-visible** — a `Field`
  description surfaces in generated schema/docs, not just in source.

**Proposed fix:** rewrite both to describe current behaviour. Same class
of drift already corrected this session in `dns_model.py` (a docstring
claiming `${output:}` was unsupported long after it shipped).

**FIXED.** The "Phase 1 — schema only" module docstring is gone, and the
`source_path` field description now documents what it actually does:
`{name}` tokens, glob wildcards, the unsupported brace expansion, and the
relative-path requirement. The matching "Phase 1" reference in
`test_models_asset.py`'s own docstring was stale too, and was fixed
alongside it.

## Related Decisions

- [asset-management.md](../design/asset-management.md) — the design this
  reviews; `Status: current`, unchanged by these findings (they are
  implementation defects, not design errors — with the partial exception
  of finding 3, where the doc's own example implies the behaviour the
  code doesn't deliver).
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  — why an asset is copied byte-for-byte; unaffected by any fix here.
- `src/strata/utils/path_safety.py` — `validate_relative_path()`/
  `validate_no_path_traversal()`, the existing helpers findings 1/2 should
  reuse rather than reinvent.
- `src/strata/utils/layout.py` — claims ownership of *every* derived path
  ("Nothing outside this module should join a path segment"); the reason
  finding 2's new helper has a placement question at all, and the module
  `sync_asset()`'s two existing joins technically bypass.
- `src/strata/controllers/references.py` — checked as a candidate home
  and ruled out: it resolves `(kind, meta.name)` document identity
  (ADR-0015), nothing filesystem-related.

## Remaining Work / Open Questions

All five findings are fixed — nothing open.

- [x] ~~**Finding 1**~~ — `validate_relative_path()` added to
      `AssetSpecModel.source_path`; 5 traversal tests + 5 confirming glob
      syntax still passes the validator.
- [x] ~~**Finding 2**~~ — **decided: `path_safety.py`**. New
      `resolve_within()`, called per match in the glob branch; 3 tests.
- [x] ~~**Finding 3**~~ — **decided: preserve structure**. Glob-aware
      `source_path`, recursive files-only `files` listing; 6 tests;
      design doc updated.
- [x] ~~**Finding 4**~~ — `str`/`int`/`float` accepted,
      `dict`/`list`/`bool` rejected (sub-question resolved: no `bool`);
      7 tests.
- [x] ~~**Finding 5**~~ — both stale docstrings rewritten, plus a third
      found in `test_models_asset.py`.
- [x] ~~Full check suite~~ — **2474 passed**, mypy/ruff/lint-imports
      clean; [asset-management.md](../design/asset-management.md) updated
      for finding 3's design change.

### Follow-ups deliberately left out of scope

- `sync_source()` has the same two un-helpered path joins `sync_asset()`
  did, and still bypasses `layout.py`'s "every derived path belongs here"
  rule. Not touched — pre-existing, unrelated to assets, and provisioner
  sources are not token-filled, so they lack the runtime-injection vector
  that made finding 2 reachable. Worth a separate look.
- `validate_no_path_traversal()` rejects any `..` substring, so a
  legitimate filename containing `..` (e.g. `file..json`) is rejected.
  Pre-existing and shared with `SourceModel`; noted, not changed.

## Changelog

- 2026-10-13: Created from a code review of the shipped asset-management
  feature. Five findings, all verified empirically against the real
  models/helpers rather than inferred from reading. Nothing fixed yet —
  finding 3 deliberately left as an open decision rather than guessed at,
  since it is the only one that would change already-shipped output.
- 2026-10-13: Added the Prior-art check section, after searching for an
  existing path-resolution helper rather than assuming a new one was
  needed. Result split the two security findings: finding 1 has real
  prior art (`path_safety.py`, already used by every other path-bearing
  field — asset is the sole exception), finding 2 has none
  (`is_relative_to()` appears nowhere in `src/`; all 11 `relative_to()`
  uses are display-only). `references.py` checked and ruled out as
  unrelated (document identity, not paths). That search also surfaced a
  new placement question for finding 2's helper — `layout.py` claims to
  own every path join, which `sync_asset()` (and, pre-existing,
  `sync_source()`) bypasses — added as a blocking sub-decision on
  finding 2.
- 2026-10-13: **Finding 3 reframed from "pick one of three listing
  behaviours" to a design change: make `source_path` glob-aware**, after
  the review comment that operators will expect ordinary Linux glob
  syntax (`openapi/*.json`, `openapi/api.json`) to work. Verified the
  direction directly rather than assuming it: glob metacharacters do not
  collide with the existing `{name}` token regex (`\{(\w+)\}` — even
  `{json,xml}` is safely ignored rather than misparsed), `fill_pattern()`
  already passes globs through untouched, real `pathlib` glob semantics
  match operator expectations for all five tested patterns, and the
  no-metacharacter case still resolves to today's directory behaviour so
  nothing existing changes meaning. Also confirmed a glob can still
  escape via `..` (so finding 2's containment check is still needed, and
  must run per match) while an absolute pattern raises
  `NotImplementedError` rather than escaping silently. The original three
  options are kept in the doc as superseded, and one genuinely new
  sub-question replaces the old one: flatten vs. preserve structure when
  a glob spans several directories.
- 2026-10-13: **Finding 4 FIXED.** Decision taken: accept
  `str`/`int`/`float` as a `{name}` fill value, reject everything else
  **including `bool`**. Implementation added one check to the loop
  `sync_asset()` was already running over `segment_tokens()`. The
  non-obvious part, recorded in both the code comment and the test: `bool`
  is a *subclass of* `int` in Python, so the natural
  `isinstance(value, (str, int, float))` would have silently accepted
  `True`/`False` — the explicit `isinstance(value, bool)` rejection has to
  come first. `None` deliberately still falls through to `fill_pattern()`'s
  clearer "Unknown token" error, leaving that existing behaviour and its
  test untouched. 7 new parametrised tests; full suite 2456 passed,
  mypy/ruff/lint-imports clean.
- 2026-10-13: **Findings 1, 2, 3 and 5 FIXED — review complete.** Both
  open decisions taken: finding 2's helper lives in `path_safety.py`
  (new `resolve_within()`, resolving both sides before comparing so a
  lexical-only check can't be fooled by `subdir/../../outside`), and
  finding 3's glob matches **preserve structure** relative to the
  pattern's last wildcard-free directory. Finding 3 drove the largest
  change — `sync_asset()` now branches literal-vs-glob, `files` became a
  recursive files-only listing, and
  [asset-management.md](../design/asset-management.md) was updated since
  it was a design change rather than a defect fix. Finding 1's validator
  was confirmed not to constrain finding 3's syntax (5 dedicated tests
  assert wildcards still pass). Finding 5 turned up a third stale
  "Phase 1" reference in `test_models_asset.py`'s own docstring. 18 new
  tests total; full suite **2474 passed**, mypy/ruff/lint-imports clean.
  Two pre-existing, out-of-scope follow-ups recorded under Remaining
  Work rather than silently fixed or dropped.
