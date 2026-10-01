/**
 * graphViewProvider.ts — "Graph" tree view: every cross-document reference
 * in the solution, from `strata graph --output json`.
 *
 * Deliberately a thin renderer. Everything about *what* references what —
 * including whether a reference resolves — is computed once, server-side,
 * by `graph_controller.build_reference_graph()`; this provider only groups
 * the resulting edges by source document and draws them. Re-deriving any of
 * that client-side (re-parsing YAML, re-walking reference fields) would be
 * exactly the drift v1's own `dependencyGraphProvider.ts` was replaced for —
 * see that CLI's CHANGELOG entry on the diagram preview pane.
 */

import * as path from 'path';
import * as vscode from 'vscode';
import { GraphEdge, StrataCLIError, StrataCLINotFoundError, StrataClient } from '../strataClient';

export type GraphItem = DocumentItem | EdgeItem;

export class DocumentItem extends vscode.TreeItem {
    constructor(
        public readonly key: string,
        public readonly edges: GraphEdge[],
        root: string | undefined,
    ) {
        super(key, vscode.TreeItemCollapsibleState.Collapsed);
        this.contextValue = 'strataGraphDocument';
        this.iconPath = new vscode.ThemeIcon('file-code');
        const sourcePath = edges[0]?.source_path;
        if (sourcePath) {
            const resolved = root ? path.resolve(root, sourcePath) : sourcePath;
            this.resourceUri = vscode.Uri.file(resolved);
            this.command = { command: 'vscode.open', title: 'Open', arguments: [this.resourceUri] };
            this.tooltip = resolved;
        }
    }
}

export class EdgeItem extends vscode.TreeItem {
    constructor(
        public readonly edge: GraphEdge,
        root: string | undefined,
    ) {
        super(`${edge.location} \u2192 ${edge.target_kind}/${edge.target_name}`, vscode.TreeItemCollapsibleState.None);
        this.contextValue = 'strataGraphEdge';
        this.iconPath = new vscode.ThemeIcon(edge.resolved ? 'arrow-right' : 'warning');
        if (!edge.resolved) {
            this.description = 'unresolved';
            this.tooltip = `'${edge.target_name}' does not match any ${edge.target_kind} document`;
        }
        const resolved = root ? path.resolve(root, edge.source_path) : edge.source_path;
        this.command = {
            command: 'strata.openGraphLocation',
            title: 'Open Reference Location',
            arguments: [vscode.Uri.file(resolved), edge.location],
        };
    }
}

export class GraphViewProvider implements vscode.TreeDataProvider<GraphItem>, vscode.Disposable {
    private readonly _onDidChangeTreeData = new vscode.EventEmitter<GraphItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private _client: StrataClient | undefined;
    private _edges: GraphEdge[] = [];
    private _root: string | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Re-run `strata graph` and refresh the view. */
    async refresh(): Promise<void> {
        if (!this._client) return;
        try {
            const envelope = await this._client.graph();
            this._edges = envelope.data.edges ?? [];
            this._root = envelope.context.root;
        } catch (err) {
            this._edges = [];
            this._root = undefined;
            const message =
                err instanceof StrataCLINotFoundError
                    ? err.message
                    : err instanceof StrataCLIError
                        ? (err.stderr || err.message)
                        : String(err);
            void vscode.window.showWarningMessage(`Strata: graph failed to run — ${message}`);
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: GraphItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: GraphItem): GraphItem[] {
        if (!element) {
            return this._groupedBySource().map(([key, edges]) => new DocumentItem(key, edges, this._root));
        }
        if (element instanceof DocumentItem) {
            return element.edges.map((edge) => new EdgeItem(edge, this._root));
        }
        return [];
    }

    dispose(): void {
        this._onDidChangeTreeData.dispose();
    }

    private _groupedBySource(): [string, GraphEdge[]][] {
        const bySource = new Map<string, GraphEdge[]>();
        for (const edge of this._edges) {
            const key = `${edge.source_kind}/${edge.source_name}`;
            if (!bySource.has(key)) bySource.set(key, []);
            bySource.get(key)!.push(edge);
        }
        return [...bySource.entries()].sort(([a], [b]) => a.localeCompare(b));
    }
}
