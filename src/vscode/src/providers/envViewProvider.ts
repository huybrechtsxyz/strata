/**
 * EnvViewProvider — "Environments" pane in the Strata activity bar.
 *
 * Shows deployment manifests found in the workspace with their cached
 * build output status, health badges, drift indicators, and lock status.
 *
 *   🟢  deploy_prd    2/2 cached
 *        ▶  infrastructure   2026-07-01 10:23  3 outputs
 *              db_host:  sql.azure.com
 *              db_name:  mydb
 *              api_key:  *** (sensitive)
 *        ▶  services   2026-07-01 10:25  1 output
 *        📋 History
 *              ✅ deploy run  2026-07-01 14:32
 *              ❌ deploy run  2026-06-25 17:41
 *
 * Clicking a deployment opens the manifest file.
 * Stage items expand to show Terraform output key→value pairs (lazy loaded).
 * History section shows last 10 deploy operations (lazy loaded).
 */

import * as vscode from 'vscode';
import type {
    StrataClient,
    EnvDeploymentStatus,
    DeployHealthData,
    DeployHistoryEntry,
} from '../strataClient';

type ItemKind =
    | 'deployment'
    | 'stage'
    | 'output'
    | 'history-section'
    | 'history-entry'
    | 'loading'
    | 'error'
    | 'empty';

export class EnvTreeItem extends vscode.TreeItem {
    /** Populated for `output` items */
    outputKey?: string;
    outputValue?: string | null;
    /** Populated for `history-entry` items */
    historyEntry?: DeployHistoryEntry;

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

    /** filePath → lock state */
    private _locks = new Map<string, { locked: boolean; holder: string | null }>();
    /** filePath → drift detected */
    private _drifts = new Map<string, boolean>();
    /** filePath → health data */
    private _health = new Map<string, DeployHealthData>();
    /** `${filePath}::${stageName}` → loaded outputs (Record<key, value|null>) */
    private _outputs = new Map<string, Record<string, string | null>>();
    /** Stage keys currently loading outputs */
    private _outputsLoading = new Set<string>();
    /** filePath → history entries */
    private _history = new Map<string, DeployHistoryEntry[]>();
    /** FilePaths currently loading history */
    private _historyLoading = new Set<string>();

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    refresh(): void {
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

    async refreshLock(filePath: string): Promise<void> {
        if (!this._client) return;
        try {
            const lock = await this._client.getLockStatus(filePath);
            this._locks.set(filePath, { locked: lock.locked, holder: lock.holder });
            this._onChange.fire();
        } catch { /* badge stays as-is */ }
    }

    markDrift(filePath: string, drifted: boolean): void {
        this._drifts.set(filePath, drifted);
        this._onChange.fire();
    }

    /** Called after `deploy health` completes — updates the deployment row badge. */
    updateHealth(filePath: string, health: DeployHealthData): void {
        this._health.set(filePath, health);
        this._onChange.fire();
    }

    /** Evict cached outputs/history for a deployment so they reload on next expand. */
    invalidateDeployment(filePath: string): void {
        for (const key of [...this._outputs.keys()]) {
            if (key.startsWith(filePath)) this._outputs.delete(key);
        }
        this._history.delete(filePath);
        this._onChange.fire();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: EnvTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: EnvTreeItem): EnvTreeItem[] {
        // ── Outputs for a cached stage ──────────────────────────────────────
        if (element?.kind === 'stage' && element.filePath && element.stageName) {
            const stageData = element.deploymentData?.stages.find(s => s.name === element.stageName);
            if (stageData?.cache?.output_count) {
                return this._getOutputItems(element.filePath, element.stageName);
            }
            return [];
        }

        // ── History section children ────────────────────────────────────────
        if (element?.kind === 'history-section' && element.filePath) {
            return this._getHistoryItems(element.filePath);
        }

        // ── Deployment children (stages + history) ──────────────────────────
        if (element?.kind === 'deployment' && element.deploymentData) {
            return this._buildDeploymentChildren(element.deploymentData, element.filePath);
        }

        if (element) return [];

        // ── Root ────────────────────────────────────────────────────────────
        if (!this._loaded && !this._loading) {
            void this._load();
            return [this._loadingItem()];
        }
        if (this._loading) return [this._loadingItem()];
        if (this._error) return [this._errorItem(this._error)];
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
            const health = this._health.get(d.file);

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

            let healthLine = '';
            if (health && health.summary !== 'no_checks_defined') {
                healthLine = health.summary.failed === 0
                    ? `\n\n🟢 Health: ${health.summary.passed}/${health.summary.total_stages} stages passing`
                    : `\n\n🔴 Health: ${health.summary.failed} stage(s) failing`;
            }
            item.tooltip = new vscode.MarkdownString(
                `**${label}**\n\n` +
                `File: \`${d.file}\`\n\n` +
                `Cache: ${d.cached_count}/${d.stage_count} stages` +
                (lock?.locked ? `\n\n🔒 Locked by: ${lock.holder ?? 'unknown'}` : '') +
                (drifted ? '\n\n⚠️ Drift detected' : '') +
                healthLine,
            );

            // Icon: health > lock > cache state
            let iconName: string;
            let iconColor: string;
            if (health && health.summary !== 'no_checks_defined') {
                iconName = health.summary.failed === 0 ? 'pass-filled' : 'error';
                iconColor = health.summary.failed === 0 ? 'testing.iconPassed' : 'testing.iconFailed';
            } else if (lock?.locked) {
                iconName = 'lock';
                iconColor = 'list.warningForeground';
            } else if (allCached) {
                iconName = 'pass';
                iconColor = 'testing.iconPassed';
            } else if (someCached) {
                iconName = 'warning';
                iconColor = 'list.warningForeground';
            } else {
                iconName = 'circle-slash';
                iconColor = 'disabledForeground';
            }
            item.iconPath = new vscode.ThemeIcon(iconName, new vscode.ThemeColor(iconColor));
            return item;
        });
    }

    private _buildDeploymentChildren(deployment: EnvDeploymentStatus, filePath?: string): EnvTreeItem[] {
        const stages = this._buildStages(deployment, filePath);
        if (!filePath) return stages;

        const historySection = new EnvTreeItem(
            'History',
            'history-section',
            vscode.TreeItemCollapsibleState.Collapsed,
            filePath,
        );
        historySection.iconPath = new vscode.ThemeIcon('history');
        historySection.description = 'last 10 operations';
        historySection.tooltip = 'Recent deployment operations for this file';
        return [...stages, historySection];
    }

    private _buildStages(deployment: EnvDeploymentStatus, filePath?: string): EnvTreeItem[] {
        if (deployment.stages.length === 0) {
            const item = new EnvTreeItem('(no stages defined)', 'stage');
            item.iconPath = new vscode.ThemeIcon('dash');
            return [item];
        }
        return deployment.stages.map((s) => {
            const hasOutputs = s.cached && (s.cache?.output_count ?? 0) > 0;
            const collapsible = hasOutputs
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None;

            const item = new EnvTreeItem(s.name, 'stage', collapsible, filePath, deployment, s.name);
            if (s.cached && s.cache) {
                const ts = s.cache.refreshed_at ?? 'unknown';
                const n = s.cache.output_count ?? 0;
                item.description = `${ts}  ${n} output${n !== 1 ? 's' : ''}`;
                item.tooltip = `${s.provisioner} — last cached ${ts}` +
                    (hasOutputs ? '\n\nExpand to view outputs · Right-click to deploy this stage' : '\n\nRight-click to deploy this stage');
                item.iconPath = new vscode.ThemeIcon('check', new vscode.ThemeColor('testing.iconPassed'));
            } else {
                item.description = 'no cache';
                item.tooltip = `${s.provisioner} — not yet deployed\n\nRight-click to deploy this stage`;
                item.iconPath = new vscode.ThemeIcon('circle-outline', new vscode.ThemeColor('disabledForeground'));
            }
            return item;
        });
    }

    // ── Lazy output loading ───────────────────────────────────────────────────

    private _getOutputItems(filePath: string, stageName: string): EnvTreeItem[] {
        const key = `${filePath}::${stageName}`;
        const cached = this._outputs.get(key);
        if (cached !== undefined) {
            return this._buildOutputItems(cached);
        }
        if (this._outputsLoading.has(key)) {
            return [this._loadingItem()];
        }
        this._outputsLoading.add(key);
        void (async () => {
            try {
                const data = await this._client?.getEnvOutput(filePath);
                this._outputs.set(key, data?.stages[stageName]?.outputs ?? {});
            } catch {
                this._outputs.set(key, {});
            } finally {
                this._outputsLoading.delete(key);
                this._onChange.fire();
            }
        })();
        return [this._loadingItem()];
    }

    private _buildOutputItems(outputs: Record<string, string | null>): EnvTreeItem[] {
        const entries = Object.entries(outputs);
        if (entries.length === 0) {
            const empty = new EnvTreeItem('(no outputs)', 'empty');
            empty.iconPath = new vscode.ThemeIcon('dash');
            return [empty];
        }
        return entries.map(([key, value]) => {
            const item = new EnvTreeItem(key, 'output');
            item.outputKey = key;
            item.outputValue = value;
            if (value === null) {
                item.description = '*** (sensitive)';
                item.iconPath = new vscode.ThemeIcon('key', new vscode.ThemeColor('list.warningForeground'));
                item.tooltip = `${key} — sensitive value not shown`;
            } else {
                item.description = value;
                item.iconPath = new vscode.ThemeIcon('symbol-string');
                item.tooltip = `${key} = ${value}\n\nClick to copy value`;
            }
            item.contextValue = 'output';
            item.command = {
                command: 'strata.copyOutputValue',
                title: 'Copy Value',
                arguments: [key, value],
            };
            return item;
        });
    }

    // ── Lazy history loading ──────────────────────────────────────────────────

    private _getHistoryItems(filePath: string): EnvTreeItem[] {
        const cached = this._history.get(filePath);
        if (cached !== undefined) {
            return this._buildHistoryItems(cached);
        }
        if (this._historyLoading.has(filePath)) {
            return [this._loadingItem()];
        }
        this._historyLoading.add(filePath);
        void (async () => {
            try {
                const data = await this._client?.getDeployHistory(filePath, 10);
                this._history.set(filePath, data?.entries ?? []);
            } catch {
                this._history.set(filePath, []);
            } finally {
                this._historyLoading.delete(filePath);
                this._onChange.fire();
            }
        })();
        return [this._loadingItem()];
    }

    private _buildHistoryItems(entries: DeployHistoryEntry[]): EnvTreeItem[] {
        if (entries.length === 0) {
            const empty = new EnvTreeItem('No history found', 'empty');
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }
        return entries.map((e) => {
            const item = new EnvTreeItem(e.operation, 'history-entry');
            item.historyEntry = e;
            item.description = e.when + (e.stage ? `  [${e.stage}]` : '');
            item.iconPath = new vscode.ThemeIcon(
                e.success ? 'pass' : 'error',
                new vscode.ThemeColor(e.success ? 'testing.iconPassed' : 'testing.iconFailed'),
            );
            item.tooltip = `${e.operation}\n${e.when}${e.stage ? `\nStage: ${e.stage}` : ''}\nExecution: ${e.execution_id}`;
            item.contextValue = 'history-entry';
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
