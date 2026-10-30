/**
 * deploymentSelector.ts - the deployment the Integrations and Policies views
 * are scoped to (docs/work/vscode-integrations-policies-views.md V2).
 *
 * One status bar item plus the `Strata: Select Deployment` quick pick of
 * every non-partial deployment from `strata deploy list`. The choice is
 * kept in workspaceState, so it survives a reload; `onDidChange` tells both
 * views to refresh.
 */

import * as vscode from 'vscode';
import { StrataClient } from '../strataClient';

const STATE_KEY = 'strata.selectedDeployment';
const NONE_LABEL = '$(circle-slash) No deployment';

export class DeploymentSelector implements vscode.Disposable {
    private readonly _item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    private readonly _onDidChange = new vscode.EventEmitter<string | undefined>();
    readonly onDidChange = this._onDidChange.event;

    constructor(
        private readonly _client: StrataClient,
        private readonly _state: vscode.Memento,
    ) {
        this._item.command = 'strata.selectDeployment';
        this._render();
        this._item.show();
    }

    get current(): string | undefined {
        return this._state.get<string>(STATE_KEY);
    }

    async pick(): Promise<void> {
        let items: vscode.QuickPickItem[];
        try {
            const envelope = await this._client.deploymentList();
            if (!envelope.ok) {
                const first = envelope.diagnostics.find((d) => d.severity === 'error');
                void vscode.window.showErrorMessage(`Strata: cannot list deployments - ${first?.message ?? 'unknown error'}`);
                return;
            }
            items = envelope.data.deployments
                .filter((row) => !row.partial)
                .map((row) => ({
                    label: row.name,
                    description: row.workspace ?? undefined,
                    detail: row.environments.length ? `environments: ${row.environments.join(', ')}` : undefined,
                    picked: row.name === this.current,
                }));
        } catch (err) {
            void vscode.window.showErrorMessage(`Strata: cannot list deployments - ${(err as Error).message}`);
            return;
        }
        const choice = await vscode.window.showQuickPick([{ label: NONE_LABEL }, ...items], {
            title: 'Strata: Select Deployment',
            placeHolder: this.current ? `Current: ${this.current}` : 'Scope the Integrations and Policies views',
        });
        if (!choice) return;
        await this.set(choice.label === NONE_LABEL ? undefined : choice.label);
    }

    async set(name: string | undefined): Promise<void> {
        if (name === this.current) return;
        await this._state.update(STATE_KEY, name);
        this._render();
        this._onDidChange.fire(name);
    }

    private _render(): void {
        const name = this.current;
        this._item.text = `$(rocket) ${name ?? 'no deployment'}`;
        this._item.tooltip = name
            ? `Strata deployment: ${name} - click to change`
            : 'Strata: select the deployment the Integrations and Policies views are scoped to';
    }

    dispose(): void {
        this._item.dispose();
        this._onDidChange.dispose();
    }
}
