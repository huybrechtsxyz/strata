# Security gates - Implementation plan

|                      |                                                                                               |
| -------------------- | --------------------------------------------------------------------------------------------- |
| **Design**           | [security-gates-design.md](security-gates-design.md) (read it first; D/§ numbers refer to it) |
| **Vendor reference** | [aikido-api-reference.md](aikido-api-reference.md) Appendices A-D ("ref" below)               |
| **Index**            | [aikido-integration.md](aikido-integration.md)                                                |
| **Status**           | Phases 1-6 done (2026-10-26); Phase 7 next (needs a real Aikido workspace)                    |
| **Updated**          | 2026-10-26 (review fixes 1-4; credentials via `AuthenticationModel`)                          |
| **Baseline**         | strata 2.0.0rc1 (`v2-beta`)                                                                   |

> **Offline work.** Everything needed is in the design and the vendor reference. No new
> dependency: `requests` (via `strata.utils.transport`) and `packageurl-python` are installed.
> Every test runs without network. Only Phase 7 needs a real Aikido workspace.

## Ground rules

- Layering (import-linter): `commands > controllers > services > integrations > models > utils >
  logging`. `integrations/*` never import `controllers` or `services`.
- Green at the end of every phase, in the repo's order (`.github/copilot-instructions.md`):
  ```powershell
  & .\.venv\Scripts\python.exe -m mypy src
  & .\.venv\Scripts\python.exe -m ruff check --fix src tests
  & .\.venv\Scripts\python.exe -m ruff format --check src tests
  & .\.venv\Scripts\lint-imports.exe
  & .\.venv\Scripts\python.exe -m pytest -q
  ```
- **Vendor-neutral outside `integrations/aikido.py`.** No `aikido` in models, controllers, commands
  or diagnostic codes (registry entry, tests and docs excepted). Controller and `security` command
  tests use a fake capability implementation, not Aikido.
- **Reuse before adding.** Credentials come from `AuthenticationModel` (D2); policies mirror
  `cve_policy`; integration resolution mirrors `resolve_output_store_integration()`; semantic
  checks mirror the remote/asset `sources` checks and the output-store CAS check.
- Never log, print, or put into a diagnostic or `policy_results.json` a credential, token, or the
  `Authorization` / `X-AIK-API-SECRET` header.
- Platform payload types are frozen dataclasses, not `PlatformBaseModel` (extra=forbid).
- Severities use the uppercase vocabulary `CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN` everywhere in
  strata; only Aikido's own wire format is lowercase.
- Docstrings explain why and cite the design (`docs/work/security-gates-design.md D6`).
- Tests: `tests/strata/<layer>/test_<layer>_<name>.py` (existing files extended where named below);
  fixtures in `tests/strata/fixtures/aikido/` (new folder).
- One commit per phase, prefix `feat(security-gates):` / `test(security-gates):`.

## Phase overview

| Phase | Deliverable                                                                                      | Depends on                    |
| ----- | ------------------------------------------------------------------------------------------------ | ----------------------------- |
| 1     | Capabilities, ABCs, mixins, neutral types, errors, `split_registry`, `key_references`            | -                             |
| 2     | Policy models, workspace fields, semantic checks                                                 | 1                             |
| 3     | Aikido: configuration + authentication validation, transport, release gate                       | 1                             |
| 4     | Aikido: image findings, connection check                                                         | 3                             |
| 5     | Generic controller, credentials binding, deploy hook, policy results                             | 1, 2 (fake); 4 for end to end |
| 6     | `strata policy` (list/check/findings, gates only), `strata integration check`, docs, agent skill | 5                             |
| 7     | First contact against a real Aikido workspace                                                    | 6, network                    |
| 8     | Follow-on: `cve_policy` joins `policy check` / `policy findings`                                 | 6                             |
| 9     | Follow-on: `checkov_policy` joins `policy check` / `policy findings` (design first)              | 6                             |
| 10    | Follow-on: a future `opa_policy` (design first, only on real consumer demand)                    | 6                             |

Phases 1-7 complete this feature. Phases 8-10 each bring one more workspace policy under the
`strata policy` group (design D7: the dispatch table grows one row per policy); they are planned
here so the command's scope can stay small now without losing the follow-up.

---

## Phase 1 - Foundations

> **Done 2026-10-26.** Built as specified below. Full suite green: mypy (161 files), ruff check,
> ruff format, import-linter, pytest 3124 passed, `config/` validates. Nothing uses the new types
> yet. New: `integrations/security_findings.py` and its test file; changed: `integration_model.py`,
> `auth_models.py`, `errors.py`, `capabilities.py`, `sbom_utils.py`, and four test files.

**Files**

- `src/strata/models/integration_model.py`: `Capability.RELEASE_GATE = "release_gate"`,
  `Capability.IMAGE_FINDINGS = "image_findings"`, each with the rationale comment from design D1
  (why not `scanner`). `VALID_INTEGRATION_CAPABILITIES` picks them up automatically.
- `src/strata/models/auth_models.py`: `AuthenticationModel.key_references() -> tuple[str, ...]`
  (design D2): `token` -> (token); `ssh_key` -> set fields of (private_key, passphrase, known_hosts);
  `api_key` -> set fields of (api_key, api_secret, endpoint, header_name); `oauth2` -> set fields of
  (client_id, client_secret, tenant_id, token_url, authorization_url, scope); every other method
  `()`. Never literals (`token.username`) or booleans.
- `src/strata/integrations/security_findings.py` (new): `SECURITY_SEVERITY_ORDER`
  (`("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")`), `ReleaseGateRequest`, `ReleaseGateOutcome`,
  `ImageRef` (`service`, `registry`, `repository`, `tag`, `digest`, `path` property), `SecurityIssue`,
  `ImageLookup` (design D1).
- `src/strata/integrations/errors.py`: `GateNotFoundError(IntegrationError)` (optional
  `reason: str | None`), `GateTimeoutError(IntegrationError)`.
- `src/strata/integrations/capabilities.py`: `ReleaseGateIntegration`, `ImageFindingsIntegration`
  (abstract methods per D1), `CredentialedIntegration` (`bind_credentials(values)`, default no-op),
  `ConnectionCheckIntegration` (`check_connection() -> list[str]`, abstract); add both capabilities
  to `CAPABILITY_ABCS`. No `__init__` in any of them.
- `src/strata/utils/sbom_utils.py`: `split_registry(name) -> tuple[str | None, str]` next to
  `parse_image_ref()`: first segment is a registry when it contains `.` or `:` or equals
  `localhost`.

**Tests**

- `tests/strata/models/test_models_auth.py` (extend): `key_references()` per method, including
  unset optional fields omitted, `token.username` never included, `cli`/`managed_identity` -> `()`.
- `tests/strata/integrations/test_integrations_security_findings.py` (new): `ImageRef.path` with and
  without registry; frozen dataclasses; `SECURITY_SEVERITY_ORDER == tuple(CVE_SEVERITY_ORDER)`.
- `tests/strata/integrations/test_integrations_capabilities.py` (extend): both capabilities in
  `CAPABILITY_ABCS`; a fake class declaring `release_gate` without the ABC is a mismatch; a fake
  implementing both ABCs + both mixins declaring both capabilities has none and constructs with
  only `config`.
- `tests/strata/utils/test_utils_sbom_utils.py` (extend): `split_registry()` for
  `ghcr.io/org/app`, `registry:5000/img`, `localhost/img`, `org/app`, `traefik`.
- Errors: both are `IntegrationError`s; `GateNotFoundError.reason` kept.
- An Integration document declaring `capabilities: [release_gate]` validates
  (`test_models_integration` or wherever capability validation is tested).

**Done when** the foundations exist, nothing uses them yet, and every existing test still passes.

---

## Phase 2 - Policies and validation

> **Done 2026-10-26.** Built as specified below. The generic reference walk already discovers the
> nested `spec.release_gate_policy.integration` / `spec.image_gate_policy.integration` fields, so no
> extra reference work was needed; `tests/strata/controllers/test_references.py`'s pinned list of
> workspace reference paths was extended accordingly. No committed JSON schema snapshots exist to
> regenerate. Full suite green: mypy (162 files), ruff, import-linter, pytest 3155 passed, `config/`
> validates.

**Files**

- `src/strata/models/security_gate_model.py` (new): `ReleaseGatePolicyModel`,
  `ImageGatePolicyModel` (design §6.1).
- `src/strata/models/workspace_model.py`: `release_gate_policy`, `image_gate_policy` after
  `checkov_policy` (import and `References` style as for `CheckovPolicyModel` /
  `output_store_integration`).
- `src/strata/controllers/semantic_checks.py`: per set policy, next to the workspace checks
  (design D3):
  - document disabled or `capability not in spec.capabilities` -> `gate_integration_capability_missing`
    (message lists the declared capabilities, like `remote_integration_not_sources`);
  - `not issubclass(registry.get_class(type), <ABC>)` -> `gate_integration_type_unsupported`;
    `IntegrationError` from `get_class` -> no finding (as `_check_workspace_key_value_store_supports_cas`).
  - Unknown document names are already reported by the generic reference walk; confirm it sees the
    nested `integration` fields, else follow the existing practice for nested references.
- Regenerate committed JSON schema snapshots, if any.

**Tests**

- `tests/strata/models/test_models_security_gate.py` (new): defaults; bounds; enum values;
  non-hex/short `commit`; `poll_interval >= timeout`; unknown field; missing `integration`.
- `tests/strata/models/test_models_workspace.py` (extend): with and without both policies.
- `tests/strata/controllers/test_semantic_checks.py` (extend): unknown name -> reference error;
  disabled -> capability_missing; capability not declared -> capability_missing; declared but class
  lacks ABC (e.g. `webhook` declaring `release_gate`) -> type_unsupported; unregistered type -> no
  finding from this check; valid (fake registered type until Phase 3) -> clean.

**Done when** `strata validate` accepts valid policies and rejects each bad case with its code.

---

## Phase 3 - Aikido: configuration, authentication, release gate

> **Done 2026-10-26.** `integrations/aikido.py`, five fixtures in `tests/strata/fixtures/aikido/`,
> 59 tests in `test_integrations_aikido.py`; a mutation check confirmed the tests catch a truthy
> (not literal) `gate_passed` and retried 4xx. Full suite green (3214 passed). Deviations, both
> deliberate: (1) `find_image_issues()`/`check_connection()` raise `NotImplementedError` until
> Phase 4, so the type is **not registered yet** (Phase 4 task 6); (2) no structured logging
> (design ref section 11): no strata integration logs today, so adding it here would be a new
> convention; revisit with the controller in Phase 5. The image `configuration.images` block is
> already validated here (task 2), so Phase 4 only consumes it.

**Files**

- `src/strata/integrations/aikido.py` (new)
- `src/strata/integrations/registry.py`: `_KNOWN["aikido"] = ("strata.integrations.aikido", "AikidoIntegration")`
  (register at the end of Phase 4 if `find_image_issues` is not yet real; the class must be complete
  when registered)
- `tests/strata/fixtures/aikido/*.json` (ref Appendix B)
- `tests/strata/integrations/test_integrations_aikido.py` (new)

**Tasks**

1. `AikidoIntegration(ReleaseGateIntegration, ImageFindingsIntegration, CredentialedIntegration,
   ConnectionCheckIntegration)`; `TYPE = "aikido"`, `CAPABILITIES = {RELEASE_GATE,
   IMAGE_FINDINGS}`, `TRANSPORTS = {"http"}`, `COMMAND = None`; constructor
   `(config=None, *, sleep=time.sleep, clock=time.monotonic)`.
2. Constructor validation -> `IntegrationError("integration_configuration_invalid: ...")` naming the
   offending key (design §5.1, D11):
   - `configuration` keys: `region`, `http_timeout`, `max_retries`, `release_gate`, `images` only;
     types/ranges/defaults per §5.1; no duplicate `fail_on`;
   - `release_gate` block only when the document declares `release_gate`; `images` only with
     `image_findings`;
   - authentication: none, or `api_key` (only with `release_gate` declared) or `oauth2` (only with
     `image_findings`); any other method rejected; unused fields rejected
     (`api_key.api_secret/endpoint/header_name`, `oauth2.tenant_id/token_url/authorization_url/scope`).
3. `bind_credentials(values)`: with `api_key`, CI token = `values[auth.api_key.api_key]`; with
   `oauth2`, client id/secret from their keys; without authentication, env fallback for the declared
   capabilities (`AIKIDO_CI_TOKEN`, then `AIKIDO_API_KEY`; `AIKIDO_CLIENT_ID`; `AIKIDO_CLIENT_SECRET`).
   A missing value is not raised here; the capability method raises `IntegrationError("no ... credential")`
   so the controller treats it as missing data.
4. `base_url`: `endpoints.address` > `region` > CI token prefix (`AIK_CI_US_`/`_ME_`/`_AU_`) > EU
   (ref §5.1, A.1).
5. `_call()` with retries exactly per ref §6.2 (import `http_request` into the module namespace for
   monkeypatching); `_raise_for()` with truncated body and auth hint, never headers.
6. `run_release_gate(request, *, on_poll=None)`: start body per ref §6.2 / A.3.1 (all five
   `fail_on_*` explicit, `is_release_gating: true`, `version: "strata-<ver>"`, omit `base_branch`
   when unset and `sla_mode` when false), thresholds from configuration; 404 -> `GateNotFoundError`
   with `reason_phrase`; poll until `all_scans_completed is True`; injected-clock timeout ->
   `GateTimeoutError` (message carries `scan_id`); outcome `passed = body.get("gate_passed") is True`,
   `reference = str(scan_id)`, counts, breakdown, links; defensive parsing -> `IntegrationError`.

**Tests** (`tests/strata/integrations/test_integrations_aikido.py`, fake transport, injected sleep/clock)

- Configuration and authentication validation: every rejected case from task 2 names the key; the
  two §5.1 documents construct.
- `bind_credentials`: `api_key` and `oauth2` values used; env fallback incl. `AIKIDO_API_KEY` alias;
  nothing available -> capability method raises "no ... credential".
- `base_url` precedence.
- Start request: exact URL, method, `X-AIK-API-SECRET`, exact JSON body.
- 404 with and without `reason_phrase`; running -> running -> passed (sleeps, `on_poll` 1, 2);
  missing `all_scans_completed` keeps polling; timeout; `gate_passed: "true"` fails; failed fixture
  maps counts, links, breakdown.
- Retries: 503/503/200 sleeps `[1, 2]`; `Retry-After: 7` -> 7, `999` -> 60; 400/401/403/404 not
  retried; exhausted -> `IntegrationError`; `NO_RESPONSE` retried then unreachable.
- No credential value in any exception text.

**Done when** the release gate runs end to end against the fake transport.

---

## Phase 4 - Aikido: image findings and connection check

> **Done 2026-10-26.** OAuth token (JSON then form fallback, cached until 60 s before expiry,
> 300 s default), REST GET with one re-auth on 401, envelope-tolerant paging for containers and the
> issue export, image naming, `find_image_issues()`, `check_connection()`, and `aikido` registered in
> `_KNOWN`. Three new fixtures; 35 more tests (94 in `test_integrations_aikido.py`); a mutation check
> confirmed the tests catch a prefix (not exact) name match, a missing re-auth, and a missing expiry
> margin. Full suite green (3249 passed). One refinement beyond the design: when several containers
> share the image's name, the one carrying the deployed tag is chosen; only when none or several do
> is the image `ambiguous` (whether Aikido lists one container per tag is reference R3). With only
> `UNKNOWN` requested, no export call is made (Aikido has no unknown severity).

**Files**: `src/strata/integrations/aikido.py`, `src/strata/integrations/registry.py`,
`tests/strata/integrations/test_integrations_aikido.py`, `tests/strata/integrations/test_integrations_registry.py`

**Tasks**

1. OAuth `_access_token()` per ref §6.2 (Basic auth, JSON then form fallback on 400/415, cache until
   `expires_in - 60`, default 300); one re-auth + retry on a REST 401.
2. Envelope-tolerant `_items()`; containers lookup (paging cap 10) and issues export
   (`filter_status=open`, lowercased `filter_severities`, paging cap 20) per ref A.4.
3. Naming: `images.names[image.path]` override, else `repository` or `path` per `name_strategy`;
   exact case-insensitive match; zero -> `unknown`, several -> `ambiguous`; `require_tag_match` adds
   `filter_tag` and treats a digest-only or untagged image as `unknown`.
4. `find_image_issues(image, severities) -> ImageLookup`; issues mapped to `SecurityIssue` (`id` as
   str; Aikido lowercase severity -> uppercase, anything else `UNKNOWN`; `cve_id`;
   `affected_package` -> `package`).
5. `check_connection()`: OAuth token for an `image_findings` document, CI token probe (`scan_id=0`:
   401/403 rejected, anything else accepted; ref A.3.4) for a `release_gate` document; no credential
   -> `IntegrationError`. One line per verified part.
6. Register `aikido` in `_KNOWN` (if not done in Phase 3).

**Tests**

- Token: URL, Basic header, JSON body; cache and refresh by the fake clock; 415 -> form remembered;
  missing `access_token` -> error. REST 401 -> exactly one re-auth and retry.
- Containers: bare list and `{"containers": [...]}`; short-page stop; page cap.
- Naming: repository vs full for `ghcr.io/huybrechtsxyz/api`, `traefik`, digest refs; override;
  exact match; unknown; ambiguous; `require_tag_match`.
- Issues: `filter_severities=critical,high` for `{"CRITICAL","HIGH"}`; uppercase mapping and
  `UNKNOWN`; items without `id` dropped.
- `check_connection()` per document type and with nothing bound.
- Registry: `get("aikido")`, `get_class("aikido")`, `find_capability_mismatches == []`, existing
  `_KNOWN` walk passes.
- No token/secret in exception text or captured logs.

**Done when** both capabilities and the connection check work against the fake transport.

---

## Phase 5 - Generic controller, deploy hook, policy results

> **Done 2026-10-26.** `controllers/security_gate_controller.py`, `append_policy_result()`, and the
> deploy hook; 44 controller tests, 3 append tests, 7 end-to-end deploy tests (all vendor-neutral
> fakes). Full suite green (3303 passed). Refinements recorded in design 6.3/6.4:
> `evaluate_security_gates()` returns a `SecurityGateEvaluation` (per-gate diagnostics plus
> `policy_results()`, replacing a separate `security_gate_results()`); `resolve_commit_sha()` takes
> the diagnostics it warns into; redaction length is applied in the deploy hook (avoids a circular
> import); no logging (decided: `on_step` plus diagnostics, like the rest of deploy). A mutation
> check caught three of four injected faults; the fourth (removing the hook's early return) was
> not observable because `deploy_run` also stops on any error before running steps, so the test
> holds either way; that is documented in the test.

**Files**

- `src/strata/controllers/security_gate_controller.py` (new, design §6.3)
- `src/strata/controllers/policy_results.py`: `append_policy_result()` (§6.5)
- `src/strata/controllers/deploy_controller.py`: hook (§6.4)
- `tests/strata/controllers/test_security_gate_controller.py` (new)
- `tests/strata/controllers/test_policy_results.py` (extend)
- `tests/strata/controllers/test_deploy_security_gates.py` (new)

**Tasks**

1. `resolve_release_gate_integration()` / `resolve_image_findings_integration()`: named document ->
   `registry.get(type, config)` -> `isinstance` check (mirrors `resolve_output_store_integration()` /
   `_require_key_value_integration()`); `None` when the policy is unset; construction
   `IntegrationError` -> `integration_configuration_invalid`.
2. `bind_resolved_credentials(context, integration, resolved, deployment_name, on_secret)` (D2):
   keys from `spec.authentication.key_references()` (none without a block); values from `resolved`
   on the deploy path, else `resolve_values(context, deployment_name, keys)`; a missing key ->
   `integration_credentials_unresolved` (key named, value never); each value of at least
   `_MIN_REDACT_LENGTH` to `on_secret`; then `bind_credentials()` (empty mapping without a block).
   Skip for non-`CredentialedIntegration`.
3. `resolve_commit_sha()` (D6 order); invalid value -> `release_gate_commit_invalid` warning, next
   source.
4. `images_from_sbom()`: `pkg:docker` container components -> `ImageRef` via
   `PackageURL.from_string()` + `split_registry()`; version is a digest when it contains `:`, else a
   tag; dedupe by `(path, tag, digest)`; `None` when `sbom.json` is missing.
5. Gate evaluation per design §6.3 and §8: thresholds over `SECURITY_SEVERITY_ORDER`, allowlist
   (`cve_allowed`, `_apply_allowlist` semantics), `on_missing_data`, `on_unknown_image`,
   `GateNotFoundError` always hard; both gates run even if the first failed.
6. `security_gate_results()` -> two `ManifestPolicyResultModel`s (D9); `append_policy_result()`.
7. Deploy hook after the `resolve_values()` early return: `--smoke-test` -> "would run" step only;
   otherwise evaluate with `on_secret` feeding the existing redaction set, append results, return
   on a not-ok result before any step.

**Tests** (a fake integration implementing both ABCs + `CredentialedIntegration`, registered via a
monkeypatched registry; no Aikido code exercised)

- Credentials: authentication with keys resolved from `resolved`; outside deploy via
  `resolve_values`; unresolved key -> hard error without the value; no block -> `bind_credentials({})`;
  values reach `on_secret`.
- Commit order, each source isolated; invalid skipped with a warning; none -> missing data.
- Release: pass -> info with reference; fail + deny/warn -> error/warning with counts and first link;
  `GateNotFoundError` -> hard error even with `on_missing_data: skip`; timeout/other -> missing data
  under block/warn/skip.
- Images: purl parsing (registry, Docker Hub, port, digest, no version); `max_count` boundary;
  `UNKNOWN` never counted; allowlist active/expired/package-scoped, no `cve_id` never suppressed;
  unknown per `on_unknown_image`; ambiguous -> error; no `sbom.json` -> missing data; no containers
  -> info.
- Both gates fail -> both diagnostics; results: two entries, `policy_type` = integration type,
  `phase == "deploy"`, warn breach `passed: true` with violations; `append_policy_result` creates,
  keeps others, replaces same name.
- Deploy: deny breach returns before any step; `smoke_test` makes zero calls; `dry_run` runs the
  gates; no policy -> vendor module not imported; credential values never in `on_step`/`on_line`
  text or diagnostics; build-phase `policy_results.json` entries kept.

**Done when** deploys are blocked or allowed by gate verdicts through a vendor-neutral path, and
nothing leaks.

---

## Phase 6 - Commands and documentation

> **Done 2026-10-26.** `policy_controller.py`, `list_image_findings()`, both command groups, the
> how-to and doc updates; 25 controller tests, 16 `policy` and 10 `integration` command tests (fake
> gate for `policy`, real Aikido with a stubbed transport for `integration check`). Full suite green
> (3358 passed), docs build with `-W`, `strata validate` on `config/` clean. Refinements recorded in
> design D7: each table row's check function runs its own gate (`only=`), so a row is self-contained
> and Phases 8-10 add one function each; a usage error for a policy `check` cannot run names its
> lifecycle phase (`build run`), not a plan phase number; a new `IntegrationCredentialError`
> (subclass of `IntegrationError`, raised by Aikido for a missing credential or HTTP 401/403) lets
> `integration check` exit 3 for credentials and 1 for an outage; `policy findings` exits 3 when the
> integration is misconfigured or its credentials do not resolve. A mutation check caught all three
> injected faults (results not recorded, allowlisted issues dropped, credential rejection mapped to
> exit 1).
Design D7: a general `strata policy` group (not `security`), kept small. `policy check` supports
the two gate policies only and `policy findings` the image gate only; `cve_policy`,
`checkov_policy` and a future `opa_policy` join in Phases 8-10.

**Files**

- `src/strata/controllers/policy_controller.py` (new): the policy dispatch table (one row per
  workspace policy field: phase, what backs it, check function or `None`, findings function or
  `None`) and `list_policies()`, `check_policies()`, `policy_findings()`.
- `src/strata/controllers/security_gate_controller.py`: a read-only `list_image_findings()` for
  `policy findings` (reuses `images_from_sbom()`, integration resolution, credential binding and the
  allowlist; marks allowlisted issues instead of dropping them).
- `src/strata/commands/policy_command.py` (new): `policy list`, `policy check`, `policy findings`.
- `src/strata/commands/integration_command.py` (new): `integration check <name> [--deployment D]`.
- `src/strata/commands/cli.py`: register both groups.
- `tests/strata/controllers/test_policy_controller.py`, `tests/strata/commands/test_commands_policy.py`,
  `tests/strata/commands/test_commands_integration.py` (new).
- `src/strata/templates/agent/strata/SKILL.md`: rows for `strata policy list / check / findings
  DEPLOYMENT` and `strata integration check NAME` (the drift test
  `test_agent_skill_documents_every_visible_command` fails until done).
- `docs/config/workspace.md` (policies), `docs/config/integration.md` (capabilities, `type: aikido`,
  the two-document pattern), `docs/how-to/security-gates.md` (new; add to the how-to toctree in
  `docs/index.rst`), `docs/design/v2-schema-overview.md`, `.github/CHANGELOG.md`.

**Tasks**

1. Dispatch table rows for all four existing policy fields, so `policy list` shows every configured
   policy; only `release_gate_policy` and `image_gate_policy` carry a check function, and only
   `image_gate_policy` a findings function. Backing: `trivy/grype`, `checkov`, or
   `<type> '<integration name>'` for the gates.
2. Shared options as in `deploy_command.py` (`--path`, `--build-path` defaulting to
   `<root>/build/<deployment>`, `--pin`, `--output`, `--quiet`, `--verbose`) and the existing `run`
   context / `run.report()`.
3. `policy list`: per configured policy, name, phase, enforcement, backing, `check` supported yes/no,
   and the last result from `policy_results.json` under the build path. No network.
4. `policy check [--policy NAME ...] [--commit SHA]`: default every configured policy with a check
   function; a `--policy` that is not configured or not yet checkable -> usage error (exit 2) naming
   the phase that adds it; nothing to check -> exit 2. The gates run through
   `evaluate_security_gates(..., resolved=None, version_pin, commit_override, only)` with progress
   lines on the console; results append to `policy_results.json` when the build path exists.
5. `policy findings --policy NAME [--severity LEVEL] [--integration NAME]`: only `image_gate_policy`
   for now (others -> exit 2 naming their phase). Per image: platform name, status, counts by
   severity, up to 10 issues, allowlisted ones marked; full structure in JSON. `--severity` defaults
   to the policy's `max_severity`, else `HIGH`; `--integration` names an `image_findings` document so
   it works without a policy configured. Unreachable / rejected -> exit 1.
6. `integration check <name> [--deployment D]`: with `--deployment`, resolve the document's
   `key_references()` through it (`bind_resolved_credentials()`); without it, only documents with no
   `authentication` block can be checked. Exits: unreachable 1; unknown name, unsupported type or
   `--deployment` needed 2; missing/rejected/unresolved credentials 3.
7. How-to: create the CI token and API client in Aikido, store them in Key Vault, declare them in an
   Environment, add the two Integration documents and both policies, run `strata integration check`
   for each and `strata policy check` before deploying.

**Tests**

- Controller: table rows for all four fields; `list` with and without `policy_results.json`;
  `check` default selection, `--policy` filters, unsupported/unconfigured -> usage error naming the
  phase; `findings` marks allowlisted issues and works with `--integration` and no policy. Fake
  integration only.
- Commands: `--help` for both groups; `policy check` pass 0 / `warn` breach 0 / `deny` breach 3 /
  nothing to check 2, `--policy image_gate_policy` makes no release call, `--commit` overrides env;
  `policy findings --output json` keys, unreachable 1; `integration check` ok 0 / rejected 3 /
  unreachable 1 / unsupported 2 / needs `--deployment` 2; `STRATA_OUTPUT=json`. Fake integration for
  `policy`, Aikido + fake transport for `integration check`.
- Full suite including the agent-skill drift test; docs build with `-W`.

**Done when** the commands work offline and the docs build.

---

## Phase 7 - First contact (network)

Fold answers into [aikido-api-reference.md](aikido-api-reference.md) (§13, Appendix A: [U] -> [V])
and simplify code where a fallback proves unnecessary (keep defensive parsing).

| #   | Check                       | How                                                                                                                                      | Resolves  |
| --- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| 1   | OAuth body encoding, fields | `strata integration check aikido-api --deployment <d>` with debug logging                                                                | ref R1    |
| 2   | REST envelopes              | `strata policy findings <d> --policy image_gate_policy --output json` + debug log of top-level keys                                      | ref R2    |
| 3   | Container naming            | compare `findings` with the Aikido UI for one ACR, one GHCR, one Docker Hub image                                                        | ref R3    |
| 4   | `repository` by name vs id  | `strata policy check <d> --policy release_gate_policy` with each                                                                         | ref R4    |
| 5   | `version` field             | start-scan response with `strata-<ver>`                                                                                                  | ref R5    |
| 6   | Rate limits                 | 429 / `Retry-After` during `findings` over many images                                                                                   | ref R6    |
| 7   | Scopes                      | remove scopes until a call fails                                                                                                         | ref R7    |
| 8   | CI token probe              | `strata integration check aikido-ci --deployment <d>`, valid vs invalid token                                                            | ref A.3.4 |
| 9   | Region                      | repeat 1-4 against a non-EU workspace if available                                                                                       | ref R9    |
| 10  | End to end                  | `strata build run` then `strata deploy run --dry-run` with both policies; clean repo passes; a test repo with a known critical CVE fails | -         |

---

## Phase 8 - Follow-on: `cve_policy` in `strata policy`

The CVE gate already runs in `build run` (`sbom_controller.evaluate_cve_policy(build_path,
workspace, index)`) and needs only what the build path holds (`sbom.json`) plus Trivy or Grype.

**Design (2026-10-27).** Verified first: `evaluate_cve_policy()` reads only `build_path` and the
index (scanner choice included, integration-readiness Phases 6-7), so it runs unchanged outside
`build run`; the result builder `build_controller._cve_policy_result()` is private; `_apply_allowlist()`
drops suppressed findings, so nothing can show them; `policy findings` is hard-wired to
`ImageFindingsReport`.

- **P8-1 - Same evaluation, same record.** `policy check` calls `evaluate_cve_policy()` unchanged
  and records the entry `build run` records: `phase: "build"` (it judges build output), replacing
  the build entry. `_cve_policy_result()`/`_checkov_policy_result()` move to `policy_results.py` as
  public `cve_policy_result()`/`checkov_policy_result()`, used by both.
- **P8-2 - Included by default.** A plain `policy check <deployment>` evaluates `cve_policy` too
  when configured; a missing scanner follows the policy's `on_missing_data`, as in `build run`.
- **P8-3 - One allowlist rule.** `sbom_controller` splits it into `active_allowlist()` and
  `is_allowlisted()`; the gate drops, the new read-only `cve_findings()` marks. The verdict cannot
  change.
- **P8-4 - Findings per row.** Each table row carries an optional findings function returning its
  own report (`ImageFindingsReport`, new `CveFindingsReport`); the command renders by type.
  `--severity` for `cve_policy` defaults to `severity_threshold`, else `max_severity`;
  `--integration` is only valid for `image_gate_policy` (usage error otherwise).
- **P8-5 - `policy findings --policy cve_policy` errors.** No `sbom.json`: exit 2 (as the image
  gate); scanner missing or the scan failing: exit 1 (an environment problem, not a verdict).
- **P8-6 - `policy list`** names the declared scanner (`trivy 'name'` / `grype 'name'`), else
  `trivy/grype (PATH)`.

**Files**: `controllers/policy_controller.py` (row gains check and findings functions),
`controllers/sbom_controller.py` (a read-only `cve_findings()` returning the scan result with
allowlisted findings marked), `controllers/build_controller.py` (move `_cve_policy_result()` where
both `build run` and `policy check` can use it, e.g. a public helper in `policy_results.py`, instead
of a cross-module private import), tests in `test_policy_controller.py` and `test_commands_policy.py`.

**Tasks**

1. `check`: `evaluate_cve_policy()` against `--build-path`; result via the shared result builder;
   appended to `policy_results.json` (replacing the build-phase entry, which is the intended refresh).
2. `findings`: per finding vulnerability id, package, installed/fixed version, severity, allowlisted
   flag; counts by severity.
3. `policy list` shows `check` supported for `cve_policy`.

**Tests**: check pass/breach/missing scanner under each `on_missing_data` (scanner stubbed as in
`test_sbom_controller.py`); findings marks allowlisted entries; `build run` results unchanged.

**Done when** `strata policy check --policy cve_policy` matches what `build run` would decide for
the same SBOM.

**Done (2026-10-27).** As designed (P8-1 to P8-6). The allowlist helpers are private
(`_active_allowlist()`, `_is_allowlisted()`). `_PolicyRow.findings` is now a function taking a
request; `policy_findings()` returns `ImageFindingsReport | CveFindingsReport`. "Done when" is a
test (`test_check_cve_reaches_the_same_result_as_build_run`); five mutants, all killed.

---

## Phase 9 - Follow-on: `checkov_policy` in `strata policy` (design first)

`evaluate_checkov_policy(build_path, workspace, provisioner_source_paths, namespaces=..., index=...)`
needs each provisioner's materialised source path, which `build_run()` only holds in memory
(`materialised`, filled by its step loop). A standalone check cannot get it from the build path today.

**Tasks**

1. Decide, in a short design note (and an ADR if it changes the build output contract), how a
   standalone check finds the rendered sources: re-derive them from the build path layout, or have
   `build run` record them (e.g. alongside `resolved.yaml`). Ground the choice in what a real
   pipeline runs between build and deploy.
2. Then as Phase 8: row with check and findings functions (failed checks per resource and file,
   severity), shared result builder, tests.

**Design (2026-10-27).** Verified first:

- **Where the sources land.** `build run` puts a source at
  `build_path / (source.target_path or source.source_path)`, which `source_sync.sync_source()`
  computes from the model alone. A source-less step provisioner uses `build_path / step.name`.
  `build run` reaches the provisioner of every execution step, plus the transitive `depends_on`
  closure of each (`materialise_provisioner_sources()`). A dependency without a source maps to
  `None`.
- **Pins don't move paths.** Version pins change a remote's ref (`apply_remote_version_pins()`),
  never a path. `--pin` therefore cannot move a source.
- **Helm.** The helm branch already reads `build_path/<namespace>/<module>`. It also needs the
  workspace's namespaces, which `build_resolved_workspace_graph()` gets from the private
  `_lookup_all(index, NAMESPACE, workspace.spec.namespaces)`.
- **Deploy works in the build directory.** Terraform runs in place (`cwd=path`,
  `integrations/terraform.py`), so after a deploy the build path also holds `.terraform/`.
- **Probe with checkov 3.3.16** (the version installed here). It scanned a directory with a stray
  `.terraform/modules/m/x.tf` and reported only the top-level file, so `.terraform/` is skipped.
  A check before or after the deploy sees the same files.
- **Probe with checkov 3.3.16, no API key.** Every failed check came back with `severity: null`.
  Strata maps that to `UNKNOWN`, and `findings_at_or_above()` never counts `UNKNOWN` against any
  `severity_gate`. **So with open-source Checkov, `checkov_policy` as built never breaches.** See
  P9-7.

Decisions:

- **P9-1 - Re-derive the source paths; don't record them.**
  - A new public `expected_source_paths(build_path, workspace) -> dict[str, Path | None]` sits next
    to `materialise_provisioner_sources()` in `build_controller.py`. It returns the reached set,
    each provisioner mapped to its destination, or to `None` when the provisioner has no source or
    its directory isn't on disk ("not materialised during this build", as today).
  - The path rule moves into one function, `source_sync.source_destination(build_path, source)`,
    which `sync_source()` itself uses, so the two cannot drift.
  - Why not record the paths (e.g. a section in `resolved.yaml`): that changes the build-output
    contract (an ADR), and adds a file that must stay in sync with something that is already a
    pure function of the model. Why not re-sync: it is slow, needs the network, and mutates the
    build path. No ADR is needed: the build output is unchanged.
- **P9-2 - Parity is a test, not a hope.** A real `build_run()` covers a step provisioner, a
  `depends_on`-only one, a `target_path`, and a source-less dependency. The test captures the
  `materialised` dict passed to `evaluate_checkov_policy()` and asserts `expected_source_paths()`
  returns the same dict.
- **P9-3 - Same evaluation, same record.** `check` calls `evaluate_checkov_policy(build_path,
  workspace, expected_source_paths(...), namespaces=workspace_namespaces(index, workspace),
  index=index)` unchanged. It records the result through the shared `checkov_policy_result()`
  (Phase 8) with `phase: "build"`, replacing the build entry.
  - `workspace_namespaces()` is a new public helper in `build_controller.py`;
    `build_resolved_workspace_graph()` uses it too.
  - A plain `policy check` includes `checkov_policy` (as P8-2). Missing directories follow
    `on_missing_data`, as in `build run`.
- **P9-4 - Same staleness as `cve_policy`.** The check judges what is in `--build-path`, against
  the current workspace document. If the solution changed after the build, a provisioner's
  directory may be missing, and that goes through `on_missing_data`. `build run` cleans the build
  path by default, so the path holds one build.
- **P9-5 - `policy findings --policy checkov_policy`.**
  - New `checkov_controller.checkov_findings(build_path, workspace, index, threshold)` returns a
    `CheckovFindingsReport(scanner_version, framework, severities, targets)`. Each target is one
    provisioner or Helm module: `CheckovTarget(label, path, missing, findings)`.
  - Each finding carries `unrated: bool` (as built; designed as `counted`): check id, name,
    resource, `file:line`, severity. Findings below the threshold are dropped. `UNKNOWN` findings
    are always listed and marked `[unrated, never counted]` (P9-7).
  - `--severity` defaults to `severity_gate` (upper-cased). `--integration` is a usage error, as
    for `cve_policy`.
  - Exit codes, as P8-5: a build path that does not exist exits 2 ("run `strata build run`
    first"); Checkov missing, or any target's scan failing, exits 1. A target that isn't
    materialised is data, not an error: it is shown in its row.
  - Checkov has no allowlist beyond `skip_checks`/`include_checks`, which apply inside the scan, so
    there is no allowlisted marker.
- **P9-6 - `policy list`** names the declared integration (`checkov 'name'`), else
  `checkov (PATH)`. The `checkov_policy` row becomes checkable, with findings.
- **P9-7 - The `UNKNOWN` severity gap is reported here and fixed separately.** Phase 9's "done
  when" is parity with `build run`, so the verdict does not change in this phase. `findings`
  makes the gap visible: failures appear, marked as not counted. Fixing it changes `build run`
  too, so it needs its own decision. Options:
  - (a) a policy field `unknown_severity: ignore | count`, default `ignore`, i.e. today's
    behaviour, with opt-in counting;
  - (b) count `UNKNOWN` by default, which is breaking: a green build can turn red;
  - (c) gate on check ids (`fail_checks: [...]`) instead of severity.

  Recommended: (a), as a small follow-on phase 9b, with the how-to telling open-source users to
  set it.

**Files**: `controllers/source_sync.py` (`source_destination()`), `controllers/build_controller.py`
(`expected_source_paths()`, `workspace_namespaces()`), `controllers/checkov_controller.py`
(`checkov_findings()`, report types), `controllers/policy_controller.py` (row, backing),
`commands/policy_command.py` (rendering, help), tests in `test_build_controller.py` (parity),
`test_checkov_controller.py`, `test_policy_controller.py`, `test_commands_policy.py`; docs as
Phase 8.

**Tests**:
- Parity (P9-2).
- Check: pass, breach under `deny`/`warn`, and a missing directory or missing Checkov under each
  `on_missing_data` (scanner stubbed as in `test_checkov_controller.py`), for both a terraform
  scope and a helm scope.
- The recorded result has `phase: "build"` and replaces the build entry.
- Findings: per-target rows, the `UNKNOWN` marker, the threshold default, and the exit codes.
- "Done when": the same build path gives the same `passed`/`violations` through `build run`'s
  evaluator and `policy check`.

**Done when** `strata policy check --policy checkov_policy` reproduces the `build run` verdict from a
build path alone.

**Done (2026-10-27).** As designed (P9-1 to P9-6); P9-7 decided as option (b), built as Phase 9b
below.
Refinements:
- The evaluator's target resolution is extracted into `_targets()`, which the gate and
  `checkov_findings()` share, so they cannot disagree on what is in scope. The 119 existing
  Checkov and build tests passed unchanged after the extraction.
- The finding flag is `unrated` (no severity, never counted), not `counted`. The flag means
  "the gate can never count this", not "the gate counts this at the current threshold".
- `declared_checkov()` is public, for `policy list`. Without a policy, `findings` uses the
  policy defaults.
- Parity (P9-2) is tested across five source shapes: a dependency, a transitive chain, a shared
  dependency, a provisioner reached both ways, and `target_path` with a source-less dependency
  and an unreached provisioner.
- A CLI test pins the gap: a `deny` policy with only `UNKNOWN` findings exits 0.
- Eight mutants: seven killed at first. The survivor (a source-less step's
  `build_path / step.name`) now has its own test.

### Phase 9b - Unrated Checkov findings count (P9-7, option b)

**Decision (2026-10-27): option (b).** A failed check with no severity counts against the
`checkov_policy` gate, at any `severity_gate`. No new field.

- **P9b-1 - Why (b), not (a).** Under (a), the gate stays silent until someone sets a field they
  don't know exists, so "never scanned" and "scanned clean" keep looking the same. That is the
  risk `on_missing_data` exists to prevent. A failed check is a failed check: if Checkov cannot
  rate it, it cannot be rated below the gate either.
- **P9b-2 - Scope: Checkov only.** CVE `UNKNOWN` (Trivy/Grype) is a severity the vendor actually
  assigned ("not yet rated" in the CVE database), and the image gate's `UNKNOWN` maps Aikido
  values strata does not recognise. Both stay uncounted (`cve_policy` already exposes
  `severity_threshold: UNKNOWN` for that case). Checkov's `UNKNOWN` instead means "no severity
  data at all" (`severity: null`, open-source Checkov).
- **P9b-3 - Where.** `checkov_controller`: unrated = severity outside
  `CRITICAL..LOW` (`null`, `UNKNOWN`, or a value strata does not recognise, matching
  `findings_at_or_above()`'s own mapping). Breaching = `findings_at_or_above(gate)` + unrated.
  `CheckovScanResultModel.findings_at_or_above()` is unchanged: it stays a pure severity filter,
  with its own tests.
- **P9b-4 - Message.** Unchanged when every breach is rated; otherwise it adds
  `, N of them unrated`, so a user can see why a `severity_gate: critical` policy failed.
- **P9b-5 - Breaking, by intent.** A build or `policy check` that passed with open-source
  Checkov and failed checks now fails under `enforcement: deny`. Ways out, all existing:
  `enforcement: warn`, `skip_checks` / `include_checks`, or fixing the findings. CHANGELOG
  "Changed" entry; the how-to says so.
- **P9b-6 - `policy findings`.** Unrated findings are still always listed; the marker becomes
  `[unrated]` (they are counted now). The JSON `unrated` flag is unchanged.

**Tests**: `evaluate_checkov_policy()` with only `UNKNOWN` (deny -> error, warn -> warning), an
unrecognised severity string counted, the mixed message, `severity_gate: critical` still failing
on unrated; `build run` fails on an unrated finding; the CLI case pinned in Phase 9
(`deny` + `UNKNOWN` -> exit 0) flips to exit 3; CVE and image gates unchanged (existing tests).

**Done (2026-10-27).** As designed. Six new tests (checkov controller, `build run`), the Phase 9
CLI pin flipped to exit 3; three mutants (no unrated counting, no message note, only the literal
`UNKNOWN` treated as unrated), all killed. P9-7 is closed.

---

## Phase 10 - Follow-on: a future `opa_policy` (design first, only on real demand)

Not planned until a consumer needs policy-as-code (tags, naming, regions, cost) that the bespoke
policies cannot express. Recorded so the group's shape anticipates it.

**Expected shape (to validate in its own design doc):** a new capability (e.g. `policy_decision`)
with an `opa` integration (the `opa eval`/`conftest` CLI or an OPA server over HTTP); a workspace
field `opa_policy` naming that integration, the policy bundle and query, and its input (rendered
artifacts at build, or the Terraform plan JSON at a `plan` phase, which `ManifestPolicyResultModel.phase`
already allows); `findings` = the violations OPA returns. Joins `strata policy` as one more table row.

**Done when** a consumer's real rule set runs through `strata policy check` and blocks a deploy.

---

## Definition of done

- [ ] Phases 1-6 merged; CI green (mypy strict, ruff, import-linter, pytest).
- [ ] No vendor name outside `integrations/aikido.py`, the registry entry, tests and docs.
- [ ] No new credential mechanism: credentials only via `AuthenticationModel` (or vendor env
      fallback without a block).
- [ ] Coverage of `security_gate_controller.py` and `aikido.py` >= 90% lines.
- [ ] No new runtime dependency.
- [ ] `deploy run` without gate policies behaves exactly as before (existing deploy tests unchanged).
- [ ] Secret-leak tests pass.
- [ ] Docs and agent skill updated.
- [ ] ADR recorded (done 2026-10-27: ADR-0047, ADR-0048, ADR-0049); Phase 7 findings folded in; design graduates to `docs/design/security-gates.md`,
      index + reference + plan to `docs/archive/` (Phases 8-10 move to their own work docs first if
      still open then).

## File checklist

| File                                                                                                                                                                     | Phase   | New/changed |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ----------- |
| `src/strata/models/integration_model.py`                                                                                                                                 | 1       | changed     |
| `src/strata/models/auth_models.py`                                                                                                                                       | 1       | changed     |
| `src/strata/integrations/security_findings.py`                                                                                                                           | 1       | new         |
| `src/strata/integrations/errors.py`                                                                                                                                      | 1       | changed     |
| `src/strata/integrations/capabilities.py`                                                                                                                                | 1       | changed     |
| `src/strata/utils/sbom_utils.py`                                                                                                                                         | 1       | changed     |
| `src/strata/models/security_gate_model.py`                                                                                                                               | 2       | new         |
| `src/strata/models/workspace_model.py`                                                                                                                                   | 2       | changed     |
| `src/strata/controllers/semantic_checks.py`                                                                                                                              | 2       | changed     |
| `src/strata/integrations/aikido.py`                                                                                                                                      | 3, 4    | new         |
| `src/strata/integrations/registry.py`                                                                                                                                    | 3/4     | changed     |
| `src/strata/controllers/security_gate_controller.py`                                                                                                                     | 5       | new         |
| `src/strata/controllers/policy_results.py`                                                                                                                               | 5       | changed     |
| `src/strata/controllers/deploy_controller.py`                                                                                                                            | 5       | changed     |
| `src/strata/controllers/policy_controller.py`                                                                                                                            | 6, 8-10 | new         |
| `src/strata/commands/policy_command.py`                                                                                                                                  | 6       | new         |
| `src/strata/commands/integration_command.py`                                                                                                                             | 6       | new         |
| `src/strata/commands/cli.py`                                                                                                                                             | 6       | changed     |
| `src/strata/templates/agent/strata/SKILL.md`                                                                                                                             | 6       | changed     |
| `tests/strata/models/test_models_auth.py`                                                                                                                                | 1       | changed     |
| `tests/strata/integrations/test_integrations_security_findings.py`                                                                                                       | 1       | new         |
| `tests/strata/integrations/test_integrations_capabilities.py`                                                                                                            | 1       | changed     |
| `tests/strata/utils/test_utils_sbom_utils.py`                                                                                                                            | 1       | changed     |
| `tests/strata/models/test_models_security_gate.py`                                                                                                                       | 2       | new         |
| `tests/strata/models/test_models_workspace.py`                                                                                                                           | 2       | changed     |
| `tests/strata/controllers/test_semantic_checks.py`                                                                                                                       | 2       | changed     |
| `tests/strata/fixtures/aikido/*.json`                                                                                                                                    | 3       | new         |
| `tests/strata/integrations/test_integrations_aikido.py`                                                                                                                  | 3, 4    | new         |
| `tests/strata/integrations/test_integrations_registry.py`                                                                                                                | 4       | changed     |
| `tests/strata/controllers/test_security_gate_controller.py`                                                                                                              | 5       | new         |
| `tests/strata/controllers/test_policy_results.py`                                                                                                                        | 5       | changed     |
| `tests/strata/controllers/test_deploy_security_gates.py`                                                                                                                 | 5       | new         |
| `tests/strata/controllers/test_policy_controller.py`                                                                                                                     | 6       | new         |
| `tests/strata/commands/test_commands_policy.py`                                                                                                                          | 6       | new         |
| `tests/strata/commands/test_commands_integration.py`                                                                                                                     | 6       | new         |
| `docs/config/workspace.md`, `docs/config/integration.md`, `docs/how-to/security-gates.md`, `docs/index.rst`, `docs/design/v2-schema-overview.md`, `.github/CHANGELOG.md` | 6       | changed/new |
