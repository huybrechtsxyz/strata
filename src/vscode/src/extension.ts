/**
 * extension.ts — VS Code extension entry point.
 *
 * activate() is called when VS Code detects a .strata/solution.json file
 * in the open workspace (see activationEvents in package.json).
 *
 * All heavy logic lives in the provider classes — this file only wires
 * them together and registers commands.
 *
 * TODO for each TODO block below: replace throw with a real implementation.
 */

import * as vscode from 'vscode';
import { StrataClient, getCliPath, getWorkPath } from './strataClient';
import { StatusBarProvider } from './providers/statusBarProvider';
import { TreeViewProvider } from './providers/treeViewProvider';
import { DiagnosticsProvider } from './providers/diagnosticsProvider';
import { CodeLensProvider } from './providers/codeLensProvider';

// ---------------------------------------------------------------------------
// Extension state (singleton per VS Code window)
// ---------------------------------------------------------------------------

let _client: StrataClient | undefined;
let _statusBar: StatusBarProvider | undefined;
let _treeView: TreeViewProvider | undefined;
let _diagnostics: DiagnosticsProvider | undefined;
let _codeLens: CodeLensProvider | undefined;

// ---------------------------------------------------------------------------
// activate / deactivate
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
    const workPath = getWorkPath();

    // Require an open workspace folder
    if (!workPath) {
        vscode.window.showWarningMessage(
            'Strata: no workspace folder open — extension inactive.',
        );
        return;
    }

    // ── Build providers ────────────────────────────────────────────────────────

    _client = new StrataClient(getCliPath(), workPath);

    _statusBar = new StatusBarProvider();
    _statusBar.setClient(_client);

    _treeView = new TreeViewProvider();
    _treeView.setClient(_client);

    _diagnostics = new DiagnosticsProvider();
    _diagnostics.setClient(_client);

    _codeLens = new CodeLensProvider();

    // ── Register tree view ─────────────────────────────────────────────────────

    const treeView = vscode.window.createTreeView('strataWorkspace', {
        treeDataProvider: _treeView,
        showCollapseAll: true,
    });
    context.subscriptions.push(treeView);

    // ── Register providers ─────────────────────────────────────────────────────

    _diagnostics.register();
    _codeLens.register(context);
    _statusBar.show();

    // ── Register commands ──────────────────────────────────────────────────────

    context.subscriptions.push(
        vscode.commands.registerCommand('strata.initWorkspace', async () => {
            // TODO: run `strata sln init` via a VS Code terminal or StrataClient
            throw new Error('strata.initWorkspace — not implemented');
        }),

        vscode.commands.registerCommand('strata.validateCurrentFile', async () => {
            const doc = vscode.window.activeTextEditor?.document;
            if (!doc) { return; }
            if (!_diagnostics) { return; }
            await _diagnostics.validateDocument(doc);
            // TODO: show a notification with validation summary
        }),

        vscode.commands.registerCommand('strata.validateAll', async () => {
            // TODO: iterate workspace YAML files, call _diagnostics.validateDocument() for each
            throw new Error('strata.validateAll — not implemented');
        }),

        vscode.commands.registerCommand('strata.buildDryRun', async (filePath?: string) => {
            // TODO: call _client.buildRun(filePath ?? activeDocument, dryRun=true)
            throw new Error('strata.buildDryRun — not implemented');
        }),

        vscode.commands.registerCommand('strata.buildRun', async (filePath?: string) => {
            const confirmed = await vscode.window.showWarningMessage(
                'Run a full strata build? This will execute provisioners.',
                { modal: true },
                'Build',
            );
            if (confirmed !== 'Build') { return; }
            // TODO: call _client.buildRun(filePath ?? activeDocument, dryRun=false)
            throw new Error('strata.buildRun — not implemented');
        }),

        vscode.commands.registerCommand('strata.deployDryRun', async (filePath?: string) => {
            // TODO: call _client.deployDryRun(filePath ?? activeDocument)
            throw new Error('strata.deployDryRun — not implemented');
        }),

        vscode.commands.registerCommand('strata.showGuide', async () => {
            // TODO: open a WebviewPanel rendering the guide checklist from getStatus().readiness
            throw new Error('strata.showGuide — not implemented');
        }),

        vscode.commands.registerCommand('strata.switchProfile', async () => {
            // TODO: call getStatus(), show QuickPick of profiles, re-run with chosen profile
            throw new Error('strata.switchProfile — not implemented');
        }),

        vscode.commands.registerCommand('strata.exportSchemas', async () => {
            // TODO: call _client.wireSchemas(), refresh providers
            throw new Error('strata.exportSchemas — not implemented');
        }),

        vscode.commands.registerCommand('strata.openConsole', () => {
            const terminal = vscode.window.createTerminal({
                name: 'strata',
                cwd: workPath,
            });
            terminal.show();
            // TODO: run `strata console` interactive REPL when the command is ready
        }),

        vscode.commands.registerCommand('strata.refreshTreeView', async () => {
            await _treeView?.refresh();
            await _statusBar?.refresh();
        }),

        vscode.commands.registerCommand('strata.openFile', async (item) => {
            // item is a StrataTreeItem with a filePath
            if (typeof item?.filePath === 'string') {
                const uri = vscode.Uri.file(item.filePath);
                await vscode.window.showTextDocument(uri);
            }
        }),
    );

    // ── Re-create client when settings change ─────────────────────────────────

    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('strata.cliPath')) {
                _client = new StrataClient(getCliPath(), workPath);
                _statusBar?.setClient(_client);
                _treeView?.setClient(_client);
                _diagnostics?.setClient(_client);
            }
        }),
    );

    // ── Cleanups ───────────────────────────────────────────────────────────────

    context.subscriptions.push(_statusBar, _diagnostics, _codeLens);

    // ── Initial refresh ────────────────────────────────────────────────────────

    void _treeView.refresh();
    void _statusBar.refresh();

    // Set context key so menus can toggle their visibility
    void vscode.commands.executeCommand(
        'setContext',
        'strata.workspaceActive',
        true,
    );
}

export function deactivate(): void {
    _statusBar?.hide();
    _diagnostics?.clearAll();
    void vscode.commands.executeCommand(
        'setContext',
        'strata.workspaceActive',
        false,
    );
}
