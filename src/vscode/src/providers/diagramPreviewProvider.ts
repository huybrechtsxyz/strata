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

/** Regex source `click <id> "strata://..."` directives the CLI embeds — captures the node id and its URI. */
const CLICK_DIRECTIVE_RE = /^\s*click\s+(\S+)\s+"(strata:\/\/[^"]+)"/gm;

/** Regex source `<id>["label text"]` node definitions the CLI templates emit. */
const NODE_LABEL_RE = /^\s*(\w+)\["((?:[^"\\]|\\.)*)"\]/gm;

/** A diagram with more nodes than this skips reverse-index building (one `strata diagram resolve` CLI call per node — bounded to avoid hammering the system on very large workspaces). */
const MAX_REVERSE_INDEX_NODES = 150;

/** Undo strata.utils.design_tokens.mermaid_escape() so a node's rendered SVG text can be matched back to its raw label. */
function unescapeMermaidLabel(label: string): string {
    return label
        .replace(/<br\/>/g, '\n')
        .replace(/#quot;/g, '"')
        .replace(/#lt;/g, '<')
        .replace(/#gt;/g, '>');
}

export class DiagramPreviewProvider implements vscode.Disposable {
    static readonly viewType = 'strataDiagramPreview';

    private _panel: vscode.WebviewPanel | undefined;
    private _disposables: vscode.Disposable[] = [];
    private _client: StrataClient | undefined;
    private _current: { nameOrPath: string; entry?: string } | undefined;
    /** Absolute path the CLI actually rendered (`data.definition`) — used to match live-preview saves. */
    private _currentDefinitionPath: string | undefined;
    /** `"<relFile>"` or `"<relFile>:<line>"` → node label, for the reverse cursor→node lookup. Rebuilt on every refresh(). */
    private _reverseIndex: Map<string, string> = new Map();

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
                this._reverseIndex = new Map();
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
        this._reverseIndex = new Map();
        try {
            const data = await this._client.showDiagram(this._current.nameOrPath, this._current.entry);
            this._currentDefinitionPath = data.definition;
            this._panel.title = `Strata: ${data.diagram}`;
            this._panel.webview.html = this._html(data.mermaid);
            void this._buildReverseIndex(data.mermaid);
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

    /**
     * Reverse cursor→node lookup (ADR-0034 "Node Identity & Click Resolution",
     * "workspace → diagram" direction). Call whenever the active editor's
     * cursor moves; posts a highlight message to the webview when the cursor's
     * file+line corresponds to a node in the currently displayed diagram, or
     * clears any existing highlight otherwise. No-op if no diagram is open or
     * the reverse index hasn't finished building yet.
     */
    notifyCursor(absPath: string, line1Based: number): void {
        if (!this._panel || !this._client || this._reverseIndex.size === 0) return;
        const relFile = path.relative(this._client.getWorkPath(), absPath).replace(/\\/g, '/');
        const label = this._reverseIndex.get(`${relFile}:${line1Based}`) ?? this._reverseIndex.get(relFile) ?? null;
        void this._panel.webview.postMessage({ type: 'highlight', label });
    }

    dispose(): void {
        this._panel?.dispose();
        this._disposables.forEach((d) => d.dispose());
        this._disposables = [];
    }

    // ── Reverse index (cursor → node) ────────────────────────────────────────

    /**
     * Parse every `click <id> "strata://..."` directive and `<id>["label"]`
     * definition out of the rendered Mermaid text, resolve each URI once (in
     * parallel — this is the one place multiple `strata diagram resolve`
     * calls happen per render) and index the results by file (and file+line,
     * when the URI resolves to a specific position) so `notifyCursor()` is a
     * synchronous map lookup, not a CLI call, on every cursor move.
     */
    private async _buildReverseIndex(mermaidSource: string): Promise<void> {
        const client = this._client;
        if (!client) return;

        const clicks = [...mermaidSource.matchAll(CLICK_DIRECTIVE_RE)];
        if (clicks.length === 0 || clicks.length > MAX_REVERSE_INDEX_NODES) return;

        const labelById = new Map<string, string>();
        for (const m of mermaidSource.matchAll(NODE_LABEL_RE)) {
            labelById.set(m[1], unescapeMermaidLabel(m[2]));
        }

        const index = new Map<string, string>();
        const results = await Promise.allSettled(clicks.map((m) => client.resolveDiagramUri(m[2])));
        results.forEach((result, i) => {
            if (result.status !== 'fulfilled' || !result.value) return;
            const location = result.value;
            const label = labelById.get(clicks[i][1]);
            if (!label) return;
            const key = location.line ? `${location.file}:${location.line}` : location.file;
            index.set(key, label);
        });

        // A refresh() may have started a newer render while this was in flight.
        if (this._panel) this._reverseIndex = index;
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
        // Passed through JSON.stringify (not manual escaping) so the webview
        // script gets the exact source string back, including the CLI's
        // literal 'classDef ... fill:#hex,stroke:#hex' lines that the inline
        // themeClassDefs() rewrites at render time (see below). '<' is escaped
        // to \u003c so a stray '</script>'-like substring in the Mermaid text
        // can never prematurely close this tag.
        const mermaidJson = JSON.stringify(mermaidSource).replace(/</g, '\\u003c');

        // The webview's own JS is built with String.raw and plain string
        // concatenation (no nested template literals) rather than embedded
        // directly in the outer template literal below. A regular (non-raw)
        // template literal evaluates escape sequences in its *own* text before
        // this string is ever used as HTML — '\s', '\w', '\S' etc. are not
        // recognized JS escapes, so the backslash is silently dropped (e.g.
        // '\s' becomes just 's'), corrupting any regex written directly in
        // the outer literal with NO compiler error. This only shows up at
        // runtime, not by reading the compiled .js. String.raw sidesteps it
        // by keeping backslashes verbatim — but it does *not* protect literal
        // backticks or '${', so this block avoids both entirely.
        const moduleScript = String.raw`
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

    const vscode = acquireVsCodeApi();

    // Mermaid's 'click <id> "strata://..."' directives call window.open() under
    // securityLevel: 'loose'. Intercept strata:// URLs here instead of letting
    // the webview try (and fail) to navigate to an unknown scheme — see
    // "Node Identity & Click Resolution" in ADR-0034.
    const nativeOpen = window.open ? window.open.bind(window) : null;
    window.open = function (url) {
        if (typeof url === 'string' && url.indexOf('strata://') === 0) {
            vscode.postMessage({ type: 'nodeClick', uri: url });
            return null;
        }
        return nativeOpen ? nativeOpen.apply(window, arguments) : null;
    };

    const bodyStyle = getComputedStyle(document.body);
    function cssVar(name, fallback) {
        const value = bodyStyle.getPropertyValue(name);
        return (value && value.trim()) || fallback;
    }

    // Re-theme the CLI's hardcoded classDef colors (light-theme pastels meant
    // for GitHub/Mermaid Live) to the active VS Code theme — "Design System —
    // Status Colors, Icons & Theme Integration" in ADR-0034. strata's every
    // design-system token (src/strata/utils/design_tokens.py DESIGN_TOKENS —
    // validity, severity, policy, lock, health, outcome, taxonomy, ~40 names
    // total) resolves to just 10 distinct (fill, stroke) hex pairs, so matching
    // on the hex pair itself (rather than hand-maintaining all ~40 token names
    // here) themes every current and future token automatically. An unknown
    // hex pair (a diagram author's own custom classDef) is left exactly as
    // authored — safe, visible fallback.
    const HEX_PAIR_TO_VAR = {
        '#e2e3e5,#6c757d': '--vscode-descriptionForeground', // grey (neutral/info/unknown/audit/expired/...)
        '#dbeafe,#2563eb': '--vscode-charts-blue',            // blue (low/locked/live/enabled/resource/...)
        '#fff3cd,#ffc107': '--vscode-charts-yellow',          // amber (invalid/medium/warn/held/degraded/...)
        '#ffe5d0,#fd7e14': '--vscode-charts-orange',          // orange (high/drifting/...)
        '#f8d7da,#dc3545': '--vscode-charts-red',             // red (missing/critical/deny/failing/dangling/...)
        '#d4edda,#28a745': '--vscode-charts-green',           // green (valid/unlocked/passing/success/...)
        '#f5f5f5,#adb5bd': '--vscode-descriptionForeground',  // orphan grey (dashed)
        '#fef3c7,#d97706': '--vscode-charts-orange',          // module taxonomy brown/amber
        '#d1fae5,#059669': '--vscode-charts-green',           // namespace taxonomy green
        '#e0e7ff,#4f46e5': '--vscode-charts-purple',          // network taxonomy indigo
    };

    function themeClassDefs(source) {
        const fg = cssVar('--vscode-editor-foreground', '#d4d4d4');
        const bg = cssVar('--vscode-editor-background', '#1e1e1e');
        return source.replace(
            /^(\s*classDef\s+\S+\s+)fill:(#[0-9a-fA-F]{3,8}),stroke:(#[0-9a-fA-F]{3,8})(.*)$/gim,
            function (full, prefix, fill, stroke, rest) {
                const key = fill.toLowerCase() + ',' + stroke.toLowerCase();
                const varName = HEX_PAIR_TO_VAR[key];
                if (!varName) return full;
                const themedStroke = cssVar(varName, stroke);
                return prefix + 'fill:' + bg + ',stroke:' + themedStroke + ',color:' + fg + rest;
            },
        );
    }

    // Which classDef names this specific diagram actually declares — used by
    // the hover tooltip below to tell "a strata status/kind class" (show it)
    // apart from Mermaid's own built-in node classes ('node', 'default', ...).
    function definedClassNames(source) {
        const names = new Set();
        const re = /^\s*classDef\s+(\S+)\s+/gm;
        let m;
        while ((m = re.exec(source)) !== null) names.add(m[1]);
        return names;
    }

    const rawSource = JSON.parse(document.getElementById('mermaid-source').textContent);
    const knownClassNames = definedClassNames(rawSource);
    document.getElementById('mermaid-pre').textContent = themeClassDefs(rawSource);

    mermaid.initialize({
        startOnLoad: false,
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

    await mermaid.run({ querySelector: '#mermaid-pre' });

    // ── Hover tooltips ────────────────────────────────────────────────────────
    // "Hover tooltips from DiagramNodeData.tooltip" (ADR-0034) — the CLI emits
    // one Mermaid string, not per-node metadata, so this reads back the same
    // signal Mermaid itself relies on for coloring: the classDef name(s) a
    // node carries via ':::name'. No extra CLI round-trip needed.
    const graphEl = document.getElementById('graph');
    const tooltipEl = document.getElementById('strata-tooltip');

    graphEl.addEventListener('mousemove', function (e) {
        const nodeEl = e.target.closest ? e.target.closest('.node') : null;
        if (!nodeEl) {
            tooltipEl.style.display = 'none';
            return;
        }
        const classes = Array.prototype.slice.call(nodeEl.classList);
        const statusClass = classes.filter(function (c) { return knownClassNames.has(c); })[0];
        const hasLink = !!nodeEl.querySelector('a');
        const lines = [];
        if (statusClass) lines.push('Status: ' + statusClass);
        if (hasLink) lines.push('Click to open');
        if (lines.length === 0) {
            tooltipEl.style.display = 'none';
            return;
        }
        tooltipEl.textContent = lines.join(' \u00b7 ');
        tooltipEl.style.display = 'block';
        tooltipEl.style.left = (e.clientX + 12) + 'px';
        tooltipEl.style.top = (e.clientY + 12) + 'px';
    });
    graphEl.addEventListener('mouseleave', function () { tooltipEl.style.display = 'none'; });

    // ── Reverse lookup: cursor in YAML → highlight matching node ──────────────
    // The extension host resolves every node's 'strata://' URI once per render
    // (via 'strata diagram resolve') and, on cursor move, posts back the LABEL
    // of whichever node corresponds to the cursor's file+line — see
    // DiagramPreviewProvider._buildReverseIndex(). Matching by rendered label
    // text (not Mermaid's internal node id) avoids depending on Mermaid's
    // version-specific DOM id scheme.
    let highlighted = null;
    window.addEventListener('message', function (event) {
        const msg = event.data;
        if (!msg || msg.type !== 'highlight') return;
        if (highlighted) {
            highlighted.classList.remove('strata-highlight');
            highlighted = null;
        }
        if (!msg.label) return;
        const nodes = graphEl.querySelectorAll('.node');
        for (let i = 0; i < nodes.length; i++) {
            if (nodes[i].textContent.trim() === msg.label) {
                nodes[i].classList.add('strata-highlight');
                highlighted = nodes[i];
                break;
            }
        }
    });
`;

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
    .node.strata-highlight > * {
        filter: drop-shadow(0 0 4px var(--vscode-focusBorder, #007fd4));
        stroke-width: 3px !important;
    }
    #strata-tooltip {
        position: fixed;
        z-index: 1000;
        display: none;
        max-width: 320px;
        padding: 4px 8px;
        font-size: 12px;
        line-height: 1.4;
        pointer-events: none;
        background: var(--vscode-editorHoverWidget-background, #252526);
        color: var(--vscode-editorHoverWidget-foreground, #ccc);
        border: 1px solid var(--vscode-editorHoverWidget-border, #454545);
        border-radius: 3px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
    }
</style>
</head>
<body>
<div class="subtitle">Click a node to open the workspace object it represents.</div>
<div id="graph">
    <pre class="mermaid" id="mermaid-pre"></pre>
</div>
<div id="strata-tooltip"></div>
<script id="mermaid-source" type="application/json">${mermaidJson}</script>

<script type="module">${moduleScript}</script>
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
