/**
 * WorkspaceViewProvider — "Workspace" pane in the Strata activity bar.
 *
 * Shows a live overview of workspace health and readiness:
 *
 *   ◎ HEALTHY — dev                    (or DEGRADED / BROKEN)
 *     Phase 5/8 complete
 *     ✅ Phase 1 — Solution initialized
 *     ✅ Phase 2 — Workspace configured
 *     ⏳ Phase 6 — Providers validated   ← next step
 *     …
 *   ▸ Issues (0)
 *   Next: Configure a provider…
 */

import * as vscode from 'vscode';
import type { WorkspaceStatus, ChecklistItem } from '../strataClient';

// ── Tree item kinds ──────────────────────────────────────────────────────────

type ItemKind = 'health' | 'readiness-summary' | 'phase' | 'issue' | 'next-step' | 'loading' | 'error';

export class WorkspaceTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        public readonly filePath?: string,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

// ── Provider ─────────────────────────────────────────────────────────────────

export class WorkspaceViewProvider implements vscode.TreeDataProvider<WorkspaceTreeItem>, vscode.Disposable {
    private readonly _onChange = new vscode.EventEmitter<WorkspaceTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _status: WorkspaceStatus | undefined;
    private _error: string | undefined;

    // ── Public API ────────────────────────────────────────────────────────────

    /** Called by extension.ts after every successful getStatus() call. */
    update(status: WorkspaceStatus): void {
        this._status = status;
        this._error = undefined;
        this._onChange.fire();
    }

    /** Called by extension.ts when getStatus() fails. */
    setError(message: string): void {
        this._status = undefined;
        this._error = message;
        this._onChange.fire();
    }

    /** Called while a refresh is in flight. */
    setLoading(): void {
        this._onChange.fire();
    }

    dispose(): void {
        this._onChange.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: WorkspaceTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: WorkspaceTreeItem): WorkspaceTreeItem[] {
        if (!element) {
            return this._buildRoot();
        }
        switch (element.kind) {
            case 'health': return this._buildIssues();
            case 'readiness-summary': return this._buildPhases();
            default: return [];
        }
    }

    // ── Root items ────────────────────────────────────────────────────────────

    private _buildRoot(): WorkspaceTreeItem[] {
        if (this._error) {
            return [this._errorItem(this._error)];
        }
        if (!this._status) {
            return [this._loadingItem()];
        }

        const items: WorkspaceTreeItem[] = [];

        // ── Health ────────────────────────────────────────────────────────
        const { status: healthStatus, issues } = this._status.health;
        const healthIcons = { HEALTHY: '$(pass)', DEGRADED: '$(warning)', BROKEN: '$(error)' };
        const healthIcon = healthIcons[healthStatus] ?? '$(circle-outline)';
        const profile = this._status.profiles.active ?? '(no profile)';

        const healthItem = new WorkspaceTreeItem(
            `${healthIcon} ${healthStatus} — ${profile}`,
            'health',
            issues.length > 0
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None,
        );
        healthItem.description = issues.length > 0 ? `${issues.length} issue${issues.length !== 1 ? 's' : ''}` : undefined;
        healthItem.iconPath = undefined; // icon is in the label via codicon
        items.push(healthItem);

        // ── Readiness ─────────────────────────────────────────────────────
        const { phases_complete, phases_total } = this._status.readiness;
        const readinessItem = new WorkspaceTreeItem(
            'Readiness',
            'readiness-summary',
            vscode.TreeItemCollapsibleState.Expanded,
        );
        readinessItem.description = `${phases_complete} / ${phases_total} phases`;
        readinessItem.iconPath = new vscode.ThemeIcon(
            phases_complete === phases_total ? 'check-all' : 'list-ordered',
        );
        items.push(readinessItem);

        // ── Next step hint ────────────────────────────────────────────────
        if (this._status.readiness.next_step) {
            const { hint, label } = this._status.readiness.next_step;
            const nextItem = new WorkspaceTreeItem(`Next: ${label}`, 'next-step');
            nextItem.description = hint;
            nextItem.iconPath = new vscode.ThemeIcon('arrow-right');
            nextItem.command = { command: 'strata.showGuide', title: 'Show Guide' };
            items.push(nextItem);
        }

        return items;
    }

    // ── Issue children ────────────────────────────────────────────────────────

    private _buildIssues(): WorkspaceTreeItem[] {
        if (!this._status) return [];
        return this._status.health.issues.map((issue) => {
            const item = new WorkspaceTreeItem(issue, 'issue');
            item.iconPath = new vscode.ThemeIcon('circle-filled', new vscode.ThemeColor('list.errorForeground'));
            item.tooltip = issue;
            return item;
        });
    }

    // ── Phase children ────────────────────────────────────────────────────────

    private _buildPhases(): WorkspaceTreeItem[] {
        if (!this._status) return [];
        return this._status.readiness.checklist.map((p: ChecklistItem) => {
            const phaseItem = new WorkspaceTreeItem(p.label, 'phase');
            phaseItem.description = p.detail ?? undefined;
            phaseItem.iconPath = new vscode.ThemeIcon(
                p.status === 'ok' ? 'pass-filled' :
                    p.status === 'warn' ? 'warning' :
                        'circle-outline',
                p.status === 'ok'
                    ? new vscode.ThemeColor('testing.iconPassed')
                    : p.status === 'warn'
                        ? new vscode.ThemeColor('list.warningForeground')
                        : undefined,
            );
            return phaseItem;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): WorkspaceTreeItem {
        const item = new WorkspaceTreeItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _errorItem(message: string): WorkspaceTreeItem {
        const item = new WorkspaceTreeItem('Error', 'error');
        item.description = message;
        item.iconPath = new vscode.ThemeIcon('error', new vscode.ThemeColor('list.errorForeground'));
        item.tooltip = message;
        return item;
    }
}
