/**
 * extension.ts — VS Code extension entry point.
 *
 * activate() is called when VS Code detects a .strata/solution.json file
 * in the open workspace (see activationEvents in package.json).
 *
 * Refresh pattern: one getStatus() call distributes to all 4 view providers
 * and the status bar via _refreshAll().  Providers never call the CLI directly.
 */

import * as vscode from 'vscode';
import { StrataClient, StrataCLINotFoundError, getCliPath, getWorkPath } from './strataClient';
import { StatusBarProvider } from './providers/statusBarProvider';
import { WorkspaceViewProvider } from './providers/workspaceViewProvider';
import { FilesViewProvider } from './providers/filesViewProvider';
import { RepositoriesViewProvider } from './providers/repositoriesViewProvider';
import { ToolsViewProvider } from './providers/toolsViewProvider';
import { DiagnosticsProvider } from './providers/diagnosticsProvider';
import { CodeLensProvider } from './providers/codeLensProvider';

// ---------------------------------------------------------------------------
// Extension state (singleton per VS Code window)
// ---------------------------------------------------------------------------

let _client: StrataClient | undefined;
let _statusBar: StatusBarProvider | undefined;
let _workspaceView: WorkspaceViewProvider | undefined;
let _filesView: FilesViewProvider | undefined;
let _reposView: RepositoriesViewProvider | undefined;
let _toolsView: ToolsViewProvider | undefined;
let _diagnostics: DiagnosticsProvider | undefined;
let _codeLens: CodeLensProvider | undefined;

// ---------------------------------------------------------------------------
// Shared refresh — one CLI call, all providers updated
// ---------------------------------------------------------------------------

async function _refreshAll(): Promise<void> {
    if (!_client) return;

    // Signal loading state to all panes and status bar
    _statusBar?.refresh(); // starts spinner internally
    _workspaceView?.setLoading();
    _filesView?.setLoading();
    _reposView?.setLoading();
    _toolsView?.setLoading();

    try {
        const status = await _client.getStatus();
        _statusBar?.refresh(); // will call getStatus() again internally — acceptable
        _workspaceView?.update(status);
        _filesView?.update(status);
        _reposView?.update(status);
        _toolsView?.update(status);
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        _workspaceView?.setError(message);
        _filesView?.setError(message);
        _reposView?.setError(message);
        _toolsView?.setError(message);
        if (err instanceof StrataCLINotFoundError) {
            void vscode.window.showErrorMessage(err.message);
        }
    }
}

// ---------------------------------------------------------------------------
// activate / deactivate
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
    const workPath = getWorkPath();

    if (!workPath) {
        void vscode.window.showWarningMessage('Strata: no workspace folder open — extension inactive.');
        return;
    }

    // ── Build providers ────────────────────────────────────────────────────────

    _client = new StrataClient(getCliPath(), workPath);

    _statusBar = new StatusBarProvider();
    _statusBar.setClient(_client);

    _workspaceView = new WorkspaceViewProvider();
    _filesView = new FilesViewProvider();
    _reposView = new RepositoriesViewProvider();
    _toolsView = new ToolsViewProvider();

    _diagnostics = new DiagnosticsProvider();
    _diagnostics.setClient(_client);

    _codeLens = new CodeLensProvider();

    // ── Register the 4 tree views ──────────────────────────────────────────────

    context.subscriptions.push(
        vscode.window.createTreeView('strataWorkspace', {
            treeDataProvider: _workspaceView,
            showCollapseAll: false,
        }),
        vscode.window.createTreeView('strataFiles', {
            treeDataProvider: _filesView,
            showCollapseAll: true,
        }),
        vscode.window.createTreeView('strataRepositories', {
            treeDataProvider: _reposView,
            showCollapseAll: false,
        }),
        vscode.window.createTreeView('strataTools', {
            treeDataProvider: _toolsView,
            showCollapseAll: false,
        }),
    );

    // ── Register other providers ───────────────────────────────────────────────

    _diagnostics.register();
    _codeLens.register(context);
    _statusBar.show();

    // ── Register commands ──────────────────────────────────────────────────────

    context.subscriptions.push(
        vscode.commands.registerCommand('strata.initWorkspace', () => {
            // TODO: run `strata sln init` via a VS Code terminal
            const t = vscode.window.createTerminal({ name: 'strata init', cwd: workPath });
            t.show();
            t.sendText(`${getCliPath()} sln init`);
        }),

        vscode.commands.registerCommand('strata.validateCurrentFile', async () => {
            const doc = vscode.window.activeTextEditor?.document;
            if (!doc || !_diagnostics) return;
            await _diagnostics.validateDocument(doc);
            // TODO: surface a notification with pass/fail summary
        }),

        vscode.commands.registerCommand('strata.validateAll', async () => {
            // TODO: iterate workspace YAML files, call validateDocument() for each
            void vscode.window.showInformationMessage('strata.validateAll — not yet implemented');
        }),

        vscode.commands.registerCommand('strata.buildDryRun', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No file selected for build.'); return; }
            _client?.runInTerminal(['build', 'run', '-f', target, '--dry-run'], 'strata build (dry run)');
        }),

        vscode.commands.registerCommand('strata.buildRun', async (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No file selected for build.'); return; }
            const confirmed = await vscode.window.showWarningMessage(
                'Run a full strata build? This will execute provisioners.',
                { modal: true }, 'Build',
            );
            if (confirmed !== 'Build') return;
            _client?.runInTerminal(['build', 'run', '-f', target], 'strata build');
        }),

        vscode.commands.registerCommand('strata.deployDryRun', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No file selected for deploy.'); return; }
            _client?.runInTerminal(['deploy', 'run', '-f', target, '--dry-run'], 'strata deploy (dry run)');
        }),

        vscode.commands.registerCommand('strata.showGuide', () => {
            // TODO: open a WebviewPanel rendering the readiness checklist
            void vscode.window.showInformationMessage('strata.showGuide — not yet implemented');
        }),

        vscode.commands.registerCommand('strata.switchProfile', async () => {
            // TODO: show QuickPick of available profiles then re-run with chosen profile
            void vscode.window.showInformationMessage('strata.switchProfile — not yet implemented');
        }),

        vscode.commands.registerCommand('strata.exportSchemas', async () => {
            if (!_client) return;
            try {
                await _client.wireSchemas();
                void vscode.window.showInformationMessage('Strata: schemas exported and wired.');
            } catch (err) {
                void vscode.window.showErrorMessage(`Schema export failed: ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.openConsole', () => {
            const t = vscode.window.createTerminal({ name: 'strata console', cwd: workPath });
            t.show();
            // TODO: run `strata console` interactive REPL when the command is ready
        }),

        vscode.commands.registerCommand('strata.refreshTreeView', () => {
            void _refreshAll();
        }),

        vscode.commands.registerCommand('strata.openFile', async (item?: { filePath?: string }) => {
            if (typeof item?.filePath === 'string') {
                await vscode.window.showTextDocument(vscode.Uri.file(item.filePath));
            }
        }),
    );

    // ── Re-create client when CLI path changes ─────────────────────────────────

    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('strata.cliPath')) {
                _client = new StrataClient(getCliPath(), workPath);
                _statusBar?.setClient(_client);
                _diagnostics?.setClient(_client);
                void _refreshAll();
            }
        }),
    );

    // ── Cleanups ───────────────────────────────────────────────────────────────

    context.subscriptions.push(
        _statusBar, _workspaceView, _filesView, _reposView, _toolsView,
        _diagnostics, _codeLens,
    );

    // ── Set context key so menus can use it ────────────────────────────────────

    void vscode.commands.executeCommand('setContext', 'strata.workspaceActive', true);

    // ── Initial data load ──────────────────────────────────────────────────────

    void _refreshAll();
}

export function deactivate(): void {
    _statusBar?.hide();
    _diagnostics?.clearAll();
    void vscode.commands.executeCommand('setContext', 'strata.workspaceActive', false);
}

