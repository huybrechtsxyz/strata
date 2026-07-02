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
 * TODO: implement getChildren() to call StrataClient.getStatus() and
 *   build the tree nodes from the response.
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
     * TODO: call this._client.getStatus(), store result, fire event.
     */
    async refresh(): Promise<void> {
        // TODO: if (this._client) { this._status = await this._client.getStatus(); }
        this._onDidChangeTreeData.fire();
    }

    // ── vscode.TreeDataProvider ────────────────────────────────────────────────

    getTreeItem(element: StrataTreeItem): vscode.TreeItem {
        return element;
    }

    /**
     * Build tree nodes for a given parent.
     * TODO: implement each section using this._status once refresh() populates it.
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
        // TODO: build Solution, Repositories, Documents, Tools sections from this._status
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
        if (!this._status) {
            return [];
        }
        // TODO: implement per section using this._status
        switch (section) {
            case 'Solution': return this._buildProfileNodes();
            case 'Repositories': return this._buildRepositoryNodes();
            case 'Documents': return this._buildDocumentNodes();
            case 'Tools': return this._buildToolNodes();
            default: return [];
        }
    }

    /** TODO: build profile items from this._status.profiles */
    private _buildProfileNodes(): StrataTreeItem[] {
        return [];
    }

    /** TODO: build repository items from this._status.repositories */
    private _buildRepositoryNodes(): StrataTreeItem[] {
        return [];
    }

    /** TODO: build document items from this._status.profiles.paths */
    private _buildDocumentNodes(): StrataTreeItem[] {
        return [];
    }

    /** TODO: build tool items from this._status.integrations */
    private _buildToolNodes(): StrataTreeItem[] {
        return [];
    }
}
