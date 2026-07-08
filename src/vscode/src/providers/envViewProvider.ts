/**
 * EnvViewProvider — "Environments" pane in the Strata activity bar.
 *
 * Shows deployment manifests found in the workspace with their cached
 * build output status, drift badges, and lock indicators.
 *
 *   🔒  deploy_prd    2/2 cached   ⚠ drift
 *        ✓  infrastructure   2026-07-01 10:23  3 outputs
 *        ✓  services          2026-07-01 10:25  1 output
 *   ⚠   deploy_stg    1/2 cached
 *        ✓  infra             2026-07-01 09:10  3 outputs
 *        ○  services          no cache
 *
 * Clicking a deployment opens the manifest file.
 * Context menu: Show Status, Detect Drift, Deploy Stage (stage items).
 * Refresh button in the view title triggers a fresh offline scan.
 */

import * as vscode from 'vscode';
import type { StrataClient, EnvDeploymentStatus } from '../strataClient';

type ItemKind = 'deployment' | 'stage' | 'lock' | 'drift' | 'loading' | 'error' | 'empty';

export class EnvTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly filePath?: string,
        public readonly deploymentData?: EnvDeploymentStatus,
        public readonly stageName?: string,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
        if (kind === 'deployment' && filePath) {
            this.command = {
                command: 'strata.openFile',
                title: 'Open File',
                arguments: [{ filePath }],
            };
        }
    }
}

export class EnvViewProvider
    implements vscode.TreeDataProvider<EnvTreeItem>, vscode.Disposable {
    private readonly _onChange =
        new vscode.EventEmitter<EnvTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _deployments: EnvDeploymentStatus[] = [];
    private _error: string | undefined;
    private _loading = false;
    private _loaded = false;
    /** Map of filePath → lock status text for display */
    private _locks = new Map<string, { locked: boolean; holder: string | null }>();
    /** Map of filePath → drift detected flag */
    private _drifts = new Map<string, boolean>();

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Called by extension _refreshAll() — triggers a background data refresh. */
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

    dispose(): void {
        this._onChange.dispose();
    }

    /**
     * Run `strata deploy lock status` for a deployment and update the badge.
     * Called from the extension command `strata.lockStatus`.
     */
    async refreshLock(filePath: string): Promise<void> {
        if (!this._client) return;
        try {
            const lock = await this._client.getLockStatus(filePath);
            this._locks.set(filePath, { locked: lock.locked, holder: lock.holder });
            this._onChange.fire();
        } catch {
            // ignore — badge stays as-is
        }
    }

    /**
     * Mark a deployment as drifted (called after `strata env drift` terminates).
     */
    markDrift(filePath: string, drifted: boolean): void {
        this._drifts.set(filePath, drifted);
        this._onChange.fire();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: EnvTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: EnvTreeItem): EnvTreeItem[] {
        // Stage children
        if (element?.kind === 'deployment' && element.deploymentData) {
            return this._buildStages(element.deploymentData, element.filePath);
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
        return this._buildDeployments();
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    private async _load(): Promise<void> {
        if (!this._client) return;
        this._loading = true;
        this._error = undefined;
        this._onChange.fire();

        try {
            const data = await this._client.getEnvStatus();
            this._deployments = data.deployments;
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

    private _buildDeployments(): EnvTreeItem[] {
        if (this._deployments.length === 0) {
            const item = new EnvTreeItem('No deployment manifests found', 'empty');
            item.iconPath = new vscode.ThemeIcon('info');
            return [item];
        }

        return this._deployments.map((d) => {
            const label = d.name || d.file.split(/[\\/]/).pop() || d.file;
            const allCached = d.stage_count > 0 && d.cached_count === d.stage_count;
            const someCached = d.cached_count > 0 && !allCached;
            const lock = this._locks.get(d.file);
            const drifted = this._drifts.get(d.file) === true;

            const item = new EnvTreeItem(
                label,
                'deployment',
                vscode.TreeItemCollapsibleState.Collapsed,
                d.file,
                d,
            );

            const lockSuffix = lock?.locked ? '  🔒' : '';
            const driftSuffix = drifted ? '  ⚠ drift' : '';
            item.description = `${d.cached_count}/${d.stage_count} cached${lockSuffix}${driftSuffix}`;
            item.tooltip = new vscode.MarkdownString(
                `**${label}**\n\n` +
                `File: \`${d.file}\`\n\n` +
                `Cache: ${d.cached_count}/${d.stage_count} stages` +
                (lock?.locked ? `\n\n🔒 Locked by: ${lock.holder ?? 'unknown'}` : '') +
                (drifted ? '\n\n⚠️ Drift detected' : ''),
            );
            item.iconPath = new vscode.ThemeIcon(
                lock?.locked ? 'lock' : allCached ? 'pass' : someCached ? 'warning' : 'circle-slash',
                new vscode.ThemeColor(
                    lock?.locked
                        ? 'list.warningForeground'
                        : allCached
                            ? 'testing.iconPassed'
                            : someCached
                                ? 'list.warningForeground'
                                : 'disabledForeground',
                ),
            );
            return item;
        });
    }

    private _buildStages(deployment: EnvDeploymentStatus, filePath?: string): EnvTreeItem[] {
        if (deployment.stages.length === 0) {
            const item = new EnvTreeItem('(no stages defined)', 'stage');
            item.iconPath = new vscode.ThemeIcon('dash');
            return [item];
        }

        return deployment.stages.map((s) => {
            const item = new EnvTreeItem(s.name, 'stage', vscode.TreeItemCollapsibleState.None, filePath, deployment, s.name);
            if (s.cached && s.cache) {
                const ts = s.cache.refreshed_at ?? 'unknown';
                const n = s.cache.output_count ?? 0;
                item.description = `${ts}  ${n} output${n !== 1 ? 's' : ''}`;
                item.tooltip = `${s.provisioner} — last cached ${ts}\n\nRight-click to deploy this stage`;
                item.iconPath = new vscode.ThemeIcon(
                    'check',
                    new vscode.ThemeColor('testing.iconPassed'),
                );
            } else {
                item.description = 'no cache';
                item.tooltip = `${s.provisioner} — not yet deployed\n\nRight-click to deploy this stage`;
                item.iconPath = new vscode.ThemeIcon(
                    'circle-outline',
                    new vscode.ThemeColor('disabledForeground'),
                );
            }
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): EnvTreeItem {
        const item = new EnvTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('loading~spin');
        return item;
    }

    private _errorItem(message: string): EnvTreeItem {
        const item = new EnvTreeItem(message, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }
}


