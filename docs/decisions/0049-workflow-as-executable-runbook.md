# Workflow file as executable project runbook

- Status: proposed
- Date: 2026-07-21

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

`.strata/workflow.yaml` exists today as an onboarding checklist: a sequence of steps,
each backed by one of eight hardcoded check functions, that drives `strata guide` and the
`status` / `next` / `do` commands in `strata console`. It is readable as documentation
and (via `do`) partially executable.

Two limitations prevent it from becoming a genuine project runbook:

1. **Checks are hardcoded.** Only the eight built-in check functions
   (`solution_exists`, `repos_registered`, …, `sbom_exists`) are available. A team
   that wants to express "Terraform docs are up to date" or "compliance scan passed" as
   a workflow step has no way to define that predicate.

2. **The `command` field is not interpolated at execution time.** Tokens like `{name}`
   and `{active}` appear in the built-in hints but are not substituted before the
   command is passed to the shell. A step that reads `command: "strata sln init {name}"`
   runs literally, including the braces.

Together these mean `.strata/workflow.yaml` cannot carry the full definition of a
team's process. Operators must supplement it with external runbooks, READMEs, or
tribal knowledge — defeating the goal of a single, authoritative, executable source
of truth.

The ambition: a project's `.strata/workflow.yaml` should be the thing you hand to a new
teammate. Reading it tells them every step; running `strata guide do` or `strata console`
executes each step in order. **The file is the documentation.**

## Decision Drivers

- Teams should be able to express arbitrary readiness predicates without writing Python.
- The workflow file must remain YAML — no code files required for the common case.
- Shell-command checks keep the file self-contained and language-agnostic.
- Token substitution must not break existing workflow files (additive, opt-in).
- The onboarding use-case (ADR-0014) must continue to work unchanged.

## Considered Options

### Option 1 — Shell-command checks (`check: shell: "…"`)

Extend the `check` field to accept an inline shell command string in addition to a
built-in function name. A step whose `check` evaluates to exit-0 is `ok`; non-zero
is `pending`; timeout / error is `warn`.

```yaml
- id: terraform_docs
  name: Terraform docs up to date
  check: "terraform-docs markdown . | diff - README.md"
  command: "terraform-docs markdown . > README.md"
  depends_on: [build_exists]
  hint: "Regenerate Terraform module docs"
```

Disambiguation: if `check` matches a known built-in name → use built-in. Otherwise
treat it as a shell command.

**Pros:** self-contained, language-agnostic, no extra files.  
**Cons:** shell portability (Windows vs Unix), security surface (arbitrary subprocess
at guide load time).

### Option 2 — Python plugin file (`.strata/checks.py`)

Users drop a Python file in `.strata/` and reference function names in `check`.
The `GuideController` imports the file and resolves unknown check names from it.

```python
# .strata/checks.py
def terraform_docs_current() -> str:   # return "ok" | "warn" | "pending"
    ...
```

**Pros:** full Python power, testable, no shell portability concerns.  
**Cons:** requires writing Python; couples the workflow file to a code file; import
safety concerns (arbitrary code executed at guide load time).

### Option 3 — Shell-command checks + runtime token substitution (chosen)

Combination of Option 1 (shell checks) with a runtime token substitution pass over
both `check` and `command` strings before execution.

Built-in tokens resolved at runtime:

| Token      | Value                               |
| ---------- | ----------------------------------- |
| `{name}`   | workspace name from `solution.json` |
| `{active}` | active profile name                 |
| `{work}`   | `.strata/` work path                |

Token substitution is applied:
- To `command` before `do` executes it.
- To shell-form `check` strings before evaluation (not to built-in check names).
- Unresolved tokens are left as-is (backward compatible).

Security boundary: shell-form checks are **only evaluated when the user explicitly
invokes `status`, `next`, or `do`** — not at import or on `strata guide` invocation
without arguments. A flag `--no-shell-checks` suppresses shell-form evaluation for
CI/headless contexts.

## Decision

Implement **Option 3**:

1. **Shell-form check** — if `check` is not a known built-in name, treat it as a shell
   command. Exit 0 → `ok`. Non-zero → `pending`. Execution error / timeout → `warn`.
   Evaluation is lazy (only on explicit user action; never at workflow load time).

2. **Runtime token substitution** — before executing a shell-form `check` or a `command`
   string, substitute `{name}`, `{active}`, and `{work}` tokens from live workspace state.

3. **`--no-shell-checks` flag** — on `strata guide` and `strata console`, suppresses
   evaluation of shell-form checks. Built-in checks still run. Intended for CI and
   headless contexts where arbitrary subprocess execution is undesirable.

4. **Scope** — the workflow file is no longer scoped to onboarding. Any step sequence
   that can be expressed as "check a predicate, run a command to fix it" is valid.
   Built-in checks remain the default for the eight standard onboarding phases.

Items 22 and 23 from ADR-0014 (`strata guide --next` and `strata guide --do`) are
prerequisites and should ship before or alongside this change. They are not re-decided
here.

## Implementation Items

| #   | Item                                                                       | Status |
| --- | -------------------------------------------------------------------------- | ------ |
| 1   | Shell-form check evaluation in `GuideController`                           | todo   |
| 2   | Runtime token substitution for `check` and `command` strings               | todo   |
| 3   | `--no-shell-checks` flag on `strata guide` and `strata console`            | todo   |
| 4   | Update `WorkflowStep` model — `check` type broadened to `str` (already is) | done   |
| 5   | Update `workflow.md` docs — new `check` semantics, token table             | todo   |
| 6   | Test: shell-form check happy path, exit-non-zero, timeout, error           | todo   |
| 7   | Test: token substitution in `command` and shell-form `check`               | todo   |

## Consequences

**Positive:**
- Teams ship `.strata/workflow.yaml` as their project runbook — reading it and running
  it are the same act.
- No new file formats or code files required for the common case.
- Existing workflow files continue to work without modification.

**Negative / risks:**
- Shell portability: step authors must write portable commands (or document OS
  assumptions). Recommend POSIX-compatible commands; document Windows caveats.
- Security: shell-form checks run arbitrary subprocesses. Mitigated by lazy evaluation
  and `--no-shell-checks`. Teams should treat `.strata/workflow.yaml` as trusted
  project code (same trust level as `Makefile` or `package.json` scripts).
- Performance: shell checks add latency to `status` and `next`. Acceptable because
  these are user-initiated operations, not background polling.
