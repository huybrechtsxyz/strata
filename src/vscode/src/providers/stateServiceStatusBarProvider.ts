/**
 * StateServiceStatusBarProvider — a second, independent status bar item
 * showing whether the configured strata state-service server (ADR-0065) is
 * reachable.
 *
 * Shown only when `strata.stateService.url` is configured — hidden entirely
 * otherwise, matching `showStatusBar`'s existing gating pattern for the main
 * status bar item. Polls `strata serve health <url>` on an interval
 * (`strata.stateService.pollIntervalSeconds`, default 60s, minimum 10s — same
 * shape as `workItemPollIntervalSeconds`).
 *
 * States: $(sync~spin) loading, $(radio-tower) reachable, $(warning)
 * unreachable, $(error) an actual request error (e.g. CLI not found).
 */

import * as vscode from 'vscode';
import type { StrataClient } from '../strataClient';

export class StateServiceStatusBarProvider implements vscode.Disposable {
    private readonly _item: vscode.StatusBarItem;
    private _client: StrataClient | undefined;
    private _pollTimer: ReturnType<typeof setInterval> | undefined;
    private _lastReachable: boolean | undefined;
    private _onStatus: ((url: string | undefined, reachable: boolean | undefined) => void) | undefined;

    constructor() {
        // Priority 98 — deliberately distinct from CacheWarmerProvider's 99 (both
        // StatusBarAlignment.Left) so the two never tie and land in an unstable
        // relative order.
        this._item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 98);
        this._item.command = 'strata.stateService.showTail';
    }

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /**
     * Register a callback fired after every health check, so other views
     * (the Tools row) can reuse this poll instead of starting a second one.
     */
    onStatus(cb: (url: string | undefined, reachable: boolean | undefined) => void): void {
        this._onStatus = cb;
    }

    /** Read config, show/hide, and (re)start polling. Call on activation and on relevant config changes. */
    start(): void {
        this._stopPolling();
        const url = this._url();
        if (!url) {
            this._item.hide();
            this._onStatus?.(undefined, undefined);
            return;
        }
        this._item.show();
        this._setLoading();
        void this._check();
        this._pollTimer = setInterval(() => { void this._check(); }, this._pollIntervalMs());
    }

    /** Force an immediate health check (e.g. after "Check State Service Health"). */
    async refresh(): Promise<void> {
        await this._check();
    }

    lastReachable(): boolean | undefined {
        return this._lastReachable;
    }

    dispose(): void {
        this._stopPolling();
        this._item.dispose();
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private _url(): string {
        return vscode.workspace.getConfiguration('strata').get<string>('stateService.url', '').trim();
    }

    private _pollIntervalMs(): number {
        const seconds = vscode.workspace.getConfiguration('strata').get<number>('stateService.pollIntervalSeconds', 60);
        return Math.max(10, seconds) * 1000;
    }

    private _stopPolling(): void {
        if (this._pollTimer !== undefined) {
            clearInterval(this._pollTimer);
            this._pollTimer = undefined;
        }
    }

    private _setLoading(): void {
        this._item.text = '$(sync~spin) state-service';
        this._item.tooltip = 'Strata state service \u2014 checking\u2026';
        this._item.backgroundColor = undefined;
    }

    private async _check(): Promise<void> {
        const url = this._url();
        if (!this._client || !url) {
            return;
        }
        try {
            const health = await this._client.getServerHealth(url);
            this._lastReachable = health.reachable;
            if (health.reachable) {
                this._item.text = '$(radio-tower) state-service';
                this._item.tooltip = new vscode.MarkdownString(
                    `**Strata state service**\n\n${url}\n\nReachable \u2014 last checked ${new Date().toLocaleTimeString()}`,
                );
                this._item.backgroundColor = undefined;
            } else {
                this._item.text = '$(warning) state-service';
                this._item.tooltip = new vscode.MarkdownString(
                    `**Strata state service**\n\n${url}\n\nUnreachable (status ${health.status_code ?? 'n/a'})`,
                );
                this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
            }
        } catch (err) {
            this._lastReachable = false;
            this._item.text = '$(error) state-service';
            this._item.tooltip = new vscode.MarkdownString(`**Strata state service**\n\n${url}\n\nError: ${String(err)}`);
            this._item.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
        }
        this._onStatus?.(url, this._lastReachable);
    }
}
