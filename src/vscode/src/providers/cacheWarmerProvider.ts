/**
 * CacheWarmerProvider — background warmer for the resolved-model cache (ADR-0026).
 *
 * Watches strata YAML files in the workspace. On save, debounces 500ms then runs
 * `strata cache warm --all` in the background so the SQLite cache
 * (`.strata/cache/model/cache.db`) stays fresh while the operator is actively
 * editing — by the time they run a fleet-wide command in the terminal, the
 * cache is already warm.
 *
 * Design notes:
 * - Warming is entirely best-effort: failures are swallowed (logged to the
 *   output channel only) and never surfaced as error popups. A cold/stale
 *   cache is not a user-facing problem — commands auto-warm transparently.
 * - `--all` (not per-file) because CacheController currently only exposes
 *   whole-fleet or single-deployment warming, and mapping "which deployment(s)
 *   reference this saved file" would require loading every registered
 *   deployment's inputs anyway. Simpler and correct, at the cost of warming
 *   deployments unaffected by the specific save.
 * - Respects `strata.cache.backgroundWarm` (default true).
 */

import * as vscode from 'vscode';
import type { StrataClient } from '../strataClient';

const DEBOUNCE_MS = 500;

export class CacheWarmerProvider implements vscode.Disposable {
    private readonly _item: vscode.StatusBarItem;
    private _client: StrataClient | undefined;
    private _watcher: vscode.FileSystemWatcher | undefined;
    private _debounceTimer: NodeJS.Timeout | undefined;
    private _warming = false;
    private _lastWarmedAt: Date | undefined;
    private _lastError: string | undefined;
    private readonly _output: vscode.OutputChannel;

    constructor() {
        this._output = vscode.window.createOutputChannel('Strata Cache');
        this._item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
        this._item.command = 'strata.refreshCache';
        this._renderIdle();
    }

    setClient(client: StrataClient): void {
        this._client = client;
    }

    private _enabled(): boolean {
        return vscode.workspace.getConfiguration('strata').get<boolean>('cache.backgroundWarm', true);
    }

    /** Register the file watcher, status bar item, and manual refresh command. */
    register(context: vscode.ExtensionContext): void {
        this._watcher = vscode.workspace.createFileSystemWatcher('**/*.yaml');
        const onSave = () => this._scheduleWarm();
        this._watcher.onDidChange(onSave);
        this._watcher.onDidCreate(onSave);

        context.subscriptions.push(
            vscode.commands.registerCommand('strata.refreshCache', () => this._warmNow()),
        );

        if (this._enabled()) {
            this._item.show();
            // Initial background warm shortly after activation — non-blocking.
            this._scheduleWarm();
        }
    }

    private _scheduleWarm(): void {
        if (!this._enabled()) return;
        if (this._debounceTimer) clearTimeout(this._debounceTimer);
        this._debounceTimer = setTimeout(() => void this._warmNow(), DEBOUNCE_MS);
    }

    private async _warmNow(): Promise<void> {
        if (!this._client || this._warming) return;
        this._warming = true;
        this._renderWarming();
        try {
            await this._client.warmCache();
            this._lastWarmedAt = new Date();
            this._lastError = undefined;
            this._output.appendLine(`[${this._lastWarmedAt.toISOString()}] Cache warmed successfully.`);
        } catch (err) {
            this._lastError = err instanceof Error ? err.message : String(err);
            this._output.appendLine(`Cache warm failed (non-fatal): ${this._lastError}`);
        } finally {
            this._warming = false;
            this._renderIdle();
        }
    }

    // ── Status bar rendering ────────────────────────────────────────────────────

    private _renderWarming(): void {
        this._item.text = '$(sync~spin) cache';
        this._item.tooltip = 'Strata: warming resolved-model cache…';
    }

    private _renderIdle(): void {
        if (this._lastError) {
            this._item.text = '$(warning) cache';
            this._item.tooltip = `Strata cache: last warm failed — ${this._lastError}\nClick to retry.`;
            return;
        }
        if (this._lastWarmedAt) {
            this._item.text = '$(database) cache';
            this._item.tooltip = `Strata cache: warm as of ${this._lastWarmedAt.toLocaleTimeString()}\nClick to refresh now.`;
            return;
        }
        this._item.text = '$(database) cache';
        this._item.tooltip = 'Strata cache: not yet warmed. Click to warm now.';
    }

    dispose(): void {
        if (this._debounceTimer) clearTimeout(this._debounceTimer);
        this._watcher?.dispose();
        this._item.dispose();
        this._output.dispose();
    }
}
