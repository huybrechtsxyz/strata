# Consolidate Templating on Jinja2

- Status: completed
- Date: 2026-06-24
- Completed: 2026-07-20
- Parent: [0016-console-interactive-repl.md](0016-console-interactive-repl.md) (question #11)

## Summary

Replace all `$VAR` / `${VAR}` / `str.replace()` templating in strata with Jinja2. One engine, one syntax (`{{ var }}`, `{% if %}`, `{% for %}`), everywhere.

## Implementation Snapshot

### Implemented — all surfaces

- `pyproject.toml` includes `jinja2>=3.1`.
- `utils/templater.py` — Jinja2-based `TemplateProcessor` with:
  - `process_single_template()` — `StrictUndefined` (missing vars raise)
  - `render()` — `DebugUndefined` (missing vars stay visible as `{{ var }}`)
  - `render_strict()` — `StrictUndefined` for surfaces where all vars must be present
- `commands/new/run_new_command.py` — template content and path segments via `TemplateProcessor.render()`
- `builders/base_builder.py` — `TemplateProcessor.render()`
- `controllers/solution_controller.py` — scaffold templates via `TemplateProcessor.render()`; `.j2` files copied verbatim
- `commands/sln/export_template_command.py` — exports placeholders as `{{ solution_name }}`
- `integrations/base_integration.py::_resolve_env_vars()` — migrated to `TemplateProcessor.render_strict(value, {"env": os.environ})`; users write `{{ env.VAULT_ADDR }}` instead of `${VAULT_ADDR}`
- `controllers/lifecycle_controller.py::_process_script_template()` — migrated from `re.sub()` to `TemplateProcessor.render(content, strata_context)` where `strata_context` contains only `STRATA_*` variables; OS env vars are scoped out of the template context (scripts still receive full OS env via `subprocess.run(env=...)`)
- All scaffold templates under `src/strata/templates/` — use `{{ var }}` placeholders
- All docstrings and model field descriptions referencing `$VAR` / `${VAR}` — updated to `{{ var }}`

### Intentionally out of scope (not strata templating)

These remain as-is — they are external syntax strata writes verbatim or reads from external systems:

- VS Code settings: `${workspaceFolder}`, `${input:...}` — VS Code substitution, not strata
- GitHub Actions: `${{ secrets.TOKEN }}` — GitHub Actions expression syntax
- Docker Compose / Helm values: `${KEY}` — Compose/Helm native substitution in output files
- Shell runtime variables in lifecycle scripts: `$PATH`, `$HOME` — resolved by the shell at execution time, intentionally not substituted by strata

## Context

Strata currently uses ad-hoc string substitution for templates:

| Location                                            | Mechanism                                            | Syntax             | Limitation                                     |
| --------------------------------------------------- | ---------------------------------------------------- | ------------------ | ---------------------------------------------- |
| `utils/templater.py` — `TemplateProcessor`          | `re.sub()` against `os.environ`                      | `$VAR`, `${VAR}`   | No conditionals, no loops, no filters          |
| `utils/templater.py` — `TemplateProcessor.render()` | `re.sub()` against context dict                      | `$VAR`, `${VAR}`   | Same — pure substitution only                  |
| `controllers/solution_controller.py`                | `str.replace()`                                      | `${SOLUTION_NAME}` | Hardcoded single variable                      |
| `commands/new/run_new_command.py`                   | `TemplateProcessor.render()`                         | `$VAR`, `${VAR}`   | Template files can't have conditional sections |
| `builders/base_builder.py`                          | `TemplateProcessor.render()`                         | `$VAR`, `${VAR}`   | Same                                           |
| `commands/sln/export_template_command.py`           | `str.replace()` (reverse: name → `${solution_name}`) | `${solution_name}` | Export direction — writes `${}` tokens         |

This works for simple substitution but fails when templates need:
- **Conditionals** — optional YAML sections based on feature flags or wizard answers
- **Loops** — iterating over lists (repos, modules, stages)
- **Filters** — name normalization, slugification
- **Block inheritance** — base templates with overridable sections

The upcoming console wizards (ADR 0016, Phase 2) need all of these. Rather than adding Jinja2 for wizards while keeping `$VAR` elsewhere, consolidate on one engine.

## Decision

**Use Jinja2 exclusively.** No `$VAR`, `${VAR}`, or `{var}` syntax anywhere in the codebase.

### Template Syntax

All templates use Jinja2 syntax:

```yaml
# Before (current)
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: $name

# After (Jinja2)
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: {{ name }}
```

Jinja2 enables patterns that were previously impossible:

```text
spec:
  repositories:
{% for repo in repositories %}
    - name: {{ repo.name }}
      url: {{ repo.url }}
{% endfor %}
{% if description %}
  meta:
    annotations:
      description: {{ description }}
{% endif %}
```

### Migration Scope

| Component                                      | Change                                                                              | Effort  |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- | ------- |
| `utils/templater.py`                           | Rewrite `TemplateProcessor` to use `jinja2.Environment`                             | Medium  |
| `commands/new/run_new_command.py`              | Switch `TemplateProcessor.render()` calls to Jinja2                                 | Low     |
| `builders/base_builder.py`                     | Switch `TemplateProcessor.render()` calls to Jinja2                                 | Low     |
| `controllers/solution_controller.py`           | Replace `str.replace("${SOLUTION_NAME}", ...)` with Jinja2 render                   | Low     |
| `commands/sln/export_template_command.py`      | Reverse direction: write `{{ solution_name }}` tokens instead of `${solution_name}` | Low     |
| `commands/init/init_solution_command.py`       | Update docstrings referencing `${solution_name}`                                    | Trivial |
| Template data files (`*.template.*`)           | Convert `$VAR` → `{{ var }}`                                                        | Low     |
| Scaffold template files (used by `strata new`) | Convert `$VAR` → `{{ var }}`                                                        | Low     |

### New `TemplateProcessor` API

```python
from jinja2 import Environment, BaseLoader, StrictUndefined

class TemplateProcessor:
    """Jinja2-based template processing for files with placeholders."""

    def __init__(self, template_dir: Path, cleanup_templates: bool = True) -> None:
        self.template_dir = template_dir
        self.cleanup_templates = cleanup_templates
        self._env = Environment(
            loader=BaseLoader(),
            undefined=StrictUndefined,      # fail on missing vars, don't silently blank
            keep_trailing_newline=True,      # preserve YAML formatting
            autoescape=False,                # not HTML — plain text/YAML
        )

    def process_all_templates(self) -> bool:
        """Process all *.template.* files using environment variables as context."""

    def process_single_template(self, template_path: Path) -> bool:
        """Process one template file."""

    @staticmethod
    def render(content: str, context: dict) -> str:
        """Render a template string with the given context dict."""
```

**Key differences from current implementation:**
- `StrictUndefined` instead of silently keeping placeholders — fail fast on missing variables
- Same public API (`process_all_templates`, `process_single_template`, `render`) — callers barely change
- Environment variable injection: `process_single_template` builds context from `os.environ` before rendering

### Environment Variable Templates

The current `*.template.*` files substitute environment variables (`$ARM_TENANT_ID`, etc.). Under Jinja2:

```
# Before: main.template.tf
organization = "$organization"
tenant_id    = "$ARM_TENANT_ID"

# After: main.template.tf
organization = "{{ organization }}"
tenant_id    = "{{ ARM_TENANT_ID }}"
```

`process_single_template` passes `os.environ` as the Jinja2 context, so `{{ ARM_TENANT_ID }}` resolves to the env var value. With `StrictUndefined`, missing env vars raise an error instead of silently producing empty strings.

### Export Template (Reverse Direction)

`export_template_command.py` does the reverse — it replaces a solution name with a placeholder token. Currently writes `${solution_name}`. After migration, writes `{{ solution_name }}`:

```python
# Before
return text.replace(solution_name, "${solution_name}"), count

# After
return text.replace(solution_name, "{{ solution_name }}"), count
```

### Jinja2 Environment Configuration

Use a shared `Environment` factory to ensure consistency:

```python
def create_template_environment() -> Environment:
    """Standard Jinja2 environment for all strata template rendering."""
    return Environment(
        loader=BaseLoader(),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
```

This can live in `utils/templater.py` and be imported by any module that needs to render templates.

## Dependencies

| Package      | Version      | Purpose                           |
| ------------ | ------------ | --------------------------------- |
| `Jinja2`     | ≥3.1         | Template engine                   |
| `MarkupSafe` | (transitive) | Pulled in by Jinja2 automatically |

## Testing Strategy

- **Unit tests for `TemplateProcessor`**: render with context, render with env vars, `StrictUndefined` raises on missing vars
- **Migration tests**: existing template files render identically before and after migration (compare output)
- **`strata new` tests**: scaffold commands produce the same output files
- **Builder tests**: `base_builder.py` template rendering unchanged
- **Export/import round-trip**: `sln export` → `sln init` produces valid workspace

## Implementation Plan

### Step 1: Add Jinja2 dependency

Add `Jinja2>=3.1` to `pyproject.toml` dependencies.

### Step 2: Rewrite `TemplateProcessor`

Replace the regex-based implementation with Jinja2. Keep the same public API so callers don't need structural changes.

### Step 3: Convert template data files

Update all `*.template.*` files and scaffold templates from `$VAR` / `${VAR}` to `{{ var }}`.

### Step 4: Update `solution_controller.py`

Replace `str.replace("${SOLUTION_NAME}", solution_name)` with Jinja2 render call.

### Step 5: Update `export_template_command.py`

Write `{{ solution_name }}` tokens instead of `${solution_name}`.

### Step 6: Update docstrings and documentation

Change all references to `$VAR` / `${VAR}` syntax to `{{ var }}`.

### Step 7: Run full test suite

Verify no regressions. Template output should be byte-identical except for the syntax change.

## No Backward Compatibility

This is a clean break. No support for `$VAR` alongside `{{ var }}`. All templates migrate in one pass. The old regex substitution code is deleted, not deprecated.

## Relationship to Other ADRs

| ADR                                                                 | Relationship                                                                                                                        |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [0016 — Console Interactive REPL](0016-console-interactive-repl.md) | Phase 2 wizards use Jinja2 for YAML generation. This ADR ensures the rest of the codebase is already on Jinja2 before wizards land. |

## Design Remarks

Observations from rubber-ducking the migration against the actual codebase. These should be addressed during implementation.

### 1. Two Distinct Usage Patterns

The current `TemplateProcessor` serves two different purposes:

| Pattern                                       | Who uses it                                 | Context source | Example                                                 |
| --------------------------------------------- | ------------------------------------------- | -------------- | ------------------------------------------------------- |
| **Constructor + `process_single_template()`** | `solution_controller.py` (VS Code scaffold) | `os.environ`   | `*.template.*` files → strip `.template.` from filename |
| **Static `render(content, context)`**         | `base_builder.py`, `run_new_command.py`     | explicit dict  | In-memory string substitution, no file rename           |

Both patterns must survive the migration. The ADR's proposed API already covers both — `process_single_template` for file-based env var templates, `render` for dict-based inline rendering.

### 2. Dual Undefined Modes

Two Jinja2 environments are needed, not one:

| Environment    | `undefined`       | Used by                     | Behavior on missing var                                                                         |
| -------------- | ----------------- | --------------------------- | ----------------------------------------------------------------------------------------------- |
| `_STRICT_ENV`  | `StrictUndefined` | `process_single_template()` | Raises `UndefinedError` — correct for deployment templates where missing env vars = broken      |
| `_LENIENT_ENV` | `DebugUndefined`  | `render()`                  | Renders as `{{ var }}` — leaves placeholder visible in output, matching old keep-as-is behavior |

`StrictUndefined` everywhere would break `strata new` which legitimately supports partial context (e.g., `name` provided but `owner`/`version` unknown — the user edits those later). `DebugUndefined` preserves the old UX while using Jinja2 syntax.

### 3. VS Code `${input:cliArgs}` Collision Disappears

The scaffold `tasks.json` template contains both strata placeholders (`${SOLUTION_NAME}`) and VS Code variables (`${input:cliArgs}`). The current regex accidentally works — `${input:cliArgs}` partially matches `input`, which isn't in context, so it's left alone. But this is fragile.

With Jinja2, the collision disappears entirely. `{{ SOLUTION_NAME }}` is Jinja2 syntax; `${input:cliArgs}` is not. Jinja2 ignores VS Code's `${}` syntax completely. Clean separation — no accidental partial matches.

### 4. Private Method Called Externally

`solution_controller.py` calls `processor._substitute_environment_variables(content)` directly — reaching into a private method instead of using the public API. The migration should fix this by having `process_single_template` accept a content string (or by adding a public `render_with_env()` method).

### 5. Path Segment Rendering

`run_new_command.py` renders placeholders inside **path segments**, not just file content:

```python
rendered_parts = [TemplateProcessor.render(part, context) for part in rel.parts]
```

This means directory names like `${solution_name}` become `my-platform`. Under Jinja2, bundle directory names would need `{{ solution_name }}` syntax — which is unusual for filesystem paths but works. Just need to make sure the `render()` method handles short strings (single path segments) efficiently.

### 6. Variable Naming Inconsistency

Three naming conventions exist in templates today:

| Convention  | Where                    | Example                             |
| ----------- | ------------------------ | ----------------------------------- |
| `UPPERCASE` | Scaffold templates       | `${SOLUTION_NAME}`                  |
| `lowercase` | Export/example templates | `${solution_name}`                  |
| `lowercase` | `strata new` templates   | `${name}`, `${owner}`, `${version}` |

The migration should **normalize to lowercase** — Jinja2 convention is `{{ solution_name }}`, not `{{ SOLUTION_NAME }}`. The solution controller scaffold templates should switch from `SOLUTION_NAME` to `solution_name`. One convention, everywhere.

### 7. `render()` Stays Static — But Needs an Environment

The current `render()` is a `@staticmethod` with no setup cost (just regex). With Jinja2, each call would need an `Environment` or `Template` object. Options:

1. **Module-level singleton** — one `Environment` instance created at import time, reused by `render()`
2. **Create per call** — `Environment.from_string(content).render(context)` each time
3. **Drop `@staticmethod`** — make it an instance method on a lightweight object

Option 1 is simplest. The `create_template_environment()` factory in the ADR already supports this — just store the result at module level and use it in `render()`.
Do option 1.

### 8. Implementation Order Refinement

The ADR lists 7 steps. Suggested tighter order based on dependency analysis:

1. **Add Jinja2 dep** — `pyproject.toml`
2. **Rewrite `TemplateProcessor`** — new Jinja2-based implementation with same public API
3. **Update tests first** — rewrite `test_utils_templater.py` to expect Jinja2 behavior (`UndefinedError` on missing vars, `{{ var }}` syntax). Run them — they should pass against the new implementation.
4. **Convert template files** — all `*.template.*` and scaffold files: `$VAR`/`${VAR}` → `{{ var }}`
5. **Fix `solution_controller.py`** — replace `str.replace("${SOLUTION_NAME}", ...)` and the private method call with `TemplateProcessor.render()`
6. **Fix `export_template_command.py`** — write `{{ solution_name }}` tokens
7. **Update remaining tests + docs** — solution controller tests, new command tests, docstrings

Step 3 before step 4 ensures the engine works before we change all the template files.

### 9. Scope Check: What's NOT Changing

- `cleanup_template_file()` — still needed, still deletes the source `*.template.*` file
- `process_all_templates()` glob pattern — `*.template.*` convention stays
- File rename logic (strip `.template.` from filename) — stays
- `base_builder.py` and `run_new_command.py` call sites — same API, just different syntax in template content
- `export_template_command.py` structure — still does string replacement, just different token
