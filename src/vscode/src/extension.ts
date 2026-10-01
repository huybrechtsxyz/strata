/**
 * extension.ts — VS Code extension entry point.
 *
 * activate() runs when VS Code detects a `strata.yaml` in the open
 * workspace (see `activationEvents` in package.json) — v2's own solution
 * marker (ADR-0015), replacing v1's `.strata/solution.json`.
 *
 * Deliberately small: v2's CLI surface today is `validate`, `graph`,
 * `values`, `version` (plus `build`/`deploy`/`sln`, not yet wired into the
 * extension). Every provider here maps onto a command that actually exists,
 * rather than scaffolding UI ahead of a backend to call.
 */

import * as vscode from 'vscode';
import { findFieldRange } from './fieldLocation';
import { DiagnosticsProvider } from './providers/diagnosticsProvider';
import { GraphViewProvider } from './providers/graphViewProvider';
import { StatusBarProvider } from './providers/statusBarProvider';
import { StrataClient } from './strataClient';

let _client: StrataClient | undefined;
let _diagnostics: DiagnosticsProvider | undefined;
let _graphView: GraphViewProvider | undefined;
let _statusBar: StatusBarProvider | undefined;

export function activate(context: vscode.ExtensionContext): void {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) return;

    _client = new StrataClient(folder.uri.fsPath);

    _diagnostics = new DiagnosticsProvider();
    _diagnostics.setClient(_client);
    _diagnostics.register(context);
    context.subscriptions.push(_diagnostics);

    _graphView = new GraphViewProvider();
    _graphView.setClient(_client);
    context.subscriptions.push(_graphView, vscode.window.registerTreeDataProvider('strataGraph', _graphView));

    _statusBar = new StatusBarProvider();
    _statusBar.setClient(_client);
    context.subscriptions.push(_statusBar);

    context.subscriptions.push(
        vscode.commands.registerCommand('strata.validate', async () => {
            const result = await _diagnostics!.validateSolution();
            void vscode.window.showInformationMessage(
                result.passed ? 'Strata: solution is valid.' : `Strata: ${result.errorCount} validation error(s) — see Problems panel.`,
            );
            await _statusBar?.refresh();
        }),

        vscode.commands.registerCommand('strata.refreshGraph', async () => {
            await _graphView!.refresh();
        }),

        vscode.commands.registerCommand('strata.openGraphLocation', async (uri: vscode.Uri, location: string) => {
            const document = await vscode.workspace.openTextDocument(uri);
            const editor = await vscode.window.showTextDocument(document);
            const range = findFieldRange(location, document);
            editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
            editor.selection = new vscode.Selection(range.start, range.end);
        }),
    );

    void _diagnostics.validateSolution();
    void _graphView.refresh();
    void _statusBar.refresh();
}

export function deactivate(): void {
    _diagnostics?.clearAll();
}
