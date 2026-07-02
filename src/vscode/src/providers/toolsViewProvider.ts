/**
 * ToolsViewProvider — "Tools" pane in the Strata activity bar.
 *
 * Shows integration/tool availability from `strata sln status`:
 *
 *   terraform    1.9.2   ✅
 *   helm         3.15.0  ✅
 *   docker               ❌ not found
 *   kubectl              ❌ not found
 */

import * as vscode from 'vscode';
import type { WorkspaceStatus, IntegrationInfo } from '../strataClient';

type ItemKind = 'tool' | 'loading' | 'error' | 'empty';

export class ToolTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class ToolsViewProvider implements vscode.TreeDataProvider<ToolTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<ToolTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _integrations: Record<string, IntegrationInfo> = {};
    private _error: string | undefined;
    private _loading = true;

    // ── Public API ────────────────────────────────────────────────────────────

    update(status: WorkspaceStatus): void {
        this._integrations = status.integrations;
        this._error = undefined;
        this._loading = false;
        this._onChange.fire();
    }

    setError(message: string): void {
        this._integrations = {};
        this._error = message;
        this._loading = false;
        this._onChange.fire();
    }

    setLoading(): void {
        this._loading = true;
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: ToolTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: ToolTreeItem): ToolTreeItem[] {
        if (element) return []; // tools have no children

        if (this._loading && Object.keys(this._integrations).length === 0) {
            return [this._loadingItem()];
        }
        if (this._error) {
            return [this._errorItem(this._error)];
        }

        return this._buildTools();
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildTools(): ToolTreeItem[] {
        const entries = Object.entries(this._integrations);

        if (entries.length === 0) {
            const empty = new ToolTreeItem('No integrations configured', 'empty');
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }

        // Sort: available first, then alphabetically within each group
        entries.sort(([nameA, a], [nameB, b]) => {
            if (a.available !== b.available) return a.available ? -1 : 1;
            return nameA.localeCompare(nameB);
        });

        return entries.map(([name, info]) => {
            const item = new ToolTreeItem(name, 'tool');
            item.description = info.version ?? (info.available ? '—' : 'not found');
            item.iconPath = new vscode.ThemeIcon(
                info.available ? 'pass-filled' : 'error',
                info.available
                    ? new vscode.ThemeColor('testing.iconPassed')
                    : new vscode.ThemeColor('list.errorForeground'),
            );
            item.tooltip = new vscode.MarkdownString(
                `**${name}**\n\n` +
                `${info.available ? '✅ Available' : '❌ Not found'}` +
                (info.version ? ` — v${info.version}` : '') +
                (info.info ? `\n\n${info.info}` : ''),
            );
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): ToolTreeItem {
        const item = new ToolTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errorItem(message: string): ToolTreeItem {
        const item = new ToolTreeItem('Error', 'error');
        item.description = message;
        item.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('list.errorForeground'));
        return item;
    }
}
