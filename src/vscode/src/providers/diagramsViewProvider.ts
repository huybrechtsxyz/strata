/**
 * DiagramsViewProvider — "Diagrams" sidebar view, a lightweight cookbook browser
 * over `strata diagram list` (ADR-0034 Phase 3).
 *
 * Scope note: the ADR's Part 2 catalog (185 worked examples) is prose in the
 * ADR document, not machine-readable data the CLI serves — there is nothing to
 * browse there yet. This view lists what `strata diagram list` actually
 * returns: shipped built-ins plus any workspace definitions under
 * `.strata/diagrams/`, grouped by source, with a text filter across name and
 * description. Selecting an entry opens it via the existing
 * `strata.previewDiagramFile` command (DiagramPreviewProvider), the same path
 * `Strata: Preview Diagram` uses.
 */

import * as vscode from 'vscode';
import type { StrataClient, DiagramListEntry } from '../strataClient';

type DiagramTreeNode = SectionNode | EntryNode;

interface SectionNode {
    kind: 'section';
    label: string;
    entries: DiagramListEntry[];
}

interface EntryNode {
    kind: 'entry';
    entry: DiagramListEntry;
}

export class DiagramsViewProvider implements vscode.TreeDataProvider<DiagramTreeNode>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _diagrams: DiagramListEntry[] = [];
    private _filter = '';
    private _errorMessage: string | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Free-text filter across name + description. Empty string clears it. */
    async setFilter(): Promise<void> {
        const value = await vscode.window.showInputBox({
            prompt: 'Filter diagrams by name or description (leave empty to clear)',
            value: this._filter,
            ignoreFocusOut: true,
        });
        if (value === undefined) return; // cancelled
        this._filter = value.trim().toLowerCase();
        this._onChange.fire();
    }

    async refresh(): Promise<void> {
        if (!this._client) return;
        try {
            this._diagrams = await this._client.listDiagrams();
            this._errorMessage = undefined;
        } catch (err) {
            this._diagrams = [];
            this._errorMessage = err instanceof Error ? err.message : String(err);
        }
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: DiagramTreeNode): vscode.TreeItem {
        if (element.kind === 'section') {
            const item = new vscode.TreeItem(
                `${element.label} (${element.entries.length})`,
                element.entries.length > 0 ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None,
            );
            item.contextValue = 'section';
            item.iconPath = new vscode.ThemeIcon('symbol-namespace');
            return item;
        }

        const { entry } = element;
        const item = new vscode.TreeItem(entry.name, vscode.TreeItemCollapsibleState.None);
        item.description = entry.description || undefined;
        item.tooltip = new vscode.MarkdownString(
            `**${entry.name}**\n\n${entry.description || '*(no description)*'}\n\n` +
            `Sources: ${entry.sources.length ? entry.sources.join(', ') : '*(static)*'}\n\n` +
            `\`${entry.path}\``,
        );
        item.iconPath = new vscode.ThemeIcon('type-hierarchy-sub');
        item.contextValue = 'diagram';
        item.command = { title: 'Show Diagram', command: 'strata.previewDiagramFile', arguments: [entry.name] };
        return item;
    }

    getChildren(element?: DiagramTreeNode): DiagramTreeNode[] {
        if (element) {
            return element.kind === 'section' ? element.entries.map((entry) => ({ kind: 'entry', entry })) : [];
        }

        if (this._errorMessage) {
            const item: SectionNode = { kind: 'section', label: `Error: ${this._errorMessage}`, entries: [] };
            return [item];
        }

        const filtered = this._filter
            ? this._diagrams.filter(
                (d) => d.name.toLowerCase().includes(this._filter) || d.description.toLowerCase().includes(this._filter),
            )
            : this._diagrams;

        const sections: SectionNode[] = [
            { kind: 'section' as const, label: 'Built-in', entries: filtered.filter((d) => d.source === 'built-in') },
            { kind: 'section' as const, label: 'Workspace', entries: filtered.filter((d) => d.source !== 'built-in') },
        ].filter((s) => s.entries.length > 0);

        return sections;
    }
}
