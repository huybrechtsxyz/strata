/**
 * CodeLensProvider — shows inline action buttons above strata YAML documents.
 *
 * Renders at the first line of a strata document:
 *   [Validate] [Build] [Deploy (Dry Run)] [Schema] [Guide]
 *
 * Only active for files that contain a strata apiVersion header.
 *
 * TODO: implement provideCodeLenses() to detect the document kind from the
 *   YAML content and show context-appropriate lenses (e.g. only show Build
 *   for deployment files).
 */

import * as vscode from 'vscode';

/** Strata apiVersion prefixes that identify a strata document. */
const STRATA_API_VERSIONS = [
    'strata.omp.com/v1',
    'strata.huybrechts.xyz/v1',
];

/** Kinds that support the Build action. */
const BUILDABLE_KINDS = ['deployment'];

export class CodeLensProvider
    implements vscode.CodeLensProvider, vscode.Disposable {
    private readonly _onDidChangeCodeLenses =
        new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    private _subscription: vscode.Disposable | undefined;

    // ── Public API ─────────────────────────────────────────────────────────────

    /**
     * Register the provider for YAML files.  Call once from extension activate().
     */
    register(context: vscode.ExtensionContext): void {
        this._subscription = vscode.languages.registerCodeLensProvider(
            { language: 'yaml', scheme: 'file' },
            this,
        );
        context.subscriptions.push(this._subscription);

        // Refresh lenses when config changes
        context.subscriptions.push(
            vscode.workspace.onDidChangeConfiguration((e) => {
                if (e.affectsConfiguration('strata.showCodeLens')) {
                    this._onDidChangeCodeLenses.fire();
                }
            }),
        );
    }

    // ── vscode.CodeLensProvider ────────────────────────────────────────────────

    /**
     * Return CodeLens items for the document.
     * TODO: parse the YAML to detect apiVersion and kind, then build lenses
     *   based on the kind (e.g. deployment gets Build + Deploy Dry Run).
     */
    provideCodeLenses(
        document: vscode.TextDocument,
    ): vscode.CodeLens[] {
        const config = vscode.workspace.getConfiguration('strata');
        if (!config.get<boolean>('showCodeLens', true)) {
            return [];
        }

        if (!this._isStrataDocument(document)) {
            return [];
        }

        // All actions anchor to line 0 of the document
        const topRange = new vscode.Range(0, 0, 0, 0);

        const kind = this._detectKind(document);
        const lenses: vscode.CodeLens[] = [];

        // Validate — always shown for strata documents
        lenses.push(new vscode.CodeLens(topRange, {
            title: '$(check) Validate',
            command: 'strata.validateCurrentFile',
            tooltip: 'Run strata validate on this file',
        }));

        // Build — only for deployment files
        if (kind && BUILDABLE_KINDS.includes(kind)) {
            lenses.push(new vscode.CodeLens(topRange, {
                title: '$(play) Build (Dry Run)',
                command: 'strata.buildDryRun',
                tooltip: 'Run strata build run --dry-run for this deployment',
                arguments: [document.uri.fsPath],
            }));
            lenses.push(new vscode.CodeLens(topRange, {
                title: '$(rocket) Deploy (Dry Run)',
                command: 'strata.deployDryRun',
                tooltip: 'Run strata deploy run --dry-run for this deployment',
                arguments: [document.uri.fsPath],
            }));
        }

        // Guide — always shown
        lenses.push(new vscode.CodeLens(topRange, {
            title: '$(list-ordered) Guide',
            command: 'strata.showGuide',
            tooltip: 'Show workspace readiness checklist',
        }));

        return lenses;
    }

    dispose(): void {
        this._subscription?.dispose();
        this._onDidChangeCodeLenses.dispose();
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    /**
     * Return true if the document appears to be a strata YAML file.
     * Detection: any line starts with `apiVersion: strata.`
     * TODO: improve with a proper YAML parse for accuracy.
     */
    private _isStrataDocument(document: vscode.TextDocument): boolean {
        if (document.languageId !== 'yaml') {
            return false;
        }
        for (let i = 0; i < Math.min(document.lineCount, 20); i++) {
            const line = document.lineAt(i).text.trim();
            if (STRATA_API_VERSIONS.some((v) => line.startsWith(`apiVersion: ${v}`))) {
                return true;
            }
        }
        return false;
    }

    /**
     * Extract the `kind:` value from the document.
     * TODO: use a proper YAML parser instead of regex.
     */
    private _detectKind(document: vscode.TextDocument): string | null {
        for (let i = 0; i < Math.min(document.lineCount, 20); i++) {
            const line = document.lineAt(i).text.trim();
            const match = line.match(/^kind:\s*(\S+)/);
            if (match) {
                return match[1].toLowerCase();
            }
        }
        return null;
    }
}
