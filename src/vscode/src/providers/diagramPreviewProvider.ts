/**
 * DiagramPreviewProvider — webview panel rendering `kind: diagram` definitions
 * (ADR-0034, Phase 1 "preview pane" + Phase 2 "workspace connection").
 *
 * Unlike the old `dependencyGraphProvider.ts` (removed — it re-implemented
 * `@repo/path` scanning in TypeScript), this provider does no graph-building of
 * its own. It is a thin renderer around `strata diagram show`/`diagram resolve`:
 * the CLI is the single source of truth for what nodes/edges exist and what a
 * node's `strata://` identity resolves to (see ADR-0015's `GraphController` and
 * ADR-0034's "Node Identity & Click Resolution").
 *
 * Click-to-open flow:
 *   1. `strata diagram show` emits `click <id> "strata://..."` directives
 *      straight into the generated Mermaid text — the durable identity travels
 *      with the diagram, not with this provider's in-memory state.
 *   2. The webview overrides `window.open` (which is what Mermaid calls under
 *      `securityLevel: 'loose'` for a bare-URL `click` directive) to intercept
 *      any `strata://` URL and post it back to the extension host instead of
 *      letting the browser try to navigate to an unknown scheme.
 *   3. The extension host resolves the URI via `strata diagram resolve` and
 *      opens the file at the returned line.
 *
 * Live preview: `show()` records the CLI-resolved `definition` path (the exact
 * file `diagram show` read), so callers can re-render on save without needing
 * their own notion of "which diagram is currently open".
 */

import * as path from 'path';
import * as vscode from 'vscode';
import type { StrataClient, DiagramResolveLocation } from '../strataClient';

interface WebviewMessage {
    type: 'nodeClick';
    uri: string;
}

export class DiagramPreviewProvider implements vscode.Disposable {
    static readonly viewType = 'strataDiagramPreview';

    private _panel: vscode.WebviewPanel | undefined;
    private _disposables: vscode.Disposable[] = [];
    private _client: StrataClient | undefined;
    private _current: { nameOrPath: string; entry?: string } | undefined;
    /** Absolute path the CLI actually rendered (`data.definition`) — used to match live-preview saves. */
    private _currentDefinitionPath: string | undefined;

    /**
     * Show (or re-use) the diagram panel for `nameOrPath` — a built-in name
     * ("refs", "topology"), a name under `.strata/diagrams/`, or a path to a
     * `kind: diagram` YAML file. `entry` scopes graph-backed sources to a
     * single deployment.
     */
    async show(client: StrataClient, nameOrPath: string, entry?: string): Promise<void> {
        this._client = client;
        this._current = { nameOrPath, entry };

        if (!this._panel) {
            this._panel = vscode.window.createWebviewPanel(
                DiagramPreviewProvider.viewType,
                'Strata: Diagram',
                vscode.ViewColumn.Beside,
                { enableScripts: true, retainContextWhenHidden: true },
            );
            this._panel.onDidDispose(() => {
                this._panel = undefined;
                this._current = undefined;
                this._currentDefinitionPath = undefined;
            }, null, this._disposables);
            this._panel.webview.onDidReceiveMessage(
                (msg: WebviewMessage) => void this._onMessage(msg),
                null,
                this._disposables,
            );
        } else {
            this._panel.reveal(vscode.ViewColumn.Beside, true);
        }

        await this.refresh();
    }

    /** Re-run `strata diagram show` for the currently displayed diagram. No-op if nothing is open. */
    async refresh(): Promise<void> {
        if (!this._panel || !this._client || !this._current) return;

        this._panel.webview.html = this._statusHtml('Rendering…');
        try {
            const data = await this._client.showDiagram(this._current.nameOrPath, this._current.entry);
            this._currentDefinitionPath = data.definition;
            this._panel.title = `Strata: ${data.diagram}`;
            this._panel.webview.html = this._html(data.mermaid);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this._panel.webview.html = this._statusHtml(`Failed to render diagram:\n${msg}`, true);
        }
    }

    /**
     * True when the currently open panel was rendered from `absPath` — used by
     * the extension's save listener to decide whether a saved
     * `.strata/diagrams/*.yaml` file should trigger a live re-render.
     */
    isPreviewing(absPath: string): boolean {
        if (!this._currentDefinitionPath) return false;
        return path.resolve(this._currentDefinitionPath) === path.resolve(absPath);
    }

    get isOpen(): boolean {
        return this._panel !== undefined;
    }

    dispose(): void {
        this._panel?.dispose();
        this._disposables.forEach((d) => d.dispose());
        this._disposables = [];
    }

    // ── Click resolution ─────────────────────────────────────────────────────

    private async _onMessage(msg: WebviewMessage): Promise<void> {
        if (msg.type !== 'nodeClick' || !this._client) return;

        let location: DiagramResolveLocation | null;
        try {
            location = await this._client.resolveDiagramUri(msg.uri);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            void vscode.window.showWarningMessage(`Strata: could not resolve ${msg.uri} (${message})`);
            return;
        }
        if (!location) {
            void vscode.window.showWarningMessage(`Strata: ${msg.uri} does not resolve to anything in this workspace.`);
            return;
        }

        const absPath = path.isAbsolute(location.file)
            ? location.file
            : path.join(this._client.getWorkPath(), location.file);

        let document: vscode.TextDocument;
        try {
            document = await vscode.workspace.openTextDocument(vscode.Uri.file(absPath));
        } catch {
            void vscode.window.showWarningMessage(`Strata: ${location.file} does not exist.`);
            return;
        }

        const editor = await vscode.window.showTextDocument(document, vscode.ViewColumn.One, false);
        if (location.line) {
            const lineIndex = Math.max(0, location.line - 1);
            const range = editor.document.lineAt(Math.min(lineIndex, editor.document.lineCount - 1)).range;
            editor.selection = new vscode.Selection(range.start, range.start);
            editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
        }
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    private _html(mermaidSource: string): string {
        const escapedMermaid = mermaidSource
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none';
               script-src 'unsafe-inline' https://cdn.jsdelivr.net;
               style-src 'unsafe-inline';
               img-src data:;
               connect-src https://cdn.jsdelivr.net;">
<title>Strata Diagram</title>
<style>
    body {
        margin: 0;
        padding: 16px;
        background: var(--vscode-editor-background, #1e1e1e);
        color: var(--vscode-editor-foreground, #d4d4d4);
        font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
    }
    .subtitle {
        font-size: 12px;
        color: var(--vscode-descriptionForeground, #888);
        margin-bottom: 12px;
    }
    #graph svg { max-width: 100%; height: auto; }
    .node, a { cursor: pointer !important; }
</style>
</head>
<body>
<div class="subtitle">Click a node to open the workspace object it represents.</div>
<div id="graph">
    <pre class="mermaid">${escapedMermaid}</pre>
</div>

<script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

    const vscode = acquireVsCodeApi();

    // Mermaid's 'click <id> "strata://..."' directives call window.open() under
    // securityLevel: 'loose'. Intercept strata:// URLs here instead of letting
    // the webview try (and fail) to navigate to an unknown scheme — see
    // "Node Identity & Click Resolution" in ADR-0034.
    const nativeOpen = window.open ? window.open.bind(window) : null;
    window.open = function (url, ...rest) {
        if (typeof url === 'string' && url.startsWith('strata://')) {
            vscode.postMessage({ type: 'nodeClick', uri: url });
            return null;
        }
        return nativeOpen ? nativeOpen(url, ...rest) : null;
    };

    const style = getComputedStyle(document.body);
    const cssVar = (name, fallback) => style.getPropertyValue(name)?.trim() || fallback;

    mermaid.initialize({
        startOnLoad: true,
        theme: 'base',
        themeVariables: {
            fontFamily: cssVar('--vscode-font-family', 'Segoe UI, sans-serif'),
            background: cssVar('--vscode-editor-background', '#1e1e1e'),
            primaryColor: cssVar('--vscode-button-background', '#3794ff'),
            primaryTextColor: cssVar('--vscode-editor-foreground', '#d4d4d4'),
            primaryBorderColor: cssVar('--vscode-panel-border', '#454545'),
            lineColor: cssVar('--vscode-descriptionForeground', '#888888'),
            textColor: cssVar('--vscode-editor-foreground', '#d4d4d4'),
        },
        flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
        securityLevel: 'loose',
    });
</script>
</body>
</html>`;
    }

    private _statusHtml(message: string, isError = false): string {
        const colour = isError ? 'var(--vscode-errorForeground, #f48771)' : 'var(--vscode-descriptionForeground, #888)';
        const escaped = message.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
    body {
        display: flex;
        justify-content: center;
        align-items: center;
        height: 100vh;
        margin: 0;
        background: var(--vscode-editor-background, #1e1e1e);
        color: ${colour};
        font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
        font-size: 13px;
        white-space: pre-wrap;
        text-align: center;
        padding: 24px;
        box-sizing: border-box;
    }
</style>
</head>
<body><p>${escaped}</p></body>
</html>`;
    }
}
