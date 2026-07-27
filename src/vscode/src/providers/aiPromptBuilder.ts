/**
 * AiPromptBuilder — constructs system + user prompts for AI analysis in the
 * @strata chat participant.
 *
 * Resolution order for system prompts (mirrors the Python PromptLoader):
 *   1. `<workspace>/.strata/prompts/<name>.md`  — operator override
 *   2. Built-in TypeScript string constants below   — fallback default
 */

import * as path from 'path';
import * as vscode from 'vscode';
import type { BuildPlanData, SbomData, AuditEntry } from '../strataClient';

// ---------------------------------------------------------------------------
// Built-in system prompts
// ---------------------------------------------------------------------------

const PLAN_REVIEW_SYSTEM = `You are an infrastructure change reviewer for a strata deployment platform.
Analyse the Terraform plan provided and give a structured review.

Include:
1. A 2-3 sentence plain-language **summary** of what changes.
2. A **risk level**: 🟢 Low | 🟡 Medium | 🟠 High | 🔴 Critical.
3. **Change counts**: creates / updates / replaces / deletes.
4. **Concerns**: any destructive operations, security group changes, IAM modifications, or unexpected replacements.
5. **Recommendations**: concrete next steps for the operator.

Use markdown formatting with headers and bullet points. Be concise but complete.
Highlight any resource replacements (destroy + create) prominently — these have downtime risk.`;

const FAILURE_DIAGNOSIS_SYSTEM = `You are a DevOps troubleshooting assistant for a strata infrastructure deployment platform.
A deployer step has failed. Analyse the deployment history and error context provided.

Include:
1. **Root cause**: 1-2 sentence diagnosis of the underlying problem.
2. **Category**: auth | network | config | state | resource | dependency | unknown.
3. **Remediation steps**: ordered, actionable list to fix the problem.
4. **References**: relevant Terraform/Ansible docs or error codes if applicable.

Use markdown formatting. Be actionable and specific. Do not fabricate error codes or CVE identifiers.`;

const SBOM_ANALYSIS_SYSTEM = `You are a supply-chain security analyst reviewing a software bill of materials (SBOM) for an infrastructure deployment.
Analyse the component inventory provided.

Include:
1. A 2-3 sentence **summary** of the component landscape.
2. **Risk level**: 🟢 Low | 🟡 Medium | 🟠 High | 🔴 Critical.
3. **Concerns**: outdated packages, known CVE patterns, problematic licences (copyleft, unknown), deprecated components.
4. **Recommendations**: concrete next steps.

Use markdown formatting. Focus on actionable findings. Do not fabricate CVE identifiers.`;

// ---------------------------------------------------------------------------
// AiPromptBuilder
// ---------------------------------------------------------------------------

export class AiPromptBuilder {

    constructor(private readonly workspacePath: string) { }

    // ── System prompt resolution ────────────────────────────────────────────

    async systemPrompt(name: 'plan_review' | 'failure_diagnosis' | 'sbom_analysis'): Promise<string> {
        const override = await this._readWorkspaceOverride(name);
        if (override) return override;
        switch (name) {
            case 'plan_review': return PLAN_REVIEW_SYSTEM;
            case 'failure_diagnosis': return FAILURE_DIAGNOSIS_SYSTEM;
            case 'sbom_analysis': return SBOM_ANALYSIS_SYSTEM;
        }
    }

    // ── User prompt builders ────────────────────────────────────────────────

    buildPlanUserPrompt(plan: BuildPlanData, promptOverride?: string): string {
        const stages = plan.terraform_plan.map((s) => {
            const lines = [
                `**Stage: ${s.stage}** — ${s.ok ? '✅ planned' : '❌ failed'}`,
                s.error ? `Error: ${s.error}` : '',
                s.messages.length > 0
                    ? `Messages:\n${s.messages.slice(0, 20).map(m => `  - ${m}`).join('\n')}`
                    : '',
            ].filter(Boolean);
            return lines.join('\n');
        });

        const artifactSummary = [
            `${plan.artifact_diff.filter(a => a.status === 'new').length} new`,
            `${plan.artifact_diff.filter(a => a.status === 'changed').length} changed`,
            `${plan.artifact_diff.filter(a => a.status === 'unchanged').length} unchanged`,
        ].join(', ');

        return [
            `**Deployment:** ${plan.deployment}`,
            `**File:** ${plan.file}`,
            `**Artifact changes:** ${artifactSummary}`,
            '',
            '## Terraform Plan Output',
            stages.join('\n\n') || '*(no stages)*',
            promptOverride ? `\n## Additional context\n${promptOverride}` : '',
        ].filter(s => s !== undefined && s !== null && s.trim() !== '' || s === '').join('\n');
    }

    diagnosisUserPrompt(entry: AuditEntry, userQuestion?: string): string {
        const stageLines = entry.stages.map((s) => {
            const steps = s.steps.map(st => `    - ${st.step}: ${st.success ? '✅' : '❌'}`).join('\n');
            return [
                `**Stage: ${s.name}** (${s.provisioner ?? 'unknown'}) — ${s.success ? '✅ success' : '❌ failed'}`,
                steps,
            ].filter(Boolean).join('\n');
        });

        return [
            `**Deployment:** ${entry.deployment}`,
            `**Timestamp:** ${entry.timestamp}`,
            `**Overall:** ${entry.success ? '✅ success' : '❌ failed'}`,
            `**Duration:** ${entry.duration_seconds.toFixed(1)}s`,
            entry.commit_sha ? `**Commit:** ${entry.commit_sha.substring(0, 8)}` : '',
            '',
            '## Stage Results',
            stageLines.join('\n\n') || '*(no stages)*',
            userQuestion ? `\n## User question\n${userQuestion}` : '',
        ].filter(s => s !== undefined && s !== null).join('\n');
    }

    sbomUserPrompt(sbom: SbomData, userQuestion?: string): string {
        const components = sbom.components.slice(0, 150).map(
            c => `- ${c.name}@${c.version ?? 'unknown'} (${c.type})`
        );
        const truncated = sbom.component_count > 150
            ? `\n*(showing 150 of ${sbom.component_count} components)*` : '';

        return [
            `**Deployment:** ${sbom.deployment}`,
            `**Total components:** ${sbom.component_count}`,
            sbom.vulnerabilities_found
                ? `**Vulnerabilities found:** Critical=${sbom.critical_count}, High=${sbom.high_count}` : '',
            '',
            '## Component Inventory',
            components.join('\n') || '*(no components)*',
            truncated,
            userQuestion ? `\n## User question\n${userQuestion}` : '',
        ].filter(s => s !== undefined && s !== null).join('\n');
    }

    // ── Internal helpers ────────────────────────────────────────────────────

    private async _readWorkspaceOverride(name: string): Promise<string | null> {
        try {
            const overridePath = path.join(this.workspacePath, '.strata', 'prompts', `${name}.md`);
            const uri = vscode.Uri.file(overridePath);
            const bytes = await vscode.workspace.fs.readFile(uri);
            const text = Buffer.from(bytes).toString('utf-8').trim();
            return text.length > 0 ? text : null;
        } catch {
            return null; // file does not exist — use built-in
        }
    }
}
