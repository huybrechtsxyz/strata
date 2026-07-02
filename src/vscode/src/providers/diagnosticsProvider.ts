/**
 * DiagnosticsProvider — runs `strata validate` on save and pushes errors
 * to the VS Code Problems panel as squiggly underlines.
 *
 * Only activates for YAML files inside a strata workspace (files whose
 * path contains one of the registered config dirs, or any *.yaml when the
 * workspace is initialized).
 *
 * TODO: implement _validate() to call StrataClient.validateFile() and
 *   convert ValidationError[] into vscode.Diagnostic[].
 */

import * as vscode from 'vscode';
import type { StrataClient } from '../strataClient';

export class DiagnosticsProvider implements vscode.Disposable {
    private readonly _collection: vscode.DiagnosticCollection;
    private readonly _subscriptions: vscode.Disposable[] = [];
    private _client: StrataClient | undefined;

    constructor() {
        this._collection = vscode.languages.createDiagnosticCollection('strata');
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /**
     * Register the on-save listener.  Call once from extension activate().
     */
    register(): void {
        this._subscriptions.push(
            vscode.workspace.onDidSaveTextDocument((doc) => {
                const config = vscode.workspace.getConfiguration('strata');
                if (config.get<boolean>('validateOnSave', true)) {
                    this._onSave(doc);
                }
            }),
            vscode.workspace.onDidCloseTextDocument((doc) => {
                this._collection.delete(doc.uri);
            }),
        );
    }

    /**
     * Validate a specific document and update diagnostics.
     * Can be called from commands as well as the on-save listener.
     * TODO: call this._client.validateFile() and push results.
     */
    async validateDocument(document: vscode.TextDocument): Promise<void> {
        if (!this._client) {
            return;
        }
        if (!this._isStrataNYaml(document)) {
            return;
        }

        // TODO: const result = await this._client.validateFile(document.uri.fsPath);
        // TODO: const diagnostics = this._toDiagnostics(result.errors, document);
        // TODO: this._collection.set(document.uri, diagnostics);

        // Placeholder — clear while not implemented
        this._collection.set(document.uri, []);
    }

    /**
     * Clear all diagnostics (e.g. when workspace is closed).
     */
    clearAll(): void {
        this._collection.clear();
    }

    dispose(): void {
        this._collection.dispose();
        this._subscriptions.forEach((d) => d.dispose());
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private _onSave(document: vscode.TextDocument): void {
        void this.validateDocument(document);
    }

    /**
     * Return true for YAML files that are likely strata documents.
     * TODO: refine to only match files in registered config paths.
     */
    private _isStrataNYaml(document: vscode.TextDocument): boolean {
        return (
            document.languageId === 'yaml' ||
            document.uri.fsPath.endsWith('.yaml') ||
            document.uri.fsPath.endsWith('.yml')
        );
    }

    /**
     * Convert strata ValidationError[] into vscode.Diagnostic[].
     * TODO: implement — map field paths to document ranges.
     */
    private _toDiagnostics(
        _errors: Array<{ field: string | null; message: string; severity: string }>,
        _document: vscode.TextDocument,
    ): vscode.Diagnostic[] {
        // TODO: parse each error.field as a YAML key path, find its line,
        //   create a Diagnostic with appropriate severity and range.
        return [];
    }
}
