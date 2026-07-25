# Approval workflows and gates

- Status: proposed
- Date: 2026-07-11

## Foundational Constraints

### Git Is Required

Strata cannot function without git. Every workspace is a git repository. Every deployment
references a commit. Every operator who can run `strata deploy` has already authenticated
to git — and therefore has a verifiable identity.

This constraint is not a limitation — it is the approval infrastructure:

- **Identity is ambient.** `git config user.name` + `user.email` identifies who is acting.
  In CI, the runner's service principal or OIDC token is the identity. No login screen
  needed — authentication already happened to get here.
- **A git artifact is tamper-evident proof.** A signed tag, a merge commit, or a branch
  creation event is cryptographically tied to a key. It is timestamped, attributable, and
  permanently part of the repository history. This is stronger than a button click in a
  web UI.
- **Approval can be a git operation.** An approver creates a signed tag
  (`git tag -s approve/<deploy-id> <commit>`) or merges a PR. Strata reads the tag/merge
  as the approval token — no external system required.
- **The audit trail is the git log.** `git log --show-signature` produces an immutable,
  signed record of every approval. This satisfies most compliance requirements without
  a separate audit database.

**Implication:** strata does not need to build an auth system or a web UI for approvals.
It needs to define a convention for what git artifact constitutes approval, verify it at
deploy time, and record it in the audit log (ADR 0018).

### Cloud CLIs Are Available

Strata has access to `az` (Azure CLI), `aws` (AWS CLI), and `gcloud` (Google Cloud CLI).
This is significant for approvals because:

- **Identity is stronger than git alone.** When a cloud CLI is authenticated, the caller's
  cloud IAM principal is already verified. This is harder to spoof than a git config string
  and is backed by MFA and corporate SSO policies.
  - `az ad signed-in-user show` → Azure AD / Entra ID principal
  - `aws sts get-caller-identity` → AWS IAM identity + account + ARN
  - `gcloud config get-value account` → GCP identity
- **GCP Cloud Deploy has first-class native approval gates.** `requireApproval: true` in a
  delivery pipeline and `gcloud deploy releases approve` are already the "approval as a
  command" primitive this ADR is trying to design — for GCP targets it already exists.
- **AWS CodePipeline has native manual approval actions.** `aws codepipeline put-approval-result`
  provides approval state management, notification routing, and an audit trail without strata
  managing any state.
- **Cloud-native notification infrastructure is available.** `aws sns publish`,
  `az eventgrid event send`, `gcloud pubsub topics publish` — no need to build a
  notification system from scratch.
- **Cloud-native state stores are available.** AWS SSM Parameter Store, Azure App
  Configuration, and GCP Cloud Deploy itself can hold approval state. No separate
  database is needed for cloud targets.

**Implication:** for cloud-managed targets, strata should delegate approval mechanics to
the provider's native primitive where it exists, rather than reinventing them. The
git-tag convention remains the right approach for bare-metal and self-hosted targets
where no cloud approval API is available.

---

## Context and Problem Statement

Today, any user with access to the strata workspace can validate, build, and deploy infrastructure. There is no way to require human (or automated) approval before deployments proceed, especially for critical environments like production.

The gap:
- **No approval gates** — No mechanism to pause deployment and require sign-off from authorized users.
- **No role-based controls** — No distinction between developers who can deploy to dev/staging vs. ops who can deploy to prod.
- **No audit trail of approvals** — No record of who approved what and when.
- **No integration with external approval systems** — No way to integrate with Slack, email, or ticketing systems for approval notifications.
- **No automated gate conditions** — No way to require approval only under certain conditions (e.g., approval required if cost > $1000).

This creates risk: anyone can deploy anything, potentially causing production outages or cost surprises.

## Considered Options

- **Option A**: Native approval engine in strata (CLI-based approval workflow)
- **Option B**: Delegate approval to git operations (signed tags, PR merges)
- **Option C**: Delegate approval to cloud-provider native gates (`gcloud deploy releases approve`, `aws codepipeline put-approval-result`, Azure Pipelines approval steps)
- **Option D**: Hybrid — provider-native approval for cloud targets; git-tag convention for bare-metal/self-hosted targets
- **Option E**: Hybrid — native approval engine with webhooks/integrations to external systems
- **Option F**: Policy engine integration (approval conditions as policies)

## Decision Outcome

[To be decided in implementation]

## Implementation Status

Not yet started. Placeholder for future work.

## Detailed Design

[To be filled in]

### Approval Model

What does an approval request look like?
- Who can approve (roles, users, groups)?
  - For cloud targets: cloud IAM roles/groups (Azure AD groups, AWS IAM groups, GCP IAM roles)
  - For bare-metal: git GPG keys and signed-tag conventions
- How many approvals are required (1, 2, consensus)?
- What triggers an approval requirement (environment, cost threshold, change type)?
- Approval timeout (auto-deny or wait indefinitely)?

### Approval Mechanics

How does the workflow work?
- Approval as a command? (`strata deploy request-approval`, `strata deploy approve`)?
- For GCP targets: delegate to `gcloud deploy releases approve` — already exists.
- For AWS targets: delegate to `aws codepipeline put-approval-result` — already exists.
- For Azure targets: delegate to Azure Pipelines approval gates via `az pipelines`.
- For bare-metal/self-hosted: git signed-tag convention (`git tag -s approve/<deploy-id>`).
- Approval state stored where?
  - Cloud targets: in the cloud provider's native approval state (Cloud Deploy, CodePipeline, Azure Pipelines)
  - Non-cloud: git tags in the workspace repository
- How is approval revoked or expired?
- Can approvals be conditional (e.g., approve if policy passes)?

### Identity Verification

When a deployment requires approval, how is the approver's identity established?
- Cloud targets: cross-reference the approver's action against their cloud IAM principal
  (already authenticated via `az`/`aws`/`gcloud`).
- Non-cloud targets: GPG-signed git tag tied to a known public key.
- CI runners: OIDC token from the CI provider (GitHub Actions, Azure Pipelines, etc.).

### Integration Points

Where do approvals appear?
- CLI (list pending approvals)?
- VS Code extension (notification in chat)?
- Cloud-native notifications: AWS SNS, Azure Event Grid, GCP Pub/Sub
- GitHub PR comments?
- External approval systems?

### Audit Trail

What gets recorded?
- Who requested approval? (git identity + cloud IAM principal where available)
- Who approved/rejected?
- When?
- Why (approval comment)?
- What was the deployment (changes, cost, affected resources)?
- For cloud targets: the cloud provider's own approval event is the canonical record;
  strata mirrors it into the local audit log (ADR 0018).

## Open Questions

1. Should approval be role-based or user-specific?
2. Can approval requirements change per environment?
3. How do we handle approval timeouts?
4. Should approval be required for `build` or only `deploy`?
5. Can multiple approvers operate in parallel or must they be sequential?
6. Should approval be a blocker or just a logging mechanism?
7. How do we integrate with GitHub PR approval system?
8. Should we support integrations with Slack, Jira, or other tools?

## Next Steps

1. Define approval personas (developer, ops, security, finance)
2. Design approval workflow state machine
3. Prototype CLI-based approval system
4. Design GitHub PR integration
5. Design Slack/email notifications
6. Build audit trail storage mechanism
