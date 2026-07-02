/**
 * StatusBarProvider — shows workspace readiness and active profile in the status bar.
 *
 * Displays: $(cloud) strata: <profile> | Phase X/Y | Z errors
 *
 * TODO: implement refresh() to call StrataClient.getStatus() and update the item.
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus } from '../strataClient';

export class StatusBarProvider implements vscode.Disposable {
    private readonly _item: vscode.StatusBarItem;
    private _client: StrataClient | undefined;

    constructor() {
        this._item = vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Left,
            100,
        );
        this._item.command = 'strata.showGuide';
        this._item.tooltip = 'Strata workspace status — click to show guide';
        this._setLoading();
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    show(): void {
        const config = vscode.workspace.getConfiguration('strata');
        if (config.get<boolean>('showStatusBar', true)) {
            this._item.show();
        }
    }

    hide(): void {
        this._item.hide();
    }

    /**
     * Re-query the workspace and update the status bar text.
     * TODO: call this._client.getStatus() and render the result.
     */
    async refresh(): Promise<void> {
        if (!this._client) {
            this._setLoading();
            return;
        }
        // TODO: const status = await this._client.getStatus();
        // TODO: this._render(status);
        this._item.text = '$(cloud) strata: loading…';
    }

    dispose(): void {
        this._item.dispose();
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private _setLoading(): void {
        this._item.text = '$(cloud) strata';
        this._item.tooltip = 'Strata — loading workspace state';
    }

    /**
     * Render status bar text from a WorkspaceStatus response.
     * TODO: wire up once refresh() calls getStatus()
     */
    private _render(status: WorkspaceStatus): void {
        const profile = status.profiles.active ?? '(no profile)';
        const { phases_complete, phases_total } = status.readiness;
        const errorCount = status.health.issues.length;

        const phase = `Phase ${phases_complete}/${phases_total}`;
        const errors = errorCount > 0 ? ` | ${errorCount} issues` : '';

        this._item.text = `$(cloud) strata: ${profile} | ${phase}${errors}`;

        if (status.health.status === 'BROKEN') {
            this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
        } else if (status.health.status === 'DEGRADED') {
            this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        } else {
            this._item.backgroundColor = undefined;
        }
    }
}
