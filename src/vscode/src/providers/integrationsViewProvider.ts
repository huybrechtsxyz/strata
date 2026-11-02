/**
 * integrationsViewProvider.ts - the Integrations tree view
 * (docs/work/vscode-integrations-policies-views.md V3).
 *
 * One row per `strata integration list` row, scoped to the selected
 * deployment when there is one. Children are the row's problems and the
 * `used_for` entries. Clicking a declared row opens its document; the inline
 * "Check connection" action runs `strata integration check`.
 */

import * as vscode from 'vscode';
import { IntegrationRow, StrataClient } from '../strataClient';

type Node = IntegrationItem | DetailItem;

export class IntegrationItem extends vscode.TreeItem {
    constructor(readonly row: IntegrationRow) {
        const hasChildren = row.problems.length > 0 || row.used_for.length > 0;
        super(row.name, hasChildren ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
        const version = row.installed_version ?? row.required_version;
        this.description = version ? `${row.type} ${version}` : row.type;
        this.iconPath = rowIcon(row);
        this.tooltip = rowTooltip(row);
        this.contextValue = 'integration';
        if (row.source) {
            this.resourceUri = vscode.Uri.file(row.source);
            this.command = { command: 'vscode.open', title: 'Open Integration', arguments: [this.resourceUri] };
        }
    }
}

class DetailItem extends vscode.TreeItem {
    constructor(label: string, icon: vscode.ThemeIcon, tooltip?: string) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.iconPath = icon;
        this.tooltip = tooltip ?? label;
    }
}

function rowIcon(row: IntegrationRow): vscode.ThemeIcon {
    if (!row.enabled) return new vscode.ThemeIcon('circle-slash', new vscode.ThemeColor('disabledForeground'));
    if (row.ok) return new vscode.ThemeIcon('pass', new vscode.ThemeColor('testing.iconPassed'));
    return new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground'));
}

function rowTooltip(row: IntegrationRow): vscode.MarkdownString {
    const lines = [`**${row.name}** (${row.type})`, ''];
    lines.push(`- ${row.declared ? 'declared' : 'type default'}, ${row.enabled ? 'enabled' : 'disabled'}${row.required ? ', required' : ''}`);
    if (row.local_tool) {
        const found = row.available ? `found${row.installed_version ? ` (${row.installed_version})` : ''}` : 'not found';
        lines.push(`- tool \`${row.command ?? row.name}\`: ${found}`);
    }
    if (row.required_version) lines.push(`- requires ${row.required_version}`);
    if (row.connection_check) lines.push('- has a connection check');
    if (row.credential_holder) lines.push('- holds credentials');
    return new vscode.MarkdownString(lines.join('\n'));
}

export class IntegrationsViewProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
    private readonly _onDidChange = new vscode.EventEmitter<Node | undefined>();
    readonly onDidChangeTreeData = this._onDidChange.event;
    private _rows: IntegrationRow[] = [];
    private _message: string | undefined;

    constructor(
        private readonly _client: StrataClient,
        private readonly _deployment: () => string | undefined,
    ) { }

    async refresh(): Promise<void> {
        try {
            const envelope = await this._client.integrationList(this._deployment());
            this._rows = envelope.data?.integrations ?? [];
            const first = envelope.diagnostics.find((d) => d.severity === 'error');
            this._message = envelope.ok ? undefined : first?.message ?? 'strata integration list failed';
        } catch (err) {
            this._rows = [];
            this._message = (err as Error).message;
        }
        this._onDidChange.fire(undefined);
    }

    getTreeItem(element: Node): vscode.TreeItem {
        return element;
    }

    getChildren(element?: Node): Node[] {
        if (!element) {
            if (this._message) return [new DetailItem(this._message, new vscode.ThemeIcon('warning'))];
            return this._rows.map((row) => new IntegrationItem(row));
        }
        if (!(element instanceof IntegrationItem)) return [];
        return [
            ...element.row.problems.map(
                (p) => new DetailItem(p.message, new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground')), p.code),
            ),
            ...element.row.used_for.map((use) => new DetailItem(use, new vscode.ThemeIcon('references'))),
        ];
    }

    dispose(): void {
        this._onDidChange.dispose();
    }
}
