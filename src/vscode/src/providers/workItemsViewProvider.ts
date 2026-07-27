import * as vscode from 'vscode';
import type { StrataClient, WorkItemSummary } from '../strataClient';

type ItemKind = 'section' | 'workitem' | 'empty' | 'loading' | 'error';

/** Default auto-poll interval in milliseconds. Configurable via strata.workItemPollIntervalSeconds. */
const DEFAULT_POLL_INTERVAL_MS = 60_000;

/** Minimum time between loads triggered by visibility changes (debounce). */
const VISIBILITY_DEBOUNCE_MS = 5_000;

const TYPE_ICONS: Record<string, string> = {
    approval: 'shield',
    cost_review: 'graph',
    security_review: 'bug',
    verify: 'check-all',
    scheduled: 'clock',
    cab: 'organization',
    incident: 'warning',
    promotion_gate: 'arrow-up',
    drift_decision: 'diff',
    rollback: 'history',
};

export class WorkItemTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        public readonly workItem?: WorkItemSummary,
        collapsible = vscode.TreeItemCollapsibleState.None,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class WorkItemsViewProvider implements vscode.TreeDataProvider<WorkItemTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<WorkItemTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _items: WorkItemSummary[] = [];
    private _loading = false;
    private _error: string | undefined;
    private _workPath: string | undefined;
    private _pollTimer: ReturnType<typeof setInterval> | undefined;
    private _lastLoadAt = 0;
    private _treeView: vscode.TreeView<WorkItemTreeItem> | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
        this._startPolling();
    }

    setWorkPath(workPath: string): void { this._workPath = workPath; }

    /**
     * Register the tree view so the provider can update its badge and react to
     * visibility changes. Call this from extension.ts after createTreeView.
     */
    register(treeView: vscode.TreeView<WorkItemTreeItem>): void {
        this._treeView = treeView;
        // Refresh when the panel becomes visible (e.g. user switches to the Strata sidebar)
        treeView.onDidChangeVisibility(e => {
            if (e.visible) {
                const age = Date.now() - this._lastLoadAt;
                if (age > VISIBILITY_DEBOUNCE_MS) {
                    this.refresh();
                }
            }
        });
    }

    refresh(): void {
        this._loading = true;
        this._onChange.fire();
        void this._load();
    }

    dispose(): void {
        this._stopPolling();
        this._onChange.dispose();
    }

    getTreeItem(element: WorkItemTreeItem): vscode.TreeItem { return element; }

    getChildren(element?: WorkItemTreeItem): WorkItemTreeItem[] {
        if (element) {
            return [];
        }

        // Root level
        if (this._loading && this._items.length === 0) {
            const l = new WorkItemTreeItem('Loading work items\u2026', 'loading');
            l.iconPath = new vscode.ThemeIcon('loading~spin');
            return [l];
        }
        if (this._error) {
            const e = new WorkItemTreeItem(this._error, 'error');
            e.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('list.errorForeground'));
            return [e];
        }

        const pending = this._items.filter(i => i.status === 'pending');
        if (pending.length === 0) {
            const empty = new WorkItemTreeItem('No pending work items', 'empty');
            empty.iconPath = new vscode.ThemeIcon('check', new vscode.ThemeColor('testing.iconPassed'));
            return [empty];
        }

        return pending.map(i => this._buildItem(i));
    }

    private _buildItem(item: WorkItemSummary): WorkItemTreeItem {
        const shortId = item.id.includes('/') ? item.id.split('/').slice(1).join('/') : item.id;
        const deployName = item.deployment.split('/').pop()?.replace('.yaml', '') ?? item.deployment;
        const treeItem = new WorkItemTreeItem(shortId, 'workitem', item);

        treeItem.description = deployName;
        treeItem.tooltip = new vscode.MarkdownString(
            `**${this._labelForType(item.type)}**\n\n` +
            `Deployment: ${item.deployment}\n\n` +
            `Created: ${item.created_at.slice(0, 19).replace('T', ' ')} UTC\n\n` +
            `By: ${item.created_by}` +
            (item.expires_at ? `\n\nExpires: ${item.expires_at.slice(0, 19).replace('T', ' ')} UTC` : ''),
        );
        treeItem.iconPath = new vscode.ThemeIcon(
            TYPE_ICONS[item.type] ?? 'circle-filled',
            new vscode.ThemeColor('charts.yellow'),
        );
        treeItem.contextValue = 'workitem-pending';
        treeItem.command = {
            command: 'strata.showWorkItem',
            title: 'Show Work Item',
            arguments: [item.id],
        };
        return treeItem;
    }

    private _labelForType(type: string): string {
        return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // ------------------------------------------------------------------
    // Polling
    // ------------------------------------------------------------------

    private _pollIntervalMs(): number {
        const cfg = vscode.workspace.getConfiguration('strata');
        const seconds = cfg.get<number>('workItemPollIntervalSeconds', 60);
        return Math.max(10, seconds) * 1000;
    }

    private _startPolling(): void {
        this._stopPolling();
        const interval = this._pollIntervalMs();
        this._pollTimer = setInterval(() => { void this._load(); }, interval);
        // Initial load immediately
        void this._load();
    }

    private _stopPolling(): void {
        if (this._pollTimer !== undefined) {
            clearInterval(this._pollTimer);
            this._pollTimer = undefined;
        }
    }

    // ------------------------------------------------------------------
    // Data loading + badge update
    // ------------------------------------------------------------------

    private async _load(): Promise<void> {
        if (!this._client) {
            this._loading = false;
            this._onChange.fire();
            return;
        }
        try {
            this._items = await this._client.listWorkItems();
            this._error = undefined;
        } catch (err) {
            this._error = err instanceof Error ? err.message : String(err);
        } finally {
            this._loading = false;
            this._lastLoadAt = Date.now();
            this._onChange.fire();
            this._updateBadge();
        }
    }

    private _updateBadge(): void {
        if (!this._treeView) return;
        const pendingCount = this._items.filter(i => i.status === 'pending').length;
        this._treeView.badge = pendingCount > 0
            ? { tooltip: `${pendingCount} pending work item${pendingCount === 1 ? '' : 's'}`, value: pendingCount }
            : undefined;
    }
}
