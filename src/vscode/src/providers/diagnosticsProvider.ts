/**
 * diagnosticsProvider.ts — runs `strata validate` and pushes findings to the
 * Problems panel.
 *
 * Whole-solution, not per-file. v2's `validate` command has no `-f FILE` —
 * it always discovers and checks the entire solution from `strata.yaml`
 * (ADR-0015) — so one run's `diagnostics` array covers every document at
 * once. This provider buckets that array by `Diagnostic.source` (already a
 * solution-relative path) into per-file `DiagnosticCollection` entries,
 * rather than shelling out to the CLI once per saved file the way v1's
 * per-file `validate -f` model required.
 */

import * as path from 'path';
import * as vscode from 'vscode';
import { findFieldRange } from '../fieldLocation';
import { StrataCLIError, StrataCLINotFoundError, StrataClient } from '../strataClient';

const SOURCE = 'strata';

export interface ValidateResult {
    passed: boolean;
    errorCount: number;
}

export class DiagnosticsProvider implements vscode.Disposable {
    private readonly _collection = vscode.languages.createDiagnosticCollection(SOURCE);
    private readonly _subscriptions: vscode.Disposable[] = [];
    private _client: StrataClient | undefined;
    private _timer: ReturnType<typeof setTimeout> | undefined;

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Register the on-save trigger. Call once from `activate()`. */
    register(context: vscode.ExtensionContext): void {
        this._subscriptions.push(
            vscode.workspace.onDidSaveTextDocument((doc) => {
                if (doc.languageId !== 'yaml' && !doc.uri.fsPath.endsWith('.yaml') && !doc.uri.fsPath.endsWith('.yml')) {
                    return;
                }
                const config = vscode.workspace.getConfiguration('strata');
                if (config.get<boolean>('validateOnSave', true)) {
                    this._debounced();
                }
            }),
        );
        context.subscriptions.push(...this._subscriptions);
    }

    private _debounced(): void {
        if (this._timer) clearTimeout(this._timer);
        // A solution-wide check is one CLI call regardless of which file
        // changed, so a short debounce just coalesces rapid saves.
        this._timer = setTimeout(() => void this.validateSolution(), 300);
    }

    /** Run `strata validate` and refresh the Problems panel. Safe to call directly (e.g. a command). */
    async validateSolution(): Promise<ValidateResult> {
        if (!this._client) {
            return { passed: true, errorCount: 0 };
        }

        try {
            const envelope = await this._client.validate();
            const root = envelope.context.root;
            const bySource = new Map<string, vscode.Diagnostic[]>();

            for (const item of envelope.diagnostics) {
                const uriString = this._resolveUri(item.source, root);
                if (!uriString) continue;
                if (!bySource.has(uriString)) bySource.set(uriString, []);
                bySource.get(uriString)!.push(await this._toDiagnostic(item, uriString));
            }

            this._collection.clear();
            for (const [uriString, diags] of bySource) {
                this._collection.set(vscode.Uri.file(uriString), diags);
            }

            return { passed: envelope.ok, errorCount: envelope.summary.errors };
        } catch (err) {
            this._collection.clear();
            const message =
                err instanceof StrataCLINotFoundError
                    ? err.message
                    : err instanceof StrataCLIError
                        ? (err.stderr || err.message)
                        : String(err);
            void vscode.window.showWarningMessage(`Strata: validate failed to run — ${message}`);
            return { passed: false, errorCount: 0 };
        }
    }

    clearAll(): void {
        this._collection.clear();
    }

    dispose(): void {
        if (this._timer) clearTimeout(this._timer);
        this._collection.dispose();
        this._subscriptions.forEach((d) => d.dispose());
    }

    // ── helpers ──────────────────────────────────────────────────────────

    private _resolveUri(source: string | undefined, root: string | undefined): string | undefined {
        if (!source) return undefined;
        return root ? path.resolve(root, source) : source;
    }

    private async _toDiagnostic(
        item: { severity: string; message: string; location?: string; code?: string },
        uriString: string,
    ): Promise<vscode.Diagnostic> {
        const severity =
            item.severity === 'error'
                ? vscode.DiagnosticSeverity.Error
                : item.severity === 'warning'
                    ? vscode.DiagnosticSeverity.Warning
                    : vscode.DiagnosticSeverity.Information;

        let range = new vscode.Range(0, 0, 0, 0);
        if (item.location) {
            try {
                const document = await vscode.workspace.openTextDocument(uriString);
                range = findFieldRange(item.location, document);
            } catch {
                // File not readable (deleted since validate ran, etc.) — fall back to line 0.
            }
        }

        const diagnostic = new vscode.Diagnostic(range, item.message, severity);
        diagnostic.source = SOURCE;
        if (item.code) diagnostic.code = item.code;
        return diagnostic;
    }
}
