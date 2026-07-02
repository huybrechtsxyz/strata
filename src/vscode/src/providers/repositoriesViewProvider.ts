/**
 * RepositoriesViewProvider — "Repositories" pane in the Strata activity bar.
 *
 *   infra          (main)   ✅ cloned
 *     URL: https://…
 *     Path: repos/infra
 *   platform       (dev)    ❌ not cloned
 *     URL: https://…
 */

import * as vscode from 'vscode';
import type { WorkspaceStatus, RepositoryInfo } from '../strataClient';

type ItemKind = 'repository' | 'repo-detail' | 'loading' | 'error' | 'empty';

export class RepoTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class RepositoriesViewProvider implements vscode.TreeDataProvider<RepoTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<RepoTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _repos: RepositoryInfo[] = [];
    private _error: string | undefined;
    private _loading = true;

    // ── Public API ────────────────────────────────────────────────────────────

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
            return [empty];
        }

        return this._repos.map((repo) => {
            const item = new RepoTreeItem(
                repo.name,
                'repository',
                vscode.TreeItemCollapsibleState.Collapsed,
            );
            item.description = `(${repo.branch})`;
            item.iconPath = new vscode.ThemeIcon(
                repo.cloned ? 'source-control' : 'cloud-download',
                repo.cloned
                    ? new vscode.ThemeColor('testing.iconPassed')
                    : new vscode.ThemeColor('list.warningForeground'),
            );
            item.tooltip = new vscode.MarkdownString(
                `**${repo.name}** \`${repo.branch}\`\n\n` +
                `${repo.cloned ? '✅ Cloned' : '❌ Not cloned'}\n\n` +
                `URL: ${repo.url}\n\nPath: ${repo.path}`,
            );
            return item;
        });
    }

    private _buildRepoDetails(repo: RepositoryInfo): RepoTreeItem[] {
        const url = new RepoTreeItem(repo.url, 'repo-detail');
        url.iconPath = new vscode.ThemeIcon('link');
        url.description = 'URL';

        const localPath = new RepoTreeItem(repo.path, 'repo-detail');
        localPath.iconPath = new vscode.ThemeIcon('folder-opened');
        localPath.description = 'path';

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
