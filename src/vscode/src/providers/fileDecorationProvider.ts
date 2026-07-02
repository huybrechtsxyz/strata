/**
 * FileDecorationProvider — shows ✅ / ⚠️ / ❌ badges on strata YAML files
 * in the Explorer tree based on current diagnostics.
 *
 * Listens to DiagnosticsProvider.onDidChangeValidation and to the VS Code
 * DiagnosticCollection so badges update in real-time after save or on-type
 * validation.
 *
 * Toggled via the `strata.showFileDecorations` setting.
 */

import * as vscode from 'vscode';
import type { WorkspaceStatus } from '../strataClient';

/** Per-file validation state. */
interface FileState {
    errors: number;
    warnings: number;
}

export class FileDecorationProvider
    implements vscode.FileDecorationProvider, vscode.Disposable {
    private readonly _onDidChangeFileDecorations = new vscode.EventEmitter<vscode.Uri | vscode.Uri[]>();
    readonly onDidChangeFileDecorations = this._onDidChangeFileDecorations.event;

    /** Absolute path (lowercase on Windows) → state. */
    private readonly _states = new Map<string, FileState>();

    /** Set of known strata file paths (from last status). */
    private readonly _strataFiles = new Set<string>();

    private readonly _subscriptions: vscode.Disposable[] = [];
    private _registration: vscode.Disposable | undefined;

    // ── Public API ─────────────────────────────────────────────────────────────

    /** Register the decoration provider and listen for diagnostic changes. */
    register(): void {
        this._syncEnabled();

        // Re-evaluate when the setting changes
        this._subscriptions.push(
            vscode.workspace.onDidChangeConfiguration((e) => {
                if (e.affectsConfiguration('strata.showFileDecorations')) {
                    this._syncEnabled();
                }
            }),
        );

        // Listen to diagnostic changes from any source (our DiagnosticsProvider
        // writes to the `strata` collection, but we also pick up YAML extension
        // diagnostics for free).
        this._subscriptions.push(
            vscode.languages.onDidChangeDiagnostics((e) => {
                const changed: vscode.Uri[] = [];
                for (const uri of e.uris) {
                    if (this._updateState(uri)) {
                        changed.push(uri);
                    }
                }
                if (changed.length > 0) {
                    this._onDidChangeFileDecorations.fire(changed);
                }
            }),
        );
    }

    /** Update the set of known strata files from workspace status. */
    update(status: WorkspaceStatus): void {
        this._strataFiles.clear();
        const groups = status.profiles.paths ?? {};
        for (const entries of Object.values(groups)) {
            for (const entry of entries) {
                this._strataFiles.add(this._normKey(entry.path));
            }
        }
        // Re-fire for all known files so decorations refresh
        const uris = [...this._strataFiles].map((p) => vscode.Uri.file(p));
        if (uris.length > 0) {
            this._onDidChangeFileDecorations.fire(uris);
        }
    }

    // ── FileDecorationProvider ─────────────────────────────────────────────────

    provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
        const key = this._normKey(uri.fsPath);
        if (!this._strataFiles.has(key)) {
            return undefined;
        }

        const state = this._states.get(key);

        if (!state) {
            // File is known but has not been validated yet — no badge
            return undefined;
        }

        if (state.errors > 0) {
            return {
                badge: '❌',
                color: new vscode.ThemeColor('list.errorForeground'),
                tooltip: `${state.errors} validation error${state.errors > 1 ? 's' : ''}`,
                propagate: true,
            };
        }

        if (state.warnings > 0) {
            return {
                badge: '⚠️',
                color: new vscode.ThemeColor('list.warningForeground'),
                tooltip: `${state.warnings} warning${state.warnings > 1 ? 's' : ''}`,
                propagate: true,
            };
        }

        // Validated with zero issues
        return {
            badge: '✓',
            color: new vscode.ThemeColor('charts.green'),
            tooltip: 'Validation passed',
        };
    }

    dispose(): void {
        this._registration?.dispose();
        this._onDidChangeFileDecorations.dispose();
        this._subscriptions.forEach((d) => d.dispose());
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    /** Register or unregister based on the current setting value. */
    private _syncEnabled(): void {
        const enabled = vscode.workspace
            .getConfiguration('strata')
            .get<boolean>('showFileDecorations', true);

        if (enabled && !this._registration) {
            this._registration = vscode.window.registerFileDecorationProvider(this);
        } else if (!enabled && this._registration) {
            this._registration.dispose();
            this._registration = undefined;
        }
    }

    /**
     * Read current VS Code diagnostics for a URI and update our state map.
     * Returns true if the state changed (caller should fire onDidChange).
     */
    private _updateState(uri: vscode.Uri): boolean {
        const key = this._normKey(uri.fsPath);
        if (!this._strataFiles.has(key)) {
            return false;
        }

        const diagnostics = vscode.languages.getDiagnostics(uri);
        let errors = 0;
        let warnings = 0;
        for (const d of diagnostics) {
            if (d.severity === vscode.DiagnosticSeverity.Error) errors++;
            else if (d.severity === vscode.DiagnosticSeverity.Warning) warnings++;
        }

        const prev = this._states.get(key);
        if (prev && prev.errors === errors && prev.warnings === warnings) {
            return false; // no change
        }

        this._states.set(key, { errors, warnings });
        return true;
    }

    private _normKey(fsPath: string): string {
        const p = fsPath.replace(/\\/g, '/');
        return process.platform === 'win32' ? p.toLowerCase() : p;
    }
}
