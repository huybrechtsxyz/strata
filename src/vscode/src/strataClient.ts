/**
 * StrataClient — thin wrapper around the strata CLI.
 *
 * Every method spawns `strata <cmd> --output json --quiet` and parses the
 * structured JSON envelope that the CLI emits on stdout.
 *
 * JSON envelope shape (every command):
 *   { success, command, execution_id, timestamp, data: T, messages, errors }
 *
 * Exit codes:
 *   0  — success
 *   1  — system / execution failure
 *   2  — usage error (bad CLI args — should not happen from the extension)
 *   3  — validation failure — stdout still contains the valid JSON envelope
 */

import * as vscode from 'vscode';
import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// JSON shapes returned inside the CLI envelope's `data` field
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

/** Matches ValidationError.to_dict() in strata/models/validation_error.py */
export interface ValidationError {
    code: string;
    message: string;
    phase: number;
    field?: string;
    value?: string;
    context?: Record<string, unknown>;
}

/** Matches run_validate_command._output_data */
export interface ValidationResult {
    file: string;
    kind: string | null;
    deep: boolean;
    validation_passed: boolean;
    errors: ValidationError[];
    suggestions?: string[];
}

// ---------------------------------------------------------------------------
// CLI response envelope
// ---------------------------------------------------------------------------

export interface CliResponse<T> {
    success: boolean;
    command: string;
    execution_id: string;
    timestamp: string;
    data: T;
    messages: string[];
    errors: string[];
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Thrown when the CLI executable is not found (ENOENT). */
export class StrataCLINotFoundError extends Error {
    constructor(cliPath: string) {
        super(
            `Strata CLI not found: "${cliPath}". ` +
            `Check the strata.cliPath setting (e.g. "uv run strata" for uv projects).`,
        );
        this.name = 'StrataCLINotFoundError';
    }
}

/** Thrown when the CLI returns success:false (exit 1) and no parseable output. */
export class StrataCLIError extends Error {
    constructor(
        public readonly response: CliResponse<unknown> | null,
        public readonly stderr: string,
    ) {
        const detail = response?.errors?.join('; ') ?? stderr;
        super(`Strata CLI error: ${detail}`);
        this.name = 'StrataCLIError';
    }
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

    /** Run `strata sln status --output json --quiet` and return workspace state. */
    async getStatus(): Promise<WorkspaceStatus> {
        const resp = await this._run<WorkspaceStatus>([
            'sln', 'status', '--output', 'json', '--quiet',
        ]);
        return resp.data;
    }

    // ── Validation ────────────────────────────────────────────────────────────

    /** Run `strata validate -f <filePath> --output json --quiet`. */
    async validateFile(filePath: string): Promise<ValidationResult> {
        // Exit code 3 = validation failure — _run() handles it, returns envelope
        const resp = await this._run<ValidationResult>([
            'validate', '-f', filePath, '--output', 'json', '--quiet',
        ]);
        return resp.data;
    }

    // ── Schema ────────────────────────────────────────────────────────────────

    /** Run `strata schema wire` to export schemas and patch .vscode/settings.json. */
    async wireSchemas(): Promise<void> {
        await this._run<Record<string, unknown>>([
            'schema', 'wire', '--output', 'json', '--quiet',
        ]);
    }

    // ── Build / Deploy — run in terminal so user sees streaming output ─────────

    /**
     * Open a VS Code terminal and run a build or deploy command there.
     * Streaming CLI output is visible to the user in real time.
     */
    runInTerminal(args: string[], terminalName: string): void {
        const parts = this.cliPath.trim().split(/\s+/);
        const fullCmd = [...parts, ...args, '--work-path', this.workPath].join(' ');
        const terminal = vscode.window.createTerminal({ name: terminalName, cwd: this.workPath });
        terminal.show();
        terminal.sendText(fullCmd);
    }

    // ── Internal ──────────────────────────────────────────────────────────────

    /**
     * Spawn the CLI, parse JSON from stdout, and return the envelope.
     *
     * - Always appends `--work-path <workPath>` so the CLI targets the correct workspace.
     * - Exit code 3 (validation failure) is treated as a successful call — stdout
     *   contains the valid envelope with validation_passed:false in data.
     * - Exit code 'ENOENT' means the CLI binary is not installed.
     */
    private async _run<T>(args: string[]): Promise<CliResponse<T>> {
        // Support compound CLI paths like "uv run strata"
        const parts = this.cliPath.trim().split(/\s+/);
        const executable = parts[0];
        const prefixArgs = parts.slice(1);

        const fullArgs = [
            ...prefixArgs,
            ...args,
            '--work-path', this.workPath,
        ];

        let stdout = '';
        let stderr = '';

        try {
            const result = await execFileAsync(executable, fullArgs, {
                timeout: 30_000,
                maxBuffer: 10 * 1024 * 1024, // 10 MB
                windowsHide: true,
            });
            stdout = result.stdout;
            stderr = result.stderr;
        } catch (err: unknown) {
            const execErr = err as NodeJS.ErrnoException & {
                stdout?: string;
                stderr?: string;
                code?: number | string;
            };

            stdout = execErr.stdout ?? '';
            stderr = execErr.stderr ?? '';

            // CLI not installed
            if (execErr.code === 'ENOENT') {
                throw new StrataCLINotFoundError(this.cliPath);
            }

            // Exit code 3 = validation failure — stdout has valid JSON
            if (typeof execErr.code === 'number' && execErr.code === 3 && stdout) {
                // Fall through to parse below
            } else if (!stdout) {
                // Exit 1 with no parseable output — surface the error
                throw new StrataCLIError(null, stderr);
            }
        }

        let envelope: CliResponse<T>;
        try {
            envelope = JSON.parse(stdout) as CliResponse<T>;
        } catch {
            throw new StrataCLIError(null, `Could not parse CLI output: ${stdout.slice(0, 200)}`);
        }

        // success:false with exit 1 — propagate as error so callers don't have to check
        if (!envelope.success && envelope.errors?.length) {
            throw new StrataCLIError(envelope as CliResponse<unknown>, stderr);
        }

        return envelope;
    }
}

// ---------------------------------------------------------------------------
// Helpers read by extension.ts
// ---------------------------------------------------------------------------

/** Read the CLI path from VS Code settings. */
export function getCliPath(): string {
    const config = vscode.workspace.getConfiguration('strata');
    return config.get<string>('cliPath', 'strata');
}

/** Return the first open workspace folder's fs path, or undefined. */
export function getWorkPath(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

