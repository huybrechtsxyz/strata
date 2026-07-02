/**
 * FilesViewProvider — "Files" pane in the Strata activity bar.
 *
 * Shows all strata YAML documents grouped by kind:
 *
 *   ▸ configuration (3)
 *     main.yaml
 *     database.yaml
 *   ▸ deployment (1)
 *     platform.yaml
 *   …
 *
 * Clicking a file opens it in the editor.
 * Items have contextValue "document" so the context menu can offer Validate.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import type { WorkspaceStatus } from '../strataClient';

type ItemKind = 'kind-group' | 'document' | 'loading' | 'error' | 'empty';

/** Kind display labels — ordered by logical importance */
const KIND_ORDER: string[] = [
    'workspace',
    'configuration',
    'deployment',
    'environment',
    'namespace',
    'module',
    'resource',
    'provider',
    'network',
    'dns',
    'firewall',
    'tenant',
];

export class FilesTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly filePath?: string,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
        if (kind === 'document' && filePath) {
            this.command = { command: 'strata.openFile', title: 'Open File', arguments: [{ filePath }] };
            this.resourceUri = vscode.Uri.file(filePath);
        }
    }
}

export class FilesViewProvider implements vscode.TreeDataProvider<FilesTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<FilesTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _status: WorkspaceStatus | undefined;
    private _error: string | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    update(status: WorkspaceStatus): void {
        this._status = status;
        this._error = undefined;
        this._onChange.fire();
    }

    setError(message: string): void {
        this._status = undefined;
        this._error = message;
        this._onChange.fire();
    }

    setLoading(): void {
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: FilesTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: FilesTreeItem): FilesTreeItem[] {
        if (this._error) {
            return [this._errorItem(this._error)];
        }
        if (!this._status) {
            return [this._loadingItem()];
        }

        if (!element) {
            return this._buildKindGroups();
        }

        if (element.kind === 'kind-group') {
            return this._buildFileItems(element.label as string);
        }

        return [];
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildKindGroups(): FilesTreeItem[] {
        const paths = this._status!.profiles.paths;

        // Sort by KIND_ORDER, then alphabetically for any unknown kinds
        const sortedKinds = Object.keys(paths).sort((a, b) => {
            const ia = KIND_ORDER.indexOf(a);
            const ib = KIND_ORDER.indexOf(b);
            if (ia !== -1 && ib !== -1) return ia - ib;
            if (ia !== -1) return -1;
            if (ib !== -1) return 1;
            return a.localeCompare(b);
        });

        if (sortedKinds.length === 0) {
            const empty = new FilesTreeItem('No files found', 'empty');
            empty.iconPath = new vscode.ThemeIcon('info');
            empty.tooltip = 'No strata YAML files found in the active profile paths.';
            return [empty];
        }

        return sortedKinds.map((kindName) => {
            const files = paths[kindName];
            const item = new FilesTreeItem(
                kindName,
                'kind-group',
                vscode.TreeItemCollapsibleState.Collapsed,
            );
            item.description = `${files.length}`;
            item.iconPath = new vscode.ThemeIcon('folder');
            return item;
        });
    }

    private _buildFileItems(kindName: string): FilesTreeItem[] {
        const files = this._status!.profiles.paths[kindName] ?? [];
        return files.map(({ name, path: filePath }) => {
            const item = new FilesTreeItem(
                name,
                'document',
                vscode.TreeItemCollapsibleState.None,
                filePath,
            );
            item.description = path.basename(filePath);
            item.iconPath = new vscode.ThemeIcon('file-code');
            item.tooltip = filePath;
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): FilesTreeItem {
        const item = new FilesTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errorItem(message: string): FilesTreeItem {
        const item = new FilesTreeItem('Error', 'error');
        item.description = message;
        item.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('list.errorForeground'));
        return item;
    }
}
