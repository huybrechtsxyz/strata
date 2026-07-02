/**
 * DependencyGraphProvider — webview panel showing how strata documents
 * reference each other via `@repo/path.yaml` cross-references.
 *
 * Nodes = strata YAML documents (coloured by kind).
 * Edges = `@repo/path` references found inside each file.
 *
 * Rendered as a Mermaid flowchart inside a webview panel.
 * Clicking a node opens the corresponding file in the editor.
 *
 * Data sources:
 *   - `profiles.paths` from `sln status` → gives all known documents with kind + path
 *   - Text scan of each file for `@repo_name/…yaml` patterns → gives edges
 *   - `repositories[]` from `sln status` → maps @repo to filesystem path for resolution
 */

import * as path from 'path';
import * as vscode from 'vscode';
import type { RepositoryInfo, WorkspaceStatus } from '../strataClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GraphNode {
    id: string;          // sanitised identifier for Mermaid
    label: string;       // display label (meta.name or filename)
    kind: string;        // strata document kind
    filePath: string;    // absolute filesystem path
}

interface GraphEdge {
    from: string;        // source node id
    to: string;          // target node id
}

// ---------------------------------------------------------------------------
// Mermaid colour palette per kind
// ---------------------------------------------------------------------------

const KIND_COLOURS: Record<string, string> = {
    configuration: '#4A90D9',
    deployment: '#E74C3C',
    workspace: '#2ECC71',
    environment: '#F39C12',
    module: '#9B59B6',
    namespace: '#1ABC9C',
    provider: '#3498DB',
    resource: '#E67E22',
    network: '#16A085',
    firewall: '#C0392B',
    dns: '#2980B9',
    tenant: '#8E44AD',
};

// Regex for @repo/path cross-references
const REF_RE = /@([a-zA-Z0-9_-]+)\/([\w./%-]+\.yaml)\b/g;

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class DependencyGraphProvider implements vscode.Disposable {

    private _panel: vscode.WebviewPanel | undefined;
    private _disposables: vscode.Disposable[] = [];

    // ── Public API ────────────────────────────────────────────────────────────

    async show(status?: WorkspaceStatus): Promise<void> {
        if (this._panel) {
            this._panel.reveal(vscode.ViewColumn.One);
        } else {
            this._panel = vscode.window.createWebviewPanel(
                'strataDependencyGraph',
                'Strata: Dependency Graph',
                vscode.ViewColumn.One,
                {
                    enableScripts: true,
                    retainContextWhenHidden: true,
                },
            );

            this._panel.onDidDispose(() => {
                this._panel = undefined;
            }, null, this._disposables);

            // Handle messages from webview (node clicks → open file)
            this._panel.webview.onDidReceiveMessage(
                (msg: { command: string; filePath?: string }) => {
                    if (msg.command === 'openFile' && msg.filePath) {
                        void vscode.window.showTextDocument(
                            vscode.Uri.file(msg.filePath),
                        );
                    }
                },
                null,
                this._disposables,
            );
        }

        if (status) {
            await this._render(status);
        } else {
            this._panel.webview.html = this._emptyHtml();
        }
    }

    async update(status: WorkspaceStatus): Promise<void> {
        if (this._panel) {
            await this._render(status);
        }
    }

    dispose(): void {
        this._panel?.dispose();
        this._disposables.forEach((d) => d.dispose());
        this._disposables = [];
    }

    // ── Graph building ────────────────────────────────────────────────────────

    private async _buildGraph(status: WorkspaceStatus): Promise<{ nodes: GraphNode[]; edges: GraphEdge[] }> {
        const nodes: GraphNode[] = [];
        const nodeByPath = new Map<string, GraphNode>();
        const edges: GraphEdge[] = [];

        // Build node list from profiles.paths
        const paths = status.profiles.paths;
        let counter = 0;
        for (const [kind, files] of Object.entries(paths)) {
            for (const file of files) {
                counter++;
                const id = `n${counter}`;
                const node: GraphNode = {
                    id,
                    label: file.name,
                    kind,
                    filePath: file.path,
                };
                nodes.push(node);
                // Index by normalised path for edge resolution
                nodeByPath.set(this._normPath(file.path), node);
            }
        }

        // Build repo map for resolving @repo/ references
        const repoMap = new Map<string, string>();
        for (const repo of (status.repositories ?? [])) {
            if (repo.path) {
                repoMap.set(repo.name, repo.path);
            }
        }

        // Scan each file for @repo/path references → edges
        for (const node of nodes) {
            try {
                const doc = await vscode.workspace.openTextDocument(
                    vscode.Uri.file(node.filePath),
                );
                const text = doc.getText();
                const re = new RegExp(REF_RE.source, 'g');
                let m: RegExpExecArray | null;

                while ((m = re.exec(text)) !== null) {
                    const repoName = m[1];
                    const refPath = m[2];
                    const repoBase = repoMap.get(repoName);
                    if (!repoBase) continue;

                    const resolved = this._normPath(path.join(repoBase, refPath));
                    const target = nodeByPath.get(resolved);
                    if (target && target.id !== node.id) {
                        // Deduplicate edges
                        const exists = edges.some(
                            (e) => e.from === node.id && e.to === target.id,
                        );
                        if (!exists) {
                            edges.push({ from: node.id, to: target.id });
                        }
                    }
                }
            } catch {
                // File not readable — skip silently
            }
        }

        return { nodes, edges };
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    private async _render(status: WorkspaceStatus): Promise<void> {
        if (!this._panel) return;
        const { nodes, edges } = await this._buildGraph(status);
        this._panel.webview.html = this._html(nodes, edges);
    }

    /**
     * Build Mermaid flowchart definition + webview HTML.
     */
    private _html(nodes: GraphNode[], edges: GraphEdge[]): string {
        if (nodes.length === 0) {
            return this._emptyHtml();
        }

        // Build Mermaid definition
        const lines: string[] = ['graph LR'];

        // Node definitions — use round-edge rectangles with kind prefix
        for (const n of nodes) {
            const escapedLabel = n.label.replace(/"/g, '#quot;');
            lines.push(`    ${n.id}["${n.kind}: ${escapedLabel}"]`);
        }

        // Edges
        for (const e of edges) {
            lines.push(`    ${e.from} --> ${e.to}`);
        }

        // Style classes per kind
        const usedKinds = [...new Set(nodes.map((n) => n.kind))];
        for (const kind of usedKinds) {
            const colour = KIND_COLOURS[kind] ?? '#95A5A6';
            const kindNodes = nodes.filter((n) => n.kind === kind).map((n) => n.id);
            lines.push(`    classDef cls_${kind} fill:${colour},stroke:#333,stroke-width:1px,color:#fff`);
            lines.push(`    class ${kindNodes.join(',')} cls_${kind}`);
        }

        // Click handlers — each node calls the vscode API
        for (const n of nodes) {
            lines.push(`    click ${n.id} callback "${n.id}"`);
        }

        const mermaidDef = lines.join('\n');

        // Node map for click handler resolution
        const nodeMap = Object.fromEntries(
            nodes.map((n) => [n.id, { filePath: n.filePath, label: n.label }]),
        );

        return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'none';
                   script-src 'unsafe-inline' https://cdn.jsdelivr.net;
                   style-src 'unsafe-inline';
                   img-src data:;">
    <title>Strata Dependency Graph</title>
    <style>
        body {
            margin: 0;
            padding: 16px;
            background: var(--vscode-editor-background, #1e1e1e);
            color: var(--vscode-editor-foreground, #d4d4d4);
            font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
            display: flex;
            flex-direction: column;
            height: 100vh;
            box-sizing: border-box;
        }
        h2 {
            margin: 0 0 4px 0;
            font-size: 14px;
            font-weight: 600;
            color: var(--vscode-foreground, #ccc);
        }
        .subtitle {
            font-size: 12px;
            color: var(--vscode-descriptionForeground, #888);
            margin-bottom: 16px;
        }
        .legend {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 12px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            color: var(--vscode-descriptionForeground, #888);
        }
        .legend-swatch {
            width: 12px;
            height: 12px;
            border-radius: 2px;
        }
        #graph {
            flex: 1;
            overflow: auto;
        }
        #graph svg {
            max-width: 100%;
            height: auto;
        }
        .node { cursor: pointer !important; }
    </style>
</head>
<body>
    <h2>Dependency Graph</h2>
    <div class="subtitle">${nodes.length} document${nodes.length !== 1 ? 's' : ''}, ${edges.length} reference${edges.length !== 1 ? 's' : ''} — click a node to open the file</div>

    <div class="legend">
        ${usedKinds.map((k) => `<span class="legend-item"><span class="legend-swatch" style="background:${KIND_COLOURS[k] ?? '#95A5A6'}"></span>${k}</span>`).join('\n        ')}
    </div>

    <div id="graph">
        <pre class="mermaid">
${mermaidDef}
        </pre>
    </div>

    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

        const vscode = acquireVsCodeApi();
        const nodeMap = ${JSON.stringify(nodeMap)};

        // Register click callback — Mermaid calls window.callback(nodeId)
        window.callback = function(nodeId) {
            const node = nodeMap[nodeId];
            if (node) {
                vscode.postMessage({ command: 'openFile', filePath: node.filePath });
            }
        };

        mermaid.initialize({
            startOnLoad: true,
            theme: 'dark',
            flowchart: {
                useMaxWidth: true,
                htmlLabels: true,
                curve: 'basis',
                rankSpacing: 60,
                nodeSpacing: 30,
            },
            securityLevel: 'loose',   // required for click callbacks
        });
    </script>
</body>
</html>`;
    }

    private _emptyHtml(): string {
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
            color: var(--vscode-descriptionForeground, #888);
            font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
            font-size: 14px;
        }
    </style>
</head>
<body>
    <p>No workspace data available. Run <strong>Strata: Refresh</strong> first.</p>
</body>
</html>`;
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /** Normalise a path for comparison (lowercase on Windows, forward slashes). */
    private _normPath(p: string): string {
        const normalised = path.resolve(p).replace(/\\/g, '/');
        return process.platform === 'win32' ? normalised.toLowerCase() : normalised;
    }
}
