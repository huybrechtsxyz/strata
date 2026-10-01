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

/** The CLI itself could not be found on `strata.cliPath` (ENOENT). */
export class StrataCLINotFoundError extends Error {
    constructor(cliPath: string) {
        super(`strata CLI not found (looked for '${cliPath}'). Set "strata.cliPath" if it is not on PATH.`);
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

function getCliPath(): string {
    return vscode.workspace.getConfiguration('strata').get<string>('cliPath', 'strata');
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
        const cliPath = getCliPath();
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

    /** `strata version` — a bare string, not an envelope. */
    async version(): Promise<string> {
        const cliPath = getCliPath();
        const [command, ...rest] = cliPath.split(' ');
        const { stdout } = await execFileAsync(command, [...rest, 'version'], { cwd: this.cwd });
        return stdout.trim();
    }
}
