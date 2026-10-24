/**
 * extension.ts — VS Code extension entry point.
 *
 * activate() runs when VS Code detects a `strata.yaml` in the open
 * workspace (see `activationEvents` in package.json) — v2's own solution
 * marker (ADR-0015), replacing v1's `.strata/solution.json`.
 *
 * Deliberately small: v2's CLI surface today is `validate`, `graph`,
 * `values`, `version`, `sln update` (plus `build`/`deploy`, not yet wired
 * into the extension). Every provider here maps onto a command that actually exists,
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
let _output: vscode.OutputChannel | undefined;

const UPDATE_CONFIRM =
    'Refresh the solution files? This rewrites .strata/ (including the editor schemas) and creates any missing ' +
    '.vscode/ and .github/ scaffold files. Existing files outside .strata/ are never overwritten.';

async function updateSolution(client: StrataClient, output: vscode.OutputChannel): Promise<void> {
    const choice = await vscode.window.showInformationMessage(UPDATE_CONFIRM, { modal: true }, 'Update');
    if (choice !== 'Update') return;

    try {
        const envelope = await client.slnUpdate();
        output.appendLine(`strata ${envelope.command} (${envelope.version})`);
        for (const diagnostic of envelope.diagnostics) {
            output.appendLine(`  ${diagnostic.severity}: ${diagnostic.message}`);
        }
        if (!envelope.ok) {
            const first = envelope.diagnostics.find((d) => d.severity === 'error');
            void vscode.window.showErrorMessage(`Strata: update failed — ${first?.message ?? 'see the Strata output channel'}`);
            return;
        }
        const changed = envelope.diagnostics.filter((d) => d.severity === 'info').length;
        const action = await vscode.window.showInformationMessage(`Strata: solution files refreshed (${changed} change(s)).`, 'Show Details');
        if (action === 'Show Details') output.show();
    } catch (err) {
        void vscode.window.showErrorMessage(`Strata: update failed — ${(err as Error).message}`);
    }
}

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

    _output = vscode.window.createOutputChannel('Strata');
    context.subscriptions.push(_output);

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

        vscode.commands.registerCommand('strata.updateSolution', async () => {
            await updateSolution(_client!, _output!);
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
