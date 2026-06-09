# Linus — History

## Core Context

Python / CLI Dev for strata. Implements Click commands, services, controllers, models.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.
Key paths: `src/strata/cli.py`, `commands/cli_common.py`, `models/`, `services/`, `controllers/`.

## Learnings

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

### 2026-04-22 — CLI/models code review

**cli.py**

**commands/base_command.py**
- `_Initialize()` only records `_start_time`; `_project_id` and `_execution_id` are declared but never assigned.
- PascalCase on `ShowConsoleHeader` / `ShowConsoleFooter` / `_Initialize` / `_BeforeExecute` — inconsistent with Python convention; rest of codebase uses snake_case. Minor but worth standardising.
- `work_path` stored in `self._work_path` in the command — per decisions it should come from `ctx.obj`, not be a constructor arg.

**commands/cli_common.py**
- Decorator set (`click_work_path`, `click_output_format`, `click_output_verbose`, `click_output_quiet`) is clean and reusable.
- `validate_verbose_quiet_exclusive` and `validate_output_quiet_exclusive` rely on `ctx.params` order — Click processes params left-to-right so the first of a mutually-exclusive pair will see the second as not yet set. This is a known Click ordering hazard; only catches the case where the exclusive param was declared earlier.
- `click_work_path` uses `exists=False`, so it won't error on a non-existent path — this is intentional for `xyz project init` but risky for all other commands that require the workspace to exist. Consider a second decorator (`click_work_path_required`) with `exists=True`.
- `OUTPUT_FORMATS` list comprehension `[f for f in OUTPUT_FORMATS if f]` is redundant noise since the list has no falsy entries.

**models/**
- Pydantic v2 patterns are correct throughout: `model_validate`, `model_dump_json`, `field_validator` with `@classmethod`, `model_validator(mode="after")`, `Annotated` + `StringConstraints`. No v1 compat shims detected.
- `PlatformName` regex `^[a-z][a-z0-9_-]*$` is referenced everywhere — solid shared type.
- `ScriptPathModel.validate_script_path` and `ScriptsModel.validate_and_normalize_scripts` both call `Path.exists()` at parse time. This means loading a model from YAML will blow up if any script file doesn't exist at that moment — bad for cross-machine/CI use. Consider separating schema validation from filesystem validation.
- `project_model.py` uses plain `str` for `apiVersion`/`kind` instead of `PlatformVersion`/`PlatformKind` enums — no type safety at the model boundary. Other models use the enums.
- `ProjectMetaModel.name` uses plain `str` with manual non-empty check, not `PlatformName` — inconsistent with the rest of the codebase.

**utils/configuration_loader.py**
- Purely a file-I/O + deep-merge utility. Clean separation: no schema knowledge, no glob selection, no `@repo` resolution.
- `@repo/path` references are NOT handled here. They live in `utils/system.py:resolve_path()` which takes an optional `repo_map` dict. The `repo_map` is built by `ConfigurationService.get_repo_map()` and passed down through `workspace_service` and others at validation time.
- The pattern works but `repo_map` must be fully populated before any path resolution call — no lazy resolution, no partial maps.

**services/project_service.py**
- Singleton via `__new__` with `_instances` dict + lock — same pattern as other services.
- Implements `load_from_json` and `save_to_json` only. No `init_workspace`, no `create_project`, no `add_repository`, no `activate_profile` — nothing that would back `xyz project init` or `xyz project add`.
- `_validate_dynamic` is a no-op (returns True). No cross-service validation.
- Service is ready as a persistence layer (load/save JSON) but has zero business logic methods.

**Context wiring gap**
- `main()` in `cli.py` has no `@click.pass_context`, no `ctx.obj = {}`, no `--work-path` option, and no `.strata/cli.yaml` loading into `default_map`. This gap is total — zero of the three decisions are implemented in `main()`.
- To wire up per decisions: add `@click.pass_context`, accept `--work-path` with env var fallback `STRATA_WORK_PATH`, implement CWD-walk for `.strata/` sentinel, load `.strata/cli.yaml` into `ctx.default_map`, store resolved path in `ctx.obj['work_path']`.

### 2026-05-19 — VS Code tasks.json for config repo

**Key file paths:**
- Config repo tasks: `e:\SourcesXYZ\xyz-configuration\.vscode\tasks.json`
- Platform SDK template: `e:\SourcesXYZ\strata\src\strata\templates\vscode\tasks.template.json`

**Changes made to `xyz-configuration/.vscode/tasks.json`:**
- Removed `Check: lint + format + types` task (SDK-only, not for config operators)
- Replaced `uv run strata ${input:cliArgs}` with `strata ${input:cliArgs}` in `Run: haven`
- Added three operator-focused tasks: `xyz: validate`, `xyz: deploy run`, `xyz: build run` — all using a `configFile` promptString input
- Added `configFile` input (promptString, default `@haven/deploy/deploy-prd.yaml`)
- Updated `cliArgs` input description to reflect real examples

**tasks.template.json:** Already used `xyz ${input:cliArgs}` — no changes needed.

### 2026-05-19 — sln group implementation

**New sln group pattern:**
- `cli_sln.py` is the group wiring file: defines `sln_group` Click group and attaches `init`, `clean`, `status`, `export` subcommands.
- `cli.py` registers `sln_group` and removes the flat `init`, `clean`, `status` registrations.
- Subcommand modules for non-trivial commands live under `src/strata/commands/sln/` (e.g., `export_template_command.py`).
- Simple delegates (init, clean, status) are wired directly in `cli_sln.py` using existing command classes.

**Export command location:**
- `src/strata/commands/sln/export_template_command.py` — contains `SolutionExportCommand` (extends `BaseCommand`) and `export_command` Click entry point.

**`_substitute()` in `export_template_command.py`:**
- Handles template variable replacement in scaffold output files.
- Called during workspace-to-template export to substitute workspace-specific values with template placeholders.
