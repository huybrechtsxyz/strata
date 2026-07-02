/**
 * GuideViewProvider — renders the workspace readiness checklist in a WebView panel.
 *
 * Opens as a tab in the editor area.  Uses VS Code theme CSS variables so it
 * automatically matches the user's colour theme (light, dark, high-contrast).
 *
 * Refresh button in the panel posts { command: 'refresh' } back to the
 * extension, which calls _refreshAll() to re-query the CLI.
 */

import * as vscode from 'vscode';
import type { WorkspaceStatus, ChecklistItem } from '../strataClient';

export class GuideViewProvider implements vscode.Disposable {
    private _panel: vscode.WebviewPanel | undefined;
    private _status: WorkspaceStatus | undefined;
    private _onRefreshRequested: (() => void) | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Register the callback invoked when the user clicks Refresh in the panel.
     * Extension wires this to _refreshAll().
     */
    onRefresh(cb: () => void): void {
        this._onRefreshRequested = cb;
    }

    /** Open or reveal the panel, rendering the supplied (or last known) status. */
    show(status?: WorkspaceStatus): void {
        if (status) {
            this._status = status;
        }

        if (this._panel) {
            this._panel.reveal(vscode.ViewColumn.One);
            this._panel.webview.html = this._buildHtml();
            return;
        }

        this._panel = vscode.window.createWebviewPanel(
            'strataGuide',
            'Strata Guide',
            vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            },
        );

        this._panel.webview.html = this._buildHtml();

        // Handle messages from the webview (Refresh button)
        this._panel.webview.onDidReceiveMessage((msg: { command: string }) => {
            if (msg.command === 'refresh') {
                this._onRefreshRequested?.();
            }
        });

        this._panel.onDidDispose(() => {
            this._panel = undefined;
        });
    }

    /** Called by _refreshAll() after every successful getStatus() call. */
    update(status: WorkspaceStatus): void {
        this._status = status;
        if (this._panel) {
            this._panel.webview.html = this._buildHtml();
        }
    }

    dispose(): void {
        this._panel?.dispose();
    }

    // ── HTML builder ──────────────────────────────────────────────────────────

    private _buildHtml(): string {
        if (!this._status) {
            return this._loadingPage();
        }

        const { health, solution, profiles, readiness } = this._status;
        const solutionName = solution.name ?? solution.id ?? 'Workspace';
        const profile = profiles.active ?? '(no profile)';
        const { phases_complete, phases_total, checklist, next_step } = readiness;

        const healthColour = health.status === 'HEALTHY'
            ? 'var(--vscode-testing-iconPassed, #73c991)'
            : health.status === 'DEGRADED'
                ? 'var(--vscode-list-warningForeground, #cca700)'
                : 'var(--vscode-list-errorForeground, #f14c4c)';

        const healthIcon = health.status === 'HEALTHY' ? '●' : health.status === 'DEGRADED' ? '▲' : '✕';

        const phaseRows = checklist.map((p: ChecklistItem) => {
            const icon = p.status === 'ok' ? '✅' : p.status === 'warn' ? '⚠️' : '○';
            const cls = p.status === 'ok' ? 'ok' : p.status === 'warn' ? 'warn' : 'pending';
            const detail = p.detail
                ? `<div class="phase-detail">${this._esc(p.detail)}</div>`
                : '';
            const isNext = next_step?.phase === p.phase && p.status !== 'ok';
            return `
                <tr class="phase-row ${cls}${isNext ? ' next-step' : ''}">
                    <td class="phase-icon">${icon}</td>
                    <td class="phase-label">
                        <span class="phase-name">${this._esc(p.label)}</span>
                        ${detail}
                    </td>
                </tr>`;
        }).join('');

        const issueRows = health.issues.length > 0
            ? `<section class="section issues-section">
                <h3 class="section-title">Issues</h3>
                <ul class="issue-list">
                    ${health.issues.map(i => `<li>${this._esc(i)}</li>`).join('')}
                </ul>
               </section>`
            : '';

        const nextStepBlock = next_step
            ? `<section class="section next-section">
                <h3 class="section-title">Next Step</h3>
                <div class="next-card">
                    <div class="next-label">${this._esc(next_step.label)}</div>
                    <div class="next-hint">${this._esc(next_step.hint)}</div>
                    ${next_step.see_also
                ? `<div class="next-see-also">See: ${this._esc(next_step.see_also)}</div>`
                : ''}
                </div>
               </section>`
            : `<section class="section next-section">
                <div class="next-card complete">All phases complete 🎉</div>
               </section>`;

        const progressPct = phases_total > 0
            ? Math.round((phases_complete / phases_total) * 100)
            : 0;

        return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<title>Strata Guide</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--vscode-font-family, system-ui, sans-serif);
    font-size: var(--vscode-font-size, 13px);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
    padding: 24px;
    max-width: 700px;
    margin: 0 auto;
  }

  /* ── Header ──────────────────────────────────────────────────────────── */
  .header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 20px;
  }
  .header-left h1 {
    font-size: 1.4em;
    font-weight: 600;
    color: var(--vscode-editor-foreground);
  }
  .header-left .subtitle {
    color: var(--vscode-descriptionForeground);
    margin-top: 4px;
    font-size: 0.9em;
  }
  .btn-refresh {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    border-radius: 3px;
    padding: 5px 12px;
    cursor: pointer;
    font-size: 0.85em;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .btn-refresh:hover { background: var(--vscode-button-hoverBackground); }

  /* ── Health badge ────────────────────────────────────────────────────── */
  .health-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 4px;
    background: var(--vscode-sideBar-background, var(--vscode-editor-inactiveSelectionBackground));
    margin-bottom: 20px;
  }
  .health-icon { font-size: 1.1em; color: ${healthColour}; }
  .health-text { font-weight: 600; color: ${healthColour}; }
  .health-profile { color: var(--vscode-descriptionForeground); }
  .health-sep { color: var(--vscode-editorIndentGuide-background); }

  /* ── Progress bar ────────────────────────────────────────────────────── */
  .progress-wrap { margin-bottom: 20px; }
  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 0.85em;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 6px;
  }
  .progress-track {
    height: 6px;
    background: var(--vscode-progressBar-background, #0e70c0);
    border-radius: 3px;
    position: relative;
    background: var(--vscode-editor-inactiveSelectionBackground);
  }
  .progress-fill {
    height: 100%;
    width: ${progressPct}%;
    background: var(--vscode-progressBar-background, #0e70c0);
    border-radius: 3px;
    transition: width 0.3s ease;
  }

  /* ── Sections ────────────────────────────────────────────────────────── */
  .section { margin-bottom: 22px; }
  .section-title {
    font-size: 0.78em;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 10px;
  }

  /* ── Checklist table ─────────────────────────────────────────────────── */
  .checklist { width: 100%; border-collapse: collapse; }
  .phase-row { vertical-align: top; }
  .phase-row + .phase-row td { padding-top: 8px; }
  .phase-icon { width: 28px; font-size: 1em; padding-top: 1px; }
  .phase-label { padding-left: 4px; }
  .phase-name { display: block; }
  .phase-detail {
    font-size: 0.87em;
    color: var(--vscode-descriptionForeground);
    margin-top: 2px;
  }
  .phase-row.pending .phase-name { color: var(--vscode-descriptionForeground); }
  .phase-row.warn .phase-name    { color: var(--vscode-list-warningForeground, #cca700); }
  .phase-row.next-step {
    background: var(--vscode-editor-inactiveSelectionBackground);
    border-radius: 4px;
  }
  .phase-row.next-step td { padding: 6px 8px; }

  /* ── Next step card ──────────────────────────────────────────────────── */
  .next-card {
    background: var(--vscode-editor-inactiveSelectionBackground);
    border-left: 3px solid var(--vscode-textLink-foreground, #3794ff);
    border-radius: 0 4px 4px 0;
    padding: 10px 14px;
  }
  .next-card.complete {
    border-left-color: var(--vscode-testing-iconPassed, #73c991);
    color: var(--vscode-testing-iconPassed, #73c991);
  }
  .next-label { font-weight: 600; margin-bottom: 4px; }
  .next-hint  { color: var(--vscode-descriptionForeground); font-size: 0.92em; }
  .next-see-also {
    color: var(--vscode-textLink-foreground, #3794ff);
    font-size: 0.85em;
    margin-top: 6px;
  }

  /* ── Issues ──────────────────────────────────────────────────────────── */
  .issue-list { list-style: none; }
  .issue-list li {
    padding: 4px 0 4px 14px;
    position: relative;
    color: var(--vscode-list-errorForeground, #f14c4c);
    font-size: 0.9em;
  }
  .issue-list li::before {
    content: '×';
    position: absolute;
    left: 0;
    font-weight: 700;
  }

  hr { border: none; border-top: 1px solid var(--vscode-editorIndentGuide-background); margin: 20px 0; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Strata Guide</h1>
    <div class="subtitle">${this._esc(solutionName)}</div>
  </div>
  <button class="btn-refresh" onclick="refresh()">↻ Refresh</button>
</div>

<div class="health-bar">
  <span class="health-icon">${healthIcon}</span>
  <span class="health-text">${health.status}</span>
  <span class="health-sep">|</span>
  <span class="health-profile">Profile: ${this._esc(profile)}</span>
</div>

<div class="progress-wrap">
  <div class="progress-label">
    <span>Readiness</span>
    <span>${phases_complete} / ${phases_total} phases</span>
  </div>
  <div class="progress-track"><div class="progress-fill"></div></div>
</div>

<section class="section">
  <h3 class="section-title">Checklist</h3>
  <table class="checklist">
    <tbody>${phaseRows}</tbody>
  </table>
</section>

${issueRows}
${nextStepBlock}

<script>
  const vscode = acquireVsCodeApi();
  function refresh() { vscode.postMessage({ command: 'refresh' }); }
</script>
</body>
</html>`;
    }

    private _loadingPage(): string {
        return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>body{font-family:system-ui;padding:40px;color:var(--vscode-editor-foreground);background:var(--vscode-editor-background);text-align:center;}</style>
</head><body><p>Loading workspace state…</p></body></html>`;
    }

    private _esc(s: string): string {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}
