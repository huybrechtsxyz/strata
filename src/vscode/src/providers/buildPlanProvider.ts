/**
 * BuildPlanProvider — side panel showing `strata build plan` results.
 *
 * Opens a WebviewPanel beside the active editor with three sections:
 *   1. Artifact Changes  — new/changed/unchanged terraform files + line deltas
 *   2. Terraform Plan    — per-stage plan messages (to-create, to-modify, to-destroy)
 *   3. Values            — variable/secret/feature resolution status
 *
 * Usage: BuildPlanProvider.show(filePath, client, context)
 */

import * as vscode from 'vscode';
import type { StrataClient, BuildPlanData, BuildPlanArtifact, BuildPlanTfStage, BuildPlanValue } from '../strataClient';

export class BuildPlanProvider {
    static readonly viewType = 'strataBuildPlan';

    /**
     * Run `strata build plan` for the given deployment file and open a side panel
     * with the structured results.  Falls back to a terminal if the CLI times out.
     */
    static async show(filePath: string, client: StrataClient): Promise<void> {
        const rel = vscode.workspace.asRelativePath(filePath);

        let data: BuildPlanData;
        try {
            data = await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: `Strata: running build plan for ${rel}…`,
                    cancellable: false,
                },
                () => client.getBuildPlan(filePath),
            );
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            const pick = await vscode.window.showErrorMessage(
                `Build plan failed: ${msg}`,
                'Run in Terminal',
            );
            if (pick === 'Run in Terminal') {
                client.runInTerminal(['build', 'plan', '-f', filePath], 'strata build plan');
            }
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            BuildPlanProvider.viewType,
            `Build Plan: ${data.deployment}`,
            vscode.ViewColumn.Beside,
            { enableScripts: false, retainContextWhenHidden: false },
        );
        panel.webview.html = BuildPlanProvider._renderHtml(data);
    }

    private static _renderHtml(data: BuildPlanData): string {
        const changed = data.artifact_diff.filter(a => a.status !== 'unchanged');
        const unchanged = data.artifact_diff.filter(a => a.status === 'unchanged');

        const artifactRows = data.artifact_diff.map(a => {
            const badge = a.status === 'new' ? '➕ new' : a.status === 'changed' ? '✏️ changed' : '— unchanged';
            const delta = a.status !== 'unchanged' && a.lines_changed > 0 ? `+${a.lines_changed} lines` : '';
            const dimClass = a.status === 'unchanged' ? ' dim' : '';
            return `<tr class="${a.status}${dimClass}"><td>${badge}</td><td><code>${a.path}</code></td><td>${delta}</td></tr>`;
        }).join('');

        const tfRows = data.terraform_plan.map(s => {
            const icon = !s.ok ? '❌' : '✅';
            const msgs = s.messages.length
                ? `<ul>${s.messages.map(m => `<li>${_escHtml(m)}</li>`).join('')}</ul>`
                : s.ok ? '<span class="dim">No changes</span>' : '';
            const err = s.error ? `<p class="error">${_escHtml(s.error)}</p>` : '';
            return `<tr><td>${icon}</td><td><strong>${_escHtml(s.stage)}</strong>${err}${msgs}</td></tr>`;
        }).join('');

        const valueRows = data.values.map(v => {
            const statusBadge = v.status === 'ok' ? '✅ ok'
                : v.status === 'required' ? '❌ missing'
                    : v.status === 'seeded' ? '🌱 seeded'
                        : '🔄 generated';
            const detail = v.detail ? ` — ${_escHtml(v.detail)}` : '';
            return `<tr><td>${v.type}</td><td><code>${_escHtml(v.key)}</code></td><td>${_escHtml(v.store)}</td><td>${statusBadge}${detail}</td></tr>`;
        }).join('');

        const summary = `${changed.length} changed, ${unchanged.length} unchanged`;
        const tfSummary = data.terraform_plan.length
            ? `${data.terraform_plan.filter(s => s.ok).length}/${data.terraform_plan.length} stages clean`
            : 'no terraform stages';
        const missingValues = data.values.filter(v => v.status === 'required').length;

        return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); padding: 16px 20px; }
  h1 { font-size: 1.1em; margin-bottom: 4px; }
  .meta { color: var(--vscode-descriptionForeground); font-size: 0.85em; margin-bottom: 20px; }
  h2 { font-size: 0.95em; text-transform: uppercase; letter-spacing: 0.05em; color: var(--vscode-descriptionForeground); border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 4px; margin: 24px 0 10px; }
  table { border-collapse: collapse; width: 100%; }
  td { padding: 4px 8px; vertical-align: top; }
  tr:hover { background: var(--vscode-list-hoverBackground); }
  code { font-family: var(--vscode-editor-font-family); font-size: 0.9em; }
  .dim { opacity: 0.5; }
  .error { color: var(--vscode-errorForeground); }
  ul { margin: 4px 0 0 0; padding-left: 18px; }
  li { margin: 2px 0; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 0.8em; margin-bottom: 12px; }
  .badge-ok { background: var(--vscode-testing-iconPassed); color: #fff; }
  .badge-warn { background: var(--vscode-list-warningForeground); color: #fff; }
  .badge-err { background: var(--vscode-testing-iconFailed); color: #fff; }
</style>
</head>
<body>
<h1>📋 Build Plan: ${_escHtml(data.deployment)}</h1>
<div class="meta">${_escHtml(data.file)} &nbsp;·&nbsp; ${summary} &nbsp;·&nbsp; ${tfSummary}${missingValues > 0 ? ` &nbsp;·&nbsp; <span style="color:var(--vscode-errorForeground)">${missingValues} missing value(s)</span>` : ''}</div>

<h2>Artifact Changes</h2>
${data.artifact_diff.length === 0 ? '<p class="dim">No artifacts found — run build first.</p>' : `<table>${artifactRows}</table>`}

<h2>Terraform Plan</h2>
${data.terraform_plan.length === 0 ? '<p class="dim">No Terraform stages — use --artifacts-only to see only artifact changes.</p>' : `<table>${tfRows}</table>`}

<h2>Values</h2>
${data.values.length === 0 ? '<p class="dim">No values resolved.</p>' : `<table><thead><tr><th>Type</th><th>Key</th><th>Store</th><th>Status</th></tr></thead><tbody>${valueRows}</tbody></table>`}
</body>
</html>`;
    }
}

function _escHtml(s: string): string {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
