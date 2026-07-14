# Approval workflows and gates

- Status: proposed
- Date: 2026-07-11

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
- **Option B**: Delegate approval to external tools (GitHub PR reviews, Spacelift, env0)
- **Option C**: Hybrid — native approval engine with webhooks/integrations to external systems
- **Option D**: Policy engine integration (approval conditions as policies)

## Decision Outcome

[To be decided in implementation]

## Implementation Status

Not yet started. Placeholder for future work.

## Detailed Design

[To be filled in]

### Approval Model

What does an approval request look like?
- Who can approve (roles, users, groups)?
- How many approvals are required (1, 2, consensus)?
- What triggers an approval requirement (environment, cost threshold, change type)?
- Approval timeout (auto-deny or wait indefinitely)?

### Approval Mechanics

How does the workflow work?
- Approval as a command? (`strata deploy request-approval`, `strata deploy approve`)?
- Approval state stored where (in deployment manifest, separate record, external system)?
- How is approval revoked or expired?
- Can approvals be conditional (e.g., approve if policy passes)?

### Integration Points

Where do approvals appear?
- CLI (list pending approvals)?
- VS Code extension (notification in chat)?
- Slack/email notifications?
- GitHub PR comments?
- External approval systems?

### Audit Trail

What gets recorded?
- Who requested approval?
- Who approved/rejected?
- When?
- Why (approval comment)?
- What was the deployment (changes, cost, affected resources)?

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
