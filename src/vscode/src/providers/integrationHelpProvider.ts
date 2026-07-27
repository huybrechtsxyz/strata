/**
 * IntegrationHelpProvider — renders integration help docs in a WebView panel.
 *
 * Triggered by the "Show Help" inline action on any tool item in the
 * strataWorkspace Tools section.
 *
 * Flow:
 *   1.  User right-clicks a tool in the Tools tree (or clicks the $(book) icon)
 *       → `strata.showIntegrationHelp` command fires with the integration name.
 *   2.  Provider runs `strata help --topic <name> --output json` via StrataClient.
 *   3.  Markdown content is rendered as themed HTML inside a WebviewPanel.
 *   4.  If the CLI returns nothing (integration not in topic registry), falls
 *       back to a "no help available" page.
 *
 * Singleton panel — subsequent calls for the same topic reveal the existing
 * panel; calls for a different topic replace its content.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import type { StrataClient } from '../strataClient';

// ---------------------------------------------------------------------------
// Bundled help file reader
// ---------------------------------------------------------------------------

/** Priority order for resolving a help topic:
 *  1. <workPath>/.strata/help/<topic>.md  — workspace custom / override
 *  2. resources/help/<topic>.md           — bundled with the extension
 *  3. CLI fallback                        — for dynamic / unlisted topics
 */
function _readHelpFile(topic: string, workPath: string | undefined): string | undefined {
    const candidates: string[] = [];

    // 1. Workspace override
    if (workPath) {
        candidates.push(path.join(workPath, '.strata', 'help', `${topic}.md`));
        candidates.push(path.join(workPath, '.strata', 'help', `${topic.replace(/_/g, '-')}.md`));
    }

    // 2. Bundled resources
    try {
        const ext = vscode.extensions.getExtension('huybrechts-xyz.xyz-strata');
        const root = ext?.extensionUri.fsPath ?? path.resolve(__dirname, '..', '..');
        candidates.push(path.join(root, 'resources', 'help', `${topic}.md`));
        candidates.push(path.join(root, 'resources', 'help', `${topic.replace(/_/g, '-')}.md`));
    } catch { /* ignore */ }

    for (const file of candidates) {
        try {
            if (fs.existsSync(file)) {
                return fs.readFileSync(file, 'utf-8');
            }
        } catch { /* skip */ }
    }
    return undefined;
}

// ---------------------------------------------------------------------------
// Minimal Markdown → HTML converter (no external deps)
// ---------------------------------------------------------------------------

function mdToHtml(md: string): string {
    let html = md
        // Headings
        .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // Bold / italic
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Links
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

    // Fenced code blocks
    html = html.replace(/```[\w]*\n([\s\S]*?)```/g, (_m, code) =>
        `<pre><code>${code.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`
    );

    // Tables (simple: header | cell | cell)
    html = html.replace(/(?:^[|].+\n)+/gm, (table) => {
        const rows = table.trim().split('\n').filter(r => !/^[|\s-]+$/.test(r));
        if (rows.length === 0) return table;
        const [header, ...body] = rows;
        const thCells = header.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('');
        const trs = body.map(row => {
            const tds = row.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('');
            return `<tr>${tds}</tr>`;
        }).join('\n');
        return `<table><thead><tr>${thCells}</tr></thead><tbody>${trs}</tbody></table>`;
    });

    // Unordered lists — group consecutive `- ` lines
    html = html.replace(/(?:^- .+\n?)+/gm, (block) => {
        const items = block.trim().split('\n').map(l => `<li>${l.replace(/^- /, '').trim()}</li>`).join('');
        return `<ul>${items}</ul>`;
    });

    // Paragraphs — wrap non-empty lines that aren't already block elements
    html = html
        .split('\n')
        .map(line => {
            const trimmed = line.trim();
            if (!trimmed) return '';
            if (/^<(h[1-6]|pre|ul|ol|li|table|th|td|tr|thead|tbody|blockquote)/.test(trimmed)) return line;
            return `<p>${line}</p>`;
        })
        .join('\n');

    return html;
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class IntegrationHelpProvider implements vscode.Disposable {
    private _panel: vscode.WebviewPanel | undefined;
    private _currentTopic: string | undefined;
    private _client: StrataClient | undefined;
    private _workPath: string | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
    }

    setWorkPath(workPath: string): void {
        this._workPath = workPath;
    }

    dispose(): void {
        this._panel?.dispose();
    }

    /**
     * Open (or re-use) the help panel for the given integration name.
     * The name should match a key in the strata `_TOPICS` registry
     * (e.g. `"checkov"`, `"helm"`, `"azure_keyvault"`).
     */
    async show(integrationName: string): Promise<void> {
        const topic = integrationName.toLowerCase().replace(/-/g, '_');

        // 1. Try workspace override then bundled resources (no CLI needed for static content)
        let markdown: string | undefined = _readHelpFile(topic, this._workPath);

        // 2. Fall back to CLI if not found in either location (e.g. unlisted workspace topics)
        if (!markdown && this._client) {
            try {
                markdown = await this._client.getHelpTopic(topic);
            } catch {
                // fall through to "no help" page
            }
        }

        if (this._panel) {
            // Re-use panel — update content and title
            this._panel.title = `Strata: ${integrationName}`;
            this._panel.webview.html = this._buildHtml(integrationName, markdown);
            this._panel.reveal(vscode.ViewColumn.Beside);
            this._currentTopic = topic;
            return;
        }

        this._panel = vscode.window.createWebviewPanel(
            'strataIntegrationHelp',
            `Strata: ${integrationName}`,
            vscode.ViewColumn.Beside,
            {
                enableScripts: false,  // static content — no scripts needed
                retainContextWhenHidden: true,
            },
        );

        this._panel.webview.html = this._buildHtml(integrationName, markdown);
        this._currentTopic = topic;

        this._panel.onDidDispose(() => {
            this._panel = undefined;
            this._currentTopic = undefined;
        });
    }

    // ── HTML builder ──────────────────────────────────────────────────────────

    private _buildHtml(name: string, markdown: string | undefined): string {
        const body = markdown
            ? mdToHtml(markdown)
            : `<p class="no-help">No help available for <strong>${this._esc(name)}</strong>.</p>
               <p>Run <code>strata help --list</code> to see all available topics,
               or check the <a href="https://strata.huybrechts.xyz">online docs</a>.</p>`;

        return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none';">
<title>Strata: ${this._esc(name)}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--vscode-font-family, system-ui, sans-serif);
    font-size: var(--vscode-font-size, 13px);
    line-height: 1.6;
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
    padding: 24px 28px;
    max-width: 720px;
  }

  h1 { font-size: 1.4em; font-weight: 700; margin-bottom: 16px; border-bottom: 1px solid var(--vscode-panel-border, #444); padding-bottom: 8px; }
  h2 { font-size: 1.15em; font-weight: 600; margin: 20px 0 8px; color: var(--vscode-editor-foreground); }
  h3 { font-size: 1.05em; font-weight: 600; margin: 16px 0 6px; }
  h4 { font-size: 0.95em; font-weight: 600; margin: 12px 0 4px; color: var(--vscode-descriptionForeground); }

  p { margin: 6px 0 10px; color: var(--vscode-editor-foreground); }

  code {
    font-family: var(--vscode-editor-font-family, 'Courier New', monospace);
    font-size: 0.9em;
    background: var(--vscode-textCodeBlock-background, rgba(128,128,128,.15));
    padding: 1px 5px;
    border-radius: 3px;
  }

  pre {
    background: var(--vscode-textCodeBlock-background, rgba(128,128,128,.15));
    border: 1px solid var(--vscode-panel-border, #444);
    border-radius: 4px;
    padding: 12px 14px;
    overflow-x: auto;
    margin: 10px 0 14px;
  }
  pre code {
    background: none;
    padding: 0;
    font-size: 0.88em;
    white-space: pre;
  }

  ul {
    padding-left: 20px;
    margin: 6px 0 12px;
  }
  ul li { margin: 3px 0; }

  table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0 14px;
    font-size: 0.92em;
  }
  th, td {
    border: 1px solid var(--vscode-panel-border, #444);
    padding: 5px 10px;
    text-align: left;
  }
  th {
    background: var(--vscode-sideBar-background, rgba(128,128,128,.1));
    font-weight: 600;
  }

  a {
    color: var(--vscode-textLink-foreground, #3794ff);
    text-decoration: none;
  }
  a:hover { text-decoration: underline; }

  .no-help {
    color: var(--vscode-descriptionForeground);
    font-style: italic;
  }

  strong { font-weight: 600; }
  em { font-style: italic; }
</style>
</head>
<body>
${body}
</body>
</html>`;
    }

    private _esc(s: string): string {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}
