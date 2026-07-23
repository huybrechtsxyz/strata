/**
 * WorkspaceHealthProvider — "Workspace" pane in the Strata activity bar.
 *
 * Merges the former WorkspaceViewProvider, RepositoriesViewProvider, and
 * ToolsViewProvider into a single collapsible panel:
 *
 *   $(pass) HEALTHY — dev (active)
 *     Phase 5/8 complete
 *   ▸ Profiles (2)
 *       ● dev (active)
 *       ○ prd
 *   ▸ Repositories (1)
 *       $(repo) haven  (main)  ✓
 *   ▸ Tools (3)
 *       $(check) terraform  1.9.0
 *       $(check) ansible    2.15
 *       $(x)    kubectl     not found
 *
 * Context menus:
 *   profile → Switch Profile (activates via strata.activateProfile)
 *   repository → Sync, Remove
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus, ToolsStatusRow } from '../strataClient';

// ---------------------------------------------------------------------------
// Tree item
// ---------------------------------------------------------------------------

export type HealthItemKind =
    | 'health'
    | 'readiness-summary'
    | 'phase'
    | 'issue'
    | 'profiles-section'
    | 'profile'
    | 'profile-active'
    | 'repos-section'
    | 'repository'
    | 'tools-section'
    | 'tool'
    | 'loading'
    | 'error';

export class HealthTreeItem extends vscode.TreeItem {
    /** Carries: profile name (string) | repo name (string) | undefined */
    readonly payload: unknown;

    constructor(
        label: string,
        public readonly kind: HealthItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        payload?: unknown,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
        this.payload = payload;
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class WorkspaceHealthProvider
    implements vscode.TreeDataProvider<HealthTreeItem>, vscode.Disposable {

    private readonly _onChange =
        new vscode.EventEmitter<HealthTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _status: WorkspaceStatus | undefined;
    private _error: string | undefined;
    private _syncing = new Set<string>();
    private _tools: ToolsStatusRow[] | undefined;
    private _toolsLoading = false;
    private _toolsDeploymentFile: string | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void { this._client = client; }

    update(status: WorkspaceStatus): void {
        this._status = status;
        this._error = undefined;
        // Reset tools cache so next expand re-fetches with fresh deployment context
        this._tools = undefined;
        this._toolsLoading = false;
        this._onChange.fire();
    }

    /** Pass the active deployment file so tools status can resolve requirements. */
    setActiveDeployment(file?: string): void {
        if (this._toolsDeploymentFile !== file) {
            this._toolsDeploymentFile = file;
            // Invalidate tools cache when active deployment changes
            this._tools = undefined;
            this._toolsLoading = false;
            this._onChange.fire();
        }
    }

    setError(message: string): void {
        this._status = undefined;
        this._error = message;
        this._onChange.fire();
    }

    setLoading(): void { this._onChange.fire(); }

    async syncRepo(name?: string): Promise<void> {
        if (!this._client) return;
        const key = name ?? '__all__';
        this._syncing.add(key);
        this._onChange.fire();
        try {
            await this._client.syncRepo(name);
            void vscode.window.showInformationMessage(
                name ? `Strata: ${name} synced.` : 'Strata: all repositories synced.',
            );
        } catch (err) {
            void vscode.window.showErrorMessage(`Strata: sync failed — ${String(err)}`);
        } finally {
            this._syncing.delete(key);
            this._onChange.fire();
        }
    }

    async removeRepo(name: string): Promise<void> {
        if (!this._client) return;
        const confirmed = await vscode.window.showWarningMessage(
            `Remove repository "${name}" from the workspace?`,
            { modal: true }, 'Remove',
        );
        if (confirmed !== 'Remove') return;
        try {
            await this._client.removeRepo(name);
            void vscode.window.showInformationMessage(`Strata: repository "${name}" removed.`);
        } catch (err) {
            void vscode.window.showErrorMessage(`Strata: remove failed — ${String(err)}`);
        }
    }

    dispose(): void { this._onChange.dispose(); }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: HealthTreeItem): vscode.TreeItem { return element; }

    getChildren(element?: HealthTreeItem): HealthTreeItem[] {
        if (!element) return this._buildRoot();
        switch (element.kind) {
            case 'health': return this._buildIssues();
            case 'readiness-summary': return this._buildPhases();
            case 'profiles-section': return this._buildProfiles();
            case 'repos-section': return this._buildRepos();
            case 'tools-section': return this._buildTools();
            default: return [];
        }
    }

    // ── Root section ──────────────────────────────────────────────────────────

    private _buildRoot(): HealthTreeItem[] {
        if (this._error) return [this._errItem(this._error)];
        if (!this._status) return [this._loadingItem()];

        const items: HealthTreeItem[] = [];

        // Health badge + issues
        const { status: h, issues } = this._status.health;
        const icon = h === 'HEALTHY' ? '$(pass)' : h === 'DEGRADED' ? '$(warning)' : '$(error)';
        const profile = this._status.profiles.active ?? 'no profile';
        const healthItem = new HealthTreeItem(
            `${icon}  ${h}  —  ${profile}`,
            'health',
            issues.length
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None,
        );
        if (h === 'DEGRADED') {
            healthItem.iconPath = new vscode.ThemeIcon('warning', new vscode.ThemeColor('problemsWarningIcon.foreground'));
        } else if (h === 'BROKEN') {
            healthItem.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground'));
        }
        items.push(healthItem);

        // Readiness
        const { phases_complete, phases_total } = this._status.readiness;
        const complete = phases_complete >= phases_total;
        const readItem = new HealthTreeItem(
            `Phase ${phases_complete}/${phases_total}${complete ? ' — complete ✅' : ' — in progress'}`,
            'readiness-summary',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        readItem.iconPath = new vscode.ThemeIcon('list-ordered');
        items.push(readItem);

        // Profiles
        const profileCount = this._status.profiles.all.length;
        const profSection = new HealthTreeItem(
            `Profiles  (${profileCount})`,
            'profiles-section',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        profSection.iconPath = new vscode.ThemeIcon('account');
        items.push(profSection);

        // Repositories
        const repoCount = this._status.repositories.length;
        const repoSection = new HealthTreeItem(
            `Repositories  (${repoCount})`,
            'repos-section',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        repoSection.iconPath = new vscode.ThemeIcon('repo');
        items.push(repoSection);

        // Tools — count from cached rows when available
        const toolCount = this._tools ? this._tools.filter(t => t.available).length : '\u2026';
        const toolLabel = this._tools
            ? `Tools  (${toolCount}\u2009/\u2009${this._tools.length})`
            : `Tools  (loading\u2026)`;
        const toolSection = new HealthTreeItem(
            toolLabel,
            'tools-section',
            vscode.TreeItemCollapsibleState.Collapsed,
        );
        toolSection.iconPath = new vscode.ThemeIcon('tools');
        items.push(toolSection);

        return items;
    }

    // ── Section children ──────────────────────────────────────────────────────

    private _buildIssues(): HealthTreeItem[] {
        return (this._status?.health.issues ?? []).map(issue => {
            const item = new HealthTreeItem(issue, 'issue');
            item.iconPath = new vscode.ThemeIcon('warning');
            return item;
        });
    }

    private _buildPhases(): HealthTreeItem[] {
        return (this._status?.readiness.checklist ?? []).map(p => {
            const icon = p.status === 'ok' ? '$(check)' : p.status === 'warn' ? '$(warning)' : '$(circle-slash)';
            const item = new HealthTreeItem(
                `${icon}  Phase ${p.phase} — ${p.label}`,
                'phase',
            );
            if (p.detail) item.description = p.detail;
            return item;
        });
    }

    private _buildProfiles(): HealthTreeItem[] {
        if (!this._status) return [];
        return this._status.profiles.all.map(p => {
            const isActive = p === this._status!.profiles.active;
            const icon = isActive ? '$(circle-filled)' : '$(circle-outline)';
            const item = new HealthTreeItem(
                `${icon}  ${p}`,
                isActive ? 'profile-active' : 'profile',
                vscode.TreeItemCollapsibleState.None,
                p,
            );
            if (isActive) item.description = 'active';
            item.contextValue = isActive ? 'profile-active' : 'profile';
            return item;
        });
    }

    private _buildRepos(): HealthTreeItem[] {
        return (this._status?.repositories ?? []).map(repo => {
            const syncing = this._syncing.has(repo.name) || this._syncing.has('__all__');
            const icon = syncing ? '$(sync~spin)' : repo.cloned ? '$(repo)' : '$(repo-clone)';
            const item = new HealthTreeItem(
                `${icon}  ${repo.name}`,
                'repository',
                vscode.TreeItemCollapsibleState.None,
                repo.name,
            );
            item.description = repo.branch ? `(${repo.branch})` : '';
            item.tooltip = new vscode.MarkdownString(`**${repo.name}**\n\n${repo.url ?? ''}\n\n${repo.path ?? ''}`);
            item.contextValue = 'repository';
            return item;
        });
    }

    private _buildTools(): HealthTreeItem[] {
        // If tools haven't been fetched yet, kick off a background load
        if (!this._tools && !this._toolsLoading && this._client) {
            this._toolsLoading = true;
            void this._client.getToolsStatus(this._toolsDeploymentFile).then(rows => {
                this._tools = rows;
                this._toolsLoading = false;
                this._onChange.fire(); // re-render the section with real data
            }).catch(() => {
                this._toolsLoading = false;
                this._tools = [];
                this._onChange.fire();
            });
            // Return a spinner while loading
            const loading = new HealthTreeItem('Probing tools\u2026', 'loading');
            loading.iconPath = new vscode.ThemeIcon('sync~spin');
            return [loading];
        }

        const rows = this._tools ?? [];
        if (rows.length === 0) {
            const empty = new HealthTreeItem('No tools detected', 'tool');
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }

        const sorted = [...rows].sort((a, b) => {
            const tier = (r: ToolsStatusRow) => {
                if (r.requirement != null && r.available) return 0;
                if (r.requirement != null && !r.available) return 1;
                return 2;
            };
            const td = tier(a) - tier(b);
            return td !== 0 ? td : a.name.localeCompare(b.name);
        });

        return sorted.map(row => {
            const configured = row.requirement != null;
            const reqLabel = row.requirement === 'required' ? '  req'
                : row.requirement === 'optional' ? '  opt' : '';

            let icon: string;
            let iconColor: vscode.ThemeColor;
            let cv: string;

            if (configured && row.available) {
                icon = 'pass-filled';
                iconColor = new vscode.ThemeColor('testing.iconPassed');
                cv = 'tool';
            } else if (configured && !row.available) {
                icon = row.requirement === 'required' ? 'error' : 'warning';
                iconColor = row.requirement === 'required'
                    ? new vscode.ThemeColor('list.errorForeground')
                    : new vscode.ThemeColor('list.warningForeground');
                cv = 'tool-unavailable';
            } else {
                icon = row.available ? 'circle-filled' : 'circle-outline';
                iconColor = new vscode.ThemeColor('disabledForeground');
                cv = 'tool-unconfigured';
            }

            const item = new HealthTreeItem(row.name, 'tool');
            item.description = (row.version ?? (configured && !row.available ? 'not found' : '')) + reqLabel;
            item.iconPath = new vscode.ThemeIcon(icon, iconColor);
            item.contextValue = cv;
            item.tooltip = new vscode.MarkdownString(
                `**${row.name}**\n\n` +
                (configured && row.available ? '\u2705 Configured & available'
                    : configured && !row.available ? '\u274c Not installed'
                        : row.available ? 'Available \u2014 not configured'
                            : 'Not installed \u2014 not required') +
                (row.version ? ` \u2014 v${row.version}` : '') +
                (row.requirement ? `\n\nDeployment requirement: **${row.requirement}**` : '') +
                `\n\n*Click \$(book) to open setup guide*`,
            );
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): HealthTreeItem {
        const item = new HealthTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errItem(msg: string): HealthTreeItem {
        const item = new HealthTreeItem(msg, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }
}
