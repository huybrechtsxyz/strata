/**
 * TreeViewProvider — Workspace Explorer sidebar panel.
 *
 * Shows the live workspace structure:
 *   STRATA WORKSPACE
 *   ├── Solution: my-project
 *   │   ├── Profile: dev (active)
 *   │   └── Profile: prd
 *   ├── Repositories
 *   │   └── infra (repos/infra) — main
 *   ├── Documents
 *   │   ├── config/main.yaml (configuration) ✅
 *   │   └── deploy/main.yaml (deployment) ❌
 *   └── Tools
 *       ├── terraform (1.9.0) ✅
 *       └── helm ❌
 *
 * Populated via refresh() → StrataClient.getStatus(). Sections expand lazily
 * via getChildren(). Non-active profiles have an inline Switch command.
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus } from '../strataClient';

// ---------------------------------------------------------------------------
// Tree item types
// ---------------------------------------------------------------------------

export type NodeKind =
    | 'root'
    | 'section'
    | 'profile'
    | 'repository'
    | 'document'
    | 'tool'
    | 'loading'
    | 'error';

export class StrataTreeItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly kind: NodeKind,
        public readonly filePath: string | undefined,
        collapsibleState: vscode.TreeItemCollapsibleState,
    ) {
        super(label, collapsibleState);
        this.contextValue = kind;
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class TreeViewProvider
    implements vscode.TreeDataProvider<StrataTreeItem> {
    private readonly _onDidChangeTreeData =
        new vscode.EventEmitter<StrataTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private _client: StrataClient | undefined;
    private _status: WorkspaceStatus | undefined;

    // ── Public API ─────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /**
     * Re-query workspace and refresh the tree.
     */
    async refresh(): Promise<void> {
        if (this._client) {
            try {
                this._status = await this._client.getStatus();
            } catch {
                // status unavailable — clear so tree shows loading until next call
                this._status = undefined;
            }
        }
        this._onDidChangeTreeData.fire();
    }

    // ── vscode.TreeDataProvider ────────────────────────────────────────────────

    getTreeItem(element: StrataTreeItem): vscode.TreeItem {
        return element;
    }

    /**
     * Build tree nodes for a given parent.
     */
    getChildren(element?: StrataTreeItem): StrataTreeItem[] {
        if (!this._client) {
            return [this._makeLoading()];
        }

        if (!element) {
            // Root — show top-level sections
            return this._makeRootSections();
        }

        switch (element.kind) {
            case 'section':
                return this._makeSectionChildren(element.label);
            default:
                return [];
        }
    }

    // ── Private builders ───────────────────────────────────────────────────────

    private _makeLoading(): StrataTreeItem {
        const item = new StrataTreeItem(
            'Loading…',
            'loading',
            undefined,
            vscode.TreeItemCollapsibleState.None,
        );
        item.description = 'waiting for strata';
        return item;
    }

    private _makeRootSections(): StrataTreeItem[] {
        if (!this._status) {
            return [this._makeLoading()];
        }
        return [
            this._makeSection('Solution'),
            this._makeSection('Repositories'),
            this._makeSection('Documents'),
            this._makeSection('Tools'),
        ];
    }

    private _makeSection(label: string): StrataTreeItem {
        return new StrataTreeItem(
            label,
            'section',
            undefined,
            vscode.TreeItemCollapsibleState.Expanded,
        );
    }

    private _makeSectionChildren(section: string): StrataTreeItem[] {
        if (!this._status) return [];
        switch (section) {
            case 'Solution': return this._buildProfileNodes();
            case 'Repositories': return this._buildRepositoryNodes();
            case 'Documents': return this._buildDocumentNodes();
            case 'Tools': return this._buildToolNodes();
            default: return [];
        }
    }

    private _buildProfileNodes(): StrataTreeItem[] {
        const profiles = this._status!.profiles;
        if (!profiles.all?.length) {
            const empty = new StrataTreeItem('No profiles', 'profile', undefined, vscode.TreeItemCollapsibleState.None);
            empty.iconPath = new vscode.ThemeIcon('dash');
            return [empty];
        }
        return profiles.all.map((name) => {
            const active = name === profiles.active;
            const item = new StrataTreeItem(name, 'profile', undefined, vscode.TreeItemCollapsibleState.None);
            item.description = active ? '(active)' : undefined;
            item.iconPath = new vscode.ThemeIcon(active ? 'account' : 'circle-outline');
            item.tooltip = active ? `Profile: ${name} — currently active` : `Profile: ${name}`;
            if (!active) {
                item.command = {
                    command: 'strata.switchProfile',
                    title: 'Switch to Profile',
                };
            }
            return item;
        });
    }

    private _buildRepositoryNodes(): StrataTreeItem[] {
        const repos = this._status!.repositories ?? [];
        if (!repos.length) {
            const empty = new StrataTreeItem('No repositories configured', 'repository', undefined, vscode.TreeItemCollapsibleState.None);
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }
        return repos.map((r) => {
            const item = new StrataTreeItem(r.name, 'repository', undefined, vscode.TreeItemCollapsibleState.None);
            item.description = `(${r.branch})`;
            item.iconPath = new vscode.ThemeIcon(
                r.cloned ? 'source-control' : 'cloud-download',
                r.cloned
                    ? new vscode.ThemeColor('testing.iconPassed')
                    : new vscode.ThemeColor('list.warningForeground'),
            );
            item.tooltip = `${r.name} — ${r.cloned ? 'cloned' : 'not cloned'}\n${r.url}`;
            return item;
        });
    }

    private _buildDocumentNodes(): StrataTreeItem[] {
        const paths = this._status!.profiles.paths;
        if (!paths || Object.keys(paths).length === 0) {
            const empty = new StrataTreeItem('No documents found', 'document', undefined, vscode.TreeItemCollapsibleState.None);
            empty.iconPath = new vscode.ThemeIcon('info');
            return [empty];
        }
        const items: StrataTreeItem[] = [];
        for (const [kind, files] of Object.entries(paths)) {
            for (const f of files) {
                const item = new StrataTreeItem(
                    f.name || f.path.split(/[\\/]/).pop() || f.path,
                    'document',
                    f.path,
                    vscode.TreeItemCollapsibleState.None,
                );
                item.description = kind;
                item.iconPath = new vscode.ThemeIcon('file-code');
                item.tooltip = f.path;
                item.command = {
                    command: 'strata.openFile',
                    title: 'Open File',
                    arguments: [{ filePath: f.path }],
                };
                items.push(item);
            }
        }
        return items;
    }

    private _buildToolNodes(): StrataTreeItem[] {
        const integrations = this._status!.integrations ?? {};
        const entries = Object.values(integrations);
        if (!entries.length) {
            const empty = new StrataTreeItem('No tools detected', 'tool', undefined, vscode.TreeItemCollapsibleState.None);
            empty.iconPath = new vscode.ThemeIcon('dash');
            return [empty];
        }
        return entries.map((tool) => {
            const item = new StrataTreeItem(tool.name, 'tool', undefined, vscode.TreeItemCollapsibleState.None);
            item.description = tool.version ?? (tool.available ? 'available' : 'not found');
            item.iconPath = new vscode.ThemeIcon(
                tool.available ? 'check' : 'close',
                new vscode.ThemeColor(tool.available ? 'testing.iconPassed' : 'testing.iconFailed'),
            );
            item.tooltip = tool.info ?? (tool.available ? `${tool.name} is available` : `${tool.name} is not installed`);
            return item;
        });
    }
}
