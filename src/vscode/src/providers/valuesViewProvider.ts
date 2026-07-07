/**
 * ValuesViewProvider — "Values" pane in the Strata activity bar.
 *
 * Shows resolved deployment values (env vars, secrets, features) for the
 * active deployment file, via `strata values list -f <file> --output json`.
 *
 *   deploy_prd.yaml
 *   ├── 🔑  DB_PASSWORD        *** (secret, resolved)
 *   ├── ⚙   APP_ENV             production
 *   ├── ⚙   REGION              westeurope
 *   └── ⚠   MISSING_VAR         (unresolved)
 *
 * Clicking on a value copies its key to clipboard.
 * Context menu: Copy Key, Copy Value (for non-secrets).
 * Refresh: re-loads values for the currently active deployment file.
 */

import * as vscode from 'vscode';
import type { StrataClient, ValuesData, ValueEntry } from '../strataClient';

type ItemKind = 'file' | 'value' | 'loading' | 'error' | 'empty';

export class ValuesTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly entry?: ValueEntry,
        public readonly filePath?: string,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

export class ValuesViewProvider
    implements vscode.TreeDataProvider<ValuesTreeItem>, vscode.Disposable {
    private readonly _onChange =
        new vscode.EventEmitter<ValuesTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _data: ValuesData | undefined;
    private _currentFile: string | undefined;
    private _error: string | undefined;
    private _loading = false;

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /**
     * Load values for the given deployment file.
     * Called when the user runs `strata.showValues` or opens a deployment YAML.
     */
    loadFile(filePath: string): void {
        this._currentFile = filePath;
        void this._load();
    }

    /** Refresh current file's values. */
    refresh(): void {
        if (this._currentFile) {
            void this._load();
        }
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

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: ValuesTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: ValuesTreeItem): ValuesTreeItem[] {
        if (element?.kind === 'file' && this._data) {
            return this._buildValues(this._data.entries);
        }
        if (element) return [];

        if (this._loading) {
            return [this._loadingItem()];
        }
        if (this._error) {
            return [this._errorItem(this._error)];
        }
        if (!this._currentFile) {
            const item = new ValuesTreeItem('Open a deployment YAML to inspect values', 'empty');
            item.iconPath = new vscode.ThemeIcon('info');
            return [item];
        }
        if (!this._data) {
            return [this._loadingItem()];
        }
        return this._buildRoot();
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    private async _load(): Promise<void> {
        if (!this._client || !this._currentFile) return;
        this._loading = true;
        this._error = undefined;
        this._data = undefined;
        this._onChange.fire();

        try {
            this._data = await this._client.getValues(this._currentFile);
        } catch (err) {
            this._error = err instanceof Error ? err.message : String(err);
        }

        this._loading = false;
        this._onChange.fire();
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    private _buildRoot(): ValuesTreeItem[] {
        if (!this._data) return [];
        const fileName = this._currentFile!.split(/[\\/]/).pop() ?? this._currentFile!;
        const fileItem = new ValuesTreeItem(
            fileName,
            'file',
            vscode.TreeItemCollapsibleState.Expanded,
            undefined,
            this._currentFile,
        );
        fileItem.description = `${this._data.count} values`;
        fileItem.iconPath = new vscode.ThemeIcon('file-code');
        fileItem.tooltip = this._currentFile;
        return [fileItem];
    }

    private _buildValues(entries: ValueEntry[]): ValuesTreeItem[] {
        if (entries.length === 0) {
            const empty = new ValuesTreeItem('No values defined', 'empty');
            empty.iconPath = new vscode.ThemeIcon('dash');
            return [empty];
        }

        return entries.map((e) => {
            const displayValue = e.secret
                ? '***'
                : e.value !== null
                    ? e.value
                    : '(null)';

            const item = new ValuesTreeItem(e.key, 'value', vscode.TreeItemCollapsibleState.None, e);
            item.description = displayValue;
            item.tooltip = new vscode.MarkdownString(
                `**${e.key}**\n\n` +
                `Source: \`${e.source}\`\n\n` +
                (e.secret ? '🔑 Secret value (masked)\n\n' : `Value: \`${e.value ?? 'null'}\`\n\n`) +
                (e.resolved ? '✅ Resolved' : '⚠️ Unresolved'),
            );
            item.iconPath = new vscode.ThemeIcon(
                e.secret
                    ? 'key'
                    : e.resolved
                        ? 'symbol-variable'
                        : 'warning',
                new vscode.ThemeColor(
                    e.secret
                        ? 'charts.yellow'
                        : e.resolved
                            ? 'foreground'
                            : 'list.warningForeground',
                ),
            );
            // Click copies key to clipboard
            item.command = {
                command: 'strata.copyValueKey',
                title: 'Copy Key',
                arguments: [e.key],
            };
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): ValuesTreeItem {
        const item = new ValuesTreeItem('Loading values…', 'loading');
        item.iconPath = new vscode.ThemeIcon('loading~spin');
        return item;
    }

    private _errorItem(message: string): ValuesTreeItem {
        const item = new ValuesTreeItem(message, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }
}
