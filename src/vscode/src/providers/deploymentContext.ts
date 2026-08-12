/**
 * DeploymentContext — singleton state manager for the currently-focused deployment.
 *
 * All extension views and commands default to the active deployment rather than
 * "whatever file happens to be open".  Selection is persisted across sessions via
 * workspaceState and fires onDidChange so all providers can react.
 *
 * Usage:
 *   const ctx = new DeploymentContext(extensionContext);
 *   ctx.onDidChange(file => { ... });
 *   ctx.setFile('/path/to/deploy.yaml');
 *   const file = ctx.activeFile;
 */

import * as vscode from 'vscode';
import * as path from 'path';
import type { WorkspaceStatus } from '../strataClient';

const STORAGE_KEY = 'strata.activeDeployment';

export class DeploymentContext implements vscode.Disposable {
    private _activeFile: string | undefined;
    private readonly _onChange = new vscode.EventEmitter<string | undefined>();
    readonly onDidChange = this._onChange.event;

    constructor(private readonly _extCtx: vscode.ExtensionContext) {
        this._activeFile = _extCtx.workspaceState.get<string>(STORAGE_KEY);
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    get activeFile(): string | undefined { return this._activeFile; }

    get activeName(): string | undefined {
        return this._activeFile ? path.basename(this._activeFile, '.yaml') : undefined;
    }

    /** Set the active deployment file and persist the selection. */
    setFile(filePath: string): void {
        this._activeFile = filePath;
        void this._extCtx.workspaceState.update(STORAGE_KEY, filePath);
        this._onChange.fire(filePath);
    }

    /** Clear the active deployment. */
    clear(): void {
        this._activeFile = undefined;
        void this._extCtx.workspaceState.update(STORAGE_KEY, undefined);
        this._onChange.fire(undefined);
    }

    /**
     * Present a Quick Pick populated from deployment files in the workspace status
     * and update the active file on selection.
     */
    async selectDeployment(status: WorkspaceStatus): Promise<void> {
        const deployments = status.deployments ?? [];
        if (deployments.length === 0) {
            void vscode.window.showWarningMessage(
                'Strata: no deployment files found. Create one with Strata: New File.',
            );
            return;
        }
        const items = deployments.map(d => ({
            label: `$(cloud) ${d.name}`,
            description: vscode.workspace.asRelativePath(d.path),
            detail: d.path === this._activeFile ? '✓ currently active' : undefined,
            filePath: d.path,
        }));
        const pick = await vscode.window.showQuickPick(items, {
            title: 'Strata: Select Active Deployment',
            placeHolder: 'Select the deployment to focus on',
            matchOnDescription: true,
        });
        if (pick) {
            this.setFile(pick.filePath);
        }
    }

    /**
     * Auto-select when exactly one deployment exists and nothing is currently
     * selected.  Called after the first successful workspace status fetch.
     */
    autoSelect(status: WorkspaceStatus): void {
        if (this._activeFile) return;
        const deployments = status.deployments ?? [];
        if (deployments.length === 1) {
            this.setFile(deployments[0].path);
        }
    }

    dispose(): void {
        this._onChange.dispose();
    }
}
