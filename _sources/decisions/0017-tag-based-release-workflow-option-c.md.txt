# Tag-based release workflow support (Option C: Validation + Visibility)

- Status: proposed
- Date: 2026-07-03
- Related: [GitHub Issue #111](https://github.com/huybrechtsxyz/strata/issues/111), [ADR 0011 (Promotion strategies)](0011-promotion-strategies-for-version-progression.md)

## Summary

Issue #111 requests support for tag-based release workflows: quality-gate tagging after CI passes, release candidate selection, semantic versioning, and audit-compliant branch retention. Three options are under consideration:

- **Option A**: Strata orchestrates the full workflow (release policies, branch creation, approval gates)
- **Option B**: Strata is a pure consumer; CI/CD owns everything
- **Option C**: Strata validates and provides visibility; CI/CD orchestrates

This ADR proposes **Option C** — a minimal, non-invasive enhancement that:

1. Extends `GitIntegration` with tag listing operations
2. Adds a built-in `ref_convention` policy to validate tag naming conventions
3. Enriches `strata repo status` to show latest quality-gate and release tags

This approach respects Strata's core mission (validate & deploy configuration) while providing just enough tooling to support disciplined release workflows without duplicating CI/CD responsibilities.

## Context and Problem Statement

### What teams need

Teams following periodic release cadences (e.g., weekly, monthly) need a structured promotion path:

1. **Quality gate** — After merge to main, CI passes → auto-tag with `tested` or `rc-<date>`
2. **Release candidate selection** — Every X weeks, identify latest quality-gated commit
3. **Release branch creation** — Cut `release/X.Y.Z` from that tag
4. **Release validation** — Full test suite on release branch
5. **Approval & versioning** — After sign-off, tag with semver (e.g., `v1.2.0`)
6. **Retention** — Keep release branch for X months (audit compliance for ISAE 3402, NIS2)

### What Strata provides today

Strata has foundational pieces that align perfectly:

- **Git ref pinning** — `SourceModel` supports branch/tag/commit SHA resolution at build time ✅
- **Deployment manifests** — Record exact commit SHAs, timestamps, deployed-by identity, artifact hashes — audit-grade ✅
- **Repository management** — `strata repo sync` handles clone/pull with branch tracking ✅
- **Version labels** — Builders extract `version` labels from workspace/deployment metadata ✅

### What's missing

- No git tag operations in `GitIntegration` (list, inspect, create would be needed)
- No validation mechanism for tag naming conventions
- No visibility into what tags exist and which is the latest quality gate
- No enforcement that deployments pin to release tags, not arbitrary commits or branches

### Why Option C?

Release orchestration (approval gates, timing, branch creation/deletion) is fundamentally a **CI/CD concern**, not a platform configuration concern. GitHub Actions and Azure DevOps pipelines already have mature UX for this. Strata should focus on:

1. **Validation** — Enforce that references follow team conventions
2. **Visibility** — Show operators what tags exist so they know what to promote
3. **Integration** — Consume tags that CI/CD creates, use them in deployments

This keeps Strata in its lane while providing real value with minimal scope and zero new state files.

## Considered Options

| Option                         | Scope                                                  | Who orchestrates                                        | Strata work                                                          |
| ------------------------------ | ------------------------------------------------------ | ------------------------------------------------------- | -------------------------------------------------------------------- |
| **A: Full orchestration**      | Release policies, cadences, approval, branch lifecycle | Strata (new `strata release` command group)             | Large: policy model, state tracking, CLI commands                    |
| **B: Pure consumption**        | Only consume tags that CI creates                      | CI/CD (100%)                                            | Small: just tag pinning in `SourceModel`                             |
| **C: Validation + visibility** | Validate conventions, show status                      | CI/CD (orchestration); Strata (validation & visibility) | Small-Medium: extend GitIntegration, new policy, enhance repo status |

## Decision Outcome

Chosen: **Option C — Validation + Visibility**, because it:

- ✅ Solves the real problem without scope creep
- ✅ Respects the separation of concerns (CI/CD → orchestration; Strata → configuration validation)
- ✅ Uses the existing policy engine, no new model types needed
- ✅ Minimal implementation (3 small pieces)
- ✅ No new state files or event tracking
- ✅ Familiar UX (existing policy and repo commands)
- ❌ Requires CI/CD to handle orchestration (teams must own that)
- ❌ Doesn't provide drift-free scheduling (CI is responsible for cadence)

### Relationship to ADR 0011 (Promotion Strategies)

ADR 0011 solves: "How do we gradually roll out a version across environments with canary waves and controlled rollback?"

This ADR (0017) solves: "How do we manage the release lifecycle itself and ensure references follow convention?"

They are **complementary**, not competing:

```
Code → CI/CD (Option C concern)
  ├─ Merge to main
  ├─ Tests pass → auto-tag `tested`
  ├─ Every X weeks: create release branch, tag with `v1.2.0`
  ├─ Tag ref in configuration

Config → Strata validation & deployment (ADR 0011 concern)
  ├─ `strata validate` → checks tag name matches `v\d+\.\d+\.\d+` pattern
  ├─ `spec.overrides.remotes[].reference: v1.2.0` → pins to release tag
  ├─ `strata promote start` → gradually rolls out across environments
  └─ Canary wave 1, then wave 2, with full audit trail
```

## Detailed Design

### 1. Extend GitIntegration with tag operations

**File:** `src/strata/integrations/git.py`

Add two read-only methods:

```python
@dataclass
class TagInfo:
    """Git tag metadata."""
    name: str                    # e.g., "v1.2.0", "tested"
    commit: str                  # full SHA
    short_commit: str            # short SHA (7 chars)
    tagger: Optional[str]        # creator name (annotated tags only)
    created: Optional[datetime]  # when tag was created
    message: Optional[str]       # tag message (annotated tags only)
    is_annotated: bool          # vs lightweight

class GitIntegration(BaseIntegration):
    def list_tags(
        self,
        working_dir: Path,
        pattern: Optional[str] = None,
        sort: str = "-creatordate",  # "-creatordate", "version:refname", "refname"
        timeout: int = 30,
    ) -> List[TagInfo]:
        """
        List tags in the repository, optionally filtered by pattern.
        
        Args:
            working_dir: Repository path
            pattern: Optional grep pattern to filter tags (e.g., "^v[0-9]", "^tested")
            sort: Sort order (default: newest first)
            timeout: Command timeout in seconds
            
        Returns:
            List of TagInfo sorted by creation date (newest first)
            
        Raises:
            IntegrationError: If git command fails
        """
        # git tag --list <pattern> --sort=<sort> --format='%(refname:short)|%(objectname:short)|%(creatordate:iso)|%(taggername)|%(contents)'
        # Parse each line into TagInfo
        # Return []  if no tags match
```

**Why these methods?**
- `list_tags()` with optional pattern lets policies discover tags matching conventions (e.g., `^v\d+\.\d+\.\d+$` for semver)
- Pattern filtering is cheap (server-side git grep) and prevents data explosion in repos with thousands of tags
- Sorting by creation date makes it easy to find "latest quality gate tag"
- `TagInfo` dataclass provides structured data for policies and commands

### 2. Implement RefConventionPolicy (new built-in)

**File:** `src/strata/validators/policies/ref_convention_policy.py`

```python
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

class RefConventionPolicy(BasePolicy):
    """
    Validate that remote references follow declared tag naming conventions.
    
    Runs at VALIDATE phase. Inspects all deployments/environments and their
    spec.overrides.remotes[].reference values. If a reference looks like a tag
    (starts with 'v', or contains a pattern), check it against the declared
    convention for that remote.
    
    Configuration:
    
        policies:
          - name: release_conventions
            type: ref_convention
            phase: validate
            enforcement: warn  # or deny
            configuration:
              remotes:
                - name: my-service
                  release_pattern: "^v\\d+\\.\\d+\\.\\d+$"    # semver
                  quality_pattern: "^tested(-\\d+)?$"          # quality gate
                - name: tf-landscape
                  release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
    """
    
    def __init__(self, policy_model: PolicyModel):
        super().__init__(policy_model)
        self._config = policy_model.configuration or {}
    
    @property
    def phase(self) -> str:
        return "validate"
    
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        violations = []
        
        # Extract remote-to-pattern mapping from configuration
        remote_patterns = {}
        for remote_config in self._config.get("remotes", []):
            name = remote_config.get("name")
            release_pattern = remote_config.get("release_pattern")
            quality_pattern = remote_config.get("quality_pattern")
            
            if name and (release_pattern or quality_pattern):
                remote_patterns[name] = {
                    "release": release_pattern,
                    "quality": quality_pattern,
                }
        
        if not remote_patterns:
            # No configuration — nothing to validate
            return PolicyResult(
                passed=True,
                policy_name=self.policy.name,
                enforcement=self.policy.enforcement,
                violations=[],
            )
        
        # Load all deployments and environments; inspect their remote references
        try:
            deployment_service = context.deployment_service
            config_service = context.configuration_service
            
            # Check each deployment
            for deployment in deployment_service.all():
                violations.extend(
                    self._check_remote_refs(
                        deployment.model.spec.overrides.remotes or [],
                        remote_patterns,
                        f"deployment {deployment.model.meta.name}",
                    )
                )
            
            # Check each environment
            if config_service:
                for environment in config_service.environments:
                    violations.extend(
                        self._check_remote_refs(
                            environment.spec.overrides.remotes or [],
                            remote_patterns,
                            f"environment {environment.meta.name}",
                        )
                    )
        except Exception as e:
            # If services unavailable or error loading, skip validation
            # (this is not a ref convention error, just a diagnostic state)
            return PolicyResult(
                passed=True,
                policy_name=self.policy.name,
                enforcement=self.policy.enforcement,
                violations=[],
                details={"skipped": str(e)},
            )
        
        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.policy.name,
            enforcement=self.policy.enforcement,
            violations=violations,
        )
    
    def _check_remote_refs(
        self, remotes: List[RemoteOverride], patterns: Dict[str, Any], context: str
    ) -> List[str]:
        """Check each remote reference against its declared pattern."""
        violations = []
        
        for remote in remotes:
            name = remote.name
            ref = remote.reference
            
            if name not in patterns:
                # No convention declared for this remote — skip
                continue
            
            pattern_config = patterns[name]
            
            # Heuristic: if ref looks like a release tag (matches one of the patterns),
            # validate against the appropriate pattern
            release_pattern = pattern_config.get("release")
            quality_pattern = pattern_config.get("quality")
            
            # Try to guess which pattern applies
            # (in a real scenario, CI would tell us via metadata, but we infer here)
            if self._looks_like_ref(ref):
                # It's not a branch (branches have slashes, refs don't in this context)
                if not self._matches_pattern(ref, release_pattern) and \
                   not self._matches_pattern(ref, quality_pattern):
                    pattern_str = " or ".join(
                        p for p in [release_pattern, quality_pattern] if p
                    )
                    violations.append(
                        f"{context}: remote '{name}' reference '{ref}' "
                        f"does not match expected pattern ({pattern_str})"
                    )
        
        return violations
    
    @staticmethod
    def _looks_like_ref(ref: str) -> bool:
        """Heuristic: does this look like a tag vs branch vs commit?"""
        import re
        # Looks like a tag if it starts with v, has no slashes, matches semver/calver patterns
        # This is a heuristic — exact determination would need git inspection
        return bool(re.match(r"^[vr]?\d+", ref)) or re.match(r"^tested", ref)
    
    @staticmethod
    def _matches_pattern(ref: str, pattern: Optional[str]) -> bool:
        """Check if ref matches the regex pattern."""
        if not pattern:
            return False
        import re
        try:
            return bool(re.fullmatch(pattern, ref))
        except re.error:
            return False
```

**Registration:**

In `src/strata/validators/policies/__init__.py`:

```python
from strata.validators.policies.ref_convention_policy import RefConventionPolicy

# Update __all__
__all__ = [
    "BasePolicy",
    "TenantZonePolicy",
    "NamingPolicy",
    "RefConventionPolicy",  # NEW
    "PolicyContext",
    "PolicyEngine",
    "PolicyResult",
]
```

In `src/strata/validators/policies/policy_engine.py` (in `__init__` method or class initialization):

```python
# Register built-in policy types
PolicyEngine.register_type("tenant_zone", TenantZonePolicy)
PolicyEngine.register_type("required_tags", RequiredTagsPolicy)
PolicyEngine.register_type("naming_pattern", NamingPolicy)
PolicyEngine.register_type("ref_convention", RefConventionPolicy)  # NEW
PolicyEngine.register_type("script", ScriptPolicy)
```

**What it validates:**

- At `strata validate` time, load all deployments and environments
- For each `spec.overrides.remotes[]` reference, check if it matches the declared pattern
- Report violations (warn/deny) if a reference doesn't follow convention
- Example: if config declares `release_pattern: "^v\d+\.\d+\.\d+$"` for `my-service`, but a deployment references `my-service@main`, warn the operator

### 3. Enhance `strata repo status` with tag visibility

**File:** `src/strata/commands/repo/status_repo_solution_command.py`

Add tag discovery to the per-repo output:

```python
class RepoStatus:
    """Per-repository status (replaces previous string tuples)."""
    
    @dataclass
    class Tags:
        latest_release: Optional[TagInfo] = None      # e.g., v1.2.0 (14 days old)
        latest_quality: Optional[TagInfo] = None      # e.g., tested (2 days old)
        all_tags: List[TagInfo] = field(default_factory=list)

class StatusRepoSolutionCommand(BaseCommand):
    """Show git state and tag status for all registered repositories."""
    
    OPERATION = "solution_repo_status"
    
    def execute(self) -> CommandResult:
        """Existing logic, plus tag discovery."""
        
        # For each repository, after checking branch status:
        if git_integration.is_available():
            tags = self._discover_tags(repo_path, repo_config)
            # Add tags to output
    
    def _discover_tags(
        self, repo_path: Path, repo_config: RepositoryConfigModel
    ) -> RepoStatus.Tags:
        """
        Discover quality-gate and release tags for this repository.
        
        Logic:
        1. Look for tags matching quality-gate pattern (config-driven or defaults)
           - Default: "tested", "tested-*", "rc-*"
           - Get the latest by creation date
        2. Look for tags matching release pattern (config-driven or defaults)
           - Default: "v\d+\.\d+\.\d+", "release-*"
           - Get the latest by creation date
        3. Return both with age in human-readable format
        """
        
        # If repository has no release conventions configured, skip
        # (silently — this is not an error, just not applicable)
        
        git = IntegrationFactory.create({"type": "git"})
        
        # List all tags (or filtered by pattern if config says so)
        all_tags = git.list_tags(repo_path)
        
        # Find latest quality-gate tag
        quality_patterns = repo_config.get("quality_patterns", ["tested", "tested-*", "rc-*"])
        latest_quality = self._find_latest_matching(all_tags, quality_patterns)
        
        # Find latest release tag
        release_patterns = repo_config.get("release_patterns", [r"^v\d+\.\d+\.\d+$"])
        latest_release = self._find_latest_matching(all_tags, release_patterns)
        
        return RepoStatus.Tags(
            latest_release=latest_release,
            latest_quality=latest_quality,
            all_tags=all_tags[:10],  # Keep most recent 10 for reference
        )
    
    @staticmethod
    def _find_latest_matching(tags: List[TagInfo], patterns: List[str]) -> Optional[TagInfo]:
        """Find the first (oldest) tag matching any of the patterns."""
        import re
        for tag in tags:  # Already sorted by date (newest first)
            for pattern in patterns:
                if re.fullmatch(pattern, tag.name):
                    return tag
        return None
```

**Console output (with colors):**

```
Repository Status
─────────────────

  ✅ my-service  [main → origin/main]
     Latest release: v1.2.0 (14 days old, abc1234)
     Latest quality: tested (2 days old, def5678)

  ✅ tf-landscape  [main → origin/main ↑0 ↓2]
     Latest release: v2.4.0 (7 days old, ghi9abc)
     Latest quality: tested-20260701 (1 day old, jkl2def)

  ⚠️  config-repo  [dev → origin/main]
     Latest release: none
     Latest quality: none
     (No matching release or quality tags — check naming conventions)

  ⚪ legacy-service  (not cloned)
```

**JSON output (with `--output json`):**

```json
{
  "success": true,
  "data": {
    "repositories": [
      {
        "name": "my-service",
        "state": "clean",
        "branch": "main",
        "tracking": "origin/main",
        "tags": {
          "latest_release": {
            "name": "v1.2.0",
            "commit": "abc123456789",
            "short_commit": "abc1234",
            "created": "2026-06-19T14:30:00Z",
            "age_days": 14,
            "is_annotated": true
          },
          "latest_quality": {
            "name": "tested",
            "commit": "def567890123",
            "short_commit": "def5678",
            "created": "2026-07-01T10:15:00Z",
            "age_days": 2,
            "is_annotated": true
          }
        }
      }
    ]
  }
}
```

## Usage Examples

### Example 1: Declare release conventions in configuration

```yaml
# configuration.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: my-platform
spec:
  # Existing fields...
  
  policies:
    # NEW: Validate remote references follow conventions
    - name: release_tag_conventions
      type: ref_convention
      phase: validate
      enforcement: warn
      configuration:
        remotes:
          - name: my-service
            release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
            quality_pattern: "^tested(-\\d+)?$"
          - name: tf-landscape
            release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
          - name: config-repo
            release_pattern: "^release-\\d{4}-\\d{2}-\\d{2}$"  # calver
            quality_pattern: "^ready$"
```

### Example 2: Check what's available to promote

```bash
$ strata repo status

Repository Status
─────────────────

  ✅ my-service  [main → origin/main]
     Latest release: v1.2.0 (14 days old, abc1234)
     Latest quality: tested (2 days old, def5678)

  ✅ tf-landscape  [main → origin/main]
     Latest release: v2.4.0 (7 days old, ghi9abc)
     Latest quality: tested-20260701 (1 day old, jkl2def)
```

### Example 3: Validate before deployment

```bash
$ strata validate -f environments/production.yaml

Validating configuration...

✅ Configuration syntax: OK
✅ Cross-reference validation: OK
✅ Tenant zone policy: OK
⚠️  Release tag conventions: WARNING
    • environment production: remote 'my-service' reference 'main' 
      does not match expected pattern (^v\d+\.\d+\.\d+$ or ^tested(-\d+)?$)
    
    Hint: Use a release tag (e.g., v1.2.0) or quality gate tag (e.g., tested)
    instead of branch references in production environments.

Overall: PASSED (1 warning)
```

### Example 4: Pin to a release tag in deployment

```yaml
# environments/production.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: production
spec:
  overrides:
    remotes:
      - name: my-service
        reference: v1.2.0  # ✅ Matches release_pattern
      - name: tf-landscape
        reference: v2.4.0  # ✅ Matches release_pattern
```

Then promote gradually using ADR 0011:

```bash
$ strata promote start --remote my-service --version v1.2.0 --to production --wave 1

[wave 1] Promoting my-service to v1.2.0 in production (canary: acme tenant)
  ✅ Branch: promote/my-service-v1.2.0-production
  ✅ Created PR #4521
  ✅ Gate check: acceptance has v1.2.0 ✓
  
Waiting for approval... (or run `strata promote start --wave 2` to advance)
```

## Implementation Plan

### Phase 1: Extend GitIntegration (1-2 days)

- [ ] Add `TagInfo` dataclass to `git.py`
- [ ] Implement `list_tags()` method
- [ ] Unit tests: list tags with pattern, sort by date, handle empty repo
- [ ] PR review & merge

### Phase 2: Implement RefConventionPolicy (2-3 days)

- [ ] Create `ref_convention_policy.py`
- [ ] Register in `policy_engine.py`
- [ ] Unit tests: policy config parsing, pattern matching, multi-remote validation
- [ ] Integration tests: validate with real deployments/environments
- [ ] PR review & merge

### Phase 3: Enhance repo status (1-2 days)

- [ ] Modify `status_repo_solution_command.py` to call `list_tags()` per repo
- [ ] Add `Tags` dataclass to command output
- [ ] Update console rendering (rich tables)
- [ ] Update JSON output schema
- [ ] Unit tests: tag discovery, age calculation, rendering
- [ ] PR review & merge

### Phase 4: Documentation (1 day)

- [ ] Update [platform/readme.md](../platform/readme.md) with release workflow section
- [ ] Add example configuration to docs
- [ ] Link from ADR 0011 to this ADR

## Consequences

### Positive

- ✅ Minimal scope: three focused changes, no new model types, no new CLI groups
- ✅ Uses existing infrastructure (policy engine, git integration, repo command)
- ✅ Familiar UX: `strata validate` and `strata repo status` remain unchanged, just enhanced
- ✅ Zero new state files — all validation is read-only
- ✅ Works with existing CI/CD (GitHub Actions, Azure DevOps) — no special integration needed
- ✅ No breaking changes — purely additive
- ✅ Supports multiple versioning schemes (semver, calver, custom regex)
- ✅ Scales: policy engine can handle any number of remote repositories

### Negative

- ❌ Teams must implement release orchestration in CI/CD (not built into Strata)
- ❌ No automated cadence scheduling (CI/CD is responsible)
- ❌ No enforcement of branch retention policies (CI/CD cleanup job needed)
- ❌ Requires discipline: nothing stops a manual `git push v1.2.0` that violates the convention
- ❌ Policy is heuristic-based (guesses release vs quality tag based on name patterns)

### Neutral

- ~~ GitIntegration gains read-only methods — future extensions (tag creation/moving) are now easier if needed
- ~~ Policy engine already supports this pattern (naming_pattern does similar validation)
- ~~ No dependency changes required (git, regex are stdlib)

## Open Questions

1. **Should `ref_convention` policy be enforcement: warn by default?**
   - Proposed: yes — validating conventions is helpful but shouldn't block deployment (teams adjust patterns)
   - Alternative: deny — stricter but may be too aggressive for adoption

2. **Should `list_tags()` accept a limit to prevent large repos from slowing down `repo status`?**
   - Proposed: yes, default limit=100, let caller override
   - Alternative: no limit, rely on git to be fast (it usually is)

3. **Should repo config support quality/release patterns, or only the policy?**
   - Proposed: Only policy (single source of truth)
   - Alternative: Both (allows per-repo customization, but adds complexity)

4. **How should we handle repos with no release tags yet (e.g., new projects)?**
   - Proposed: `strata repo status` shows "none", no warning
   - `strata validate` can warn if policy is enabled and no quality tags found

5. **Should we compute tag age for display?**
   - Proposed: yes, use `now() - created` to show "14 days old" (better UX than ISO timestamps)
   - Alternative: show raw ISO timestamp (more precise but less readable)

## Appendix: Example Release Workflow (Using CI/CD + Option C)

### Week 1: Main branch development

```
Developer PRs → CI tests pass → merge → auto-tag: tested
                                         └─ `.github/workflows/tag.yml` runs after merge
```

### Week 4: Release candidate creation (CI/CD job on schedule)

```
Latest "tested" tag → create branch release/1.2.0
                   → run full test suite on branch
                   → create GitHub release draft (waiting for approval)
```

### Release approval (manual)

```
GitHub release UI → Approve → CI/CD publishes release
                           → pushes tag v1.2.0
```

### Strata validation + deployment

```
$ strata repo status
  Latest release: v1.2.0
  Latest quality: tested

$ strata validate -f environments/production.yaml
  ✅ All remote references use release tags

$ strata promote start --remote my-service --version v1.2.0 --to production --wave 1
  ✅ Canary rollout begins (ADR 0011)
```

This workflow requires **zero Strata enhancement** for orchestration. Option C just provides the validation and visibility guardrails.

