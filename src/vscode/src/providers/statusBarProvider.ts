/**
 * StatusBarProvider — shows workspace readiness and active profile in the status bar.
 *
 * Displays: $(cloud) strata: <profile> | Phase X/Y   (healthy)
 *           $(warning) strata: <profile> | 2 issues  (degraded)
 *           $(error) strata: broken                  (broken)
 *           $(sync~spin) strata                      (loading)
 */

import * as vscode from 'vscode';
import { StrataCLINotFoundError } from '../strataClient';
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

    /** Re-query the workspace and update the status bar. */
    async refresh(): Promise<void> {
        if (!this._client) {
            this._setLoading();
            return;
        }

        this._setLoading();

        try {
            const status = await this._client.getStatus();
            this._render(status);
        } catch (err) {
            this._setError(err);
        }
    }

    /** Update the status bar from an already-fetched status object. */
    update(status: WorkspaceStatus): void {
        this._render(status);
    }

    /** Show the loading spinner. */
    setLoading(): void {
        this._setLoading();
    }

    /** Show an error state from an already-caught error. */
    setError(err: unknown): void {
        this._setError(err);
    }

    dispose(): void {
        this._item.dispose();
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private _setLoading(): void {
        this._item.text = '$(sync~spin) strata';
        this._item.tooltip = 'Strata — loading workspace state…';
        this._item.backgroundColor = undefined;
    }

    private _setError(err: unknown): void {
        const isNotFound = err instanceof StrataCLINotFoundError;
        this._item.text = isNotFound
            ? '$(error) strata: CLI not found'
            : '$(error) strata: error';
        this._item.tooltip = new vscode.MarkdownString(
            isNotFound
                ? `**Strata CLI not found**\n\nCheck the \`strata.cliPath\` setting.\n\n${String(err)}`
                : `**Strata error**\n\n${String(err)}`,
        );
        this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    }

    private _render(status: WorkspaceStatus): void {
        const profile = status.profiles.active ?? '(no profile)';
        const { phases_complete, phases_total } = status.readiness;
        const issueCount = status.health.issues.length;

        // ── Text ────────────────────────────────────────────────────────────
        let icon: string;
        let suffix = '';

        switch (status.health.status) {
            case 'HEALTHY':
                icon = '$(cloud)';
                suffix = ` | Phase ${phases_complete}/${phases_total}`;
                break;
            case 'DEGRADED':
                icon = '$(warning)';
                suffix = ` | ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
                break;
            case 'BROKEN':
                icon = '$(error)';
                suffix = ` | ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
                break;
        }

        this._item.text = `${icon} strata: ${profile}${suffix}`;

        // ── Tooltip ─────────────────────────────────────────────────────────
        const md = new vscode.MarkdownString('', true);
        md.isTrusted = true;
        md.appendMarkdown(`**${status.health.status}** — ${profile}\n\n`);
        md.appendMarkdown(`Readiness: ${phases_complete}/${phases_total} phases\n\n`);

        if (status.health.issues.length > 0) {
            md.appendMarkdown('**Issues:**\n');
            for (const issue of status.health.issues) {
                md.appendMarkdown(`- ${issue}\n`);
            }
            md.appendMarkdown('\n');
        }

        if (status.readiness.next_step) {
            md.appendMarkdown(`**Next:** ${status.readiness.next_step.hint}\n\n`);
        }

        md.appendMarkdown('_Click to open guide_');
        this._item.tooltip = md;

        // ── Background colour ────────────────────────────────────────────────
        this._item.backgroundColor =
            status.health.status === 'BROKEN'
                ? new vscode.ThemeColor('statusBarItem.errorBackground')
                : status.health.status === 'DEGRADED'
                    ? new vscode.ThemeColor('statusBarItem.warningBackground')
                    : undefined;
    }
}

