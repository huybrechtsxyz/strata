/**
 * RepositoriesViewProvider — "Repositories" pane in the Strata activity bar.
 *
 *   infra          (main)   ✅ cloned
 *     URL: https://…
 *     Path: repos/infra
 *   platform       (dev)    ❌ not cloned  [Sync]
 *
 * Clicking a repo detail opens the local path if available.
 * Context menu: Sync, Remove.
 * Title bar: Add repository.
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus, RepositoryInfo } from '../strataClient';

type ItemKind = 'repository' | 'repo-detail' | 'loading' | 'error' | 'empty';

export class RepoTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly repoName?: string,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class RepositoriesViewProvider implements vscode.TreeDataProvider<RepoTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<RepoTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _repos: RepositoryInfo[] = [];
    private _error: string | undefined;
    private _loading = true;
    /** Repos currently being synced (for spinner display) */
    private _syncing = new Set<string>();

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    update(status: WorkspaceStatus): void {
        this._repos = status.repositories;
        this._error = undefined;
        this._loading = false;
        this._onChange.fire();
    }

    setError(message: string): void {
        this._repos = [];
        this._error = message;
        this._loading = false;
        this._onChange.fire();
    }

    setLoading(): void {
        this._loading = true;
        this._onChange.fire();
    }

    /**
     * Sync one (or all) repositories via `strata repo sync`.
     * Shows a spinner on the syncing repo while in progress.
     */
    async syncRepo(name?: string): Promise<void> {
        if (!this._client) {
            void vscode.window.showWarningMessage('Strata: CLI not available.');
            return;
        }
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

    /**
     * Remove a repository via `strata repo remove`.
     */
    async removeRepo(name: string): Promise<void> {
        if (!this._client) {
            void vscode.window.showWarningMessage('Strata: CLI not available.');
            return;
        }
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

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: RepoTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: RepoTreeItem): RepoTreeItem[] {
        if (this._loading && !this._error && this._repos.length === 0) {
            return [this._loadingItem()];
        }
        if (this._error) {
            return [this._errorItem(this._error)];
        }

        if (!element) {
            return this._buildRepos();
        }

        // Children of a repo item — show URL and local path
        const repo = this._repos.find((r) => r.name === element.label);
        if (repo) {
            return this._buildRepoDetails(repo);
        }

        return [];
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildRepos(): RepoTreeItem[] {
        if (this._repos.length === 0) {
            const empty = new RepoTreeItem('No repositories configured', 'empty');
            empty.iconPath = new vscode.ThemeIcon('info');
            empty.tooltip = 'Use "Strata: Add Repository" to add one.';
            return [empty];
        }

        return this._repos.map((repo) => {
            const syncing = this._syncing.has(repo.name) || this._syncing.has('__all__');
            const item = new RepoTreeItem(
                repo.name,
                'repository',
                vscode.TreeItemCollapsibleState.Collapsed,
                repo.name,
            );
            item.description = `(${repo.branch})`;
            item.iconPath = new vscode.ThemeIcon(
                syncing ? 'loading~spin' : repo.cloned ? 'source-control' : 'cloud-download',
                syncing
                    ? undefined
                    : repo.cloned
                        ? new vscode.ThemeColor('testing.iconPassed')
                        : new vscode.ThemeColor('list.warningForeground'),
            );
            item.tooltip = new vscode.MarkdownString(
                `**${repo.name}** \`${repo.branch}\`\n\n` +
                `${repo.cloned ? '✅ Cloned' : '❌ Not cloned'}\n\n` +
                `URL: ${repo.url}\n\nPath: ${repo.path}\n\n` +
                `*Right-click to sync or remove*`,
            );
            return item;
        });
    }

    private _buildRepoDetails(repo: RepositoryInfo): RepoTreeItem[] {
        const url = new RepoTreeItem(repo.url, 'repo-detail');
        url.iconPath = new vscode.ThemeIcon('link');
        url.description = 'URL';
        url.tooltip = 'Repository URL';

        const localPath = new RepoTreeItem(repo.path, 'repo-detail');
        localPath.iconPath = new vscode.ThemeIcon('folder-opened');
        localPath.description = 'path';
        if (repo.cloned) {
            localPath.command = {
                command: 'vscode.openFolder',
                title: 'Open Folder',
                arguments: [vscode.Uri.file(repo.path), { forceNewWindow: false }],
            };
        }

        const branch = new RepoTreeItem(repo.branch, 'repo-detail');
        branch.iconPath = new vscode.ThemeIcon('git-branch');
        branch.description = 'branch';

        return [url, localPath, branch];
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): RepoTreeItem {
        const item = new RepoTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errorItem(message: string): RepoTreeItem {
        const item = new RepoTreeItem('Error', 'error');
        item.description = message;
        item.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('list.errorForeground'));
        return item;
    }
}


