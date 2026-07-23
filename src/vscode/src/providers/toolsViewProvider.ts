/**
 * ToolsViewProvider — "Tools" pane in the Strata activity bar.
 *
 * Shows ALL known integrations from `strata tools status --output json`,
 * grouped into three visual tiers:
 *
 *   ✅ green  = configured in the deployment + available (version shown)
 *   ❌ red    = configured in the deployment + NOT installed (needs action)
 *   ○ gray   = not configured / not referenced (informational, no action needed)
 *
 * When an active deployment file is known the tool passes `-f <file>` to
 * `strata tools status`, which populates the `requirement` field
 * ("required" | "optional" | null) on each row.
 */

import * as vscode from 'vscode';
import type { StrataClient, ToolsStatusRow } from '../strataClient';

type ItemKind =
    | 'tool-configured-ok'       // configured (req/opt) + available
    | 'tool-configured-missing'  // configured (req/opt) + NOT available
    | 'tool-unconfigured'        // not referenced — gray
    | 'loading'
    | 'error'
    | 'empty';

export class ToolTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind | 'tool' | 'tool-unavailable',
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class ToolsViewProvider implements vscode.TreeDataProvider<ToolTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<ToolTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _rows: ToolsStatusRow[] = [];
    private _error: string | undefined;
    private _loading = true;
    private _deploymentFile: string | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /**
     * Refresh the list. Optionally pass the active deployment file so
     * `strata tools status -f <file>` can populate the `requirement` field.
     */
    refresh(deploymentFile?: string): void {
        this._deploymentFile = deploymentFile;
        this._loading = true;
        this._onChange.fire();
        void this._load();
    }

    setError(message: string): void {
        this._rows = [];
        this._error = message;
        this._loading = false;
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
        if (element) return [];

        if (this._loading && this._rows.length === 0) {
            return [this._loadingItem()];
        }
        if (this._error) {
            return [this._errorItem(this._error)];
        }

        return this._buildTools();
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private async _load(): Promise<void> {
        if (!this._client) return;
        try {
            this._rows = await this._client.getToolsStatus(this._deploymentFile);
            this._error = undefined;
        } catch (err) {
            this._error = err instanceof Error ? err.message : String(err);
            this._rows = [];
        } finally {
            this._loading = false;
            this._onChange.fire();
        }
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildTools(): ToolTreeItem[] {
        if (this._rows.length === 0) {
            const empty = new ToolTreeItem('No tools detected', 'empty');
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }

        // Sort: tier 0 (configured+ok) → tier 1 (configured+missing) → tier 2 (unconfigured)
        const tier = (r: ToolsStatusRow): number => {
            const configured = r.requirement != null;
            if (configured && r.available) return 0;
            if (configured && !r.available) return 1;
            return 2;
        };

        const sorted = [...this._rows].sort((a, b) => {
            const td = tier(a) - tier(b);
            if (td !== 0) return td;
            return a.name.localeCompare(b.name);
        });

        return sorted.map(row => this._rowToItem(row));
    }

    private _rowToItem(row: ToolsStatusRow): ToolTreeItem {
        const configured = row.requirement != null;
        const reqLabel = row.requirement === 'required'
            ? '  required'
            : row.requirement === 'optional'
                ? '  optional'
                : '';

        // ── Tier 0: configured + available ─────────────────────────────────────
        if (configured && row.available) {
            const item = new ToolTreeItem(row.name, 'tool-configured-ok');
            item.description = (row.version ?? '—') + reqLabel;
            item.iconPath = new vscode.ThemeIcon('pass-filled',
                new vscode.ThemeColor('testing.iconPassed'));
            item.tooltip = this._tooltip(row, '✅ Configured & available');
            return item;
        }

        // ── Tier 1: configured + NOT available (needs action) ───────────────────
        if (configured && !row.available) {
            const item = new ToolTreeItem(row.name, 'tool-configured-missing');
            const severity = row.requirement === 'required'
                ? new vscode.ThemeColor('list.errorForeground')
                : new vscode.ThemeColor('list.warningForeground');
            const icon = row.requirement === 'required' ? 'error' : 'warning';
            item.description = 'not found' + reqLabel;
            item.iconPath = new vscode.ThemeIcon(icon, severity);
            item.tooltip = this._tooltip(row,
                row.requirement === 'required'
                    ? '❌ Required — install this tool'
                    : '⚠️ Optional — some features unavailable');
            return item;
        }

        // ── Tier 2: not configured (gray, informational) ───────────────────────
        const item = new ToolTreeItem(row.name, 'tool-unconfigured');
        item.description = row.version ?? '';
        item.iconPath = new vscode.ThemeIcon(
            row.available ? 'circle-filled' : 'circle-outline',
            new vscode.ThemeColor('disabledForeground'),
        );
        item.tooltip = this._tooltip(row,
            row.available ? 'Available but not configured' : 'Not installed — not required');
        return item;
    }

    private _tooltip(row: ToolsStatusRow, status: string): vscode.MarkdownString {
        const caps = row.capabilities?.length
            ? `\n\nCapabilities: ${row.capabilities.join(', ')}`
            : '';
        const req = row.requirement
            ? `\n\nDeployment requirement: **${row.requirement}**`
            : '';
        return new vscode.MarkdownString(
            `**${row.name}**  \`${row.command ?? row.name}\`\n\n${status}` +
            (row.version ? ` \u2014 v${row.version}` : '') +
            caps + req +
            `\n\n*Click \$(book) to open setup guide*`,
        );
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private _loadingItem(): ToolTreeItem {
        const item = new ToolTreeItem('Loading\u2026', 'loading');
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
