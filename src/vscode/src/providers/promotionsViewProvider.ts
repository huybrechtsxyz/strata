/**
 * PromotionsViewProvider — "Promotions" pane in the Strata activity bar.
 *
 * Shows promotion strategies, rings with current versions, in-flight
 * promotions, and recent history.
 *
 *   📋 In-Flight
 *        🔄 myapp → staging  v2.1.0
 *   🔗 Version Matrix
 *        ▶ dev        image/myapp: v2.1.0
 *        ▶ staging    image/myapp: v2.0.0
 *        ▶ prd        image/myapp: v1.9.0
 *   📜 History
 *        ✅ myapp → prd  v1.9.0  2026-07-01
 *        ✅ myapp → staging  v2.0.0  2026-06-28
 */

import * as vscode from 'vscode';
import type {
    StrataClient,
    PromotionStatusEntry,
    PromotionMatrixRing,
    PromotionHistoryEntry,
} from '../strataClient';

type ItemKind =
    | 'section'
    | 'inflight'
    | 'ring'
    | 'version-pin'
    | 'history-entry'
    | 'loading'
    | 'error'
    | 'empty';

export class PromotionTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly data?: PromotionStatusEntry | PromotionMatrixRing | PromotionHistoryEntry,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class PromotionsViewProvider
    implements vscode.TreeDataProvider<PromotionTreeItem>, vscode.Disposable {
    private readonly _onChange =
        new vscode.EventEmitter<PromotionTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _inflight: PromotionStatusEntry[] = [];
    private _matrix: PromotionMatrixRing[] = [];
    private _history: PromotionHistoryEntry[] = [];
    private _error: string | undefined;
    private _loading = false;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    refresh(): void {
        this._loading = true;
        this._error = undefined;
        this._onChange.fire();
        void this._load();
    }

    setLoading(): void {
        this._loading = true;
        this._onChange.fire();
    }

    setError(message: string): void {
        this._error = message;
        this._loading = false;
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── TreeDataProvider ──────────────────────────────────────────────────────

    getTreeItem(element: PromotionTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: PromotionTreeItem): PromotionTreeItem[] {
        if (this._loading && !element) {
            return [new PromotionTreeItem('Loading…', 'loading')];
        }
        if (this._error && !element) {
            return [new PromotionTreeItem(`⚠ ${this._error}`, 'error')];
        }

        // Root level — show 3 sections
        if (!element) {
            return this._getRootItems();
        }

        // Section children
        if (element.kind === 'section') {
            const label = element.label as string;
            if (label.includes('In-Flight')) return this._getInflightItems();
            if (label.includes('Version Matrix')) return this._getMatrixItems();
            if (label.includes('History')) return this._getHistoryItems();
        }

        // Ring children (version pins)
        if (element.kind === 'ring' && element.data) {
            return this._getRingPins(element.data as PromotionMatrixRing);
        }

        return [];
    }

    // ── Private ───────────────────────────────────────────────────────────────

    private _getRootItems(): PromotionTreeItem[] {
        const items: PromotionTreeItem[] = [];

        // In-Flight section
        const inflightLabel = this._inflight.length > 0
            ? `📋 In-Flight (${this._inflight.length})`
            : '📋 In-Flight';
        items.push(new PromotionTreeItem(
            inflightLabel,
            'section',
            this._inflight.length > 0
                ? vscode.TreeItemCollapsibleState.Expanded
                : vscode.TreeItemCollapsibleState.Collapsed,
        ));

        // Version Matrix section
        items.push(new PromotionTreeItem(
            '🔗 Version Matrix',
            'section',
            vscode.TreeItemCollapsibleState.Expanded,
        ));

        // History section
        items.push(new PromotionTreeItem(
            '📜 History',
            'section',
            vscode.TreeItemCollapsibleState.Collapsed,
        ));

        return items;
    }

    private _getInflightItems(): PromotionTreeItem[] {
        if (this._inflight.length === 0) {
            return [new PromotionTreeItem('No in-flight promotions', 'empty')];
        }
        return this._inflight.map((p) => {
            const icon = p.status === 'in-progress' ? '🔄' : p.status === 'completed' ? '✅' : '⏪';
            const item = new PromotionTreeItem(
                `${icon} ${p.target} → ${p.ring}  ${p.version}`,
                'inflight',
                vscode.TreeItemCollapsibleState.None,
                p,
            );
            item.tooltip = `Strategy: ${p.strategy}\nProgression: ${p.progression}\nBranch: ${p.branch ?? 'n/a'}\nEvents: ${p.event_count}`;
            return item;
        });
    }

    private _getMatrixItems(): PromotionTreeItem[] {
        if (this._matrix.length === 0) {
            return [new PromotionTreeItem('No rings configured', 'empty')];
        }
        return this._matrix.map((ring) => {
            const pinCount = Object.keys(ring.versions).length;
            const envList = ring.environments.length > 0
                ? ` (${ring.environments.join(', ')})`
                : '';
            const item = new PromotionTreeItem(
                `${ring.ring}${envList}`,
                'ring',
                pinCount > 0
                    ? vscode.TreeItemCollapsibleState.Collapsed
                    : vscode.TreeItemCollapsibleState.None,
                ring,
            );
            item.tooltip = `Environments: ${ring.environments.join(', ') || 'none'}\nRequires: ${ring.require ?? 'nothing'}\nPins: ${pinCount}`;
            item.iconPath = new vscode.ThemeIcon('layers');
            return item;
        });
    }

    private _getRingPins(ring: PromotionMatrixRing): PromotionTreeItem[] {
        const entries = Object.entries(ring.versions);
        if (entries.length === 0) {
            return [new PromotionTreeItem('(no pins)', 'empty')];
        }
        return entries.map(([target, version]) => {
            const item = new PromotionTreeItem(
                `${target}: ${version}`,
                'version-pin',
            );
            item.iconPath = new vscode.ThemeIcon('tag');
            return item;
        });
    }

    private _getHistoryItems(): PromotionTreeItem[] {
        if (this._history.length === 0) {
            return [new PromotionTreeItem('No promotion history', 'empty')];
        }
        return this._history.map((h) => {
            const icon = h.outcome === 'success' ? '✅' : h.outcome === 'rolled_back' ? '⏪' : '❌';
            const date = h.started_at ? h.started_at.slice(0, 10) : '?';
            const item = new PromotionTreeItem(
                `${icon} ${h.target} → ${h.ring ?? '?'}  ${h.to_version ?? '?'}  ${date}`,
                'history-entry',
                vscode.TreeItemCollapsibleState.None,
                h,
            );
            item.tooltip = `From: ${h.from_version ?? '?'}\nTo: ${h.to_version ?? '?'}\nBy: ${h.initiated_by ?? '?'}\nOutcome: ${h.outcome ?? '?'}`;
            return item;
        });
    }

    private async _load(): Promise<void> {
        if (!this._client) {
            this._error = 'No client configured';
            this._loading = false;
            this._onChange.fire();
            return;
        }

        try {
            const [inflight, matrix, history] = await Promise.all([
                this._client.getPromotionStatus(),
                this._client.getPromotionMatrix(),
                this._client.getPromotionHistory(undefined, 10),
            ]);
            this._inflight = inflight;
            this._matrix = matrix.rings;
            this._history = history;
            this._error = undefined;
        } catch (err) {
            this._error = err instanceof Error ? err.message : String(err);
        } finally {
            this._loading = false;
            this._onChange.fire();
        }
    }
}
