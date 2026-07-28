/**
 * DiagnosticsProvider — runs `strata validate` on save and pushes results
 * to the VS Code Problems panel as squiggly underlines.
 *
 * Field path → document range mapping:
 *   The CLI returns errors with an optional `field` path like
 *   "spec.stages[0].provisioner".  We extract the last key segment and scan
 *   the document for the first line matching `^\s*<key>\s*:` to locate the
 *   error position.  This is a best-effort heuristic — precise enough for
 *   most single-document strata YAMLs.
 */

import * as vscode from 'vscode';
import { StrataCLIError } from '../strataClient';
import type { StrataClient, ValidationError, PolicyCheckData } from '../strataClient';

/** Strata apiVersion prefixes that identify a strata YAML document. */
const STRATA_API_PREFIXES = ['strata.omp.com/v1', 'strata.huybrechts.xyz/v1'];

/**
 * Matches workspace-local template source directories (e.g. `.strata/templates/tenant.yaml`).
 * Files here intentionally contain unrendered `{{ var }}` Jinja2 placeholders — they are
 * rendered into concrete documents by `strata new` and must never be validated in place.
 */
const TEMPLATE_DIR_PATTERN = /[\\/]\.strata[\\/]templates[\\/]/;

/** Diagnostic source label shown in the Problems panel. */
const SOURCE = 'strata';

export interface ValidateResult {
    passed: boolean;
    errorCount: number;
}

/** Fired when on-save validation discovers new errors (not when user triggers manually). */
export interface ValidationChangeEvent {
    uri: vscode.Uri;
    relativePath: string;
    previousCount: number;
    currentCount: number;
}

export class DiagnosticsProvider implements vscode.Disposable {
    private readonly _collection: vscode.DiagnosticCollection;
    private readonly _subscriptions: vscode.Disposable[] = [];
    private _client: StrataClient | undefined;

    /** Per-file error count for detecting new errors on save. */
    private readonly _errorCounts = new Map<string, number>();

    /** Debounce timer for validateOnType. */
    private _typeTimer: ReturnType<typeof setTimeout> | undefined;

    private readonly _onDidChangeValidation = new vscode.EventEmitter<ValidationChangeEvent>();
    /** Fires when on-save validation detects a change in error count. */
    readonly onDidChangeValidation = this._onDidChangeValidation.event;

    constructor() {
        this._collection = vscode.languages.createDiagnosticCollection(SOURCE);
    }

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    /** Register on-save, on-type, and on-close listeners. Call once from activate(). */
    register(): void {
        this._subscriptions.push(
            vscode.workspace.onDidSaveTextDocument((doc) => {
                const config = vscode.workspace.getConfiguration('strata');
                if (config.get<boolean>('validateOnSave', true)) {
                    void this._validateOnSave(doc);
                }
            }),
            vscode.workspace.onDidChangeTextDocument((e) => {
                const config = vscode.workspace.getConfiguration('strata');
                if (config.get<boolean>('validateOnType', false)) {
                    if (this._typeTimer) clearTimeout(this._typeTimer);
                    this._typeTimer = setTimeout(() => {
                        void this.validateDocument(e.document);
                    }, 1500);
                }
            }),
            vscode.workspace.onDidCloseTextDocument((doc) => {
                this._collection.delete(doc.uri);
                this._errorCounts.delete(doc.uri.toString());
            }),
        );
    }

    /**
     * Internal: validate on save and fire change event when error count differs.
     * Separated from the public validateDocument() so that manual "Validate Current File"
     * calls don't trigger background notifications.
     */
    private async _validateOnSave(doc: vscode.TextDocument): Promise<void> {
        const key = doc.uri.toString();
        const previousCount = this._errorCounts.get(key) ?? 0;
        const result = await this.validateDocument(doc);
        this._errorCounts.set(key, result.errorCount);

        if (result.errorCount !== previousCount) {
            this._onDidChangeValidation.fire({
                uri: doc.uri,
                relativePath: vscode.workspace.asRelativePath(doc.uri),
                previousCount,
                currentCount: result.errorCount,
            });
        }
    }

    /**
     * Validate a document via the CLI and push results to the Problems panel.
     * Returns a summary so callers (e.g. the command handler) can notify the user.
     */
    async validateDocument(document: vscode.TextDocument): Promise<ValidateResult> {
        if (!this._client) {
            return { passed: true, errorCount: 0 };
        }
        if (!this._isStrataDocument(document)) {
            return { passed: true, errorCount: 0 };
        }

        try {
            const result = await this._client.validateFile(document.uri.fsPath);
            const diagnostics = this._toDiagnostics(result.errors, document);

            // Add version-lock-specific hints (informational, not errors)
            const versionHints = this._getVersionLockHints(document);
            diagnostics.push(...versionHints);

            this._collection.set(document.uri, diagnostics);
            return { passed: result.validation_passed, errorCount: result.errors.length };
        } catch (err) {
            // CLI itself failed (parse error, missing file, etc.) — surface as a
            // single error on line 0 so the user knows validation couldn't run.
            const msg = err instanceof StrataCLIError
                ? (err.response?.errors?.join('; ') ?? err.stderr)
                : String(err);
            const diagnostic = new vscode.Diagnostic(
                new vscode.Range(0, 0, 0, 0),
                `Strata validation failed: ${msg}`,
                vscode.DiagnosticSeverity.Error,
            );
            diagnostic.source = SOURCE;
            this._collection.set(document.uri, [diagnostic]);
            return { passed: false, errorCount: 1 };
        }
    }

    /** Clear all diagnostics (call from deactivate). */
    clearAll(): void {
        this._collection.clear();
    }

    /**
     * Run `strata policy check` for a deployment file and push violations to
     * the Problems panel as Error (enforcement=deny) or Warning (enforcement=warn).
     *
     * Returns the raw result so callers can gate the deploy confirmation dialog.
     * Returns null if the CLI is unavailable or the deployment has no policies.
     */
    async checkPolicyDiagnostics(deploymentUri: vscode.Uri): Promise<PolicyCheckData | null> {
        if (!this._client) return null;
        try {
            const result = await this._client.checkPolicy(deploymentUri.fsPath);
            const policyDiags: vscode.Diagnostic[] = [];
            for (const r of result.results) {
                if (r.passed) continue;
                const severity = r.enforcement === 'deny'
                    ? vscode.DiagnosticSeverity.Error
                    : vscode.DiagnosticSeverity.Warning;
                for (const violation of r.violations) {
                    const diag = new vscode.Diagnostic(
                        new vscode.Range(0, 0, 0, 0),
                        `Policy "${r.policy}" [${r.phase}] (${r.enforcement}): ${violation}`,
                        severity,
                    );
                    diag.source = 'strata-policy';
                    diag.code = r.policy;
                    policyDiags.push(diag);
                }
            }
            // Keep existing validation diagnostics, replace only policy ones
            const existing = [...(this._collection.get(deploymentUri) ?? [])];
            const validationDiags = existing.filter(d => d.source !== 'strata-policy');
            this._collection.set(deploymentUri, [...validationDiags, ...policyDiags]);
            return result;
        } catch {
            return null; // no policies defined or CLI unavailable — don't block
        }
    }

    dispose(): void {
        if (this._typeTimer) clearTimeout(this._typeTimer);
        this._collection.dispose();
        this._onDidChangeValidation.dispose();
        this._subscriptions.forEach((d) => d.dispose());
    }

    // ── Core conversion ───────────────────────────────────────────────────────

    /**
     * Convert strata ValidationError[] into vscode.Diagnostic[].
     *
     * Field path mapping:
     *   - null / empty → line 0
     *   - "spec.stages[0].provisioner" → extract last segment "provisioner",
     *     scan document for first `^\s*provisioner\s*:` match
     */
    private _toDiagnostics(
        errors: ValidationError[],
        document: vscode.TextDocument,
    ): vscode.Diagnostic[] {
        return errors.map((err) => {
            const range = err.field
                ? this._findFieldRange(err.field, document)
                : new vscode.Range(0, 0, 0, document.lineAt(0).text.length);

            const detail = err.value ? ` (got: ${err.value})` : '';
            const diagnostic = new vscode.Diagnostic(
                range,
                `${err.message}${detail}`,
                vscode.DiagnosticSeverity.Error,
            );
            diagnostic.source = SOURCE;
            diagnostic.code = err.code;

            // Phase 1 = structural (Pydantic), Phase 2 = cross-reference
            if (err.context) {
                diagnostic.relatedInformation = [
                    new vscode.DiagnosticRelatedInformation(
                        new vscode.Location(document.uri, range),
                        `Phase ${err.phase}: ${JSON.stringify(err.context)}`,
                    ),
                ];
            }

            return diagnostic;
        });
    }

    // ── Field path → document range ───────────────────────────────────────────

    /**
     * Map a dotted field path to a Range in the document.
     *
     * Strategy:
     *   1. Extract all key segments (strip `[n]` array indices).
     *   2. Walk from the last segment backwards until one is found in the doc.
     *   3. Try to find the key under the correct parent block to avoid false
     *      matches (e.g. "name" appearing many times).
     *   4. Highlight from the key start to end of the line's value portion.
     */
    private _findFieldRange(fieldPath: string, document: vscode.TextDocument): vscode.Range {
        // Split "spec.stages[0].provisioner" → ["spec", "stages", "provisioner"]
        const segments = fieldPath
            .split(/[.\[\]]/)
            .filter((s) => s && !/^\d+$/.test(s));

        if (segments.length === 0) {
            return new vscode.Range(0, 0, 0, document.lineAt(0).text.length);
        }

        // Try matching from most-specific (last) to least-specific (first parent)
        for (let i = segments.length - 1; i >= 0; i--) {
            const key = segments[i];
            const line = this._scanForKey(key, document);
            if (line !== -1) {
                return this._keyRange(line, key, document);
            }
        }

        // Fallback: line 0
        return new vscode.Range(0, 0, 0, document.lineAt(0).text.length);
    }

    /** Scan document lines for `key:` — returns the first matching line number or -1. */
    private _scanForKey(key: string, document: vscode.TextDocument): number {
        const pattern = new RegExp(`^\\s*${this._escapeRegex(key)}\\s*:`);
        for (let i = 0; i < document.lineCount; i++) {
            if (pattern.test(document.lineAt(i).text)) {
                return i;
            }
        }
        return -1;
    }

    /** Return a Range covering the value portion of a `key: value` line. */
    private _keyRange(lineNum: number, key: string, document: vscode.TextDocument): vscode.Range {
        const lineText = document.lineAt(lineNum).text;
        const keyStart = lineText.indexOf(key);
        if (keyStart === -1) {
            return new vscode.Range(lineNum, 0, lineNum, lineText.length);
        }
        // Try to highlight the value after `: `, or just the key if no value
        const colonIdx = lineText.indexOf(':', keyStart + key.length);
        const valueStart = colonIdx !== -1 ? colonIdx + 1 : keyStart;
        const trimmedValue = lineText.slice(valueStart).trim();
        const valueOffset = trimmedValue.length > 0
            ? lineText.lastIndexOf(trimmedValue, lineText.length)
            : keyStart;
        const end = valueOffset + trimmedValue.length || lineText.length;
        return new vscode.Range(lineNum, Math.max(0, valueOffset), lineNum, end);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Return true only for YAML files that contain a strata apiVersion header. */
    private _isStrataDocument(document: vscode.TextDocument): boolean {
        if (document.languageId !== 'yaml' &&
            !document.uri.fsPath.endsWith('.yaml') &&
            !document.uri.fsPath.endsWith('.yml')) {
            return false;
        }
        // Template source files are never validated as concrete documents —
        // skip before spawning the CLI to avoid false-positive schema errors.
        if (TEMPLATE_DIR_PATTERN.test(document.uri.fsPath)) {
            return false;
        }
        // Fast scan of first 20 lines — avoid validating every YAML in the workspace
        for (let i = 0; i < Math.min(document.lineCount, 20); i++) {
            const line = document.lineAt(i).text.trim();
            if (STRATA_API_PREFIXES.some((p) => line.startsWith(`apiVersion: ${p}`))) {
                return true;
            }
        }
        return false;
    }

    private _escapeRegex(s: string): string {
        return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    // ── Version lock hints ────────────────────────────────────────────────────

    /**
     * Produce informational diagnostics for version-lock documents:
     * - Pointer lock → shows the source path
     * - Hash present → notes hash-verified pointer
     * - Wave lock → shows wave number
     */
    private _getVersionLockHints(document: vscode.TextDocument): vscode.Diagnostic[] {
        const hints: vscode.Diagnostic[] = [];

        let isVersionLock = false;
        let sourceLine = -1;
        let sourceValue = '';
        let hashLine = -1;
        let hashValue = '';
        let waveLine = -1;
        let waveValue = '';

        for (let i = 0; i < Math.min(document.lineCount, 50); i++) {
            const text = document.lineAt(i).text;
            if (/^\s*kind:\s*version-lock/i.test(text)) {
                isVersionLock = true;
            }
            const srcMatch = text.match(/^\s*source:\s*(.+)/);
            if (srcMatch && sourceLine === -1) {
                sourceLine = i;
                sourceValue = srcMatch[1].trim().replace(/^["']|["']$/g, '');
            }
            const hashMatch = text.match(/^\s*hash:\s*(.+)/);
            if (hashMatch && hashLine === -1) {
                hashLine = i;
                hashValue = hashMatch[1].trim().replace(/^["']|["']$/g, '');
            }
            const waveMatch = text.match(/^\s*wave:\s*(\d+)/);
            if (waveMatch && waveLine === -1) {
                waveLine = i;
                waveValue = waveMatch[1];
            }
        }

        if (!isVersionLock) return hints;

        // Pointer lock hint
        if (sourceLine >= 0 && sourceValue) {
            const range = new vscode.Range(sourceLine, 0, sourceLine, document.lineAt(sourceLine).text.length);
            const msg = hashValue
                ? `Pointer lock → ${sourceValue} (hash-verified: ${hashValue.slice(0, 12)}…)`
                : `Pointer lock → ${sourceValue}`;
            const hint = new vscode.Diagnostic(range, msg, vscode.DiagnosticSeverity.Information);
            hint.source = 'strata-version';
            hints.push(hint);
        }

        // Wave lock hint
        if (waveLine >= 0) {
            const range = new vscode.Range(waveLine, 0, waveLine, document.lineAt(waveLine).text.length);
            const hint = new vscode.Diagnostic(
                range,
                `Wave ${waveValue} lock — partial rollout`,
                vscode.DiagnosticSeverity.Information,
            );
            hint.source = 'strata-version';
            hints.push(hint);
        }

        return hints;
    }
}
