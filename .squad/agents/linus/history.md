# Linus — History

## Core Context

Python / CLI Dev for strata. Implements Click commands, services, controllers, models.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.
Key paths: `src/strata/cli.py`, `commands/cli_common.py`, `models/`, `services/`, `controllers/`.

**Past implementations (condensed):**
- 2026-04-22: CLI/models code review — `PlatformFileNotFoundError` in `service_exception`; `PlatformName` regex `^[a-z][a-z0-9_-]*$` used everywhere; `@repo/path` refs handled by `utils/system.py:resolve_path()`, not config_loader; `cli.py` context wiring (work_path, default_map) must be done at `main()` level; `ScriptPathModel` calls `Path.exists()` at parse time (fragile for CI); `ProjectMetaModel.name` is plain `str` (not `PlatformName`)
- 2026-05-19: VS Code tasks.json for config repo — operator tasks use `strata ${input:cliArgs}` (not `uv run`); `xyz: validate`, `xyz: deploy run`, `xyz: build run` with `configFile` promptString input
- 2026-05-19: sln group — `cli_sln.py` groups `init`, `clean`, `status`, `export`; subcommand modules under `src/strata/commands/sln/`; `_substitute()` in `export_template_command.py` handles template variable replacement

## Learnings

### 2026-07-28 — `strata new --list` missing solution-level templates

Fixed: `--list` only walked the filesystem for templates, never `solution.json`'s `spec.templates[]`. Hoisted a `_load_solution_spec()` best-effort load ahead of the `--list` early-return; `_collect_available_templates()` / `_collect_templates_with_descriptions()` now take an optional `solution_templates` param merged in as a third source (`type: "bundle (solution)"`, last-write-wins on name collision). 37 tests passing. Flagged (not fixed): the "Scaffold bundles (strata sln init --template <name>):" header text is now misleading for solution templates, which are invoked via `strata new <name> <NAME>` — out of scope for this surgical fix.

### 2026-07-28 — Cross-deployment dependency gating: `DeploymentManifestModel.spec.status` is the authoritative signal; proposed `spec.requires`

- Found `DeploymentManifestModel.spec.status` (`success|partial|failed`, ADR-0021) is the authoritative, already-implemented deployment-outcome signal. Proposed `spec.requires: Optional[List[str]]` on `DeploymentModel`, checked pre-flight in `deploy run`/`validate --deep` — a simple hard precondition, no human-approval machinery. Argued successfully against routing through ADR-0057 gates (environment-scoped, human-decision-oriented — wrong fit).

### 2026-07-28 — Secret post_generate/derive feature: implementation footprint estimates

- Estimated footprint for 3 design options: Option D (docs recipe) = zero core code; Option C (`derive:` spec on `SecretStoreModel`) = ~1 new field, 1 resolution branch, cycle detection, ~9-11 tests; Option A (`post_generate` hook) = ~15-18 tests across 3 call sites, discouraged due to duplicated logic and new subprocess attack surface. No code changes made — estimation only.

### 2026-06-15 — Policy Engine Phase 1

**Files created:**
- `src/strata/models/policy_model.py` — `PolicyModel` with `name`, `type`, `phase`, `enforcement`, `description`, `configuration`, `enabled`
- `src/strata/validators/policies/__init__.py` — package exporting 5 public symbols
- `src/strata/validators/policies/base_policy.py` — `PolicyContext` dataclass, `PolicyResult` dataclass, `BasePolicy` ABC
- `src/strata/validators/policies/policy_engine.py` — `PolicyEngine` with `evaluate()`, `has_denials()`, `_create()` type dispatch
- `src/strata/validators/policies/customer_zone_policy.py` — built-in zone enforcement policy

**Files modified:**
- `src/strata/models/configuration_model.py` — imported `PolicyModel`, added `policies: Optional[List[PolicyModel]]` field to `ConfigurationSpecModel` with `default_factory=list`
- `src/strata/commands/deploy/run_deploy_command.py` — added `_evaluate_plan_policies()` method, wired it after `deploy_plan_after` gate inside the `STEP_PLAN && STEP_APPLY in steps_to_run` block

**Key patterns:**
- Policies sit in `validators/` layer, NOT in `integrations/` — they are stateless evaluators, no singleton lifecycle
- `PolicyContext` uses `Optional[Any]` for service fields to avoid circular imports
- `CustomerZonePolicy` degrades gracefully at 5 levels: no plan data → no zone config → no customer context → no customer zones → no resource_changes → all return `passed=True`
- Region normalization: `.lower().replace(" ", "")` to handle `"West Europe"` vs `"westeurope"`
- Plan data comes from `deployer.show_plan()` (not `load_plan_json` — that method doesn't exist); `show_plan()` returns `(bool, dict, list)`
- Policy evaluation is guarded: `if hasattr(deployer, "show_plan")` — works only for TerraformDeployer
- `PolicyEngine._create()` uses a deferred import of `CustomerZonePolicy` to avoid module-level circular dependency risk
- `default_factory=list` with `Optional[List[PolicyModel]]` is valid Pydantic v2 — field is never `None`, just empty list by default

### 2026-06-10 — `strata guide` command implementation

**Files created:**
- `src/strata/commands/guide/__init__.py` — empty package marker
- `src/strata/data/guide-hints.yaml` — built-in defaults for all 7 phases + 11 kinds
- `src/strata/commands/cli_guide.py` — Click wiring with `@click_file` as first decorator
- `src/strata/commands/guide/show_guide_command.py` — GuideCommand class (~300 lines)

**Files modified:**
- `src/strata/cli.py` — added `guide_command` import, `main.add_command(guide_command, name="guide")`, added `"guide"` to `"Inspection & Validation"` section in `_HELP_SECTIONS`

**Key patterns:**
- `PlatformFileNotFoundError` lives in `strata.exceptions.service_exception`, not `base_exception`
- `INIT_REQUIRED = False` means `_initialize()` loads solution.json if present but never errors if absent — `_load_solution()` just reads `self._solution_controller.solution` after init
- `execute()` always returns `True` and calls `_finalize(success=True)` — guide never raises exit code 1 or 3
- Console rendering done inside `_run_workspace_mode()` / `_run_file_mode()` directly; JSON rendering sets `self._output_data` which `_finalize()` emits
- File mode ok items for phase 2/3/4 use `_format_file_item_ok()` helper to render "Kind: {value}", "apiVersion: {value}", "Name: {value}" format
- Workspace mode renders all detail in `({detail})` parens; file mode ok uses "Key: value" format, warn uses ": " separator, pending uses " — " separator
- Phase 3 hint is always built dynamically from missing repos (null sentinel in YAML)
- `_load_hints()` merges `.strata/guide.yaml` shallowly per-key: scalars replace scalars, phases/kinds sub-keys replace individually

### 2026-06-09 — DNS record value/var/secret union + references block

**Files modified:**
- `src/strata/models/dns_model.py` — added `DnsReferencesModel`, updated `DnsRecordModel` to value/var/secret union with `validate_exactly_one_source`, added `references` to `DnsSpecModel` with `validate_references_declared` cross-check validator
- `src/strata/models/platform_artifact_model.py` — imported `DnsReferencesModel`, added `references` field to `PlatformDnsModel`, updated `from_dns_model()` to pass `references=model.spec.references`
- `src/strata/builders/terraform_builder.py` — updated `_build_dns_vars()` to resolve value/var/secret per record, build `secret_records_dict`, return `{"dns_zones": ..., "dns_secret_records": ...}`; updated `_save_terraform_vars()` to write `dns_secret_records.auto.tfvars.json`; added file to dry-run planned list and `after_build` base_files

**Key patterns:**
- `var:` keys resolved via `self.variable_refs.get(key, {}).get("value")` — only resolves if env declares a literal-store variable; emits `null` and warning otherwise (no resolved_values param on TerraformBuilder)
- `secret:` keys always emit `null` in `dns.auto.tfvars.json` and populate `dns_secret_records.auto.tfvars.json` with record coordinates + secret_key
- `validate_references_declared` runs as second `mode="after"` validator on DnsSpecModel; walks all zones/records and checks every var/secret key is declared in `spec.references`
- `dns_secret_records.auto.tfvars.json` is always written (even if empty `{}`) — listed in both after_build base_files and dry-run planned list
- All 22 existing DNS model+service tests pass unchanged — test data was already pre-authored with new syntax

### 2026-06-09 — DNS kind implementation

**Files created:**
- `src/strata/models/dns_model.py` — DnsRecordType enum, DnsRecordModel, DnsZoneModel, DnsSpecModel, DnsMetaModel, DnsModel
- `src/strata/services/dns_service.py` — DnsService with merge_dns() and merge_dnsfiles() following exact FirewallService skeleton
- `src/strata/templates/solution/dot.strata/templates/dns.yaml` — boilerplate template with A, AAAA, CNAME, MX, TXT (SPF+DMARC), CAA, NS examples

**Files modified:**
- `src/strata/models/common_models.py` — added `DNS = "dns"` to PlatformKind enum (alphabetical order between DEPLOYMENT and ENVIRONMENT)
- `src/strata/models/platform_artifact_model.py` — imported DnsModel+DnsZoneModel, added PlatformDnsModel (from_dns_model classmethod), added `dns_zones` field to PlatformSpecModel
- `src/strata/models/workspace_model.py` — added WorkspaceDnsModel class, `dns_zones` field to WorkspaceSpecModel, `validate_unique_dns_zones()` model_validator
- `src/strata/validators/platform_validator.py` — added DnsService import + `PlatformKind.DNS: DnsService` to _KIND_TO_SERVICE dict
- `src/strata/commands/cli_schema.py` — added DnsModel import + `PlatformKind.DNS: DnsModel` to _KIND_TO_MODEL dict
- `src/strata/services/unknown_service.py` — added DNS elif branch in get_service_by_kind()
- `src/strata/builders/terraform_builder.py` — added _build_dns_vars(), wired into _build_terraform_vars(), added "dns.auto.tfvars.json" to dry-run planned list, after_build base_files list, and _save_terraform_vars write calls

**Key patterns:**
- DnsMetaModel uses Optional labels (unlike FirewallMetaModel which requires labels) — matches design spec
- priority constraint validated via model_validator (mode="after") on DnsRecordModel — checks type is MX or SRV
- merge strategy: zones by name (last wins for ttl/provider), records by (name, type) tuple (last wins)
- PlatformDnsModel reuses DnsZoneModel directly (no flattening needed for zone/record structures)
- dns_zones field name in PlatformSpecModel and WorkspaceSpecModel is plural to match pattern (firewalls → dns_zones)
- _build_dns_vars() outputs `{"dns_zones": {...}}` top-level key; records include all fields (ttl, priority) even when None for Terraform variable completeness

### 2026-06-01 — HelmBuilder implementation

**Files created/modified:**
- `src/strata/builders/helm_builder.py` — new builder following exact `ComposeBuilder` skeleton
- `src/strata/commands/builders/run_build_command.py` — added `HelmBuilder` import, `_execute_helm_build()`, and call in `execute()` after compose

**Key patterns:**
- Output is per-module (not per-namespace) because Helm deploys are release-scoped; `meta.yaml` carries `releaseName`/`namespace` as per-module metadata
- Service key prefix logic: `{module}-{service}`, prefix omitted when names match (same as ComposeBuilder)
- `values.yaml` structure: `env` dict (literals direct, `var`/`secret`/`feature` as `${KEY}` tokens), `persistence` dict (only for mounts with `storage_class`), `configuration` merged verbatim
- `after_build` always returns True — absence of helm modules is not an error
- `before_build` validates `deployment_service.is_validated()` and workspace service presence
- Dry-run logs per-file messages if verbose; never skips error accumulation


