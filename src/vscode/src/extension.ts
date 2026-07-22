/**
 * extension.ts — VS Code extension entry point.
 *
 * activate() is called when VS Code detects a .strata/solution.json file
 * in the open workspace (see activationEvents in package.json).
 *
 * Architecture: deployment-centric.
 *   DeploymentContext holds the active deployment file — all views and commands
 *   default to it rather than "whatever file is open in the editor".
 *
 * Refresh pattern: one getStatus() call distributes to all providers via
 * _refreshAll(). Providers never call the CLI directly (except lazy loaders).
 */

import * as vscode from 'vscode';
import { StrataClient, StrataCLINotFoundError, getCliPath, getWorkPath } from './strataClient';
import { StatusBarProvider } from './providers/statusBarProvider';
import { WorkspaceHealthProvider } from './providers/workspaceHealthProvider';
import { DeploymentExplorerProvider } from './providers/deploymentExplorerProvider';
import { OperationsViewProvider } from './providers/operationsViewProvider';
import { DeploymentContext } from './providers/deploymentContext';
import { DiagnosticsProvider } from './providers/diagnosticsProvider';
import { CodeLensProvider } from './providers/codeLensProvider';
import { GuideViewProvider } from './providers/guideViewProvider';
import { CrossReferenceProvider } from './providers/crossReferenceProvider';
import { SnippetProvider } from './providers/snippetProvider';
import { StrataTaskProvider } from './providers/strataTaskProvider';
import { FileDecorationProvider } from './providers/fileDecorationProvider';
import { StrataChatParticipant } from './providers/strataChatParticipant';
import { AuditViewProvider } from './providers/auditViewProvider';
import { ValuesViewProvider } from './providers/valuesViewProvider';
import { BuildPlanProvider } from './providers/buildPlanProvider';
import { PromotionsViewProvider } from './providers/promotionsViewProvider';

// ---------------------------------------------------------------------------
// Extension state (singleton per VS Code window)
// ---------------------------------------------------------------------------

let _client: StrataClient | undefined;
let _statusBar: StatusBarProvider | undefined;
let _workspaceHealth: WorkspaceHealthProvider | undefined;
let _deploymentExplorer: DeploymentExplorerProvider | undefined;
let _operationsView: OperationsViewProvider | undefined;
let _deployCtx: DeploymentContext | undefined;
let _diagnostics: DiagnosticsProvider | undefined;
let _codeLens: CodeLensProvider | undefined;
let _guideView: GuideViewProvider | undefined;
let _crossRef: CrossReferenceProvider | undefined;
let _snippets: SnippetProvider | undefined;
let _taskProvider: StrataTaskProvider | undefined;
let _fileDecorations: FileDecorationProvider | undefined;
let _chatParticipant: StrataChatParticipant | undefined;
let _auditView: AuditViewProvider | undefined;
let _valuesView: ValuesViewProvider | undefined;
let _promotionsView: PromotionsViewProvider | undefined;
let _lastStatus: import('./strataClient').WorkspaceStatus | undefined;
let _lastDriftTarget: string | undefined;
let _lastDeployTarget: string | undefined;

// ---------------------------------------------------------------------------
// Shared refresh — one CLI call, all providers updated
// ---------------------------------------------------------------------------

async function _refreshAll(): Promise<void> {
    if (!_client) return;

    _statusBar?.setLoading();
    _workspaceHealth?.setLoading();
    _auditView?.setLoading();
    _promotionsView?.setLoading();

    try {
        const status = await _client.getStatus();
        _lastStatus = status;
        _statusBar?.update(status);
        _workspaceHealth?.update(status);
        _guideView?.update(status);
        _crossRef?.update(status.repositories ?? []);
        _fileDecorations?.update(status);
        _chatParticipant?.update(status);
        _deploymentExplorer?.update(status);
        _auditView?.refresh();
        _promotionsView?.refresh();

        // Auto-select deployment if only one exists and none is selected
        _deployCtx?.autoSelect(status);
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        _workspaceHealth?.setError(message);
        _deploymentExplorer?.setError(message);
        _statusBar?.setError(err);
        _auditView?.setError(message);
        _promotionsView?.setError(message);
        if (err instanceof StrataCLINotFoundError) {
            void vscode.window.showErrorMessage(err.message);
        }
    }
}

// ---------------------------------------------------------------------------
// Resolve the effective target file (deployment context → active editor fallback)
// ---------------------------------------------------------------------------

function _resolveTarget(explicit?: string): string | undefined {
    return explicit
        ?? _deployCtx?.activeFile
        ?? vscode.window.activeTextEditor?.document.uri.fsPath;
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

    // Deployment context — must be created first (others subscribe to it)
    _deployCtx = new DeploymentContext(context);

    _statusBar = new StatusBarProvider();
    _statusBar.setClient(_client);

    _workspaceHealth = new WorkspaceHealthProvider();
    _workspaceHealth.setClient(_client);

    _deploymentExplorer = new DeploymentExplorerProvider();
    _deploymentExplorer.setClient(_client);
    _deploymentExplorer.setWorkPath(workPath);
    _deploymentExplorer.setDeploymentContext(_deployCtx);

    _operationsView = new OperationsViewProvider();
    _operationsView.setClient(_client);
    _operationsView.setDeploymentContext(_deployCtx);

    _diagnostics = new DiagnosticsProvider();
    _diagnostics.setClient(_client);

    _codeLens = new CodeLensProvider();

    _guideView = new GuideViewProvider();
    _guideView.onRefresh(() => { void _refreshAll(); });

    _crossRef = new CrossReferenceProvider();
    _snippets = new SnippetProvider();
    _taskProvider = new StrataTaskProvider(getCliPath(), workPath);
    _fileDecorations = new FileDecorationProvider();
    _chatParticipant = new StrataChatParticipant();
    _auditView = new AuditViewProvider();
    _auditView.setClient(_client);
    _valuesView = new ValuesViewProvider();
    _valuesView.setClient(_client);
    _promotionsView = new PromotionsViewProvider();
    _promotionsView.setClient(_client);

    // Propagate deployment context changes to status bar
    context.subscriptions.push(
        _deployCtx.onDidChange((filePath) => {
            _statusBar?.updateDeployment(
                filePath ? require('path').basename(filePath, '.yaml') : undefined,
            );
            _operationsView?.refresh();
        }),
    );

    // ── Register tree views ────────────────────────────────────────────────────

    context.subscriptions.push(
        vscode.window.createTreeView('strataWorkspace', {
            treeDataProvider: _workspaceHealth,
            showCollapseAll: false,
        }),
        vscode.window.createTreeView('strataDeployments', {
            treeDataProvider: _deploymentExplorer,
            showCollapseAll: false,
        }),
        vscode.window.createTreeView('strataOperations', {
            treeDataProvider: _operationsView,
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
        vscode.window.createTreeView('strataPromotions', {
            treeDataProvider: _promotionsView!,
            showCollapseAll: true,
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

        // ── Deployment context ─────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.selectDeployment', async () => {
            if (!_lastStatus) { void vscode.window.showWarningMessage('Strata: workspace not ready yet.'); return; }
            await _deployCtx?.selectDeployment(_lastStatus);
        }),

        vscode.commands.registerCommand('strata.setActiveDeployment', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) return;
            _deployCtx?.setFile(target);
            void vscode.window.showInformationMessage(
                `Strata: active deployment set to ${require('path').basename(target, '.yaml')}`,
            );
        }),

        // ── New File ───────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.newFile', async () => {
            const kinds = [
                { label: '$(cloud) deployment', description: 'Orchestrates workspace + environments into a deployable unit' },
                { label: '$(package) workspace', description: 'Infrastructure blueprint: providers, provisioners, topology' },
                { label: '$(globe) environment', description: 'Environment-specific overrides and variable values' },
                { label: '$(settings-gear) configuration', description: 'Shared configuration layer (e.g. per cloud provider)' },
                { label: '$(layers) namespace', description: 'Application namespace for a workspace' },
                { label: '$(server) module', description: 'Reusable infrastructure module' },
                { label: '$(plug) provider', description: 'Cloud provider credentials and location' },
                { label: '$(shield) resource', description: 'Custom resource definition' },
                { label: '$(broadcast) network', description: 'Network topology definition' },
                { label: '$(firewall) firewall', description: 'Firewall ruleset' },
                { label: '$(globe) dns', description: 'DNS zone configuration' },
                { label: '$(organization) tenant', description: 'Tenant scoping' },
            ];
            const kind = await vscode.window.showQuickPick(kinds, {
                title: 'Strata: New File — choose kind',
                matchOnDescription: true,
            });
            if (!kind) return;
            const kindName = kind.label.replace(/\$\([^)]+\)\s*/, '').trim();
            const name = await vscode.window.showInputBox({
                prompt: `Name for the new ${kindName}`,
                placeHolder: `e.g. my_${kindName}`,
                validateInput: v => /^[a-z][a-z0-9_-]*$/.test(v) ? null : 'Lowercase alphanumeric, underscores and hyphens only',
            });
            if (!name) return;
            _client?.runInTerminal(['new', kindName, '--name', name], `strata new ${kindName}`);
        }),

        // ── Init ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.initWorkspace', () => {
            const t = vscode.window.createTerminal({ name: 'strata init', cwd: workPath });
            t.show();
            t.sendText(`${getCliPath()} sln init`);
        }),

        // ── Validate ──────────────────────────────────────────────────────────

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
                    const strataUris: vscode.Uri[] = [];
                    for (const uri of yamlUris) {
                        const doc = await vscode.workspace.openTextDocument(uri);
                        const isStrata = Array.from({ length: Math.min(doc.lineCount, 20) }, (_, i) => doc.lineAt(i).text)
                            .some((l) => l.trimStart().startsWith('apiVersion: strata.'));
                        if (isStrata) strataUris.push(uri);
                    }
                    if (strataUris.length === 0) {
                        void vscode.window.showInformationMessage('Strata: no strata YAML files found.');
                        return;
                    }
                    let totalErrors = 0;
                    for (let idx = 0; idx < strataUris.length; idx++) {
                        if (token.isCancellationRequested) break;
                        progress.report({ message: `${idx + 1}/${strataUris.length} — ${vscode.workspace.asRelativePath(strataUris[idx])}` });
                        const doc = await vscode.workspace.openTextDocument(strataUris[idx]);
                        const result = await _diagnostics!.validateDocument(doc);
                        totalErrors += result.errorCount;
                    }
                    const label = totalErrors === 0
                        ? `Strata: all ${strataUris.length} file${strataUris.length !== 1 ? 's' : ''} passed ✅`
                        : `Strata: ${totalErrors} error${totalErrors !== 1 ? 's' : ''} in ${strataUris.length} files — see Problems panel`;
                    if (totalErrors === 0) {
                        void vscode.window.showInformationMessage(label);
                    } else {
                        void vscode.window.showWarningMessage(label, 'Open Problems').then(v => {
                            if (v) void vscode.commands.executeCommand('workbench.actions.view.problems');
                        });
                    }
                },
            );
        }),

        // ── Build ─────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.buildDryRun', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            _client?.runInTerminal(['build', 'run', '-f', target, '--dry-run'], 'strata build (dry run)');
        }),

        vscode.commands.registerCommand('strata.buildRun', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            const confirmed = await vscode.window.showWarningMessage('Run a full strata build? This will execute provisioners.', { modal: true }, 'Build');
            if (confirmed !== 'Build') return;
            _client?.runInTerminal(['build', 'run', '-f', target], 'strata build');
        }),

        vscode.commands.registerCommand('strata.buildPlan', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target || !_client) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            await BuildPlanProvider.show(target, _client);
        }),

        // ── Deploy ────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.deployDryRun', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            _client?.runInTerminal(['deploy', 'run', '-f', target, '--dry-run'], 'strata deploy (dry run)');
        }),

        vscode.commands.registerCommand('strata.deployRun', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }

            // Policy gate
            let policyNote = '';
            let policyBlocked = false;
            if (_client && _diagnostics) {
                try {
                    const result = await vscode.window.withProgress(
                        { location: vscode.ProgressLocation.Window, title: 'Strata: checking policies…' },
                        () => _diagnostics!.checkPolicyDiagnostics(vscode.Uri.file(target)),
                    );
                    if (result) {
                        if (result.denied > 0) { policyNote = `⛔ ${result.denied} policy violation(s)`; policyBlocked = true; }
                        else if (result.failed > 0) { policyNote = `⚠️ ${result.failed} policy warning(s)`; }
                        else if (result.policies_checked > 0) { policyNote = `✅ ${result.policies_checked} policies passed`; }
                    }
                } catch { /* non-fatal */ }
            }

            if (policyBlocked) {
                const pick = await vscode.window.showErrorMessage(`Deploy blocked: ${policyNote}`, { modal: true }, 'Deploy Anyway', 'View Violations');
                if (pick === 'View Violations') { void vscode.commands.executeCommand('workbench.actions.view.problems'); return; }
                if (pick !== 'Deploy Anyway') return;
            } else {
                const msg = policyNote ? `Run a full strata deploy? ${policyNote}. This will apply infrastructure changes.` : 'Run a full strata deploy? This will apply infrastructure changes.';
                const confirmed = await vscode.window.showWarningMessage(msg, { modal: true }, 'Deploy');
                if (confirmed !== 'Deploy') return;
            }

            _lastDeployTarget = target;
            _client?.runInTerminal(['deploy', 'run', '-f', target, '--force'], 'strata deploy');
        }),

        vscode.commands.registerCommand('strata.deployStage', async (filePath?: string, stageName?: string, dryRun = false) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            if (!stageName) {
                stageName = await vscode.window.showInputBox({ prompt: 'Stage name', placeHolder: 'e.g. infrastructure' });
                if (!stageName) return;
            }
            if (!dryRun) {
                const confirmed = await vscode.window.showWarningMessage(`Deploy stage "${stageName}"? This will apply infrastructure changes.`, { modal: true }, 'Deploy');
                if (confirmed !== 'Deploy') return;
            }
            const args = ['deploy', 'run', '-f', target, '--stage', stageName];
            if (dryRun) args.push('--dry-run'); else { args.push('--force'); _lastDeployTarget = target; }
            _client?.runInTerminal(args, `strata deploy ${stageName}${dryRun ? ' (dry run)' : ''}`);
        }),

        // ── Env commands ───────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.envStatus', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            _client?.runInTerminal(['env', 'status', '-f', target, '--offline'], 'strata env status');
        }),

        vscode.commands.registerCommand('strata.envDrift', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
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
                    void vscode.window.showInformationMessage(`Strata Doctor: all ${total} check${total !== 1 ? 's' : ''} passed ✅`);
                } else {
                    const parts: string[] = [];
                    if (passed > 0) parts.push(`${passed} passed`);
                    if (warnings > 0) parts.push(`${warnings} warning${warnings !== 1 ? 's' : ''}`);
                    if (failed > 0) parts.push(`${failed} failed`);
                    void vscode.window.showWarningMessage(`Strata Doctor: ${parts.join(' · ')}`, ...(failed > 0 ? ['Show Details'] : [])).then(v => {
                        if (v === 'Show Details') _client?.runInTerminal(['env', 'doctor'], 'strata env doctor');
                    });
                }
            } catch (err) {
                void vscode.window.showErrorMessage(`Strata Doctor failed: ${String(err)}`);
            }
        }),

        // ── Audit commands ─────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.auditChanges', (filePath?: string) => {
            const target = filePath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
            if (!target) { _client?.runInTerminal(['audit', 'changes'], 'strata audit changes'); return; }
            _client?.runInTerminal(['audit', 'changes', '-f', target], 'strata audit changes');
        }),

        vscode.commands.registerCommand('strata.auditResend', () => {
            _client?.runInTerminal(['audit', 'resend'], 'strata audit resend');
        }),

        vscode.commands.registerCommand('strata.auditExport', async () => {
            const uri = await vscode.window.showSaveDialog({ defaultUri: vscode.Uri.file('audit-export.json'), filters: { 'JSON': ['json'], 'NDJSON': ['ndjson'] }, title: 'Export Audit Trail' });
            if (!uri) return;
            const formatArgs = uri.fsPath.endsWith('.ndjson') ? ['--format', 'ndjson'] : ['--format', 'json'];
            _client?.runInTerminal(['audit', 'export', ...formatArgs, '--out', uri.fsPath], 'strata audit export');
        }),

        vscode.commands.registerCommand('strata.auditFilter', () => { _auditView?.cycleFilter(); }),

        vscode.commands.registerCommand('strata.auditSetLimit', async () => {
            const input = await vscode.window.showInputBox({ prompt: 'Number of audit entries to show (5–200)', value: '20', validateInput: v => { const n = parseInt(v, 10); return isNaN(n) || n < 5 || n > 200 ? 'Enter a number between 5 and 200' : null; } });
            if (!input) return;
            _auditView?.setLimit(parseInt(input, 10));
        }),

        // ── Guide / profile ────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.showGuide', () => { _guideView?.show(_lastStatus); }),

        vscode.commands.registerCommand('strata.switchProfile', async () => {
            if (!_client) return;
            const profiles = _lastStatus?.profiles;
            if (!profiles?.all.length) { void vscode.window.showWarningMessage('Strata: no profiles found.'); return; }
            const items = profiles.all.map(p => ({ label: p, description: p === profiles.active ? '(active)' : '' }));
            const selected = await vscode.window.showQuickPick(items, { title: 'Strata: Switch Profile', placeHolder: `Current: ${profiles.active ?? 'none'}` });
            if (!selected || selected.label === profiles.active) return;
            try {
                await _client.activateProfile(selected.label);
                void vscode.window.showInformationMessage(`Strata: profile "${selected.label}" activated.`);
                void _refreshAll();
            } catch (err) {
                void vscode.window.showErrorMessage(`Strata: could not activate profile — ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.activateProfile', async (profileName?: string) => {
            if (!_client) return;
            const name = profileName ?? await vscode.window.showInputBox({ prompt: 'Profile name to activate' });
            if (!name) return;
            try {
                await _client.activateProfile(name);
                void vscode.window.showInformationMessage(`Strata: profile "${name}" activated.`);
                void _refreshAll();
            } catch (err) {
                void vscode.window.showErrorMessage(`Strata: could not activate profile — ${String(err)}`);
            }
        }),

        // ── Schema ─────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.exportSchemas', async () => {
            if (!_client) return;
            try { await _client.wireSchemas(); void vscode.window.showInformationMessage('Strata: schemas exported and wired.'); }
            catch (err) { void vscode.window.showErrorMessage(`Schema export failed: ${String(err)}`); }
        }),

        vscode.commands.registerCommand('strata.openSchema', async (args?: { kind?: string }) => {
            let kind = args?.kind;
            if (!kind) {
                const doc = vscode.window.activeTextEditor?.document;
                if (doc) {
                    for (let i = 0; i < Math.min(doc.lineCount, 20); i++) {
                        const m = doc.lineAt(i).text.trim().match(/^kind:\s*(\S+)/);
                        if (m) { kind = m[1].toLowerCase(); break; }
                    }
                }
            }
            if (!kind) { void vscode.window.showWarningMessage('Strata: could not determine document kind.'); return; }
            const schemaUri = vscode.Uri.joinPath(vscode.Uri.file(workPath), '.strata', 'schemas', `${kind}.json`);
            try {
                await vscode.window.showTextDocument(schemaUri, { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true, preview: true });
            } catch {
                const pick = await vscode.window.showWarningMessage(`Schema not found for kind "${kind}". Export schemas first.`, 'Export & Wire Schemas');
                if (pick) void vscode.commands.executeCommand('strata.exportSchemas');
            }
        }),

        // ── Lock ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.lockStatus', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target || !_client) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            try {
                const lock = await _client.getLockStatus(target);
                if (lock.locked) {
                    const pick = await vscode.window.showWarningMessage(`🔒 Locked by "${lock.holder ?? 'unknown'}" since ${lock.acquired_at ?? 'unknown'}`, 'Force Release');
                    if (pick === 'Force Release') void vscode.commands.executeCommand('strata.releaseLock', target);
                } else {
                    void vscode.window.showInformationMessage('🔓 No deployment lock held.');
                }
                _operationsView?.refreshLock(target);
            } catch (err) {
                void vscode.window.showErrorMessage(`Lock status failed: ${String(err)}`);
            }
        }),

        vscode.commands.registerCommand('strata.releaseLock', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target || !_client) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            const confirmed = await vscode.window.showWarningMessage('Force-release the deployment lock? Only do this if a deploy crashed.', { modal: true }, 'Release Lock');
            if (confirmed !== 'Release Lock') return;
            try {
                await _client.releaseLock(target);
                void vscode.window.showInformationMessage('Strata: deployment lock released.');
                _operationsView?.refreshLock(target);
            } catch (err) {
                void vscode.window.showErrorMessage(`Lock release failed: ${String(err)}`);
            }
        }),

        // ── Values ─────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.showValues', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            _valuesView?.loadFile(target);
            void vscode.commands.executeCommand('strataValues.focus');
        }),

        vscode.commands.registerCommand('strata.copyValueKey', async (key: string) => {
            await vscode.env.clipboard.writeText(key);
            void vscode.window.showInformationMessage(`Copied "${key}" to clipboard.`);
        }),

        vscode.commands.registerCommand('strata.copyOutputValue', async (key: string, value: string | null) => {
            if (value === null) { void vscode.window.showWarningMessage(`"${key}" is sensitive — value not available in UI.`); return; }
            await vscode.env.clipboard.writeText(value);
            void vscode.window.showInformationMessage(`Copied "${key}" to clipboard.`);
        }),

        // ── Policy ─────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.checkPolicy', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target || !_diagnostics) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            try {
                const result = await vscode.window.withProgress(
                    { location: vscode.ProgressLocation.Notification, title: 'Strata: checking policies…', cancellable: false },
                    () => _diagnostics!.checkPolicyDiagnostics(vscode.Uri.file(target)),
                );
                if (!result) { void vscode.window.showInformationMessage('Strata: no policies defined for this deployment.'); return; }
                if (result.denied > 0) {
                    void vscode.window.showErrorMessage(`Policy check: ${result.denied} deny violation(s) — see Problems panel`, 'Open Problems').then(v => { if (v) void vscode.commands.executeCommand('workbench.actions.view.problems'); });
                } else if (result.failed > 0) {
                    void vscode.window.showWarningMessage(`Policy check: ${result.failed} warning(s) — see Problems panel`, 'Open Problems').then(v => { if (v) void vscode.commands.executeCommand('workbench.actions.view.problems'); });
                } else {
                    void vscode.window.showInformationMessage(`Policy check: ${result.policies_checked} policies passed ✅`);
                }
            } catch (err) {
                void vscode.window.showErrorMessage(`Policy check failed: ${String(err)}`);
            }
        }),

        // ── SBOM ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.buildSbom', async (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            try {
                void vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Strata: generating SBOM…', cancellable: false }, async () => {
                    if (!_client) return;
                    const sbom = await _client.generateSbom(target);
                    const parts = [`${sbom.component_count} components`];
                    if (sbom.critical_count > 0) parts.push(`${sbom.critical_count} critical CVEs`);
                    if (sbom.high_count > 0) parts.push(`${sbom.high_count} high CVEs`);
                    const label = sbom.vulnerabilities_found ? `⚠️ ${parts.join(' · ')}` : `✅ ${parts.join(' · ')} — no vulnerabilities`;
                    void vscode.window.showInformationMessage(`Strata SBOM: ${label}`, ...(sbom.output_file ? ['Open SBOM'] : [])).then(v => {
                        if (v === 'Open SBOM' && sbom.output_file) void vscode.window.showTextDocument(vscode.Uri.file(sbom.output_file));
                    });
                });
            } catch {
                _client?.runInTerminal(['build', 'sbom', '-f', target], 'strata build sbom');
            }
        }),

        // ── Refs ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.manageRefs', async () => {
            if (!_client) return;
            const profiles = _lastStatus?.profiles;
            if (!profiles?.all.length) { void vscode.window.showWarningMessage('No profiles found.'); return; }
            const profilePick = await vscode.window.showQuickPick(profiles.all.map(p => ({ label: p, description: p === profiles.active ? '(active)' : '' })), { title: 'Manage Refs — select profile' });
            if (!profilePick) return;
            const typePick = await vscode.window.showQuickPick([{ label: 'env' }, { label: 'config' }, { label: 'data' }, { label: 'secret' }], { title: `Manage Refs — ${profilePick.label} — select type` });
            if (!typePick) return;
            const refType = typePick.label as 'env' | 'config' | 'data' | 'secret';
            let existing: import('./strataClient').RefEntry[] = [];
            try { existing = await _client.listRefs(profilePick.label, refType); } catch { /* none yet */ }
            const actions = [...existing.map(r => ({ label: `$(trash) Remove: ${r.name}`, description: r.path, action: 'remove' as const, name: r.name })), { label: '$(add) Add new reference…', description: '', action: 'add' as const, name: '' }];
            const actionPick = await vscode.window.showQuickPick(actions, { title: `Refs: ${profilePick.label} / ${refType}` });
            if (!actionPick) return;
            if (actionPick.action === 'remove') {
                const confirm = await vscode.window.showWarningMessage(`Remove ref "${actionPick.name}"?`, { modal: true }, 'Remove');
                if (confirm !== 'Remove') return;
                try { await _client.removeRef(profilePick.label, refType, actionPick.name); void vscode.window.showInformationMessage(`Removed ref "${actionPick.name}".`); }
                catch (err) { void vscode.window.showErrorMessage(`Remove ref failed: ${String(err)}`); }
            } else {
                const name = await vscode.window.showInputBox({ prompt: 'Reference name' });
                if (!name) return;
                const p = await vscode.window.showInputBox({ prompt: 'File path' });
                if (!p) return;
                try { await _client.addRef(profilePick.label, refType, name, p); void vscode.window.showInformationMessage(`Added ref "${name}" → ${p}.`); }
                catch (err) { void vscode.window.showErrorMessage(`Add ref failed: ${String(err)}`); }
            }
        }),

        // ── Repos ──────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.syncRepo', async (item?: { payload?: unknown }) => {
            const name = typeof item?.payload === 'string' ? item.payload : undefined;
            if (!_workspaceHealth) return;
            await _workspaceHealth.syncRepo(name);
            void _refreshAll();
        }),

        vscode.commands.registerCommand('strata.addRepo', async () => {
            const name = await vscode.window.showInputBox({ prompt: 'Repository name', placeHolder: 'e.g. my-infra' });
            if (!name) return;
            const repoPath = await vscode.window.showInputBox({ prompt: 'Repository path', placeHolder: '/path/to/repo or ../relative/path' });
            if (!repoPath || !_client) return;
            try { await _client.addRepo(name, repoPath); void vscode.window.showInformationMessage(`Strata: repository "${name}" added.`); void _refreshAll(); }
            catch (err) { void vscode.window.showErrorMessage(`Add repository failed: ${String(err)}`); }
        }),

        vscode.commands.registerCommand('strata.removeRepo', async (item?: { payload?: unknown }) => {
            const name = typeof item?.payload === 'string'
                ? item.payload
                : await vscode.window.showInputBox({ prompt: 'Repository name to remove' });
            if (!name || !_workspaceHealth) return;
            await _workspaceHealth.removeRepo(name);
            void _refreshAll();
        }),

        // ── Cost ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.showCostHistory', (filePath?: string) => {
            const target = _resolveTarget(filePath);
            if (!target) { void vscode.window.showWarningMessage('No deployment selected or file open.'); return; }
            _client?.runInTerminal(['cost', 'history', '-f', target], 'strata cost history');
        }),

        // ── Misc ───────────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.openConsole', () => {
            const t = vscode.window.createTerminal({ name: 'strata console', cwd: workPath });
            t.show();
            t.sendText(`${getCliPath()} console`);
        }),

        vscode.commands.registerCommand('strata.refreshTreeView', () => { void _refreshAll(); }),

        vscode.commands.registerCommand('strata.openFile', async (item?: { filePath?: string }) => {
            if (typeof item?.filePath === 'string') {
                await vscode.window.showTextDocument(vscode.Uri.file(item.filePath));
            }
        }),

        // ── Promotions ─────────────────────────────────────────────────────────

        vscode.commands.registerCommand('strata.promoteStatus', () => { _promotionsView?.refresh(); void vscode.commands.executeCommand('strataPromotions.focus'); }),
        vscode.commands.registerCommand('strata.promoteMatrix', () => { _promotionsView?.refresh(); void vscode.commands.executeCommand('strataPromotions.focus'); }),
        vscode.commands.registerCommand('strata.promoteHistory', () => { _promotionsView?.refresh(); void vscode.commands.executeCommand('strataPromotions.focus'); }),

        vscode.commands.registerCommand('strata.promoteStart', async () => {
            if (!_client) return;
            const ring = await vscode.window.showInputBox({ prompt: 'Target ring', placeHolder: 'e.g. staging' });
            if (!ring) return;
            const target = await vscode.window.showInputBox({ prompt: 'Target name (remote/module)', placeHolder: 'e.g. myapp' });
            if (!target) return;
            const confirmed = await vscode.window.showWarningMessage(`Promote "${target}" to ring "${ring}"?`, { modal: true }, 'Promote');
            if (confirmed !== 'Promote') return;
            _client.runPromoteStart(ring, target);
        }),

        vscode.commands.registerCommand('strata.promoteRollback', async () => {
            if (!_client) return;
            const ring = await vscode.window.showInputBox({ prompt: 'Ring to rollback', placeHolder: 'e.g. staging' });
            if (!ring) return;
            const target = await vscode.window.showInputBox({ prompt: 'Target name', placeHolder: 'e.g. myapp' });
            if (!target) return;
            const confirmed = await vscode.window.showWarningMessage(`Rollback "${target}" in ring "${ring}"?`, { modal: true }, 'Rollback');
            if (confirmed !== 'Rollback') return;
            _client.runPromoteRollback(ring, target);
        }),

        vscode.commands.registerCommand('strata.refreshPromotions', () => { _promotionsView?.refresh(); }),
    );

    // ── Re-create client when CLI path changes ─────────────────────────────────

    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('strata.cliPath')) {
                _client = new StrataClient(getCliPath(), workPath);
                _statusBar?.setClient(_client);
                _diagnostics?.setClient(_client);
                _workspaceHealth?.setClient(_client);
                _deploymentExplorer?.setClient(_client);
                _operationsView?.setClient(_client);
                _auditView?.setClient(_client);
                _valuesView?.setClient(_client);
                _promotionsView?.setClient(_client);
                void _refreshAll();
            }
        }),
    );

    // ── Validation notifications ───────────────────────────────────────────────

    if (_diagnostics) {
        context.subscriptions.push(
            _diagnostics.onDidChangeValidation((evt) => {
                if (evt.currentCount > 0 && evt.currentCount > evt.previousCount) {
                    const delta = evt.currentCount - evt.previousCount;
                    void vscode.window.showWarningMessage(
                        `Strata: ${delta} new validation error${delta !== 1 ? 's' : ''} in ${evt.relativePath}`,
                        'Open Problems',
                    ).then(v => { if (v === 'Open Problems') void vscode.commands.executeCommand('workbench.actions.view.problems'); });
                } else if (evt.currentCount === 0 && evt.previousCount > 0) {
                    void vscode.window.showInformationMessage(`Strata: ${evt.relativePath} — all errors resolved ✅`);
                }
            }),
        );
    }

    // ── Terminal close: refresh after build/deploy ─────────────────────────────

    context.subscriptions.push(
        vscode.window.onDidCloseTerminal((terminal) => {
            const name = terminal.name;
            if (name.startsWith('strata build') || name.startsWith('strata deploy')) {
                const exitCode = terminal.exitStatus?.code;
                const action = name.startsWith('strata build') ? 'Build' : 'Deploy';
                const deployTarget = _lastDeployTarget;
                if (action === 'Deploy') _lastDeployTarget = undefined;

                if (exitCode === 0) {
                    if (action === 'Deploy' && deployTarget) {
                        void (async () => {
                            try {
                                const health = await _client!.getDeployHealth(deployTarget);
                                _operationsView?.updateHealth(deployTarget, health);
                                _operationsView?.invalidateDeployment(deployTarget);
                            } catch { /* health check unavailable */ }
                        })();
                        void vscode.window.showInformationMessage('Strata: Deploy completed successfully.', 'View Outputs').then(v => {
                            if (v === 'View Outputs') _client?.runInTerminal(['env', 'output', '-f', deployTarget], 'strata env output');
                        });
                    } else {
                        void vscode.window.showInformationMessage(`Strata: ${action} completed successfully.`);
                    }
                } else if (exitCode !== undefined) {
                    void vscode.window.showWarningMessage(`Strata: ${action} exited with code ${exitCode} — check terminal output.`);
                }
                void _refreshAll();
            }
            if (name === 'strata env drift') {
                const exitCode = terminal.exitStatus?.code;
                const target = _lastDriftTarget;
                _lastDriftTarget = undefined;
                if (exitCode === 0) {
                    if (target) _operationsView?.markDrift(target, false);
                    void vscode.window.showInformationMessage('Strata: no drift detected ✅');
                } else if (exitCode === 3) {
                    if (target) _operationsView?.markDrift(target, true);
                    void vscode.window.showWarningMessage('Strata: drift detected — review the plan above.');
                }
            }
        }),
    );

    // ── File watcher: auto-refresh when solution.json changes ─────────────────

    const solutionWatcher = vscode.workspace.createFileSystemWatcher('**/.strata/solution.json');
    solutionWatcher.onDidChange(() => void _refreshAll());
    solutionWatcher.onDidCreate(() => void _refreshAll());
    solutionWatcher.onDidDelete(() => void _refreshAll());

    // ── Subscriptions cleanup ──────────────────────────────────────────────────

    context.subscriptions.push(
        _statusBar, _workspaceHealth, _deploymentExplorer, _operationsView,
        _deployCtx, _diagnostics, _codeLens, _guideView, _crossRef, _snippets,
        _taskProvider, _fileDecorations, _chatParticipant, solutionWatcher,
        _auditView!, _valuesView!,
    );

    // ── Context key ────────────────────────────────────────────────────────────

    void vscode.commands.executeCommand('setContext', 'strata.workspaceActive', true);

    // ── Initial data load ──────────────────────────────────────────────────────

    const defaultProfile = vscode.workspace.getConfiguration('strata').get<string>('defaultProfile', '');
    if (defaultProfile && _client) {
        void _client.activateProfile(defaultProfile)
            .then(() => _refreshAll())
            .catch(() => _refreshAll());
    } else {
        void _refreshAll();
    }
}