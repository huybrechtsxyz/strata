/**
 * strataClient.ts — thin wrapper around the strata v2 CLI.
 *
 * Every call spawns `strata <command> [args] --output json` and parses the
 * JSON envelope every v2 command produces (see
 * `strata.commands.json_output.build_envelope`):
 *
 *   { ok, command, version, context, summary, diagnostics, data }
 *
 * Exit codes (`strata.commands.exit_codes`):
 *   0  success
 *   1  system failure
 *   2  usage error (bad args, or not inside a solution)
 *   3  validation failure — stdout still carries the envelope
 *
 * `version` is the one exception: it is not envelope-wrapped (`click.echo`
 * of a bare string), so it gets its own method instead of going through
 * `run()`.
 */

import { execFile } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { promisify } from 'util';
import * as vscode from 'vscode';

const execFileAsync = promisify(execFile);

export type Severity = 'error' | 'warning' | 'info';

export interface Diagnostic {
    severity: Severity;
    message: string;
    source?: string;
    location?: string;
    code?: string;
}

export interface Summary {
    errors: number;
    warnings: number;
    info: number;
    documents?: number;
}

export interface Envelope<T> {
    ok: boolean;
    command: string;
    version: string;
    context: Record<string, string>;
    summary: Summary;
    diagnostics: Diagnostic[];
    data: T;
}

/** One edge from `strata graph`'s `data.edges` — mirrors `GraphEdge` in graph_controller.py. */
export interface GraphEdge {
    source_kind: string;
    source_name: string;
    source_path: string;
    location: string;
    target_kind: string;
    target_name: string;
    resolved: boolean;
}

export interface GraphData {
    edges: GraphEdge[];
}

/** One `strata deployment list` row (`DeploymentSummary` in deployment_resolution.py). */
export interface DeploymentRow {
    name: string;
    workspace: string | null;
    environments: string[];
    partial: boolean;
    source: string;
}

export interface ReadinessProblem {
    code: string;
    message: string;
}

/** One `strata integration list` row (`_row_json` in integration_command.py). */
export interface IntegrationRow {
    name: string;
    type: string;
    declared: boolean;
    registered: boolean;
    enabled: boolean;
    required: boolean;
    local_tool: boolean;
    command: string | null;
    available: boolean | null;
    required_version: string | null;
    installed_version: string | null;
    connection_check: boolean;
    ok: boolean;
    credential_holder: boolean;
    problems: ReadinessProblem[];
    used_for: string[];
    source: string | null;
}

/** `ManifestPolicyResultModel`, as recorded in policy_results.json. */
export interface PolicyResult {
    policy_name: string;
    policy_type: string;
    phase: string;
    enforcement: string;
    passed: boolean;
    violations: string[];
}

/** One `strata policy list` row. */
export interface PolicyRow {
    name: string;
    phase: string;
    enforcement: string;
    backing: string;
    check: boolean;
    findings: boolean;
    last_result: PolicyResult | null;
}

/** `strata policy findings` data: one of three shapes, told apart by its keys. */
export interface CveFindingsData {
    policy: string;
    scanner: string;
    scanner_version: string;
    severities: string[];
    findings: {
        vulnerability_id: string;
        severity: string;
        package_name: string;
        installed_version: string;
        fixed_version: string | null;
        allowlisted: boolean;
    }[];
}

export interface CheckovFindingsData {
    policy: string;
    scanner_version: string | null;
    framework: string;
    severities: string[];
    nothing: string | null;
    targets: {
        target: string;
        path: string | null;
        missing: string | null;
        findings: {
            check_id: string;
            check_name: string;
            resource: string;
            file_path: string;
            file_line_range: number[] | null;
            severity: string;
            unrated: boolean;
        }[];
    }[];
}

export interface ImageFindingsData {
    policy: string;
    integration: string;
    severities: string[];
    images: {
        image: string;
        platform_name: string | null;
        status: string;
        detail: string | null;
        issues: {
            id: string;
            severity: string;
            cve_id: string | null;
            package: string | null;
            link: string | null;
            allowlisted: boolean;
        }[];
    }[];
}

export type FindingsData = CveFindingsData | CheckovFindingsData | ImageFindingsData;

/** The CLI itself could not be found on `strata.cliPath` (ENOENT), nor in a workspace `.venv`. */
export class StrataCLINotFoundError extends Error {
    constructor(cliPath: string) {
        super(`strata CLI not found (looked for '${cliPath}'). Set "strata.cliPath" if it is not on PATH or in a workspace .venv.`);
    }
}

/** The CLI ran but failed in a way that produced no parseable envelope. */
export class StrataCLIError extends Error {
    constructor(
        message: string,
        public readonly stderr: string,
    ) {
        super(message);
    }
}

const DEFAULT_CLI_PATH = 'strata';
const VENV_DIR_NAMES = ['.venv', 'venv'];

function getConfiguredCliPath(): string {
    return vscode.workspace.getConfiguration('strata').get<string>('cliPath', DEFAULT_CLI_PATH);
}

/** A `strata` binary inside a workspace-local virtualenv, if one exists. Only consulted when `strata.cliPath` is left at its default. */
function findVenvCliPath(cwd: string): string | undefined {
    const binDir = process.platform === 'win32' ? 'Scripts' : 'bin';
    const exeName = process.platform === 'win32' ? 'strata.exe' : 'strata';

    for (const venvName of VENV_DIR_NAMES) {
        const candidate = path.join(cwd, venvName, binDir, exeName);
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }
    return undefined;
}

/** Resolve the CLI path to invoke: an explicit `strata.cliPath` always wins, else fall back to a workspace `.venv`/`venv`, else plain `strata` on PATH. */
function resolveCliPath(cwd: string): string {
    const configured = getConfiguredCliPath();
    if (configured !== DEFAULT_CLI_PATH) {
        return configured;
    }
    return findVenvCliPath(cwd) ?? DEFAULT_CLI_PATH;
}

/** Node attaches `.stdout`/`.stderr`/`.code` to the rejection of a failed `execFile`. */
interface ExecFileError extends Error {
    code?: string | number;
    stdout?: string;
    stderr?: string;
}

export class StrataClient {
    constructor(private readonly cwd: string) { }

    /** Run one `strata` subcommand and parse its JSON envelope. */
    private async run<T>(args: string[]): Promise<Envelope<T>> {
        const cliPath = resolveCliPath(this.cwd);
        const [command, ...rest] = cliPath.split(' ');

        try {
            const { stdout } = await execFileAsync(command, [...rest, ...args, '--output', 'json'], {
                cwd: this.cwd,
                maxBuffer: 10 * 1024 * 1024,
            });
            return JSON.parse(stdout) as Envelope<T>;
        } catch (err) {
            const error = err as ExecFileError;
            if (error.code === 'ENOENT') {
                throw new StrataCLINotFoundError(cliPath);
            }
            // Exit codes 2/3 still print a complete JSON envelope on stdout —
            // only a crash before the command ran produces nothing parseable.
            if (error.stdout && error.stdout.trim().length > 0) {
                try {
                    return JSON.parse(error.stdout) as Envelope<T>;
                } catch {
                    // fall through to the generic failure below
                }
            }
            throw new StrataCLIError(error.message, error.stderr ?? '');
        }
    }

    /** `strata validate [path]` — the whole solution, never one file (v2 has no `-f`). */
    async validate(path?: string): Promise<Envelope<Record<string, never>>> {
        return this.run<Record<string, never>>(['validate', ...(path ? [path] : [])]);
    }

    /** `strata graph [path]` — every cross-document reference, resolved or not. */
    async graph(path?: string): Promise<Envelope<GraphData>> {
        return this.run<GraphData>(['graph', ...(path ? [path] : [])]);
    }

    /** `strata sln update` — rewrites `.strata/` and creates missing scaffold files; never overwrites a user-owned one. */
    async slnUpdate(): Promise<Envelope<Record<string, never>>> {
        return this.run<Record<string, never>>(['sln', 'update']);
    }

    /** `strata deployment list` — every deployment, partial bases marked. */
    async deploymentList(): Promise<Envelope<{ deployments: DeploymentRow[] }>> {
        return this.run(['deployment', 'list']);
    }

    /** `strata integration list [--deployment D]` — local readiness; offline. */
    async integrationList(deployment?: string): Promise<Envelope<{ integrations: IntegrationRow[] }>> {
        return this.run(['integration', 'list', ...(deployment ? ['--deployment', deployment] : [])]);
    }

    /** `strata integration check NAME [--deployment D]` — readiness plus the connection check. */
    async integrationCheck(name: string, deployment?: string): Promise<Envelope<unknown>> {
        return this.run(['integration', 'check', name, ...(deployment ? ['--deployment', deployment] : [])]);
    }

    /** `strata policy list D` — configured policies with their last recorded result; offline. */
    async policyList(deployment: string): Promise<Envelope<{ policies: PolicyRow[] }>> {
        return this.run(['policy', 'list', deployment]);
    }

    /** `strata policy check D [--policy N]` — evaluates now and records the results. */
    async policyCheck(deployment: string, policy?: string): Promise<Envelope<{ checked: string[] }>> {
        return this.run(['policy', 'check', deployment, ...(policy ? ['--policy', policy] : [])]);
    }

    /** `strata policy findings D --policy N` — itemised findings, read-only. */
    async policyFindings(deployment: string, policy: string): Promise<Envelope<FindingsData>> {
        return this.run(['policy', 'findings', deployment, '--policy', policy]);
    }

    /** `strata version` — a bare string, not an envelope. */
    async version(): Promise<string> {
        const cliPath = resolveCliPath(this.cwd);
        const [command, ...rest] = cliPath.split(' ');
        const { stdout } = await execFileAsync(command, [...rest, 'version'], { cwd: this.cwd });
        return stdout.trim();
    }
}
