/**
 * StateServiceTailProvider — a `tail -f`-like OutputChannel for the strata
 * state-service server's `GET /v1/events/tail` endpoint (ADR-0065 Steps 2.6/2.7).
 *
 * Polls on the same interval as the status bar indicator, appending only
 * events newer than the last-seen `received_at` — a real tail-like experience
 * without the server needing any streaming/websocket support, matching Step
 * 2.6's deliberately simple, poll-friendly design. A plain OutputChannel, not
 * a webview, because it's a scrolling log of one-line summaries.
 */

import * as vscode from 'vscode';
import type { ServerTailEvent, StrataClient } from '../strataClient';

export class StateServiceTailProvider implements vscode.Disposable {
    private _channel: vscode.OutputChannel | undefined;
    private _client: StrataClient | undefined;
    private _pollTimer: ReturnType<typeof setInterval> | undefined;
    private _lastReceivedAt: string | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Open (or reveal) the tail channel and (re)start polling against `url`. */
    show(url: string, token: string, pollIntervalMs: number): void {
        if (!this._channel) {
            this._channel = vscode.window.createOutputChannel('Strata: State Service Tail');
        }
        this._channel.show(true);
        this._stopPolling();
        this._lastReceivedAt = undefined;
        this._channel.appendLine(`Tailing ${url} \u2014 polling every ${Math.round(pollIntervalMs / 1000)}s`);
        void this._poll(url, token);
        this._pollTimer = setInterval(() => { void this._poll(url, token); }, pollIntervalMs);
    }

    dispose(): void {
        this._stopPolling();
        this._channel?.dispose();
    }

    private _stopPolling(): void {
        if (this._pollTimer !== undefined) {
            clearInterval(this._pollTimer);
            this._pollTimer = undefined;
        }
    }

    private async _poll(url: string, token: string): Promise<void> {
        if (!this._client || !this._channel) {
            return;
        }
        try {
            const events = await this._client.getServerTail(url, token, 100);
            const lastSeen = this._lastReceivedAt;
            const fresh = lastSeen ? events.filter(e => (e.received_at ?? '') > lastSeen) : events;
            for (const event of fresh) {
                this._channel.appendLine(this._formatLine(event));
            }
            if (events.length > 0) {
                const newest = events[events.length - 1].received_at;
                if (newest) {
                    this._lastReceivedAt = newest;
                }
            }
        } catch (err) {
            this._channel.appendLine(`[error] ${err instanceof Error ? err.message : String(err)}`);
        }
    }

    private _formatLine(event: ServerTailEvent): string {
        const time = (event.received_at ?? '').replace('T', ' ').slice(0, 19);
        return (
            `${time}  ${event.record_type ?? ''}  ` +
            `workspace=${event.workspace ?? '-'}  deployment=${event.deployment ?? '-'}  outcome=${event.outcome ?? '-'}`
        );
    }
}
