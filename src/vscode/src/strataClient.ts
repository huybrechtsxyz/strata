/**
 * StrataClient — thin wrapper around the strata CLI.
 *
 * Every method spawns `strata <cmd> --output json --quiet` and parses
 * the structured JSON response.  No logic is implemented yet — all methods
 * throw a "not implemented" error as placeholders.
 *
 * TODO: implement each method by spawning a child process via Node's
 *   `child_process.execFile` and parsing stdout as JSON.
 */

import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// Response types — mirror the JSON shapes returned by the strata CLI
// ---------------------------------------------------------------------------

export interface ChecklistItem {
    phase: number;
    label: string;
    status: 'ok' | 'warn' | 'pending';
    detail: string | null;
}

export interface NextStep {
    phase: number;
    label: string;
    hint: string;
    see_also: string | null;
}

export interface ReadinessData {
    phases_complete: number;
    phases_total: number;
    complete: boolean;
    checklist: ChecklistItem[];
    next_step: NextStep | null;
}

export interface SolutionData {
    initialized: boolean;
    work_path: string;
    id: string | null;
    name: string | null;
}

export interface ProfileData {
    active: string | null;
    all: string[];
    paths: Record<string, Array<{ name: string; path: string }>>;
}

export interface RepositoryInfo {
    name: string;
    url: string;
    path: string;
    type: string;
    branch: string;
    cloned: boolean;
}

export interface IntegrationInfo {
    name: string;
    available: boolean;
    version: string | null;
    info: string | null;
}

export interface HealthData {
    status: 'HEALTHY' | 'DEGRADED' | 'BROKEN';
    issues: string[];
}

export interface WorkspaceStatus {
    health: HealthData;
    solution: SolutionData;
    readiness: ReadinessData;
    profiles: ProfileData;
    repositories: RepositoryInfo[];
    integrations: Record<string, IntegrationInfo>;
}

export interface ValidationError {
    field: string | null;
    message: string;
    severity: 'error' | 'warning';
}

export interface ValidationResult {
    valid: boolean;
    kind: string | null;
    name: string | null;
    file: string;
    errors: ValidationError[];
}

// ---------------------------------------------------------------------------
// CLI response envelope — wraps every command's data
// ---------------------------------------------------------------------------

export interface CliResponse<T> {
    success: boolean;
    command: string;
    data: T;
    messages: string[];
    errors: string[];
}

// ---------------------------------------------------------------------------
// StrataClient
// ---------------------------------------------------------------------------

export class StrataClient {
    constructor(
        private readonly cliPath: string,
        private readonly workPath: string,
    ) { }

    // ── Workspace ─────────────────────────────────────────────────────────────

    /**
     * Run `strata sln status --output json` and return parsed workspace state.
     * TODO: implement — spawn CLI, parse stdout
     */
    async getStatus(): Promise<WorkspaceStatus> {
        throw new Error('StrataClient.getStatus — not implemented');
    }

    // ── Validation ────────────────────────────────────────────────────────────

    /**
     * Run `strata validate -f <filePath> --output json` and return parse result.
     * TODO: implement — spawn CLI, parse stdout
     */
    async validateFile(filePath: string): Promise<ValidationResult> {
        throw new Error('StrataClient.validateFile — not implemented');
    }

    // ── Schema ────────────────────────────────────────────────────────────────

    /**
     * Run `strata schema wire --work-path <workPath>` to export schemas and
     * write yaml.schemas into .vscode/settings.json.
     * TODO: implement — spawn CLI, handle exit code
     */
    async wireSchemas(): Promise<void> {
        throw new Error('StrataClient.wireSchemas — not implemented');
    }

    // ── Build ─────────────────────────────────────────────────────────────────

    /**
     * Run `strata build run -f <deploymentFile> [--dry-run] --output json`.
     * TODO: implement — spawn CLI in terminal so user sees progress
     */
    async buildRun(deploymentFile: string, dryRun: boolean): Promise<void> {
        throw new Error('StrataClient.buildRun — not implemented');
    }

    // ── Deploy ────────────────────────────────────────────────────────────────

    /**
     * Run `strata deploy run -f <deploymentFile> --dry-run --output json`.
     * TODO: implement — spawn CLI in terminal, never run real deploy via extension
     */
    async deployDryRun(deploymentFile: string): Promise<void> {
        throw new Error('StrataClient.deployDryRun — not implemented');
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    /**
     * TODO: implement — use child_process.execFile to run the CLI and return
     * parsed JSON. Handle exit code 3 (validation failure) without throwing.
     */
    private async _run<T>(args: string[]): Promise<CliResponse<T>> {
        throw new Error('StrataClient._run — not implemented');
    }

    /**
     * TODO: implement — open a VS Code terminal and run the command there so
     * the user can see streaming output (for build/deploy operations).
     */
    private _runInTerminal(args: string[], terminalName: string): void {
        const terminal = vscode.window.createTerminal(terminalName);
        terminal.show();
        terminal.sendText(`${this.cliPath} ${args.join(' ')}`);
    }
}

/**
 * Read the CLI path from VS Code settings.
 */
export function getCliPath(): string {
    const config = vscode.workspace.getConfiguration('strata');
    return config.get<string>('cliPath', 'strata');
}

/**
 * Read the workspace root from the first opened workspace folder.
 */
export function getWorkPath(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}
