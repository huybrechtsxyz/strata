/**
 * OperationsViewProvider — "Operations" pane in the Strata activity bar.
 *
 * Shows runtime status for the active deployment (from DeploymentContext):
 *
 *   $(pulse) Status — deploy-prd
 *     $(check) Build:   2026-07-20 10:23   [2 stages]
 *     $(cloud-upload) Deploy: ✅ 2026-07-20 14:32
 *     $(diff) Drift: clean
 *     $(lock) Lock: unlocked
 *   $(dashboard) Cost: €4,702/month  ↑+€247
 *   ▸ Outputs (lazy)
 *       infrastructure
 *         db_host: sql.azure.com
 *   ▸ History (last 10, lazy)
 *       ✅ deploy run  2026-07-20 14:32  164s
 *       ❌ deploy run  2026-06-25 17:41   42s
 *
 * Subscribes to DeploymentContext.onDidChange and reloads on context switch.
 */

import * as vscode from 'vscode';
import type {
    StrataClient,
    EnvDeploymentStatus,
    DeployHealthData,
    DeployHistoryEntry,
    CostSnapshot,
} from '../strataClient';
import type { DeploymentContext } from './deploymentContext';

// ---------------------------------------------------------------------------
// Tree item
// ---------------------------------------------------------------------------

type OpsItemKind =
    | 'status-section'
    | 'status-build'
    | 'status-deploy'
    | 'status-drift'
    | 'status-lock'
    | 'cost-section'
    | 'outputs-section'
    | 'output-stage'
    | 'output-entry'
    | 'history-section'
    | 'history-entry'
    | 'no-deployment'
    | 'loading'
    | 'error'
    | 'empty';

export class OpsItem extends vscode.TreeItem {
    outputKey?: string;
    outputValue?: string | null;
    historyEntry?: DeployHistoryEntry;

    constructor(
        label: string,
        public readonly kind: OpsItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class OperationsViewProvider
    implements vscode.TreeDataProvider<OpsItem>, vscode.Disposable {

    private readonly _onChange =
        new vscode.EventEmitter<OpsItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _deployCtx: DeploymentContext | undefined;
    private _ctxSub: vscode.Disposable | undefined;

    // State keyed to active deployment file
    private _envStatus: EnvDeploymentStatus | undefined;
    private _health: DeployHealthData | undefined;
    private _drifted: boolean | undefined;
    private _locked: boolean | undefined;
    private _lockHolder: string | null | undefined;
    private _costSnapshot: CostSnapshot | undefined;

    // Lazy-loaded state
    private _outputs: Record<string, Record<string, string | null>> | undefined;
    private _outputsLoading = false;
    private _history: DeployHistoryEntry[] | undefined;
    private _historyLoading = false;

    private _loading = false;
    private _error: string | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void { this._client = client; }

    setDeploymentContext(ctx: DeploymentContext): void {
        this._ctxSub?.dispose();
        this._deployCtx = ctx;
        this._ctxSub = ctx.onDidChange(() => this._reset());
    }

    refresh(): void {
        this._reset();
        void this._loadStatus();
    }

    setLoading(): void {
        this._loading = true;
        this._onChange.fire();
    }

    setError(msg: string): void {
        this._error = msg;
        this._loading = false;
        this._onChange.fire();
    }

    /** Called from terminal-close handler after a successful deploy. */
    updateHealth(filePath: string, health: DeployHealthData): void {
        if (filePath !== this._deployCtx?.activeFile) return;
        this._health = health;
        this._onChange.fire();
    }

    /** Called from terminal-close handler after drift detection. */
    markDrift(filePath: string, drifted: boolean): void {
        if (filePath !== this._deployCtx?.activeFile) return;
        this._drifted = drifted;
        this._onChange.fire();
    }

    /** Called from lock command to refresh lock badge. */
    refreshLock(filePath: string): void {
        if (filePath !== this._deployCtx?.activeFile) return;
        void this._loadLock();
    }

    /** Invalidate cached data after a deploy completes. */
    invalidateDeployment(filePath: string): void {
        if (filePath !== this._deployCtx?.activeFile) return;
        this._outputs = undefined;
        this._history = undefined;
        this._envStatus = undefined;
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
        this._ctxSub?.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: OpsItem): vscode.TreeItem { return element; }

    getChildren(element?: OpsItem): vscode.ProviderResult<OpsItem[]> {
        if (!element) return this._buildRoot();
        switch (element.kind) {
            case 'status-section': return this._buildStatusChildren();
            case 'outputs-section': return this._buildOutputsChildren();
            case 'history-section': return this._buildHistoryChildren();
            case 'output-stage': return this._buildOutputStageChildren(element);
            default: return [];
        }
    }

    // ── Root ──────────────────────────────────────────────────────────────────

    private _buildRoot(): OpsItem[] {
        const activeFile = this._deployCtx?.activeFile;
        const activeName = this._deployCtx?.activeName;

        if (!activeFile) {
            const item = new OpsItem('No deployment selected', 'no-deployment');
            item.description = 'Select a deployment in the Deployments view';
            item.iconPath = new vscode.ThemeIcon('info');
            item.command = { command: 'strata.selectDeployment', title: 'Select Deployment' };
            return [item];
        }

        if (this._loading) return [this._loadingItem()];
        if (this._error) return [this._errItem(this._error)];

        const items: OpsItem[] = [];

        // Status section
        const statusSection = new OpsItem(
            `$(pulse)  Status — ${activeName}`,
            'status-section',
            vscode.TreeItemCollapsibleState.Expanded,
        );
        statusSection.iconPath = new vscode.ThemeIcon('pulse');
        items.push(statusSection);

        // Cost section
        if (this._costSnapshot !== undefined) {
            const monthly = this._costSnapshot.total_monthly;
            const currency = this._costSnapshot.currency;
            const delta = this._costSnapshot.delta_from_previous;
            const deltaStr = delta !== null && delta !== undefined
                ? delta > 0 ? `  ↑+${delta.toFixed(0)}` : delta < 0 ? `  ↓${Math.abs(delta).toFixed(0)}` : ''
                : '';
            const costItem = new OpsItem(
                `$(dashboard)  Cost: ${currency} ${monthly.toFixed(0)}/month${deltaStr}`,
                'cost-section',
            );
            costItem.iconPath = new vscode.ThemeIcon('dashboard');
            costItem.command = { command: 'strata.showCostHistory', title: 'Cost History', arguments: [activeFile] };
            items.push(costItem);
        }

        // Outputs section (lazy)
        const outputsSection = new OpsItem(
            '$(output)  Outputs',
            'outputs-section',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        outputsSection.iconPath = new vscode.ThemeIcon('output');
        items.push(outputsSection);

        // History section (lazy)
        const histSection = new OpsItem(
            '$(history)  Deploy History',
            'history-section',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        histSection.iconPath = new vscode.ThemeIcon('history');
        items.push(histSection);

        return items;
    }

    // ── Status children ───────────────────────────────────────────────────────

    private _buildStatusChildren(): OpsItem[] {
        const items: OpsItem[] = [];

        // Build
        const stages = this._envStatus?.stages ?? [];
        const cached = stages.filter(s => s.cached).length;
        const buildIcon = stages.length > 0
            ? (cached === stages.length ? '$(check)' : '$(warning)')
            : '$(dash)';
        const buildItem = new OpsItem(`${buildIcon}  Build`, 'status-build');
        buildItem.description = stages.length > 0
            ? `${cached}/${stages.length} stages cached`
            : 'not built';
        buildItem.command = { command: 'strata.buildPlan', title: 'Build Plan', arguments: [this._deployCtx?.activeFile] };
        items.push(buildItem);

        // Deploy (from health or history)
        const deployIcon = this._health
            ? (this._health.summary !== 'no_checks_defined' &&
                typeof this._health.summary === 'object' &&
                this._health.summary.failed === 0 ? '$(check)' : '$(warning)')
            : '$(dash)';
        const deployItem = new OpsItem(`${deployIcon}  Health`, 'status-deploy');
        deployItem.description = this._health
            ? (this._health.summary === 'no_checks_defined'
                ? 'no checks defined'
                : `${(this._health.summary as { passed: number; total_stages: number }).passed}/${(this._health.summary as { total_stages: number }).total_stages} passed`)
            : 'not checked';
        items.push(deployItem);

        // Drift
        const driftIcon = this._drifted === true ? '$(warning)' : this._drifted === false ? '$(check)' : '$(dash)';
        const driftLabel = this._drifted === true ? 'drift detected' : this._drifted === false ? 'clean' : 'not checked';
        const driftItem = new OpsItem(`${driftIcon}  Drift: ${driftLabel}`, 'status-drift');
        driftItem.command = { command: 'strata.envDrift', title: 'Run Drift', arguments: [this._deployCtx?.activeFile] };
        items.push(driftItem);

        // Lock
        const lockIcon = this._locked ? '$(lock)' : '$(unlock)';
        const lockLabel = this._locked
            ? `locked by ${this._lockHolder ?? 'unknown'}`
            : this._locked === false ? 'unlocked' : 'unknown';
        const lockItem = new OpsItem(`${lockIcon}  Lock: ${lockLabel}`, 'status-lock');
        lockItem.command = { command: 'strata.lockStatus', title: 'Lock Status', arguments: [this._deployCtx?.activeFile] };
        items.push(lockItem);

        return items;
    }

    // ── Outputs children ──────────────────────────────────────────────────────

    private _buildOutputsChildren(): vscode.ProviderResult<OpsItem[]> {
        const activeFile = this._deployCtx?.activeFile;
        if (!activeFile || !this._client) return [this._emptyItem('no deployment')];

        if (!this._outputs && !this._outputsLoading) {
            this._outputsLoading = true;
            void this._client.getEnvOutput(activeFile).then(data => {
                this._outputs = {};
                for (const [stage, stageData] of Object.entries(data.stages)) {
                    this._outputs![stage] = stageData.outputs;
                }
                this._outputsLoading = false;
                this._onChange.fire();
            }).catch(() => {
                this._outputs = {};
                this._outputsLoading = false;
                this._onChange.fire();
            });
            return [this._loadingItem()];
        }

        if (this._outputsLoading) return [this._loadingItem()];
        if (!this._outputs || Object.keys(this._outputs).length === 0) {
            return [this._emptyItem('no outputs — run a build first')];
        }

        return Object.entries(this._outputs).map(([stage, kv]) => {
            const count = Object.keys(kv).length;
            const stageItem = new OpsItem(
                `$(server)  ${stage}  (${count})`,
                'output-stage',
                count > 0 ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None,
            );
            (stageItem as OpsItem & { stageKey: string }).stageKey = stage;
            return stageItem;
        });
    }

    private _buildOutputStageChildren(stageItem: OpsItem): OpsItem[] {
        const stage = (stageItem as OpsItem & { stageKey?: string }).stageKey;
        if (!stage || !this._outputs?.[stage]) return [];
        return Object.entries(this._outputs[stage]).map(([key, val]) => {
            const item = new OpsItem(key, 'output-entry');
            item.description = val ?? '*** sensitive ***';
            item.outputKey = key;
            item.outputValue = val;
            item.contextValue = 'output-entry';
            item.command = val !== null
                ? { command: 'strata.copyOutputValue', title: 'Copy', arguments: [key, val] }
                : undefined;
            return item;
        });
    }

    // ── History children ──────────────────────────────────────────────────────

    private _buildHistoryChildren(): vscode.ProviderResult<OpsItem[]> {
        const activeFile = this._deployCtx?.activeFile;
        if (!activeFile || !this._client) return [this._emptyItem('no deployment')];

        if (!this._history && !this._historyLoading) {
            this._historyLoading = true;
            void this._client.getDeployHistory(activeFile, 10).then(data => {
                this._history = data.entries;
                this._historyLoading = false;
                this._onChange.fire();
            }).catch(() => {
                this._history = [];
                this._historyLoading = false;
                this._onChange.fire();
            });
            return [this._loadingItem()];
        }

        if (this._historyLoading) return [this._loadingItem()];
        if (!this._history || this._history.length === 0) {
            return [this._emptyItem('no deploy history')];
        }

        return this._history.map(entry => {
            const icon = entry.success ? '$(check)' : '$(error)';
            const when = entry.when ? new Date(entry.when).toLocaleString() : 'unknown';
            const item = new OpsItem(`${icon}  ${entry.operation}  ${when}`, 'history-entry');
            item.description = entry.stage !== 'all' ? entry.stage : '';
            item.historyEntry = entry;
            return item;
        });
    }

    // ── Internal loaders ──────────────────────────────────────────────────────

    private _reset(): void {
        this._envStatus = undefined;
        this._health = undefined;
        this._drifted = undefined;
        this._locked = undefined;
        this._lockHolder = undefined;
        this._costSnapshot = undefined;
        this._outputs = undefined;
        this._history = undefined;
        this._outputsLoading = false;
        this._historyLoading = false;
        this._loading = false;
        this._error = undefined;
        this._onChange.fire();
    }

    private async _loadStatus(): Promise<void> {
        const activeFile = this._deployCtx?.activeFile;
        if (!activeFile || !this._client) return;

        // Env status (build cache)
        try {
            const data = await this._client.getEnvStatus();
            this._envStatus = data.deployments.find(d => d.file === activeFile);
        } catch { /* non-fatal */ }

        // Lock status
        await this._loadLock();

        // Cost history (last snapshot)
        try {
            const costData = await this._client.getCostHistory(activeFile, 1);
            if (costData.snapshots.length > 0) {
                this._costSnapshot = costData.snapshots[costData.snapshots.length - 1];
            }
        } catch { /* non-fatal — cost history is optional */ }

        this._onChange.fire();
    }

    private async _loadLock(): Promise<void> {
        const activeFile = this._deployCtx?.activeFile;
        if (!activeFile || !this._client) return;
        try {
            const lock = await this._client.getLockStatus(activeFile);
            this._locked = lock.locked;
            this._lockHolder = lock.holder;
        } catch {
            this._locked = undefined;
        }
        this._onChange.fire();
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): OpsItem {
        const item = new OpsItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errItem(msg: string): OpsItem {
        const item = new OpsItem(msg, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }

    private _emptyItem(msg: string): OpsItem {
        const item = new OpsItem(msg, 'empty');
        item.iconPath = new vscode.ThemeIcon('info');
        return item;
    }
}
