/**
 * StrataClient — thin wrapper around the strata CLI.
 *
 * Every method spawns `strata <cmd> --output json` and parses the
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

/** Tag info returned by `strata repo status --output json` */
export interface TagInfo {
    name: string;
    commit: string;
    short_commit: string;
    created: string;  // ISO format datetime
    age_days: number;
    age_str: string;  // Human-readable age (e.g., "14 days ago")
}

/** Repository status from `strata repo status --output json` */
export interface RepositoryStatus {
    name: string;
    tags?: {
        latest_release?: TagInfo;
        latest_quality?: TagInfo;
    };
}

/** Data from `strata repo status --output json` */
export interface RepoStatusData {
    repos: RepositoryStatus[];
}

export interface IntegrationInfo {
    name: string;
    available: boolean;
    version: string | null;
    info: string | null;
}

export interface ToolsStatusRow {
    name: string;
    available: boolean;
    version: string | null;
    capabilities: string[];
    command: string | null;
    requirement: string | null; // "required" | "optional" | null
}

export interface WorkItemSummary {
    id: string;
    type: string;
    status: string;
    deployment: string;
    commit: string;
    created_by: string;
    created_at: string;
    expires_at?: string;
    resolved_by?: string;
    resolved_at?: string;
    resolution_note?: string;
    context: Record<string, unknown>;
}

export interface HealthData {
    status: 'HEALTHY' | 'DEGRADED' | 'BROKEN';
    issues: string[];
}

/** A deployment file registered in the solution (`sln add-deployment`/`sln scan-deployments`). */
export interface DeploymentRegistryEntry {
    name: string;
    path: string;
}

export interface WorkspaceStatus {
    health: HealthData;
    solution: SolutionData;
    readiness: ReadinessData;
    profiles: ProfileData;
    /** Solution-wide registered deployments — NOT profile-scoped, unlike `profiles.paths`. */
    deployments: DeploymentRegistryEntry[];
    repositories: RepositoryInfo[];
    integrations: Record<string, IntegrationInfo>;
}

// ── State service (ADR-0065) ────────────────────────────────────────────────

/** Matches HealthServeCommand's output_data (`strata serve health <url>`). */
export interface ServerHealthData {
    url: string;
    reachable: boolean;
    status_code?: number;
}

/** Matches list_tokens()'s per-row shape (`strata serve token list`) — never the hash/secret. */
export interface ServerTokenInfo {
    token_id: string;
    workspace: string;
    created_at: string;
    revoked_at: string | null;
}

/** Matches list_recent_events()'s lean projection (`strata serve tail`, ADR-0065 Step 2.6). */
export interface ServerTailEvent {
    execution_id: string;
    record_type: string;
    recorded_at: string | null;
    received_at: string | null;
    workspace: string | null;
    deployment: string | null;
    environment: string | null;
    action: string | null;
    outcome: string | null;
}

/** Matches CreateTokenServeCommand's output_data (`strata serve token create`). */
export interface CreatedServerToken {
    token_id: string;
    token: string;
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

/** Matches doctor_env_command._output_data check entry */
export interface EnvDoctorCheck {
    name: string;
    status: 'ok' | 'warn' | 'fail';
    value: string | null;
    fix_hint: string | null;
}

/** Matches doctor_env_command._output_data category entry */
export interface EnvDoctorCategory {
    name: string;
    checks: EnvDoctorCheck[];
}

/** Matches doctor_env_command._output_data */
export interface EnvDoctorData {
    summary: { passed: number; warnings: number; failed: number };
    categories: EnvDoctorCategory[];
}

// ---------------------------------------------------------------------------
// Audit types
// ---------------------------------------------------------------------------

/** Matches DeployLogStepModel — step within a stage */
export interface AuditStep {
    step: string;
    success: boolean;
    duration_seconds: number;
}

/** Matches DeployLogStageModel — per-stage result */
export interface AuditStage {
    name: string;
    provisioner: string | null;
    success: boolean;
    started_at: string;
    completed_at: string;
    duration_seconds: number;
    steps: AuditStep[];
}

/** Matches DeployLogPullRequestModel — optional PR enrichment */
export interface AuditPullRequest {
    number: number;
    title: string;
    url: string;
    author: string | null;
    merged_by: string | null;
}

/** Matches DeployLogModel — one deploy-log entry */
export interface AuditEntry {
    execution_id: string;
    timestamp: string;
    version: string;
    deployment: string;
    workspace: string | null;
    environment: string | null;
    file: string;
    success: boolean;
    duration_seconds: number;
    commit_sha: string | null;
    stages: AuditStage[];
    pull_request: AuditPullRequest | null;
}

/** Matches `data` field of `audit changes --output json` response */
export interface AuditChangesData {
    entries: AuditEntry[];
    count: number;
}

// ---------------------------------------------------------------------------
// Lock types
// ---------------------------------------------------------------------------

/** Matches `deploy lock status --output json` data */
export interface DeployLockStatus {
    locked: boolean;
    holder: string | null;
    acquired_at: string | null;
    ttl_seconds: number | null;
    backend: string | null;
}

// ---------------------------------------------------------------------------
// Drift types
// ---------------------------------------------------------------------------

/** A single drifted resource */
export interface DriftResource {
    address: string;
    change_type: 'change' | 'delete' | 'create';
    attributes: string[];
}

/** Per-stage drift result */
export interface DriftStageResult {
    stage: string;
    provisioner: string;
    drifted: boolean;
    resources: DriftResource[];
}

/** Matches `deploy drift run --output json` data */
export interface DriftData {
    drifted: boolean;
    stages: DriftStageResult[];
    acknowledged_count: number;
}

// ---------------------------------------------------------------------------
// Values types
// ---------------------------------------------------------------------------

/** A single resolved value entry */
export interface ValueEntry {
    key: string;
    value: string | null;
    source: string;
    secret: boolean;
    resolved: boolean;
}

/** Matches `values list --output json` data */
export interface ValuesData {
    deployment: string;
    file: string;
    entries: ValueEntry[];
    count: number;
}

// ---------------------------------------------------------------------------
// Cache types (ADR-0026)
// ---------------------------------------------------------------------------

/** One row from `cache status --output json` (no -f: full listing) */
export interface CacheStatusEntry {
    name: string;
    kind?: string;
    status?: string;
    written_at?: string;
    size_bytes?: number;
    strata_version?: string;
}

/** Matches `cache status --output json` data */
export interface CacheStatusData {
    entries: CacheStatusEntry[];
}

// ---------------------------------------------------------------------------
// SBOM types
// ---------------------------------------------------------------------------

/** A dependency in the SBOM */
export interface SbomComponent {
    name: string;
    version: string | null;
    type: string;
    purl: string | null;
}

/** Matches `build sbom --output json` data */
export interface SbomData {
    deployment: string;
    component_count: number;
    output_file: string | null;
    components: SbomComponent[];
    vulnerabilities_found: boolean;
    critical_count: number;
    high_count: number;
}

/** Matches status_env_command._output_data stage entry (multi-deployment mode) */
export interface EnvStageStatus {
    name: string;
    provisioner: string;
    cached: boolean;
    cache: { refreshed_at: string | null; output_count: number } | null;
}

/** Matches status_env_command._output_data deployment entry (multi-deployment mode) */
export interface EnvDeploymentStatus {
    file: string;
    name: string;
    stages: EnvStageStatus[];
    stage_count: number;
    cached_count: number;
}

/** Matches status_env_command._output_data (multi-deployment mode) */
export interface EnvStatusData {
    scan_path: string;
    deployments: EnvDeploymentStatus[];
}

// ---------------------------------------------------------------------------
// Env Output types
// ---------------------------------------------------------------------------

/** Per-stage output data from `env output --output json` */
export interface EnvOutputStage {
    provisioner: string;
    outputs: Record<string, string | null>; // null = sensitive/masked value
    ok: boolean;
    error: string | null;
}

/** Matches `env output --output json` data */
export interface EnvOutputData {
    file: string;
    stages: Record<string, EnvOutputStage>;
}

// ---------------------------------------------------------------------------
// Deploy Health types
// ---------------------------------------------------------------------------

/** A single health probe result */
export interface DeployHealthCheck {
    name: string;
    type: 'http' | 'tcp';
    passed: boolean;
    url?: string;
    status_code?: number;
    expected?: number;
    host?: string;
    port?: number;
    error?: string | null;
}

/** Per-stage health result */
export interface DeployHealthStage {
    passed: boolean;
    checks: DeployHealthCheck[];
}

/** Matches `deploy health --output json` data */
export interface DeployHealthData {
    mode: string;
    stages: Record<string, DeployHealthStage>;
    summary: { total_stages: number; passed: number; failed: number } | 'no_checks_defined';
}

// ---------------------------------------------------------------------------
// Build Plan types
// ---------------------------------------------------------------------------

/** A single artifact file in the build plan diff */
export interface BuildPlanArtifact {
    status: 'new' | 'changed' | 'unchanged';
    path: string;
    lines_changed: number;
}

/** Per-stage Terraform plan result */
export interface BuildPlanTfStage {
    stage: string;
    ok: boolean;
    messages: string[];
    error: string | null;
}

/** A resolved value entry shown in the build plan */
export interface BuildPlanValue {
    type: 'variable' | 'secret' | 'feature';
    key: string;
    store: string;
    status: 'ok' | 'required' | 'seeded' | 'generated';
    detail: string | null;
}

/** Matches `build plan --output json` data */
export interface BuildPlanData {
    file: string;
    deployment: string;
    artifact_diff: BuildPlanArtifact[];
    terraform_plan: BuildPlanTfStage[];
    values: BuildPlanValue[];
    providers: Array<{ name: string; file: string; source: string }>;
}

// ---------------------------------------------------------------------------
// Policy types
// ---------------------------------------------------------------------------

/** A single policy check result */
export interface PolicyResult {
    policy: string;
    type: string;
    phase: string;
    enforcement: 'deny' | 'warn';
    passed: boolean;
    violations: string[];
}

/** Matches `policy check --output json` data */
export interface PolicyCheckData {
    deployment: string;
    phases: string[];
    policies_checked: number;
    passed: number;
    failed: number;
    denied: number;
    notes: Array<{ phase: string; message: string }>;
    results: PolicyResult[];
}

/** A policy definition entry */
export interface PolicyEntry {
    name: string;
    type: string;
    phase: string;
    enforcement: 'deny' | 'warn';
    enabled: boolean;
    description: string;
}

/** Matches `policy list --output json` data */
export interface PolicyListData {
    deployment: string;
    policy_count: number;
    enabled_count: number;
    policies: PolicyEntry[];
}

// ---------------------------------------------------------------------------
// Deploy History types
// ---------------------------------------------------------------------------

/** A single entry in the deploy history */
export interface DeployHistoryEntry {
    when: string;
    operation: string;
    operation_key: string;
    execution_id: string;
    success: boolean;
    file: string;
    stage: string;
}

/** Matches `deploy history --output json` data */
export interface DeployHistoryData {
    mode: string;
    total: number;
    filter: string;
    entries: DeployHistoryEntry[];
}

// ---------------------------------------------------------------------------
// Cost History types
// ---------------------------------------------------------------------------

/** A single cost snapshot from `strata cost history --output json` */
export interface CostSnapshot {
    recorded_at: string;
    version?: string;
    total_monthly: number;
    currency: string;
    provisioners: Record<string, { total_monthly: number }>;
    delta_from_previous: number | null;
}

/** Matches `cost history --output json` data */
export interface CostHistoryData {
    deployment: string;
    snapshots: CostSnapshot[];
    count: number;
}

// ---------------------------------------------------------------------------
// Ref types
// ---------------------------------------------------------------------------

/** A single profile reference entry */
export interface RefEntry {
    name: string;
    path: string;
    type: string;
    created: string;
}

/** Matches `ref <type> list --output json` data */
export interface RefListData {
    profile: string;
    paths: Record<string, RefEntry[]>;
}

// ---------------------------------------------------------------------------
// Promotion types
// ---------------------------------------------------------------------------

/** A single in-flight promotion entry from `promote status` */
export interface PromotionStatusEntry {
    target: string;
    version: string;
    previous_version: string | null;
    ring: string;
    strategy: string;
    progression: string;
    branch: string | null;
    status: 'in-progress' | 'completed' | 'rolled_back';
    event_count: number;
}

/** A ring entry in the version matrix */
export interface PromotionMatrixRing {
    ring: string;
    environments: string[];
    require: string | null;
    versions: Record<string, string>;
}

/** Matches `promote matrix --output json` data */
export interface PromotionMatrixData {
    rings: PromotionMatrixRing[];
}

/** A single completed promotion record */
export interface PromotionHistoryEntry {
    name: string;
    target: string;
    from_version: string | null;
    to_version: string | null;
    ring: string | null;
    outcome: string | null;
    initiated_by: string | null;
    started_at: string | null;
    completed_at: string | null;
    branch: string | null;
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

    /** Run `strata sln status --output json` and return workspace state. */
    async getStatus(): Promise<WorkspaceStatus> {
        const resp = await this._run<WorkspaceStatus>([
            'sln', 'status', '--output', 'json',
        ]);
        return resp.data;
    }

    /** Run `strata repo status --output json` and return repository tag information. */
    async getRepoStatus(): Promise<RepoStatusData> {
        const resp = await this._run<RepoStatusData>([
            'repo', 'status', '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Validation ────────────────────────────────────────────────────────────

    /** Run `strata validate -f <filePath> --output json`. */
    async validateFile(filePath: string): Promise<ValidationResult> {
        // Exit code 3 = validation failure — _run() handles it, returns envelope
        const resp = await this._run<ValidationResult>([
            'validate', '-f', filePath, '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Schema ────────────────────────────────────────────────────────────────

    /** Run `strata schema wire` to export schemas and patch .vscode/settings.json. */
    async wireSchemas(): Promise<void> {
        await this._run<Record<string, unknown>>([
            'schema', 'wire', '--output', 'json',
        ]);
    }

    // ── Profiles ──────────────────────────────────────────────────────────────

    /** Run `strata profile activate <name> --output json`. */
    async activateProfile(name: string): Promise<void> {
        await this._run<Record<string, unknown>>([
            'profile', 'activate', name, '--output', 'json',
        ]);
    }

    // ── Env ───────────────────────────────────────────────────────────────────

    /**
     * Run `strata sln doctor --output json` and return the doctor result.
     * Pass a `filePath` to limit tool checks to those referenced by that deployment.
     * Exit code 3 (some checks failed) is handled — the envelope is still returned.
     */
    async runEnvDoctor(filePath?: string): Promise<EnvDoctorData> {
        const args: string[] = ['sln', 'doctor', '--output', 'json'];
        if (filePath) {
            args.push('-f', filePath);
        }
        try {
            const resp = await this._run<EnvDoctorData>(args);
            return resp.data;
        } catch (err: unknown) {
            // Exit 3 surfaces as StrataCLIError but stdout is parseable
            const cliErr = err as { response?: CliResponse<EnvDoctorData> };
            if (cliErr?.response?.data) {
                return cliErr.response.data;
            }
            throw err;
        }
    }

    /**
     * Run `strata sln status --output json` and return workspace state.
     */
    async getEnvStatus(): Promise<EnvStatusData> {
        const resp = await this._run<EnvStatusData>([
            'sln', 'status', '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Repositories ─────────────────────────────────────────────────────────

    /** Run `strata repo sync [--name <name>] --output json`. */
    async syncRepo(name?: string): Promise<void> {
        const args: string[] = ['repo', 'sync', '--output', 'json'];
        if (name) args.push('--name', name);
        await this._run<Record<string, unknown>>(args);
    }

    /** Run `strata repo add --name <name> --path <repoPath> --output json`. */
    async addRepo(name: string, repoPath: string): Promise<void> {
        await this._run<Record<string, unknown>>([
            'repo', 'add', '--name', name, '--path', repoPath, '--output', 'json',
        ]);
    }

    /** Run `strata repo remove --name <name> --output json`. */
    async removeRepo(name: string): Promise<void> {
        await this._run<Record<string, unknown>>([
            'repo', 'remove', '--name', name, '--output', 'json',
        ]);
    }

    // ── Lock ──────────────────────────────────────────────────────────────────

    /** Run `strata deploy lock status -f <filePath> --output json`. */
    async getLockStatus(filePath: string): Promise<DeployLockStatus> {
        try {
            const resp = await this._run<DeployLockStatus>([
                'deploy', 'lock', 'status', '-f', filePath, '--output', 'json',
            ]);
            return resp.data;
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<DeployLockStatus> };
            if (cliErr?.response?.data) return cliErr.response.data;
            throw err;
        }
    }

    /** Run `strata deploy lock release -f <filePath> --force-lock --output json`. */
    async releaseLock(filePath: string): Promise<void> {
        await this._run<Record<string, unknown>>([
            'deploy', 'lock', 'release', '-f', filePath, '--force-lock', '--output', 'json',
        ]);
    }

    // ── Drift ─────────────────────────────────────────────────────────────────

    /**
     * Run `strata deploy drift run -f <filePath> --output json`.
     * Returns drift detection results.  May be slow (runs terraform plan).
     */
    async runDrift(filePath: string): Promise<DriftData> {
        try {
            const resp = await this._run<DriftData>([
                'deploy', 'drift', 'run', '-f', filePath, '--output', 'json',
            ]);
            return resp.data;
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<DriftData> };
            if (cliErr?.response?.data) return cliErr.response.data;
            throw err;
        }
    }

    // ── Values ────────────────────────────────────────────────────────────────

    /** Run `strata values list -f <filePath> --output json`. */
    async getValues(filePath: string): Promise<ValuesData> {
        const resp = await this._run<ValuesData>([
            'values', 'list', '-f', filePath, '--output', 'json',
        ]);
        return resp.data;
    }

    // ── SBOM ──────────────────────────────────────────────────────────────────

    /**
     * Run `strata build sbom -f <filePath> --output json`.
     * Optionally pass `report: 'inventory'` for a human-readable inventory.
     */
    async generateSbom(filePath: string): Promise<SbomData> {
        const resp = await this._run<SbomData>([
            'build', 'sbom', '-f', filePath, '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Audit ─────────────────────────────────────────────────────────────────

    /**
     * Run `strata audit changes --last N [--stage S] --output json` and return
     * deploy-log entries.  Fast — reads local JSON files only.
     */
    async getAuditChanges(last: number = 20, stage?: string): Promise<AuditEntry[]> {
        const args: string[] = ['audit', 'changes', '--last', String(last), '--output', 'json'];
        if (stage) args.push('--stage', stage);
        const resp = await this._run<AuditChangesData>(args);
        return resp.data.entries;
    }

    // ── Deploy Output ─────────────────────────────────────────────────────────

    /** Run `strata deploy output -f <filePath> --output json`. */
    async getEnvOutput(filePath: string): Promise<EnvOutputData> {
        const resp = await this._run<EnvOutputData>([
            'deploy', 'output', '-f', filePath, '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Deploy Health ─────────────────────────────────────────────────────────

    /** Run `strata deploy health -f <filePath> --output json`. */
    async getDeployHealth(filePath: string): Promise<DeployHealthData> {
        try {
            const resp = await this._run<DeployHealthData>([
                'deploy', 'health', '-f', filePath, '--output', 'json',
            ]);
            return resp.data;
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<DeployHealthData> };
            if (cliErr?.response?.data) return cliErr.response.data;
            throw err;
        }
    }

    // ── Build Plan ────────────────────────────────────────────────────────────

    /**
     * Run `strata build plan -f <filePath> --output json`.
     * May be slow — runs terraform plan behind the scenes.
     */
    async getBuildPlan(filePath: string): Promise<BuildPlanData> {
        const resp = await this._run<BuildPlanData>([
            'build', 'plan', '-f', filePath, '--output', 'json',
        ], 120_000);
        return resp.data;
    }

    // ── Policy ────────────────────────────────────────────────────────────────

    /** Run `strata policy check -f <filePath> --output json`. */
    async checkPolicy(filePath: string): Promise<PolicyCheckData> {
        try {
            const resp = await this._run<PolicyCheckData>([
                'policy', 'check', '-f', filePath, '--output', 'json',
            ]);
            return resp.data;
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<PolicyCheckData> };
            if (cliErr?.response?.data) return cliErr.response.data;
            throw err;
        }
    }

    /** Run `strata policy list -f <filePath> --output json`. */
    async listPolicies(filePath: string): Promise<PolicyListData> {
        const resp = await this._run<PolicyListData>([
            'policy', 'list', '-f', filePath, '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Deploy History ────────────────────────────────────────────────────────

    /** Run `strata deploy history -f <filePath> --last <n> --output json`. */
    async getDeployHistory(filePath: string, last = 10): Promise<DeployHistoryData> {
        const resp = await this._run<DeployHistoryData>([
            'deploy', 'history', '-f', filePath, '--last', String(last), '--output', 'json',
        ]);
        return resp.data;
    }

    // ── Cost History ──────────────────────────────────────────────────────────

    /** Run `strata cost history -f <filePath> --last <n> --output json`. */
    async getCostHistory(filePath: string, last = 10): Promise<CostHistoryData> {
        try {
            const resp = await this._run<CostHistoryData>([
                'cost', 'history', '-f', filePath, '--last', String(last), '--output', 'json',
            ]);
            return resp.data;
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<CostHistoryData> };
            if (cliErr?.response?.data) return cliErr.response.data;
            // Non-fatal — return empty history
            return { deployment: filePath, snapshots: [], count: 0 };
        }
    }

    // ── Cache (ADR-0026) ──────────────────────────────────────────────────────

    /**
     * Run `strata cache warm --all` (or `-f <filePath>` for a single deployment).
     * Used by the background cache warmer — failures are non-fatal by design
     * (CacheService already treats a failed warm as "stays cold", never an error
     * surfaced to the user).
     *
     * Always passes `--no-sync-remotes`: this call can fire silently in the
     * background on every debounced save, and a full-fidelity warm would perform
     * a real `git checkout --detach <ref>` on gitops remotes — a surprising side
     * effect for an ambient feature the operator did not explicitly invoke. Use
     * the CLI directly (`strata cache warm --all`) for a full-fidelity warm that
     * also syncs remotes.
     */
    async warmCache(filePath?: string): Promise<void> {
        const args = ['cache', 'warm', '--no-sync-remotes', '--output', 'json'];
        if (filePath) {
            args.push('-f', filePath);
        } else {
            args.push('--all');
        }
        await this._run<Record<string, unknown>>(args, 120_000);
    }

    /** Run `strata cache status --output json` and return cache entry rows. */
    async getCacheStatus(): Promise<CacheStatusData> {
        const resp = await this._run<CacheStatusData>(['cache', 'status', '--output', 'json']);
        return resp.data;
    }

    // ── Refs ──────────────────────────────────────────────────────────────────

    /** Run `strata ref <type> list --profile <profile> --output json`. */
    async listRefs(
        profile: string,
        type: 'env' | 'config' | 'data' | 'secret',
    ): Promise<RefEntry[]> {
        const resp = await this._run<RefListData>([
            'ref', type, 'list', '--profile', profile, '--output', 'json',
        ]);
        return resp.data.paths[`${type}file`] ?? [];
    }

    /** Run `strata ref <type> add --profile <profile> --name <name> --path <path> --output json`. */
    async addRef(
        profile: string,
        type: 'env' | 'config' | 'data' | 'secret',
        name: string,
        path: string,
    ): Promise<void> {
        await this._run<Record<string, unknown>>([
            'ref', type, 'add', '--profile', profile, '--name', name, '--path', path, '--output', 'json',
        ]);
    }

    /** Run `strata ref <type> remove --profile <profile> --name <name> --output json`. */
    async removeRef(
        profile: string,
        type: 'env' | 'config' | 'data' | 'secret',
        name: string,
    ): Promise<void> {
        await this._run<Record<string, unknown>>([
            'ref', type, 'remove', '--profile', profile, '--name', name, '--output', 'json',
        ]);
    }

    // ── Promotions ─────────────────────────────────────────────────────────

    /** Run `strata promote status --output json`. */
    async getPromotionStatus(): Promise<PromotionStatusEntry[]> {
        try {
            const resp = await this._run<{ promotions: PromotionStatusEntry[] }>([
                'promote', 'status', '--output', 'json',
            ]);
            return resp.data.promotions ?? (resp.data as unknown as PromotionStatusEntry[]) ?? [];
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<{ promotions?: PromotionStatusEntry[] }> };
            if (cliErr?.response?.data?.promotions) return cliErr.response.data.promotions;
            return [];
        }
    }

    /** Run `strata promote matrix --output json`. */
    async getPromotionMatrix(targetName?: string): Promise<PromotionMatrixData> {
        const args: string[] = ['promote', 'matrix', '--output', 'json'];
        if (targetName) args.push('--remote', targetName);
        try {
            const resp = await this._run<{ matrix: PromotionMatrixData }>([
                'promote', 'matrix', '--output', 'json',
            ]);
            return resp.data.matrix ?? (resp.data as unknown as PromotionMatrixData) ?? { rings: [] };
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<{ matrix?: PromotionMatrixData }> };
            if (cliErr?.response?.data?.matrix) return cliErr.response.data.matrix;
            return { rings: [] };
        }
    }

    /** Run `strata promote history --output json`. */
    async getPromotionHistory(ring?: string, last = 10): Promise<PromotionHistoryEntry[]> {
        const args: string[] = ['promote', 'history', '--last', String(last), '--output', 'json'];
        if (ring) args.push('--ring', ring);
        try {
            const resp = await this._run<{ records: PromotionHistoryEntry[] }>(args);
            return resp.data.records ?? (resp.data as unknown as PromotionHistoryEntry[]) ?? [];
        } catch (err: unknown) {
            const cliErr = err as { response?: CliResponse<{ records?: PromotionHistoryEntry[] }> };
            if (cliErr?.response?.data?.records) return cliErr.response.data.records;
            return [];
        }
    }

    /** Run `strata promote start --ring <ring> --remote <target> --output json` in terminal. */
    runPromoteStart(ring: string, targetName: string): void {
        this.runInTerminal(
            ['promote', 'start', '--ring', ring, '--remote', targetName],
            `strata promote → ${ring}`,
        );
    }

    /** Run `strata promote rollback --ring <ring> --remote <target> --output json` in terminal. */
    runPromoteRollback(ring: string, targetName: string): void {
        this.runInTerminal(
            ['promote', 'rollback', '--ring', ring, '--remote', targetName],
            `strata rollback → ${ring}`,
        );
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

    // ── Tools ─────────────────────────────────────────────────────────────────

    /** Run `strata tools status --output json` and return ALL known integration rows. */
    async getToolsStatus(deploymentFile?: string): Promise<ToolsStatusRow[]> {
        const args = ['tools', 'status', '--output', 'json'];
        if (deploymentFile) {
            args.push('-f', deploymentFile);
        }
        const resp = await this._run<{ integrations: ToolsStatusRow[] }>(args);
        return resp.data?.integrations ?? [];
    }

    // ── State service (ADR-0065) ─────────────────────────────────────────────

    /** Run `strata serve health <url> --output json`. */
    async getServerHealth(url: string): Promise<ServerHealthData> {
        const resp = await this._run<ServerHealthData>(['serve', 'health', url, '--output', 'json']);
        return resp.data;
    }

    /** Run `strata serve tail <url> --token <token> --limit <limit> [--workspace <workspace>] --output json`. */
    async getServerTail(url: string, token: string, limit = 100, workspace?: string): Promise<ServerTailEvent[]> {
        const args = ['serve', 'tail', url, '--token', token, '--limit', String(limit), '--output', 'json'];
        if (workspace) {
            args.push('--workspace', workspace);
        }
        const resp = await this._run<{ events: ServerTailEvent[] }>(args);
        return resp.data?.events ?? [];
    }

    /** Run `strata serve token create --url <url> --admin-token <token> --workspace <workspace> --output json`. */
    async createIngestToken(url: string, adminToken: string, workspace: string): Promise<CreatedServerToken> {
        const resp = await this._run<CreatedServerToken>([
            'serve', 'token', 'create',
            '--url', url, '--admin-token', adminToken, '--workspace', workspace,
            '--output', 'json',
        ]);
        return resp.data;
    }

    /** Run `strata serve token list --url <url> --admin-token <token> --output json`. */
    async listIngestTokens(url: string, adminToken: string): Promise<ServerTokenInfo[]> {
        const resp = await this._run<{ tokens: ServerTokenInfo[] }>([
            'serve', 'token', 'list', '--url', url, '--admin-token', adminToken, '--output', 'json',
        ]);
        return resp.data?.tokens ?? [];
    }

    /** Run `strata serve token revoke <tokenId> --url <url> --admin-token <token> --output json`. */
    async revokeIngestToken(url: string, adminToken: string, tokenId: string): Promise<void> {
        await this._run([
            'serve', 'token', 'revoke', tokenId, '--url', url, '--admin-token', adminToken, '--output', 'json',
        ]);
    }

    // ── Help ──────────────────────────────────────────────────────────────────

    /**
     * Run `strata help --topic <name>` and return the raw Markdown string.
     * Returns undefined when the topic is not found (exit 1, empty stdout).
     */
    async getHelpTopic(name: string): Promise<string | undefined> {
        try {
            const parts = this.cliPath.trim().split(/\s+/);
            const executable = parts[0];
            const prefixArgs = parts.slice(1);
            const { stdout } = await execFileAsync(
                executable,
                [...prefixArgs, 'help', '--topic', name],
                { cwd: this.workPath, timeout: 10_000, maxBuffer: 512 * 1024 },
            );
            return stdout.trim() || undefined;
        } catch {
            return undefined;
        }
    }

    /**
     * Run `strata help --list` and return the list of available topic names.
     */
    async listHelpTopics(): Promise<string[]> {
        try {
            const parts = this.cliPath.trim().split(/\s+/);
            const executable = parts[0];
            const prefixArgs = parts.slice(1);
            const { stdout } = await execFileAsync(
                executable,
                [...prefixArgs, 'help', '--list'],
                { cwd: this.workPath, timeout: 10_000, maxBuffer: 64 * 1024 },
            );
            // Output is plain text: "name    description\n..."
            return stdout.trim().split('\n')
                .map(l => l.split(/\s{2,}/)[0].trim())
                .filter(Boolean);
        } catch {
            return [];
        }
    }

    /**
     * List work items from the CLI.
     * Calls: strata workitem list [--status STATUS] [--type TYPE] --output json
     */
    async listWorkItems(status?: string, type?: string): Promise<WorkItemSummary[]> {
        const args = ['workitem', 'list', '--output', 'json'];
        if (status) args.push('--status', status);
        if (type) args.push('--type', type);
        try {
            const resp = await this._run<{ items?: WorkItemSummary[] } | WorkItemSummary[]>(args);
            // The CLI may return an array directly or wrapped in data.items
            const data = resp.data;
            return Array.isArray(data) ? data : (data as any).items ?? [];
        } catch {
            return [];
        }
    }

    /**
     * Approve a pending work item.
     * Calls: strata workitem approve <id> [--note NOTE] --output json
     */
    async approveWorkItem(id: string, note?: string): Promise<void> {
        const args = ['workitem', 'approve', id, '--output', 'json'];
        if (note) args.push('--note', note);
        await this._run(args);
    }

    /**
     * Reject a pending work item.
     * Calls: strata workitem reject <id> [--reason REASON] --output json
     */
    async rejectWorkItem(id: string, reason?: string): Promise<void> {
        const args = ['workitem', 'reject', id, '--output', 'json'];
        if (reason) args.push('--reason', reason);
        await this._run(args);
    }

    // ── Internal ─────────────────────────────────────────────────────────────

    /**
     * Spawn the CLI, parse JSON from stdout, and return the envelope.
     *
     * - Always appends `--work-path <workPath>` so the CLI targets the correct workspace.
     * - Exit code 3 (validation failure) is treated as a successful call — stdout
     *   contains the valid envelope with validation_passed:false in data.
     * - Exit code 'ENOENT' means the CLI binary is not installed.
     */
    private async _run<T>(args: string[], timeoutMs = 30_000): Promise<CliResponse<T>> {
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
                timeout: timeoutMs,
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

