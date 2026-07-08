/**
 * AuditViewProvider — "Audit Trail" pane in the Strata activity bar.
 *
 * Shows recent deploy-log entries from `strata audit changes --output json`.
 * Each entry expands to show per-stage results with success/failure icons,
 * provisioner type, and duration.
 *
 *   ✅  xyz_platform_prd   2026-07-01 14:32   164.0s   prd
 *        ✓  infrastructure   terraform   120.0s
 *        ✓  platform          helm         60.0s
 *   ❌  xyz_platform_stg   2026-06-30 10:15    42.3s   stg
 *        ✓  infrastructure   terraform    35.0s
 *        ✗  services          helm          7.3s
 *
 * Clicking an entry opens the deployment YAML file.
 * Context menu: Resend to sinks, Export.
 * Title bar: Refresh, Export all.
 */

import * as vscode from 'vscode';
import type { StrataClient, AuditEntry, AuditStage } from '../strataClient';

type ItemKind = 'entry' | 'stage' | 'step' | 'pr' | 'loading' | 'error' | 'empty';

export class AuditTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly entryData?: AuditEntry,
        public readonly stageData?: AuditStage,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class AuditViewProvider
    implements vscode.TreeDataProvider<AuditTreeItem>, vscode.Disposable {
    private readonly _onChange =
        new vscode.EventEmitter<AuditTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _entries: AuditEntry[] = [];
    private _error: string | undefined;
    private _loading = false;
    private _loaded = false;
    /** How many entries to fetch. Adjustable via `strata.auditSetLimit`. */
    private _limit = 20;
    /** Status filter: undefined = all, true = success only, false = failures only */
    private _filterSuccess: boolean | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Trigger a background data refresh. */
    refresh(): void {
        void this._load();
    }

    /** Signal loading state while a workspace refresh is in flight. */
    setLoading(): void {
        this._loading = true;
        this._onChange.fire();
    }

    /** Called when workspace-level getStatus() fails. */
    setError(message: string): void {
        this._error = message;
        this._loading = false;
        this._onChange.fire();
    }

    /** Cycle through filter states: all → success only → failures only → all */
    cycleFilter(): void {
        if (this._filterSuccess === undefined) {
            this._filterSuccess = true;
        } else if (this._filterSuccess === true) {
            this._filterSuccess = false;
        } else {
            this._filterSuccess = undefined;
        }
        void this._load();
    }

    /** Set limit from the command palette */
    setLimit(limit: number): void {
        this._limit = Math.max(5, Math.min(200, limit));
        void this._load();
    }

    get filterLabel(): string {
        if (this._filterSuccess === true) return 'Success only';
        if (this._filterSuccess === false) return 'Failures only';
        return 'All entries';
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: AuditTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: AuditTreeItem): AuditTreeItem[] {
        // Stage children of an entry
        if (element?.kind === 'entry' && element.entryData) {
            return this._buildEntryChildren(element.entryData);
        }
        // Step children of a stage
        if (element?.kind === 'stage' && element.stageData) {
            return this._buildSteps(element.stageData);
        }
        if (element) return [];

        // Root — lazy-load on first render
        if (!this._loaded && !this._loading) {
            void this._load();
            return [this._loadingItem()];
        }
        if (this._loading) {
            return [this._loadingItem()];
        }
        if (this._error) {
            return [this._errorItem(this._error)];
        }
        return this._buildEntries();
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    private async _load(): Promise<void> {
        if (!this._client) return;
        this._loading = true;
        this._error = undefined;
        this._onChange.fire();

        try {
            const all = await this._client.getAuditChanges(this._limit);
            // Apply client-side filter (CLI doesn't support --status filter yet)
            this._entries = this._filterSuccess === undefined
                ? all
                : all.filter((e) => e.success === this._filterSuccess);
            this._loading = false;
            this._loaded = true;
        } catch (err) {
            this._error = err instanceof Error ? err.message : String(err);
            this._loading = false;
            this._loaded = true;
        }

        this._onChange.fire();
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildEntries(): AuditTreeItem[] {
        if (this._entries.length === 0) {
            const item = new AuditTreeItem('No deploy-log entries found', 'empty');
            item.iconPath = new vscode.ThemeIcon('info');
            item.description = this._filterSuccess !== undefined ? `(filter: ${this.filterLabel})` : undefined;
            return [item];
        }

        // Summary header item
        const header = new AuditTreeItem(
            `${this._entries.length} entries`,
            'empty',
        );
        header.description = this.filterLabel !== 'All entries' ? this.filterLabel : `last ${this._limit}`;
        header.iconPath = new vscode.ThemeIcon('list-ordered');
        header.tooltip = `Showing ${this._entries.length} entries. Use "Strata: Set Audit Limit" or "Strata: Filter Audit" to adjust.`;

        return [header, ...this._entries.map((e) => {
            const ts = this._formatTimestamp(e.timestamp);
            const dur = this._formatDuration(e.duration_seconds);
            const label = e.deployment;

            const item = new AuditTreeItem(
                label,
                'entry',
                vscode.TreeItemCollapsibleState.Collapsed,
                e,
            );
            const env = e.environment ? `  ${e.environment}` : '';
            item.description = `${ts}  ${dur}${env}`;
            item.tooltip = this._buildEntryTooltip(e);
            item.iconPath = new vscode.ThemeIcon(
                e.success ? 'pass' : 'error',
                new vscode.ThemeColor(
                    e.success ? 'testing.iconPassed' : 'testing.iconFailed',
                ),
            );
            // Click opens the deployment file
            if (e.file) {
                item.command = {
                    command: 'strata.openFile',
                    title: 'Open File',
                    arguments: [{ filePath: e.file }],
                };
            }
            return item;
        })];
    }

    private _buildEntryChildren(entry: AuditEntry): AuditTreeItem[] {
        const items: AuditTreeItem[] = [];

        // Stage items
        for (const stage of entry.stages) {
            const dur = this._formatDuration(stage.duration_seconds);
            const prov = stage.provisioner ?? 'unknown';

            const item = new AuditTreeItem(
                stage.name,
                'stage',
                stage.steps.length > 0
                    ? vscode.TreeItemCollapsibleState.Collapsed
                    : vscode.TreeItemCollapsibleState.None,
                undefined,
                stage,
            );
            item.description = `${prov}  ${dur}`;
            item.tooltip = `${stage.name} — ${prov}\n${stage.started_at} → ${stage.completed_at}`;
            item.iconPath = new vscode.ThemeIcon(
                stage.success ? 'check' : 'close',
                new vscode.ThemeColor(
                    stage.success ? 'testing.iconPassed' : 'testing.iconFailed',
                ),
            );
            items.push(item);
        }

        // PR enrichment — show as a single info item if present
        if (entry.pull_request) {
            const pr = entry.pull_request;
            const prItem = new AuditTreeItem(
                `PR #${pr.number}: ${pr.title}`,
                'pr',
            );
            prItem.description = pr.author ? `by ${pr.author}` : '';
            prItem.tooltip = pr.url;
            prItem.iconPath = new vscode.ThemeIcon('git-pull-request');
            if (pr.url) {
                prItem.command = {
                    command: 'vscode.open',
                    title: 'Open PR',
                    arguments: [vscode.Uri.parse(pr.url)],
                };
            }
            items.push(prItem);
        }

        // Commit SHA — show as info item if present
        if (entry.commit_sha) {
            const commitItem = new AuditTreeItem(
                entry.commit_sha.substring(0, 8),
                'step',
            );
            commitItem.description = 'commit';
            commitItem.iconPath = new vscode.ThemeIcon('git-commit');
            items.push(commitItem);
        }

        return items;
    }

    private _buildSteps(stage: AuditStage): AuditTreeItem[] {
        return stage.steps.map((s) => {
            const dur = this._formatDuration(s.duration_seconds);
            const item = new AuditTreeItem(s.step, 'step');
            item.description = dur;
            item.iconPath = new vscode.ThemeIcon(
                s.success ? 'check' : 'close',
                new vscode.ThemeColor(
                    s.success ? 'testing.iconPassed' : 'testing.iconFailed',
                ),
            );
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): AuditTreeItem {
        const item = new AuditTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('loading~spin');
        return item;
    }

    private _errorItem(message: string): AuditTreeItem {
        const item = new AuditTreeItem(message, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }

    private _formatTimestamp(ts: string): string {
        // "2026-06-24T14:32:00Z" → "2026-06-24 14:32"
        return ts.replace('T', ' ').replace(/:\d{2}Z$/, '').replace(/:\d{2}\+.*$/, '');
    }

    private _formatDuration(seconds: number): string {
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        const min = Math.floor(seconds / 60);
        const sec = seconds % 60;
        return `${min}m${sec.toFixed(0)}s`;
    }

    private _buildEntryTooltip(e: AuditEntry): string {
        const lines = [
            `Deployment: ${e.deployment}`,
            `File: ${e.file}`,
            `Time: ${e.timestamp}`,
            `Duration: ${this._formatDuration(e.duration_seconds)}`,
            `Success: ${e.success ? 'yes' : 'no'}`,
            `Stages: ${e.stages.length}`,
        ];
        if (e.environment) lines.push(`Environment: ${e.environment}`);
        if (e.commit_sha) lines.push(`Commit: ${e.commit_sha.substring(0, 8)}`);
        if (e.version) lines.push(`CLI: ${e.version}`);
        return lines.join('\n');
    }
}
