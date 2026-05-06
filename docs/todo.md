# XYZ Platform — Test & Documentation TODO

> Tracks what is still needed. Tests and docs are grouped together where they
> cover the same unit of work. Items are ordered roughly by dependency (models
> first, then services, then controllers, then commands).

---

## My TODO

- data: ✅
- templates: ✅
- logger: ✅
- utils: ✅
- exceptions: ✅
- models: ✅


- cli
- builders
- commands
- controllers
- deployers
- integrations
- services
- validators

## Models

### `common_models.py`
- **Tests:** `PlatformName` validation, `PlatformKind` enum, `PlatformVersion` enum, `ProvisionerType` enum — _covered indirectly via all model round-trip tests_

### `deployment_model.py`
- **Tests:** valid deployment YAML round-trip — _covered via `tests/data/deployments/`_

### `workspace_model.py`
- ✅ **Tests:** `workspace-standard.yaml` + `xyz-ws-platform.yaml` (real config)

### `configuration_model.py`
- ✅ **Tests:** `configuration-standard.yaml` + `xyz-config.yaml` (real config) + lifecycle phase unit tests

### `environment_model.py`
- ✅ **Tests:** `environment-standard.yaml`, `environment-insecure-secrets.yaml`, `environment-overrides.yaml`, `xyz-env-prd.yaml` (real config) + store/override unit tests

### `store_models.py`
- **Tests:** covered indirectly by `TestEnvironmentStoreValidation` in `test_models_environment.py`

### `platform_artifact_model.py`
- **Tests:** valid YAML round-trip; `approvals` field preserved — _still needed_

### `platform_template_model.py`
- ✅ **Tests:** `platform-template-standard.yaml` + `platform-template-invalid.yaml`

### `solution_model.py`, `repository_model.py`, `integration_model.py`
- ✅ **Tests:** `solution-standard.yaml` + `solution-invalid.yaml`

### `firewall_model.py`
- ✅ **Tests:** `firewall-standard.yaml` + `xyz-fw-base.yaml` (real config)

### `module_model.py`
- ✅ **Tests:** `module-standard.yaml` + `xyz-md-traefik.yaml` (real config)

### `namespace_model.py`
- ✅ **Tests:** `namespace-standard.yaml` + `xyz-ns-base.yaml` (real config)

### `provider_model.py`
- ✅ **Tests:** `provider-standard.yaml`

### `resource_model.py`
- ✅ **Tests:** `resource-standard.yaml` + `xyz-rx-vm-infra.yaml`, `xyz-rx-vm-manager.yaml`, `xyz-rx-vm-worker.yaml` (real config)

---

## Utils

### `system.py` — `resolve_path(base_path, ref, repo_map)`
- ✅ **Tests:** covered in `test_utils_system_paths.py` (`@repo` resolution, unknown repo raises, plain path, sub_paths, absolute sub_path rejected, `normalize_path`, `generate_uuid`, `resolve_work_path`)
- ✅ **Docs:** docstring on `resolve_path` updated with full `@repo-name/...` contract

### `configuration_loader.py`
- ✅ **Tests:** covered in `test_utils_configurationloader.py`

### `service_cache.py`
- ✅ **Tests:** covered in `test_utils_servicecache.py`

### `templater.py`
- ✅ **Tests:** covered in `test_utils_templater.py`

---

## Services

All services need: load-valid-YAML test, load-invalid-YAML test (exit code 3), `_validate_dynamic` called with correct args.

### `deployment_service.py`
- **Tests:** loads valid deployment YAML; rejects unknown `kind`; `get_workspace_service()` resolves correctly; `get_environment_service()` resolves; errors accumulated (not raised) during dynamic validation
- **Docs:** docstring on `_validate_dynamic` listing what cross-references are checked

### `workspace_service.py`
- **Tests:** loads valid workspace YAML; provisioner list; topology list; `get_repo_map()` returns correct dict

### `configuration_service.py`
- **Tests:** loads valid configuration YAML; `get_repo_map()` used by other services; layering values

### `environment_service.py`
- **Tests:** loads valid environment YAML; variable / secret / feature access methods

### `platform_artifact_service.py`
- **Tests:** reads and writes `platform.json`; missing file handled gracefully

### Remaining services (`firewall_service`, `module_service`, `namespace_service`, `provider_service`, `resource_service`, `integration_service`, `solution_service`, `unknown_service`, `platform_template_service`)
- **Tests:** load valid YAML, load invalid YAML (schema error produces validation message, not crash)

---

## Controllers

### `value_controller.py` — `resolve_values()`
- **Tests:** variable resolved from env; secret resolved and masked; feature flag resolved; unresolvable entry recorded in errors (not raised); returns `(bool, ResolvedValues, List[str])` shape
- **Docs:** document `ResolvedValues` dataclass fields

### `configuration_controller.py`
- **Tests:** validates config files against schema; accumulates errors; does not raise

### `repository_controller.py`
- **Tests:** `repo add` registers entry; `repo sync` calls git integration; missing clone path handled

### `solution_controller.py`
- **Tests:** init creates `project.json`; `--from-template` populates repos + profiles + refs; duplicate name rejected

### `lifecycle_controller.py`, `env_controller.py`, `logging_controller.py`, `integration_controller.py`
- **Tests:** controller runs without crash on valid input; errors accumulate correctly

---

## Integrations

All integrations need: `is_available()` returns False when binary missing; `_run_integration()` not called when unavailable; subprocess call receives correct args (mock subprocess).

### `terraform.py`
- **Tests:** `validate_workspace()` checks backend config exists; `validate_environment()` checks tfvars file; each step (`setup`, `check`, `plan`, `apply`, `destroy`, `plan_destroy`, `show_plan`, `output`) passes correct args to subprocess; `get_supported_steps()` returns expected set
- **Docs:** list supported steps and their terraform command mappings

### `git.py`
- **Tests:** `clone`, `pull`, `status` pass correct args; dirty-tree detection

### `docker.py`, `azure_appconfig.py`, `azure_keyvault.py`, `hashicorp_vault.py`, `hashicorp_consul.py`, `bitwarden.py`
- **Tests:** `is_available()` false when binary/SDK missing; correct subprocess args for primary operation

### `store_integration.py`, `registry.py`, `factory.py`
- **Tests:** `IntegrationFactory.create()` returns correct subclass; singleton behavior; unknown type raises

---

## Builders

### `platform_builder.py`
- **Tests:** `before_build / build / after_build` write expected files to output path; dry_run=True writes nothing; missing config raises

### `terraform_builder.py`
- **Tests:** generates `.tfvars.json` with correct content; `before_build / build / after_build` sequence; temp-dir write does not touch real build path

---

## Deployers

### `terraform_deployer.py`
- **Tests:** step sequence dry-run (setup→check→plan); step sequence normal (setup→check→plan→apply); `--force` passes `-auto-approve`; `validate_workspace` and `validate_environment` return messages on failure

---

## Commands (CLI)

All command tests use `click.testing.CliRunner`. All tests assert `result.exit_code`.

### `config` (`cli_config.py`)
- ✅ `config set` + `config list` tested
- **Tests missing:** `config unset`; invalid key returns exit code 2

### `init` (`cli_init.py` + `init/`)
- **Tests:** `xyz init --name NAME` creates `.platform/project.json`; `--from-template FILE` pre-populates repos + profiles + refs; missing template file returns exit code 1

### `repo` (`cli_repo.py` + `repo/`)
- **Tests:** `repo add`; `repo list`; `repo remove`; `repo sync` (mock git); `repo status` (mock git)

### `profile` (`cli_profile.py` + `profile/`)
- **Tests:** `profile add`; `profile activate`; `profile list`; `profile show`; `profile remove`; duplicate name rejected

### `ref` (`cli_ref.py` + `ref/`)
- **Tests:** `ref configfile add/list/show/remove`; `ref envfile add`; `ref secretfile add`; unknown ref type returns exit code 2

### `validate` (`cli_validate.py` + `validate/`)
- **Tests:** valid file returns exit code 0; invalid YAML returns exit code 3; `--deep` resolves cross-refs (mock services); missing file returns exit code 1

### `build` (`cli_builders.py` + `builders/`)
- **Tests:** `build run` calls builder sequence; `build run --dry-run` writes nothing; `build plan` uses temp dir (mock builders + terraform); `build plan --artifacts-only` skips terraform; `build clean` removes artifacts

### `deploy run` (`run_deploy_command.py`)
- **Tests:** dry-run executes setup→check→plan only; full run executes setup→check→plan→apply; `--stage` filters to one stage; unknown stage returns exit code 3; `_check_approvals` logs approvers when `spec.approvals` present; empty `approvers` dict skips logging

### `deploy destroy` (`destroy_deploy_command.py`)
- **Tests:** `--dry-run` runs plan-destroy only; `--force` required for real destroy; missing `--force` and no `--dry-run` returns exit code 1

### `deploy status` (`status_deploy_command.py`)
- **Tests:** default queries terraform output (mock); `--plan` reads saved tfplan file; `--history` delegates to history command

### `deploy health` (`health_deploy_command.py`)
- **Tests:** http check passes on 200; http check fails on non-200; tcp check passes on open port; tcp check fails on closed port; stage without health_checks skipped; exit code 3 on any failure

### `deploy history` (`history_deploy_command.py`)
- **Tests:** reads JSONL logs; `--lines` limits output; `--operation` filters; empty log returns exit code 0

### `values list` + `values get` (`cli_values.py`)
- **Tests:** `values list` shows all types; secrets masked (first 3 chars + `*****`); `--type secrets/variables/features` filters; `--unresolved` shows only failures; `--show-store` adds store column; exit code 3 when any entry unresolved; `values get` reveals full secret value; unknown key reported

### `log`, `clean`, `status`, `version`, `help`
- **Tests:** each command returns exit code 0 on valid input; `log list --last` reads most recent log file; `clean --dry-run` removes nothing

---

## Validators

### `platform_validator.py`
- **Tests:** valid `platform.json` passes; missing required field returns validation error message; unknown `kind` handled by `unknown_service`

---

## Documentation

### Missing reference docs
- `docs/model-reference.md` — YAML field reference for each `kind` (deployment, workspace, configuration, environment, workspace-template). Concise field table per model; note required vs optional.
- `docs/store-reference.md` — How variables / secrets / feature flags are declared and where they resolve from (env var, vault, consul, bitwarden, appconfig, keyvault). Include store type enum values.
- `docs/integration-reference.md` — Each integration: binary/SDK required, `is_available()` check, supported operations, config keys.

### Existing docs to update
- `docs/devops-workflow.md` §4.4 — `ref configfile show` can preview a single file; note that `build plan --artifacts-only` is the closest thing to a merged-config preview.
- `docs/devops-workflow.md` — Add approval YAML example to the config sample in the workspace template section (§1.3) so the template format stays in sync with the new schema.
- `README.md` — Quick-start section is likely stale; align with current command names and `uv run xyz-platform` invocation.
