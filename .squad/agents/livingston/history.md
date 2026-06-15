# Livingston — History

## Core Context

Tester / QA for strata. pytest, Click CliRunner, Pydantic model testing, integration mocking.
User: Vincent Huybrechts. Stack: Python 3.13, pytest, nox, Click testing.
Key paths: `tests/strata/`, `conftest.py`, `noxfile.py`.

**Past test suites (condensed):**
- 2026-05-18: devcontainer scaffold tests — 7 tests in `TestSolutionControllerScaffoldDevcontainer`; `get_pkg_templates_path` patched via `patch(...)`; idempotency + graceful-skip tested
- 2026-05-19: sln group test pattern — `TestSln<Verb>` naming; CLI invocation must include group prefix `["sln", "init", ...]`; subcommand tests in `tests/strata/commands/`
- 2026-05-29: github secret store tests — 4 model tests + 5 controller tests; `monkeypatch.setenv` for env isolation; `GITHUB_ACTIONS` warning verified
- 2026-06-01: HelmBuilder anticipatory tests (27 skipped) — `IMPL_MISSING` guard pattern established; mirrors `test_builders_compose.py`; `_make_helm_module`, `_make_pvc_mount` helpers; service key `{module}-{service}` rule encoded
- 2026-06-01: Helm test coverage gap analysis — gap confirmed: no `test_integrations_helm.py` or `test_deployers_helm.py`; 13 integration tests + ~45 deployer tests planned and documented
- 2026-06-02: Helm integration + deployer tests — 13 integration + 42 deployer tests, all passing (1655 total); `d._helm = MagicMock()` injection pattern; `call_args_list` inspection for multi-call verification; `ensure_available()` returns `(True, "")` on success
- 2026-06-02: ComposeDeployer tests — 38 tests, 12 classes, all passing (1693 total); mirrors Helm deployer pattern; `force=False` guard tested BEFORE iterating files; `test_parse_error_logged_not_raised` confirms exceptions caught internally
- 2026-06-09: DNS kind tests (anticipatory) — 8 model tests + 5 service tests; `_make_dns_model()` helper; validator tests inserted after firewall equivalents; `DnsService.merge_dns()` assumed from firewall pattern
- 2026-06-09: DNS record value union tests — 9 new tests in `test_models_dns.py`; `_dns_data()` helper added; tests: one-of value/var/secret, cross-reference validation, references block required for var/secret

## Learnings

### 2026-06-10 — guide command tests (anticipatory)

**What was added:** `tests/strata/commands/test_guide_command.py` (new file) — 26 tests across 3 classes.

**Test classes:**
- `TestGuideCommandWorkspaceChecklist` (16 tests) — phases 1-7 checklist logic, JSON shape, exit-code invariants.
- `TestGuideCommandFileMode` (9 tests) — file inspection mode (`-f`), file phase checklist, next_steps actions.
- `TestGuideCommandHintCustomization` (1 test) — `.strata/guide.yaml` phase 6 hint override.

**Patterns followed:**
- `IMPL_MISSING` try/except guard on `from strata.commands.cli_guide import guide_command`; `pytestmark = pytest.mark.skipif(IMPL_MISSING, ...)` skips all 26 tests until Linus lands the implementation.
- `CliRunner(mix_stderr=False)` in `_runner()` factory per spec requirement.
- All filesystem work via `tmp_path` pytest fixture; no real workspace touched.
- `_make_workspace(tmp_path, solution, build_files)` — creates `.strata/`, writes `solution.json`, optionally populates `build/`.
- `_make_solution_json(...)` builds a dict for `json.dumps` — matches `SolutionModel` shape exactly.
- `_make_repo(name, url, path, repo_type)` and `_make_profile(name, active, config_paths, ...)` factory helpers keep tests concise.
- `_make_config_yaml(dest, kind, ...)` writes a minimal strata YAML for file-mode tests.
- JSON shape assertions use `json.loads(result.output)` directly (no mock needed — behaviour is filesystem-driven).
- Local repo test: `url: ""` + `type: "local"` distinguishes local repos from remote; validates hint emits `# local repo not found:` not `git clone`.

**Key assumptions (to confirm with Linus):**
- Import path: `strata.commands.cli_guide.guide_command` (mirrors `cli_help`, `cli_status`, etc.).
- JSON top-level keys: `workspace`, `checklist`, `next_steps` (array), `complete`.
- File mode JSON top-level includes `file` block.
- Status values: `"ok"`, `"warn"`, `"pending"` (not `"✅"`/`"⚠️"`/`"⬜"`).
- `.strata/guide.yaml` phase hint override format: `phases: { 6: { hint: "..." } }`.
- Empty URL string (`""`) on `SolutionSpecRepositoryModel.url` passes Pydantic validation (no non-empty validator on that field).

**Current status:** All 26 tests skip cleanly via `pytestmark`. Zero failures, zero errors.

### 2026-06-10 — Network kind tests (anticipatory)

**What was added:**
- `tests/data/network/` — 6 YAML fixtures: `network-haven.yaml` (simple flat), `network-enterprise.yaml` (hub+2 spokes, peerings, var refs), `network-invalid.yaml` (wrong kind + empty networks), `network-overlapping-subnets.yaml` (V9 overlap), `network-peered-overlap.yaml` (V11 mutual peering overlap), `network-var-refs.yaml` (value/var/secret mix with references).
- `tests/strata/models/test_models_network.py` — 22 tests in `TestNetworkModel`: valid haven/enterprise/var-refs loads, invalid kind, empty networks, CidrSourceModel union (value/var/secret/none/two/bad-format), unique network names (V3), unique subnet names (V4), subnet overlap (V9), subnet-outside-address-space (V10), peering target exists (V5), no self-peering (V6), unique peering names (V7), undeclared var/secret refs (V8), peered overlap (V11), kind frozen.
- `tests/strata/services/test_services_network.py` — 5 tests in `TestNetworkService`: `_get_model_class`, validate standard fixture, get_kind after validate, merge networks by name (last-wins), merge subnets by (network, subnet) tuple (last-wins).

**Patterns followed:**
- Same file layout as DNS tests; imports from `strata.models.network_model` and `strata.services.network_service`.
- `_net_data()` and `_simple_network()` helpers for inline model construction — keeps tests DRY.
- `_make_network_model()` helper in service tests mirrors `_make_dns_model()` pattern.
- Tests are anticipatory — `NetworkModel`, `NetworkService`, and `PlatformKind.NETWORK` are being implemented concurrently by Linus.
- Fixture `network-invalid.yaml` uses `kind: namespace` (wrong kind) + `networks: []` (empty), matching `dns-invalid.yaml` pattern.
- `network-overlapping-subnets.yaml`: `10.0.0.0/24` overlaps `10.0.0.128/25` within same network.
- `network-peered-overlap.yaml`: two networks with mutual peerings sharing `10.0.0.0/16` — triggers V11 hard error.
- `merge_networks()` method name assumed from design spec §8.2 — confirm with Linus.

**Key rules the tests encode:**
1. `CidrSourceModel`: exactly one of value/var/secret (V1); value must be valid CIDR (V2).
2. Network names unique within spec (V3); subnet names unique within network (V4).
3. Peering target must exist in spec (V5); no self-peering (V6); unique peering names (V7).
4. var/secret keys must be declared in `references` block (V8).
5. Subnet CIDRs must not overlap within same network (V9) — literals only.
6. Subnets must fit within address space (V10) — literals only.
7. Peered networks with overlapping address spaces = hard error (V11).
8. Non-peered overlap is a warning (not tested here — warning-level, not ValidationError).

### 2026-06-15 — Policy Engine tests (Phase 1)

**What was added:** 3 new test files — 27 tests total, all passing.

**Test files:**
- `tests/strata/validators/test_policy_model.py` — 7 tests in `TestPolicyModel`: valid minimal, valid with all fields, defaults (enforcement=deny, enabled=True), invalid PlatformName, phase is plain str (no enum), configuration dict, disabled.
- `tests/strata/validators/test_policy_engine.py` — 9 tests in `TestPolicyEngine`: empty engine, phase filtering, disabled policy skipped, has_denials false/true/warn/audit, all policies run, unknown type raises ValueError.
- `tests/strata/validators/test_customer_zone_policy.py` — 11 tests across 3 classes: no-context graceful skips, zone enforcement violations (location+region fields, read action ignored, multiple violations), enforcement level in result.

**Key discoveries about actual implementation (vs ADR spec):**
- `PolicyContext` has NO `manifest` field — ADR spec was aspirational; Linus dropped it. Fields are: `phase`, `work_path`, optional `deployment_service`, `configuration_service`, `platform_artifact`, `plan_data`, `build_path`.
- `CustomerZonePolicy` reads customer zones from `plan_data["variables"]["strata_customer"]["value"]["zones"]` — NOT from `customer.auto.tfvars.json` file on disk as the ADR described.
- `PolicyEngine.__init__` sets `self.logger` via `get_logger()` — `_TestableEngine` must set `self.logger = MagicMock()` to avoid AttributeError when `evaluate()` calls `self.logger.debug()`.
- Violation message format: `"Resource '{type}.{name}' is in region '{location}' which is not in any of the customer's allowed zones: {zones}"` — uses `change.get("type")` + `change.get("name")`, NOT `change["address"]`.
- `BasePolicy.name` property exists: returns `str(self.policy.name)`.

**Patterns established:**
- `_TestableEngine(PolicyEngine)` — bypasses `_create()` by accepting pre-built policy instances; sets `self.logger = MagicMock()`.
- `_AlwaysPassPolicy` / `_AlwaysFailPolicy` stubs defined inside `if not IMPL_MISSING:` guard.
- `_make_context(plan_data, zone_map)` — builds PolicyContext + MagicMock config service in one call; no file I/O, no `tmp_path` needed.
- `PLAN_DATA_*` constants include `variables.strata_customer.value.zones` to activate enforcement.
- `patch.object(PolicyEngine, "_create", side_effect=...)` for testing `disabled_policy_skipped` through the real `__init__`.

**Current status:** 27 tests passing, 0 skipped (implementation is live).

