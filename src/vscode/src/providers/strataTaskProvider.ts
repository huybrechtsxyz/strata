/**
 * StrataTaskProvider — registers a `strata` task type so users can define
 * build/deploy/validate operations in `.vscode/tasks.json` and bind them
 * to keyboard shortcuts.
 *
 * Example task definition:
 *
 * ```json
 * {
 *   "type": "strata",
 *   "command": "build",
 *   "file": "deploy/main.yaml",
 *   "dryRun": true,
 *   "label": "Build (dry run)"
 * }
 * ```
 *
 * Supported commands: validate, build, deploy
 * Optional fields:   file, dryRun, profile, stage
 *
 * The provider also auto-detects deployment files in the workspace and
 * generates default tasks for them (validate + build dry-run + deploy dry-run).
 */

import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// Task definition shape (must match taskDefinitions in package.json)
// ---------------------------------------------------------------------------

interface StrataTaskDefinition extends vscode.TaskDefinition {
    /** strata sub-command: validate | build | deploy */
    command: string;
    /** YAML file path (relative to workspace root). Required for build/deploy. */
    file?: string;
    /** Run in dry-run mode. Default: false. */
    dryRun?: boolean;
    /** Target a specific deployment stage. */
    stage?: string;
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

const TASK_TYPE = 'strata';

export class StrataTaskProvider implements vscode.TaskProvider, vscode.Disposable {

    private _disposable: vscode.Disposable | undefined;

    constructor(
        private readonly _cliPath: string,
        private readonly _workPath: string,
    ) { }

    register(context: vscode.ExtensionContext): void {
        this._disposable = vscode.tasks.registerTaskProvider(TASK_TYPE, this);
        context.subscriptions.push(this._disposable);
    }

    dispose(): void {
        this._disposable?.dispose();
    }

    // ── TaskProvider interface ────────────────────────────────────────────────

    /**
     * Auto-discover tasks by scanning the workspace for deployment YAML files.
     * For each deployment file, create validate + build dry-run + deploy dry-run + sbom tasks.
     */
    async provideTasks(): Promise<vscode.Task[]> {
        const tasks: vscode.Task[] = [];

        // Find deployment files — files under deploy/ or files containing kind: deployment
        const deployFiles = await vscode.workspace.findFiles(
            '{deploy/**/*.yaml,**/deploy*.yaml}',
            '**/.strata/**',
        );

        for (const uri of deployFiles) {
            const relPath = vscode.workspace.asRelativePath(uri);
            const baseName = relPath.replace(/\.yaml$/, '').replace(/[/\\]/g, '-');

            tasks.push(
                this._createTask({
                    type: TASK_TYPE,
                    command: 'validate',
                    file: relPath,
                }, `strata: validate ${baseName}`),
            );

            tasks.push(
                this._createTask({
                    type: TASK_TYPE,
                    command: 'build',
                    file: relPath,
                    dryRun: true,
                }, `strata: build (dry run) ${baseName}`),
            );

            tasks.push(
                this._createTask({
                    type: TASK_TYPE,
                    command: 'build',
                    file: relPath,
                    dryRun: false,
                }, `strata: build ${baseName}`),
            );

            tasks.push(
                this._createTask({
                    type: TASK_TYPE,
                    command: 'deploy',
                    file: relPath,
                    dryRun: true,
                }, `strata: deploy (dry run) ${baseName}`),
            );

            tasks.push(
                this._createTask({
                    type: TASK_TYPE,
                    command: 'sbom',
                    file: relPath,
                }, `strata: sbom ${baseName}`),
            );
        }

        return tasks;
    }

    /**
     * Resolve a task from tasks.json — VS Code calls this when the user has
     * manually defined a task with `"type": "strata"`.
     */
    resolveTask(task: vscode.Task): vscode.Task | undefined {
        const def = task.definition as StrataTaskDefinition;
        if (def.type !== TASK_TYPE || !def.command) {
            return undefined;
        }
        return this._createTask(def, task.name);
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private _createTask(def: StrataTaskDefinition, label: string): vscode.Task {
        const args = this._buildArgs(def);
        const shellCmd = `${this._cliPath} ${args.join(' ')}`;

        const execution = new vscode.ShellExecution(shellCmd, {
            cwd: this._workPath,
        });

        const task = new vscode.Task(
            def,
            vscode.TaskScope.Workspace,
            label,
            'strata',         // source
            execution,
            '$strata',         // problem matcher (none defined yet — placeholder)
        );

        // Sensible defaults
        task.group = def.command === 'build' || def.command === 'deploy'
            ? vscode.TaskGroup.Build
            : vscode.TaskGroup.Test;
        task.presentationOptions = {
            reveal: vscode.TaskRevealKind.Always,
            panel: vscode.TaskPanelKind.Dedicated,
        };

        return task;
    }

    /**
     * Map a task definition to CLI arguments.
     */
    private _buildArgs(def: StrataTaskDefinition): string[] {
        const args: string[] = [];

        switch (def.command) {
            case 'validate':
                args.push('validate');
                if (def.file) args.push('-f', def.file);
                break;

            case 'build':
                args.push('build', 'run');
                if (def.file) args.push('-f', def.file);
                if (def.dryRun) args.push('--dry-run');
                if (def.stage) args.push('--stage', def.stage);
                break;

            case 'deploy':
                args.push('deploy', 'run');
                if (def.file) args.push('-f', def.file);
                if (def.dryRun) args.push('--dry-run');
                if (!def.dryRun) args.push('--force');
                if (def.stage) args.push('--stage', def.stage);
                break;

            case 'sbom':
                args.push('build', 'sbom');
                if (def.file) args.push('-f', def.file);
                break;

            default:
                // Pass through unknown commands for forward compatibility
                args.push(def.command);
                if (def.file) args.push('-f', def.file);
                break;
        }

        args.push('--work-path', this._workPath);
        return args;
    }
}
