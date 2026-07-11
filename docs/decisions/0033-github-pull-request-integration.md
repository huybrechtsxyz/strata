# GitHub pull request integration

- Status: proposed
- Date: 2026-07-11

## Context and Problem Statement

Today, strata operates in isolation from source control workflows. Infrastructure changes are typically tracked through a separate Git repository (configuration spec repos), but deployment actions (validate, build, deploy) happen outside the GitHub PR review process.

The gap:
- **No PR validation automation** — Configuration changes in a PR are not automatically validated by strata.
- **No plan visibility in PRs** — PR reviewers cannot see what infrastructure changes will occur without running strata locally.
- **No plan comments** — Terraform plans, cost estimates, and policy violations are not surfaced as PR comments.
- **No deployment status checks** — PRs don't show deployment readiness or approval status.
- **No auto-merge conditions** — No way to auto-merge PRs once all strata checks and approvals pass.
- **No link between PR and deployment** — No clear audit trail connecting a PR to a deployment.

This breaks the GitOps workflow: infrastructure changes should flow through code review before deployment, but strata doesn't participate in the PR process.

## Considered Options

- **Option A**: GitHub Apps (native integration via GitHub API)
- **Option B**: Webhook-based integration (external service listening to GitHub webhooks)
- **Option C**: CLI integration with CI/CD (GitHub Actions step that runs strata)
- **Option D**: All of the above (layered approach)

## Decision Outcome

[To be decided in implementation]

## Implementation Status

Not yet started. Placeholder for future work.

## Detailed Design

[To be filled in]

### GitHub App

Do we build a GitHub App?
- Permissions required (read PRs, write PR comments, read repo, trigger workflows)?
- Authentication (GitHub App private key stored where)?
- Event handlers (pull_request, push, pull_request_review)?

### Automation Triggers

When do we run checks?
- On every PR creation/update?
- On PR comment commands (e.g., `@strata-bot validate`)?
- Only on specific file changes (e.g., files in `deploy/`, `config/`)?
- Manual trigger via GitHub Actions workflow?

### PR Comments and Checks

What information goes into PRs?
- Validation results (✅/❌ with errors)?
- Terraform plan (inline or as artifact)?
- Cost estimate (if available)?
- Policy violations (if any)?
- Build artifacts (manifest, etc.)?
- Approval status?

### Status Checks

How do PR status checks work?
- Create a required check (blocks merge until passed)?
- Which checks are required vs. optional?
- Can checks be skipped by approval?

### Deployment Workflow in PRs

How does deployment connect to PRs?
- Merge PR → automatically deploy?
- Manual `/deploy` command in PR comments?
- Separate deployment workflow triggered by PR?
- Link deployment to PR for audit trail?

### Integration with Approval System

How do PR reviews and strata approvals connect?
- Does GitHub PR approval count as strata approval?
- Does strata approval requirement block PR merge?
- How do we sync PR approval status to strata?

## Open Questions

1. Should we build a GitHub App or just support GitHub Actions?
2. Should validation/build run on every PR update or only on demand?
3. Should strata block PR merge or just comment?
4. Should we support merge strategies (squash, rebase, etc.)?
5. Should we auto-deploy on PR merge?
6. How do we handle rollback if deployment fails post-merge?
7. Should PR checks be scoped to changed files only?
8. How do we handle multi-repo deployments (specs in one repo, strata in another)?
9. Should we support GitHub Enterprise?
10. How do we handle branch protection rules?

## Next Steps

1. Evaluate GitHub App vs. GitHub Actions approach
2. Prototype PR comment integration
3. Design Terraform plan display in PR comments
4. Design GitHub Actions workflow templates
5. Build GitHub App (if chosen)
6. Integrate cost and approval status into PR checks
7. Design audit trail linking PRs to deployments
