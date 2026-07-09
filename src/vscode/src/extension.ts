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
import { GuideViewProvider } from './providers/guideViewProvider';
import { CrossReferenceProvider } from './providers/crossReferenceProvider';
import { SnippetProvider } from './providers/snippetProvider';
import { DependencyGraphProvider } from './providers/dependencyGraphProvider';
import { StrataTaskProvider } from './providers/strataTaskProvider';
import { FileDecorationProvider } from './providers/fileDecorationProvider';
import { StrataChatParticipant } from './providers/strataChatParticipant';
import { EnvViewProvider } from './providers/envViewProvider';
import { AuditViewProvider } from './providers/auditViewProvider';
import { ValuesViewProvider } from './providers/valuesViewProvider';

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
let _guideView: GuideViewProvider | undefined;
let _crossRef: CrossReferenceProvider | undefined;
let _snippets: SnippetProvider | undefined;
let _depGraph: DependencyGraphProvider | undefined;
let _taskProvider: StrataTaskProvider | undefined;
let _fileDecorations: FileDecorationProvider | undefined;
let _chatParticipant: StrataChatParticipant | undefined;
let _envView: EnvViewProvider | undefined;
let _auditView: AuditViewProvider | undefined;
let _valuesView: ValuesViewProvider | undefined;
let _lastStatus: import('./strataClient').WorkspaceStatus | undefined;
/** File path of the most-recently started drift terminal, cleared on close. */
let _lastDriftTarget: string | undefined;
/** File path of the most-recently started (non-dry-run) deploy terminal, cleared on close. */
let _lastDeployTarget: string | undefined;

// ---------------------------------------------------------------------------
// Shared refresh — one CLI call, all providers updated
// ---------------------------------------------------------------------------

async function _refreshAll(): Promise<void> {
    if (!_client) return;

    // Signal loading state to all panes and status bar
    _statusBar?.setLoading();
    _workspaceView?.setLoading();
    _filesView?.setLoading();
    _reposView?.setLoading();
    _toolsView?.setLoading();
    _envView?.setLoading();
    _auditView?.setLoading();

    try {
        const status = await _client.getStatus();
        _statusBar?.update(status);
        _lastStatus = status;
        _workspaceView?.update(status);
        _filesView?.update(status);
        _reposView?.update(status);
        _toolsView?.update(status);
        _guideView?.update(status);
        _crossRef?.update(status.repositories ?? []);
        _fileDecorations?.update(status);
        _chatParticipant?.update(status);
        void _depGraph?.update(status);
        _envView?.refresh();
        _auditView?.refresh();
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        _workspaceView?.setError(message);
        _filesView?.setError(message);
        _reposView?.setError(message);
        _toolsView?.setError(message);
        _statusBar?.setError(err);
        _envView?.setError(message);
        _auditView?.setError(message);
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

    _guideView = new GuideViewProvider();
    _guideView.onRefresh(() => { void _refreshAll(); });

    _crossRef = new CrossReferenceProvider();
    _snippets = new SnippetProvider();
    _depGraph = new DependencyGraphProvider();
    _taskProvider = new StrataTaskProvider(getCliPath(), workPath);
    _fileDecorations = new FileDecorationProvider();
    _chatParticipant = new StrataChatParticipant();
    _envView = new EnvViewProvider();
    _envView.setClient(_client);
    _auditView = new AuditViewProvider();
    _auditView.setClient(_client);
    _valuesView = new ValuesViewProvider();
    _valuesView.setClient(_client);

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
        vscode.window.createTreeView('strataEnvironment', {
            treeDataProvider: _envView!,
            showCollapseAll: false,
        }),
        vscode.window.createTreeView('strataAudit', {
            treeDataProvider: _auditView!,
            showCollapseAll: true,
        }),
        vscode.window.createTreeView('strataValues', {
            treeDataProvider: _valuesView!,
            showCollapseAll: false,
        }),
    );

    // ── Register other providers ───────────────────────────────────────────────

    _diagnostics.register();
    _codeLens.register(context);
    _crossRef.register(context);
    _snippets.register(context);
    _taskProvider.register(context);
    _fileDecorations.register();
    _chatParticipant.setClient(_client);
    _chatParticipant.register(context);
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
            const r = await _diagnostics.validateDocument(doc);
            if (r.errorCount === 0) {
                void vscode.window.showInformationMessage('Strata: validation passed ✅');
            } else {
                void vscode.window.showWarningMessage(
                    `Strata: ${r.errorCount} validation error${r.errorCount !== 1 ? 's' : ''} — see Problems panel`,
                    'Open Problems',
                ).then((v) => {
                    if (v === 'Open Problems') {
                        void vscode.commands.executeCommand('workbench.actions.view.problems');
                    }
                });
            }
        }),

        vscode.commands.registerCommand('strata.validateAll', async () => {
            if (!_diagnostics || !_client) return;

            const yamlUris = await vscode.workspace.findFiles('**/*.yaml', '**/.strata/**');

            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Strata: validating workspace files', cancellable: true },
                async (progress, token) => {
                    // Pre-filter to strata documents only (fast text scan, no CLI call)
                    const strataUris: vscode.Uri[] = [];
                    for (const uri of yamlUris) {
                        const doc = await vscode.workspace.openTextDocument(uri);
                        const isStrata = Array.from(
                            { length: Math.min(doc.lineCount, 20) },
                            (_, i) => doc.lineAt(i).text,
                        ).some((l) => l.trimStart().startsWith('apiVersion: strata.'));
                        if (isStrata) strataUris.push(uri);
                    }

                    if (strataUris.length === 0) {
                        void vscode.window.showInformationMessage('Strata: no strata YAML files found in workspace.');
                        return;
                    }

                    let totalErrors = 0;
                    let idx = 0;
                    for (const uri of strataUris) {
                        if (token.isCancellationRequested) break;
                        idx++;
                        progress.report({ message: `${idx}/${strataUris.length} — ${vscode.workspace.asRelativePath(uri)}` });
                        const doc = await vscode.workspace.openTextDocument(uri);
                        // eslint-disable-next-line no-await-in-loop
                        const result = await _diagnostics!.validateDocument(doc);
                        totalErrors += result.errorCount;
                    }

                    if (totalErrors === 0) {
                        void vscode.window.showInformationMessage(
                            `Strata: all ${strataUris.length} file${strataUris.length !== 1 ? 's' : ''} passed validation ✅`,
                        );
                    } else {
                        void vscode.window.showWarningMessage(
                            `Strata: ${totalErrors} error${totalErrors !== 1 ? 's' : ''} across ${strataUris.length} file${strataUris.length !== 1 ? 's' : ''} — see Problems panel`,
                            'Open Problems',
                        ).then((v) => {
                            if (v === 'Open Problems') {
                                void vscode.commands.executeCommand('workbench.actions.view.problems');
                            }
                        });
                    }
                },
            );
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

        vscode.commands.registerCommand('strata.deployRun', async (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No file selected for deploy.'); return; }
            const confirmed = await vscode.window.showWarningMessage(
                'Run a full strata deploy? This will apply infrastructure changes.',
                { modal: true }, 'Deploy',
            );
            if (confirmed !== 'Deploy') return;
            _lastDeployTarget = target;
            _client?.runInTerminal(['deploy', 'run', '-f', target, '--force'], 'strata deploy');
        }),

        // ── Env commands ────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.envStatus', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            // --offline: reads build cache only, no terraform call — instant feedback
            _client?.runInTerminal(['env', 'status', '-f', target, '--offline'], 'strata env status');
        }),

        vscode.commands.registerCommand('strata.envDrift', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            _lastDriftTarget = target;
            _client?.runInTerminal(['env', 'drift', '-f', target], 'strata env drift');
        }),

        vscode.commands.registerCommand('strata.envDoctor', async () => {
            if (!_client) return;
            try {
                const result = await _client.runEnvDoctor();
                const { passed, warnings, failed } = result.summary;
                const total = passed + warnings + failed;
                if (failed === 0 && warnings === 0) {
                    void vscode.window.showInformationMessage(
                        `Strata Doctor: all ${total} check${total !== 1 ? 's' : ''} passed ✅`,
                    );
                } else {
                    const parts: string[] = [];
                    if (passed > 0) parts.push(`${passed} passed`);
                    if (warnings > 0) parts.push(`${warnings} warning${warnings !== 1 ? 's' : ''}`);
                    if (failed > 0) parts.push(`${failed} failed`);
                    const actions: string[] = failed > 0 ? ['Show Details'] : [];
                    void vscode.window.showWarningMessage(
                        `Strata Doctor: ${parts.join(' · ')}`,
                        ...actions,
                    ).then((v) => {
                        if (v === 'Show Details') {
                            _client?.runInTerminal(['env', 'doctor'], 'strata env doctor');
                        }
                    });
                }
            } catch (err) {
                void vscode.window.showErrorMessage(`Strata Doctor failed: ${String(err)}`);
            }
        }),

        // ── Audit commands ──────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.auditChanges', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) {
                // No file context — show all changes
                _client?.runInTerminal(['audit', 'changes'], 'strata audit changes');
                return;
            }
            _client?.runInTerminal(['audit', 'changes', '-f', target], 'strata audit changes');
        }),

        vscode.commands.registerCommand('strata.auditResend', () => {
            _client?.runInTerminal(['audit', 'resend'], 'strata audit resend');
        }),

        vscode.commands.registerCommand('strata.auditExport', async () => {
            const uri = await vscode.window.showSaveDialog({
                defaultUri: vscode.Uri.file('audit-export.json'),
                filters: {
                    'JSON': ['json'],
                    'NDJSON': ['ndjson'],
                },
                title: 'Export Audit Trail',
            });
            if (!uri) return;
            const isNdjson = uri.fsPath.endsWith('.ndjson');
            const formatArgs = isNdjson ? ['--format', 'ndjson'] : ['--format', 'json'];
            _client?.runInTerminal(
                ['audit', 'export', ...formatArgs, '--out', uri.fsPath],
                'strata audit export',
            );
        }),

        vscode.commands.registerCommand('strata.showGuide', () => {
            _guideView?.show(_lastStatus);
        }),

        vscode.commands.registerCommand('strata.switchProfile', async () => {
            if (!_client) return;
            const profiles = _lastStatus?.profiles;
            if (!profiles?.all.length) {
                void vscode.window.showWarningMessage(
                    'Strata: no profiles found. Create one first with `strata profile add`.',
                );
                return;
            }

            const items = profiles.all.map((p) => ({
                label: p,
                description: p === profiles.active ? '(active)' : '',
            }));

            const selected = await vscode.window.showQuickPick(items, {
                title: 'Strata: Switch Profile',
                placeHolder: `Current: ${profiles.active ?? 'none'} — select to activate`,
            });

            if (!selected || selected.label === profiles.active) return;

            try {
                await _client.activateProfile(selected.label);
                void vscode.window.showInformationMessage(
                    `Strata: profile "${selected.label}" activated.`,
                );
                void _refreshAll();
            } catch (err) {
                void vscode.window.showErrorMessage(
                    `Strata: could not activate profile — ${String(err)}`,
                );
            }
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

        vscode.commands.registerCommand('strata.openSchema', async (args?: { kind?: string }) => {
            const workPath = getWorkPath();
            if (!workPath) return;

            let kind = args?.kind;

            // Fallback: detect kind from the active editor
            if (!kind) {
                const doc = vscode.window.activeTextEditor?.document;
                if (doc) {
                    for (let i = 0; i < Math.min(doc.lineCount, 20); i++) {
                        const m = doc.lineAt(i).text.trim().match(/^kind:\s*(\S+)/);
                        if (m) { kind = m[1].toLowerCase(); break; }
                    }
                }
            }

            if (!kind) {
                void vscode.window.showWarningMessage('Strata: could not determine document kind.');
                return;
            }

            const schemaUri = vscode.Uri.joinPath(
                vscode.Uri.file(workPath), '.strata', 'schemas', `${kind}.json`,
            );

            try {
                await vscode.window.showTextDocument(schemaUri, {
                    viewColumn: vscode.ViewColumn.Beside,
                    preserveFocus: true,
                    preview: true,
                });
            } catch {
                const pick = await vscode.window.showWarningMessage(
                    `Schema not found for kind "${kind}". Export schemas first.`,
                    'Export & Wire Schemas',
                );
                if (pick) {
                    void vscode.commands.executeCommand('strata.exportSchemas');
                }
            }
        }),

        vscode.commands.registerCommand('strata.openConsole', () => {
            const t = vscode.window.createTerminal({ name: 'strata console', cwd: workPath });
            t.show();
            t.sendText(`${getCliPath()} console`);
        }),

        vscode.commands.registerCommand('strata.showDependencyGraph', () => {
            void _depGraph?.show(_lastStatus);
        }),

        vscode.commands.registerCommand('strata.deployStage', async (filePath?: string, stageName?: string, dryRun = false) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            if (!stageName) {
                stageName = await vscode.window.showInputBox({ prompt: 'Stage name', placeHolder: 'e.g. infrastructure' });
                if (!stageName) return;
            }
            if (!dryRun) {
                const confirmed = await vscode.window.showWarningMessage(
                    `Deploy stage "${stageName}"? This will apply infrastructure changes.`,
                    { modal: true }, 'Deploy',
                );
                if (confirmed !== 'Deploy') return;
            }
            const args = ['deploy', 'run', '-f', target, '--stage', stageName];
            if (dryRun) args.push('--dry-run'); else { args.push('--force'); _lastDeployTarget = target; }
            _client?.runInTerminal(args, `strata deploy ${stageName}${dryRun ? ' (dry run)' : ''}`);
        }),

        vscode.commands.registerCommand('strata.lockStatus', async (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target || !_client) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            try {
                const lock = await _client.getLockStatus(target);
                if (lock.locked) {
                    const pick = await vscode.window.showWarningMessage(
                        `🔒 Locked by "${lock.holder ?? 'unknown'}" since ${lock.acquired_at ?? 'unknown'}`,
                        'Force Release',
                    );
                    if (pick === 'Force Release') {
                        void vscode.commands.executeCommand('strata.releaseLock', target);
                    }
                } else {
                    void vscode.window.showInformationMessage('🔓 No deployment lock held.');
                }
                _envView?.refreshLock(target);
            } catch (err) {
                void vscode.window.showErrorMessage(`Lock status failed: ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.releaseLock', async (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target || !_client) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            const confirmed = await vscode.window.showWarningMessage(
                'Force-release the deployment lock? Only do this if a deploy crashed and left a stale lock.',
                { modal: true }, 'Release Lock',
            );
            if (confirmed !== 'Release Lock') return;
            try {
                await _client.releaseLock(target);
                void vscode.window.showInformationMessage('Strata: deployment lock released.');
                _envView?.refreshLock(target);
            } catch (err) {
                void vscode.window.showErrorMessage(`Lock release failed: ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.showValues', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            _valuesView?.loadFile(target);
            void vscode.commands.executeCommand('strataValues.focus');
        }),

        vscode.commands.registerCommand('strata.copyValueKey', async (key: string) => {
            await vscode.env.clipboard.writeText(key);
            void vscode.window.showInformationMessage(`Copied "${key}" to clipboard.`);
        }),

        vscode.commands.registerCommand('strata.buildSbom', async (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { void vscode.window.showWarningMessage('No deployment file selected.'); return; }
            try {
                void vscode.window.withProgress(
                    { location: vscode.ProgressLocation.Notification, title: 'Strata: generating SBOM…', cancellable: false },
                    async () => {
                        if (!_client) return;
                        const sbom = await _client.generateSbom(target);
                        const parts: string[] = [`${sbom.component_count} components`];
                        if (sbom.critical_count > 0) parts.push(`${sbom.critical_count} critical CVEs`);
                        if (sbom.high_count > 0) parts.push(`${sbom.high_count} high CVEs`);
                        const label = sbom.vulnerabilities_found ? `⚠️ ${parts.join(' · ')}` : `✅ ${parts.join(' · ')} — no vulnerabilities`;
                        const actions = sbom.output_file ? ['Open SBOM'] : [];
                        void vscode.window.showInformationMessage(`Strata SBOM: ${label}`, ...actions).then((v) => {
                            if (v === 'Open SBOM' && sbom.output_file) {
                                void vscode.window.showTextDocument(vscode.Uri.file(sbom.output_file));
                            }
                        });
                    },
                );
            } catch (err) {
                _client?.runInTerminal(['build', 'sbom', '-f', target], 'strata build sbom');
            }
        }),

        vscode.commands.registerCommand('strata.syncRepo', async (item?: { repoName?: string }) => {
            const name = item?.repoName;
            if (!_reposView) return;
            await _reposView.syncRepo(name);
            void _refreshAll();
        }),

        vscode.commands.registerCommand('strata.addRepo', async () => {
            const name = await vscode.window.showInputBox({ prompt: 'Repository name', placeHolder: 'e.g. my-infra' });
            if (!name) return;
            const repoPath = await vscode.window.showInputBox({ prompt: 'Repository path (local folder)', placeHolder: '/path/to/repo or ../relative/path' });
            if (!repoPath || !_client) return;
            try {
                await _client.addRepo(name, repoPath);
                void vscode.window.showInformationMessage(`Strata: repository "${name}" added.`);
                void _refreshAll();
            } catch (err) {
                void vscode.window.showErrorMessage(`Add repository failed: ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.removeRepo', async (item?: { repoName?: string }) => {
            const name = item?.repoName ?? await vscode.window.showInputBox({ prompt: 'Repository name to remove' });
            if (!name || !_reposView) return;
            await _reposView.removeRepo(name);
            void _refreshAll();
        }),

        vscode.commands.registerCommand('strata.auditFilter', () => {
            _auditView?.cycleFilter();
        }),

        vscode.commands.registerCommand('strata.auditSetLimit', async () => {
            const input = await vscode.window.showInputBox({
                prompt: 'Number of audit entries to show (5–200)',
                value: '20',
                validateInput: (v) => {
                    const n = parseInt(v, 10);
                    return isNaN(n) || n < 5 || n > 200 ? 'Enter a number between 5 and 200' : null;
                },
            });
            if (!input) return;
            _auditView?.setLimit(parseInt(input, 10));
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
                _envView?.setClient(_client);
                _auditView?.setClient(_client);
                _valuesView?.setClient(_client);
                void _refreshAll();
            }
        }),
    );

    // ── Notifications ──────────────────────────────────────────────────────────

    // 1. Validation-on-save: notify when error count changes
    if (_diagnostics) {
        context.subscriptions.push(
            _diagnostics.onDidChangeValidation((evt) => {
                if (evt.currentCount > 0 && evt.currentCount > evt.previousCount) {
                    const delta = evt.currentCount - evt.previousCount;
                    void vscode.window.showWarningMessage(
                        `Strata: ${delta} new validation error${delta !== 1 ? 's' : ''} in ${evt.relativePath}`,
                        'Open Problems',
                    ).then((v) => {
                        if (v === 'Open Problems') {
                            void vscode.commands.executeCommand('workbench.actions.view.problems');
                        }
                    });
                } else if (evt.currentCount === 0 && evt.previousCount > 0) {
                    void vscode.window.showInformationMessage(
                        `Strata: ${evt.relativePath} — all errors resolved ✅`,
                    );
                }
            }),
        );
    }

    // 2. Terminal close: refresh workspace after build/deploy terminals finish
    context.subscriptions.push(
        vscode.window.onDidCloseTerminal((terminal) => {
            const name = terminal.name;
            if (name.startsWith('strata build') || name.startsWith('strata deploy')) {
                // Terminal closed — refresh status so tree views, status bar, and
                // guide panel reflect the outcome of the build/deploy.
                const exitCode = terminal.exitStatus?.code;
                const action = name.startsWith('strata build') ? 'Build' : 'Deploy';
                const deployTarget = _lastDeployTarget;
                if (action === 'Deploy') _lastDeployTarget = undefined;

                if (exitCode === 0) {
                    if (action === 'Deploy' && deployTarget) {
                        void vscode.window.showInformationMessage(
                            'Strata: Deploy completed successfully.',
                            'Check Health', 'View Outputs',
                        ).then((v) => {
                            if (v === 'Check Health') {
                                _client?.runInTerminal(['deploy', 'health', '-f', deployTarget], 'strata deploy health');
                            } else if (v === 'View Outputs') {
                                _client?.runInTerminal(['env', 'output', '-f', deployTarget], 'strata env output');
                            }
                        });
                    } else {
                        void vscode.window.showInformationMessage(
                            `Strata: ${action} completed successfully.`,
                        );
                    }
                } else if (exitCode !== undefined) {
                    void vscode.window.showWarningMessage(
                        `Strata: ${action} exited with code ${exitCode} — check terminal output.`,
                    );
                }
                // Refresh regardless so views reflect new state
                void _refreshAll();
            }
            if (name === 'strata env drift') {
                const exitCode = terminal.exitStatus?.code;
                const target = _lastDriftTarget;
                _lastDriftTarget = undefined;
                if (exitCode === 0) {
                    if (target) _envView?.markDrift(target, false);
                    void vscode.window.showInformationMessage('Strata: no drift detected ✅');
                } else if (exitCode === 3) {
                    if (target) _envView?.markDrift(target, true);
                    void vscode.window.showWarningMessage('Strata: drift detected — review the plan above.');
                }
            }
        }),
    );

    // ── File watcher: auto-refresh when solution.json changes externally ───────

    const solutionWatcher = vscode.workspace.createFileSystemWatcher('**/.strata/solution.json');
    solutionWatcher.onDidChange(() => void _refreshAll());
    solutionWatcher.onDidCreate(() => void _refreshAll());
    solutionWatcher.onDidDelete(() => void _refreshAll());

    // ── Cleanups ───────────────────────────────────────────────────────────────

    context.subscriptions.push(
        _statusBar, _workspaceView, _filesView, _reposView, _toolsView,
        _diagnostics, _codeLens, _guideView, _crossRef, _snippets, _depGraph,
        _taskProvider, _fileDecorations, _chatParticipant, solutionWatcher,
        _envView!, _auditView!, _valuesView!,
    );

    // ── Set context key so menus can use it ────────────────────────────────────

    void vscode.commands.executeCommand('setContext', 'strata.workspaceActive', true);

    // ── Initial data load ──────────────────────────────────────────────────────

    // Auto-activate default profile if configured
    const defaultProfile = vscode.workspace.getConfiguration('strata').get<string>('defaultProfile', '');
    if (defaultProfile && _client) {
        void _client.activateProfile(defaultProfile)
            .then(() => _refreshAll())
            .catch(() => _refreshAll());   // still refresh even if profile activation fails
    } else {
        void _refreshAll();
    }
}

export function deactivate(): void {
    _statusBar?.hide();
    _diagnostics?.clearAll();
    void vscode.commands.executeCommand('setContext', 'strata.workspaceActive', false);
}

