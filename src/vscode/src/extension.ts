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
import { DeploymentSelector } from './providers/deploymentSelector';
import { DiagnosticsProvider } from './providers/diagnosticsProvider';
import { GraphViewProvider } from './providers/graphViewProvider';
import { IntegrationItem, IntegrationsViewProvider } from './providers/integrationsViewProvider';
import { PoliciesViewProvider, PolicyItem } from './providers/policiesViewProvider';
import { StatusBarProvider } from './providers/statusBarProvider';
import { Envelope, StrataClient } from './strataClient';

let _client: StrataClient | undefined;
let _diagnostics: DiagnosticsProvider | undefined;
let _graphView: GraphViewProvider | undefined;
let _statusBar: StatusBarProvider | undefined;
let _output: vscode.OutputChannel | undefined;

const UPDATE_CONFIRM =
    'Refresh the solution files? This rewrites .strata/ (including the editor schemas) and the strata agent ' +
    'skill (.claude/skills/strata/, unless your team removed its generated marker), and creates any missing ' +
    '.vscode/, .github/ and .claude/ scaffold files. Other existing files are never overwritten.';

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

/**
 * Run one CLI call behind a progress notification, write its diagnostics to
 * the output channel and report the outcome (docs/work/vscode-integrations-policies-views.md V6).
 */
async function runReported<T>(
    output: vscode.OutputChannel,
    title: string,
    call: () => Promise<Envelope<T>>,
): Promise<Envelope<T> | undefined> {
    try {
        const envelope = await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: `Strata: ${title}...` },
            call,
        );
        output.appendLine(`strata ${envelope.command} (${envelope.version}): ${envelope.ok ? 'ok' : 'failed'}`);
        for (const diagnostic of envelope.diagnostics) {
            output.appendLine(`  ${diagnostic.severity}: ${diagnostic.message}`);
        }
        const first = envelope.diagnostics.find((d) => d.severity === 'error');
        const message = envelope.ok ? `Strata: ${title} passed.` : `Strata: ${title} failed - ${first?.message ?? 'see output'}`;
        const show = envelope.ok
            ? vscode.window.showInformationMessage(message, 'Show Details')
            : vscode.window.showErrorMessage(message, 'Show Details');
        void show.then((action) => action && output.show());
        return envelope;
    } catch (err) {
        void vscode.window.showErrorMessage(`Strata: ${title} failed - ${(err as Error).message}`);
        return undefined;
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

    const client = _client;
    const output = _output;
    const selector = new DeploymentSelector(client, context.workspaceState);
    const integrationsView = new IntegrationsViewProvider(client, () => selector.current);
    const policiesView = new PoliciesViewProvider(client, () => selector.current);
    context.subscriptions.push(
        selector,
        integrationsView,
        policiesView,
        vscode.window.registerTreeDataProvider('strataIntegrations', integrationsView),
        vscode.window.registerTreeDataProvider('strataPolicies', policiesView),
        selector.onDidChange(() => {
            void integrationsView.refresh();
            void policiesView.refresh();
        }),
        vscode.commands.registerCommand('strata.selectDeployment', () => selector.pick()),
        vscode.commands.registerCommand('strata.refreshIntegrations', () => integrationsView.refresh()),
        vscode.commands.registerCommand('strata.refreshPolicies', () => policiesView.refresh()),
        vscode.commands.registerCommand('strata.checkIntegration', async (item: IntegrationItem) => {
            await runReported(output, `check ${item.row.name}`, () => client.integrationCheck(item.row.name, selector.current));
            await integrationsView.refresh();
        }),
        vscode.commands.registerCommand('strata.checkPolicy', async (item?: PolicyItem) => {
            const deployment = selector.current;
            if (!deployment) {
                await selector.pick();
                return;
            }
            const title = item ? `check ${item.row.name}` : `check policies of ${deployment}`;
            await runReported(output, title, () => client.policyCheck(deployment, item?.row.name));
            await policiesView.refresh();
        }),
        vscode.commands.registerCommand('strata.showPolicyFindings', async (item: PolicyItem) => {
            await vscode.window.withProgress(
                { location: { viewId: 'strataPolicies' }, title: `findings of ${item.row.name}` },
                () => policiesView.showFindings(item.row.name),
            );
        }),
    );
    void integrationsView.refresh();
    void policiesView.refresh();

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
