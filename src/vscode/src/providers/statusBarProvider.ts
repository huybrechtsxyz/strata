/**
 * statusBarProvider.ts — one status bar item: CLI version, or why it isn't
 * available.
 *
 * Deliberately small next to v1's equivalent — there is no readiness
 * checklist or active-profile concept in v2 yet (no `sln status`-style
 * command), so this reports only what v2 actually has today: whether the
 * CLI runs at all, and its version.
 */

import * as vscode from 'vscode';
import { StrataCLINotFoundError, StrataClient } from '../strataClient';

export class StatusBarProvider implements vscode.Disposable {
    private readonly _item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    private _client: StrataClient | undefined;

    constructor() {
        this._item.command = 'strata.validate';
        this._item.text = '$(sync~spin) strata';
        this._item.show();
    }

    setClient(client: StrataClient): void {
        this._client = client;
    }

    async refresh(): Promise<void> {
        if (!this._client) return;
        try {
            const version = await this._client.version();
            this._item.text = `$(cloud) strata ${version}`;
            this._item.tooltip = 'Click to validate the solution';
            this._item.backgroundColor = undefined;
        } catch (err) {
            this._item.text = '$(error) strata';
            this._item.tooltip =
                err instanceof StrataCLINotFoundError ? err.message : 'strata CLI is unavailable — click to retry';
            this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        }
    }

    dispose(): void {
        this._item.dispose();
    }
}
