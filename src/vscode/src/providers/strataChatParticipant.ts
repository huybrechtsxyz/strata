/**
 * StrataChatParticipant — registers a `@strata` chat participant in Copilot Chat.
 *
 * Users can type `@strata` followed by a question or command name:
 *   @strata what's the workspace status?
 *   @strata /validate config/main.yaml
 *   @strata /guide
 *
 * The participant feeds workspace context (from the last `getStatus()` call)
 * into the response so the LLM can give accurate, context-aware answers about
 * the user's strata workspace.
 *
 * Slash commands:
 *   /status   — show workspace readiness, profile, and health
 *   /validate — validate the current file or a named file
 *   /guide    — show the 8-phase readiness checklist
 *   /build    — run or dry-run a build via terminal
 *   /deploy   — run or dry-run a deploy via terminal
 *   /stage    — deploy a specific stage: /stage deploy/main.yaml networking
 *   /values   — inspect resolved deployment values
 *   /drift    — run drift detection on a deployment
 *   /repos    — show repository status with release/quality-gate tags
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus, ValidationResult, DriftData } from '../strataClient';
import { AiPromptBuilder } from './aiPromptBuilder';

const PARTICIPANT_ID = 'strata.chat';

/** Slash-command metadata registered in package.json `chatParticipants[].commands`. */
type SlashCommand = 'status' | 'validate' | 'guide' | 'build' | 'deploy' | 'stage' | 'values' | 'drift' | 'repos' | 'promote' | 'versions' | 'review' | 'diagnose' | 'sbom' | 'approvals';

export class StrataChatParticipant implements vscode.Disposable {
    private _participant: vscode.ChatParticipant | undefined;
    private _client: StrataClient | undefined;
    private _lastStatus: WorkspaceStatus | undefined;

    // ── Public API ─────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void {
        this._client = client;
    }

    update(status: WorkspaceStatus): void {
        this._lastStatus = status;
    }

    register(context: vscode.ExtensionContext): void {
        this._participant = vscode.chat.createChatParticipant(
            PARTICIPANT_ID,
            (request, chatContext, response, token) =>
                this._handleRequest(request, chatContext, response, token),
        );

        this._participant.iconPath = new vscode.ThemeIcon('cloud');

        this._participant.followupProvider = {
            provideFollowups: (result, _ctx, _token) => this._provideFollowups(result),
        };

        context.subscriptions.push(this._participant);
    }

    dispose(): void {
        this._participant?.dispose();
    }

    // ── Request handler ────────────────────────────────────────────────────────

    private async _handleRequest(
        request: vscode.ChatRequest,
        _context: vscode.ChatContext,
        response: vscode.ChatResponseStream,
        token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        const command = request.command as SlashCommand | undefined;

        try {
            switch (command) {
                case 'status':
                    return await this._handleStatus(response, token);
                case 'validate':
                    return await this._handleValidate(request, response, token);
                case 'guide':
                    return await this._handleGuide(response, token);
                case 'build':
                    return await this._handleBuild(request, response);
                case 'deploy':
                    return await this._handleDeploy(request, response);
                case 'stage':
                    return await this._handleStage(request, response);
                case 'values':
                    return await this._handleValues(request, response, token);
                case 'drift':
                    return await this._handleDrift(request, response);
                case 'repos':
                    return await this._handleRepos(response, token);
                case 'promote':
                    return await this._handlePromote(request, response, token);
                case 'versions':
                    return await this._handleVersions(request, response, token);
                case 'review':
                    return await this._handleReview(request, response, token);
                case 'diagnose':
                    return await this._handleDiagnose(request, response, token);
                case 'sbom':
                    return await this._handleSbom(request, response, token);
                case 'approvals':
                    return await this._handleApprovals(response, token);
                default:
                    return await this._handleFreeform(request, response, token);
            }
        } catch (err) {
            response.markdown(`**Error:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }
    }

    // ── /status ────────────────────────────────────────────────────────────────

    private async _handleStatus(
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        const status = await this._getStatus();

        response.markdown(`## Workspace Status\n\n`);
        response.markdown(`| Property | Value |\n|---|---|\n`);
        response.markdown(`| **Health** | ${this._healthIcon(status.health.status)} ${status.health.status} |\n`);
        response.markdown(`| **Profile** | ${status.profiles.active ?? '*(none)*'} |\n`);
        response.markdown(`| **Readiness** | Phase ${status.readiness.phases_complete}/${status.readiness.phases_total} |\n`);
        response.markdown(`| **Repositories** | ${status.repositories?.length ?? 0} |\n`);

        if (status.health.issues.length > 0) {
            response.markdown(`\n### Issues\n\n`);
            for (const issue of status.health.issues) {
                response.markdown(`- ⚠️ ${issue}\n`);
            }
        }

        if (status.readiness.next_step) {
            const ns = status.readiness.next_step;
            response.markdown(`\n### Next Step\n\n`);
            response.markdown(`**${ns.label}**\n\n`);
            if (ns.hint) {
                response.markdown(`\`\`\`sh\n${ns.hint}\n\`\`\`\n`);
            }
        }

        return { metadata: { command: 'status' } };
    }

    // ── /validate ──────────────────────────────────────────────────────────────

    private async _handleValidate(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        // Determine target file: from prompt text or active editor
        let filePath = request.prompt.trim();
        if (!filePath) {
            filePath = vscode.window.activeTextEditor?.document.uri.fsPath ?? '';
        }
        if (!filePath) {
            response.markdown('No file specified and no active editor. Please provide a file path or open a strata YAML file.');
            return { errorDetails: { message: 'No file to validate' } };
        }

        response.markdown(`Validating **${vscode.workspace.asRelativePath(filePath)}**…\n\n`);

        let result: ValidationResult;
        try {
            result = await this._client.validateFile(filePath);
        } catch (err) {
            response.markdown(`**Validation failed:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }

        if (result.validation_passed) {
            response.markdown('✅ **Validation passed** — no errors found.');
        } else {
            response.markdown(`❌ **${result.errors.length} error(s) found:**\n\n`);
            for (const err of result.errors) {
                const field = err.field ? ` (\`${err.field}\`)` : '';
                response.markdown(`- ${err.message}${field}\n`);
            }
        }

        return { metadata: { command: 'validate' } };
    }

    // ── /guide ─────────────────────────────────────────────────────────────────

    private async _handleGuide(
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        const status = await this._getStatus();

        response.markdown(`## Workspace Readiness — ${status.readiness.phases_complete}/${status.readiness.phases_total} phases complete\n\n`);

        for (const item of status.readiness.checklist) {
            const icon = item.status === 'ok' ? '✅' : item.status === 'pending' ? '⏳' : '❌';
            const detail = item.detail ? ` — ${item.detail}` : '';
            response.markdown(`${icon} **Phase ${item.phase}:** ${item.label}${detail}\n\n`);
        }

        if (status.readiness.next_step) {
            const ns = status.readiness.next_step;
            response.markdown(`### What to do next\n\n`);
            response.markdown(`**${ns.label}**\n\n`);
            if (ns.hint) {
                response.markdown(`\`\`\`sh\n${ns.hint}\n\`\`\`\n`);
            }
        }

        return { metadata: { command: 'guide' } };
    }

    // ── /build ─────────────────────────────────────────────────────────────────

    private async _handleBuild(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        // Resolve target file: from prompt or active editor
        let filePath = request.prompt.trim();
        if (!filePath) filePath = vscode.window.activeTextEditor?.document.uri.fsPath ?? '';

        if (!filePath) {
            response.markdown('No deployment file specified. Open a deployment YAML or type: `/build path/to/deploy.yaml`');
            return { errorDetails: { message: 'No file' } };
        }

        const rel = vscode.workspace.asRelativePath(filePath);
        response.markdown(`**Build target:** \`${rel}\`\n\n`);
        response.button({ title: '▶ Dry Run', command: 'strata.buildDryRun', arguments: [filePath] });
        response.button({ title: '⚡ Full Build', command: 'strata.buildRun', arguments: [filePath] });
        response.markdown(`\nDry-run shows what would change without applying. Full build generates Terraform artifacts.\n`);

        return { metadata: { command: 'build', filePath } };
    }

    // ── /deploy ────────────────────────────────────────────────────────────────

    private async _handleDeploy(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        let filePath = request.prompt.trim();
        if (!filePath) filePath = vscode.window.activeTextEditor?.document.uri.fsPath ?? '';

        if (!filePath) {
            response.markdown('No deployment file specified. Open a deployment YAML or type: `/deploy path/to/deploy.yaml`');
            return { errorDetails: { message: 'No file' } };
        }

        const rel = vscode.workspace.asRelativePath(filePath);
        response.markdown(`**Deploy target:** \`${rel}\`\n\n`);
        response.button({ title: '▶ Dry Run', command: 'strata.deployDryRun', arguments: [filePath] });
        response.button({ title: '🚀 Full Deploy', command: 'strata.deployRun', arguments: [filePath] });
        response.markdown(`\nDry-run shows the plan without applying. Full deploy requires confirmation.\n`);

        return { metadata: { command: 'deploy', filePath } };
    }

    // ── /stage ─────────────────────────────────────────────────────────────────

    private async _handleStage(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        // Syntax: /stage [file] [stage-name]
        // e.g. /stage deploy/main.yaml infrastructure
        //      /stage infrastructure   (uses active editor for file)
        const parts = request.prompt.trim().split(/\s+/).filter(Boolean);
        let filePath: string | undefined;
        let stageName: string | undefined;

        if (parts.length >= 2) {
            [filePath, stageName] = parts;
        } else if (parts.length === 1) {
            stageName = parts[0];
            filePath = vscode.window.activeTextEditor?.document.uri.fsPath;
        } else {
            filePath = vscode.window.activeTextEditor?.document.uri.fsPath;
        }

        if (!filePath) {
            response.markdown('Usage: `/stage [file] <stage-name>`\n\nExample: `/stage deploy/main.yaml networking`\n\nOr open a deployment YAML and type: `/stage networking`');
            return { errorDetails: { message: 'No file or stage specified' } };
        }

        if (!stageName) {
            response.markdown(`**File:** \`${vscode.workspace.asRelativePath(filePath)}\`\n\nSpecify a stage name: \`/stage networking\``);
            return { errorDetails: { message: 'No stage name' } };
        }

        const rel = vscode.workspace.asRelativePath(filePath);
        response.markdown(`**Stage deploy:** \`${stageName}\` in \`${rel}\`\n\n`);
        response.button({ title: '▶ Dry Run', command: 'strata.deployStage', arguments: [filePath, stageName, true] });
        response.button({ title: '🚀 Deploy Stage', command: 'strata.deployStage', arguments: [filePath, stageName, false] });

        return { metadata: { command: 'stage', filePath, stageName } };
    }

    // ── /values ────────────────────────────────────────────────────────────────

    private async _handleValues(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        let filePath = request.prompt.trim();
        if (!filePath) filePath = vscode.window.activeTextEditor?.document.uri.fsPath ?? '';

        if (!filePath) {
            response.markdown('Open a deployment YAML or type: `/values path/to/deploy.yaml`');
            return { errorDetails: { message: 'No file' } };
        }

        response.markdown(`Loading values for **${vscode.workspace.asRelativePath(filePath)}**…\n\n`);

        try {
            const data = await this._client.getValues(filePath);
            const resolved = data.entries.filter((e) => e.resolved).length;
            const secrets = data.entries.filter((e) => e.secret).length;
            const unresolved = data.entries.filter((e) => !e.resolved).length;

            response.markdown(`| Property | Count |\n|---|---|\n`);
            response.markdown(`| **Total values** | ${data.count} |\n`);
            response.markdown(`| **Resolved** | ${resolved} |\n`);
            response.markdown(`| **Secrets** | ${secrets} |\n`);
            if (unresolved > 0) response.markdown(`| **⚠ Unresolved** | ${unresolved} |\n`);

            // Show non-secret values in a table
            const visible = data.entries.filter((e) => !e.secret).slice(0, 20);
            if (visible.length > 0) {
                response.markdown(`\n### Configuration Values\n\n| Key | Value | Source |\n|---|---|---|\n`);
                for (const v of visible) {
                    const val = v.value !== null ? `\`${v.value}\`` : '*null*';
                    response.markdown(`| \`${v.key}\` | ${val} | ${v.source} |\n`);
                }
            }

            if (secrets > 0) {
                response.markdown(`\n> 🔑 ${secrets} secret value${secrets !== 1 ? 's' : ''} are masked. Use the Values view to inspect.\n`);
            }
        } catch (err) {
            response.markdown(`**Failed to load values:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }

        response.button({ title: 'Open Values View', command: 'strata.showValues', arguments: [filePath] });
        return { metadata: { command: 'values', filePath } };
    }

    // ── /drift ─────────────────────────────────────────────────────────────────

    private async _handleDrift(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        let filePath = request.prompt.trim();
        if (!filePath) filePath = vscode.window.activeTextEditor?.document.uri.fsPath ?? '';

        if (!filePath) {
            response.markdown('Open a deployment YAML or type: `/drift path/to/deploy.yaml`');
            return { errorDetails: { message: 'No file' } };
        }

        const rel = vscode.workspace.asRelativePath(filePath);
        response.markdown(`Running drift detection on **${rel}**…\n\n⏳ This runs \`terraform plan\` and may take a moment.\n\n`);

        let drift: DriftData;
        try {
            drift = await this._client.runDrift(filePath);
        } catch (err) {
            response.markdown(`**Drift detection failed:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown('You can run it manually from the terminal:\n');
            response.button({ title: '🔍 Run in Terminal', command: 'strata.envDrift', arguments: [filePath] });
            return { errorDetails: { message: String(err) } };
        }

        const driftedStages = drift.stages.filter((s) => s.drifted);

        if (driftedStages.length === 0) {
            response.markdown('✅ **No drift detected** — infrastructure matches configuration.\n');
        } else {
            response.markdown(`⚠️ **Drift detected in ${driftedStages.length} stage${driftedStages.length !== 1 ? 's' : ''}:**\n\n`);
            for (const stage of driftedStages) {
                response.markdown(`### Stage: \`${stage.stage}\`\n\n`);
                if (stage.resources.length === 0) {
                    response.markdown('*(no resource details available)*\n\n');
                } else {
                    response.markdown(`| Resource | Change |\n|---|---|\n`);
                    for (const r of stage.resources) {
                        const badge = r.change_type === 'delete' ? '🗑' : r.change_type === 'create' ? '➕' : '✏️';
                        const attrs = r.attributes.length > 0 ? ` *(${r.attributes.slice(0, 3).join(', ')})*` : '';
                        response.markdown(`| \`${r.address}\` | ${badge} ${r.change_type}${attrs} |\n`);
                    }
                    response.markdown('\n');
                }
            }
            response.button({ title: '🚀 Re-deploy to fix', command: 'strata.deployRun', arguments: [filePath] });
        }

        return { metadata: { command: 'drift', filePath } };
    }

    // ── /repos ─────────────────────────────────────────────────────────────────

    private async _handleRepos(
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        try {
            const data = await this._client.getRepoStatus();

            if (!data.repos || data.repos.length === 0) {
                response.markdown('No repositories configured. Initialize with:\n\n```sh\nstrata repo add --name <name> --path <path>\n```\n');
                return { metadata: { command: 'repos' } };
            }

            response.markdown(`## Repository Status\n\n`);

            for (const repo of data.repos) {
                response.markdown(`### ${repo.name}\n\n`);

                if (!repo.tags || Object.keys(repo.tags).length === 0) {
                    response.markdown(`*No tags found*\n\n`);
                    continue;
                }

                if (repo.tags.latest_release) {
                    const tag = repo.tags.latest_release;
                    response.markdown(
                        `**Release:** [\`${tag.name}\`](command:strata.copyToClipboard?%5B%22${encodeURIComponent(tag.name)}%22%5D) ` +
                        `(${tag.age_str}, commit \`${tag.short_commit}\`)\n\n`
                    );
                }

                if (repo.tags.latest_quality) {
                    const tag = repo.tags.latest_quality;
                    response.markdown(
                        `**Quality Gate:** [\`${tag.name}\`](command:strata.copyToClipboard?%5B%22${encodeURIComponent(tag.name)}%22%5D) ` +
                        `(${tag.age_str}, commit \`${tag.short_commit}\`)\n\n`
                    );
                }
            }

            response.markdown(`💡 Use \`strata repo status --verbose\` for detailed git status.\n`);

            return { metadata: { command: 'repos' } };
        } catch (err) {
            response.markdown(`**Error fetching repository status:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }
    }

    // ── /promote ────────────────────────────────────────────────────────────────

    private async _handlePromote(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        const subcommand = request.prompt.trim().split(/\s+/)[0]?.toLowerCase();

        try {
            if (subcommand === 'history') {
                const history = await this._client.getPromotionHistory(undefined, 10);
                if (history.length === 0) {
                    response.markdown('No promotion history found. Run your first promotion with:\n\n```sh\nstrata promote start --ring <ring> --remote <target>\n```\n');
                    return { metadata: { command: 'promote' } };
                }
                response.markdown(`## Promotion History\n\n`);
                response.markdown(`| Date | Target | Ring | Version | Outcome |\n|---|---|---|---|---|\n`);
                for (const h of history) {
                    const date = h.started_at?.slice(0, 10) ?? '?';
                    const icon = h.outcome === 'success' ? '✅' : h.outcome === 'rolled_back' ? '⏪' : '❌';
                    response.markdown(`| ${date} | ${h.target} | ${h.ring ?? '?'} | ${h.to_version ?? '?'} | ${icon} ${h.outcome ?? '?'} |\n`);
                }
                return { metadata: { command: 'promote' } };
            }

            // Default: show matrix + in-flight
            const [matrix, inflight] = await Promise.all([
                this._client.getPromotionMatrix(),
                this._client.getPromotionStatus(),
            ]);

            if (inflight.length > 0) {
                response.markdown(`## In-Flight Promotions\n\n`);
                for (const p of inflight) {
                    const icon = p.status === 'in-progress' ? '🔄' : '✅';
                    response.markdown(`- ${icon} **${p.target}** → \`${p.ring}\` (${p.version}) — ${p.status}\n`);
                }
                response.markdown(`\n`);
            }

            if (matrix.rings.length === 0) {
                response.markdown('No promotion rings configured. Set up a promotion strategy in your configuration.\n');
                return { metadata: { command: 'promote' } };
            }

            response.markdown(`## Version Matrix\n\n`);
            response.markdown(`| Ring | Environments | Pins |\n|---|---|---|\n`);
            for (const ring of matrix.rings) {
                const envs = ring.environments.join(', ') || '—';
                const pinCount = Object.keys(ring.versions).length;
                response.markdown(`| **${ring.ring}** | ${envs} | ${pinCount} |\n`);
            }

            // Show version details per ring
            for (const ring of matrix.rings) {
                const entries = Object.entries(ring.versions);
                if (entries.length > 0) {
                    response.markdown(`\n### ${ring.ring}\n\n`);
                    for (const [target, version] of entries) {
                        response.markdown(`- \`${target}\`: **${version}**\n`);
                    }
                }
            }

            response.button({ title: '▶ Promote', command: 'strata.promoteStart' });
            response.button({ title: '⏪ Rollback', command: 'strata.promoteRollback' });

            return { metadata: { command: 'promote' } };
        } catch (err) {
            response.markdown(`**Error:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }
    }

    // ── /versions ──────────────────────────────────────────────────────────────

    private async _handleVersions(
        _request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        try {
            const matrix = await this._client.getPromotionMatrix();

            if (matrix.rings.length === 0) {
                response.markdown('No version pins found. Ensure promotion strategies are configured and lock files exist in `versions/`.\n');
                return { metadata: { command: 'versions' } };
            }

            response.markdown(`## Version Pins by Ring\n\n`);

            for (const ring of matrix.rings) {
                const entries = Object.entries(ring.versions);
                response.markdown(`### ${ring.ring}`);
                if (ring.require) {
                    response.markdown(` *(requires: ${ring.require})*`);
                }
                response.markdown(`\n\n`);

                if (entries.length === 0) {
                    response.markdown(`*(no pins)*\n\n`);
                    continue;
                }

                response.markdown(`| Target | Version |\n|---|---|\n`);
                for (const [target, version] of entries) {
                    response.markdown(`| \`${target}\` | **${version}** |\n`);
                }
                response.markdown(`\n`);
            }

            response.markdown(`💡 Use \`strata versions export -f <deploy>.yaml\` to see all resolved pins for a deployment.\n`);

            return { metadata: { command: 'versions' } };
        } catch (err) {
            response.markdown(`**Error:** ${err instanceof Error ? err.message : String(err)}`);
            return { errorDetails: { message: String(err) } };
        }
    }

    // ── /review ────────────────────────────────────────────────────────────────

    private async _handleReview(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        const filePath = this._resolveFilePath(request.prompt);
        if (!filePath) {
            response.markdown('**Usage:** `@strata /review [deploy-file.yaml]`\n\nNo deployment file specified and no active deployment found.');
            return { errorDetails: { message: 'No deployment file' } };
        }

        response.progress('Running terraform plan…');

        let plan;
        try {
            plan = await this._client.getBuildPlan(filePath);
        } catch (err) {
            response.markdown(`**Plan failed:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown('Run `strata build run` first to ensure artifacts are built.\n');
            response.button({ title: '🔨 Build', command: 'strata.buildRun', arguments: [filePath] });
            return { errorDetails: { message: String(err) } };
        }

        response.progress('Analysing plan with AI…');

        const builder = new AiPromptBuilder(this._client['workPath'] as string);
        const systemPrompt = await builder.systemPrompt('plan_review');
        const userPrompt = builder.buildPlanUserPrompt(plan, request.prompt || undefined);

        const messages = [
            vscode.LanguageModelChatMessage.User(
                `${systemPrompt}\n\n---\n\n${userPrompt}`,
            ),
        ];

        try {
            const chatResponse = await request.model.sendRequest(messages, {}, token);
            for await (const fragment of chatResponse.text) {
                response.markdown(fragment);
            }
        } catch (err) {
            response.markdown(`**AI analysis unavailable:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown(`Plan ran successfully. ${plan.terraform_plan.length} stage(s) planned.\n`);
        }

        response.button({ title: '🚀 Deploy', command: 'strata.deployRun', arguments: [filePath] });
        return { metadata: { command: 'review', filePath } };
    }

    // ── /diagnose ──────────────────────────────────────────────────────────────

    private async _handleDiagnose(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        const filePath = this._resolveFilePath(request.prompt);
        response.progress('Loading deployment history…');

        let entries;
        try {
            entries = await this._client.getAuditChanges(5);
        } catch (err) {
            response.markdown(`**Could not load audit history:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown('Run `strata deploy run` first to create a deployment record.\n');
            return { errorDetails: { message: String(err) } };
        }

        const failures = entries.filter(e => !e.success);
        if (failures.length === 0) {
            response.markdown('✅ **No failed deployments found** in recent history.\n\n');
            response.markdown('All recent deployments completed successfully.\n');
            return { metadata: { command: 'diagnose' } };
        }

        const last = failures[0];
        response.progress('Diagnosing failure with AI…');

        const builder = new AiPromptBuilder(this._client['workPath'] as string);
        const systemPrompt = await builder.systemPrompt('failure_diagnosis');
        const userPrompt = builder.diagnosisUserPrompt(last, request.prompt || undefined);

        const messages = [
            vscode.LanguageModelChatMessage.User(
                `${systemPrompt}\n\n---\n\n${userPrompt}`,
            ),
        ];

        try {
            const chatResponse = await request.model.sendRequest(messages, {}, token);
            for await (const fragment of chatResponse.text) {
                response.markdown(fragment);
            }
        } catch (err) {
            response.markdown(`**AI analysis unavailable:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown(`Last failure: **${last.deployment}** at ${last.timestamp}\n`);
            response.markdown(`Failed stages: ${last.stages.filter(s => !s.success).map(s => s.name).join(', ')}\n`);
        }

        if (filePath) {
            response.button({ title: '🔄 Retry Deploy', command: 'strata.deployRun', arguments: [filePath] });
        }
        return { metadata: { command: 'diagnose' } };
    }

    // ── /sbom ──────────────────────────────────────────────────────────────────

    private async _handleSbom(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('Strata CLI is not available.');
            return { errorDetails: { message: 'CLI not available' } };
        }

        const filePath = this._resolveFilePath(request.prompt);
        if (!filePath) {
            response.markdown('**Usage:** `@strata /sbom [deploy-file.yaml]`\n\nNo deployment file specified and no active deployment found.');
            return { errorDetails: { message: 'No deployment file' } };
        }

        response.progress('Loading SBOM…');

        let sbom;
        try {
            sbom = await this._client.generateSbom(filePath);
        } catch (err) {
            response.markdown(`**SBOM unavailable:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown('Run `strata build sbom` first to generate the SBOM.\n');
            response.button({ title: '📦 Generate SBOM', command: 'strata.buildSbom', arguments: [filePath] });
            return { errorDetails: { message: String(err) } };
        }

        if (sbom.component_count === 0) {
            response.markdown('**No components found in SBOM.** Run `strata build sbom` to generate it.\n');
            return { metadata: { command: 'sbom', filePath } };
        }

        response.progress(`Analysing ${sbom.component_count} components with AI…`);

        const builder = new AiPromptBuilder(this._client['workPath'] as string);
        const systemPrompt = await builder.systemPrompt('sbom_analysis');
        const userPrompt = builder.sbomUserPrompt(sbom, request.prompt || undefined);

        const messages = [
            vscode.LanguageModelChatMessage.User(
                `${systemPrompt}\n\n---\n\n${userPrompt}`,
            ),
        ];

        try {
            const chatResponse = await request.model.sendRequest(messages, {}, token);
            for await (const fragment of chatResponse.text) {
                response.markdown(fragment);
            }
        } catch (err) {
            response.markdown(`**AI analysis unavailable:** ${err instanceof Error ? err.message : String(err)}\n\n`);
            response.markdown(`SBOM has **${sbom.component_count}** components.`);
            if (sbom.vulnerabilities_found) {
                response.markdown(` **Vulnerabilities found**: Critical=${sbom.critical_count}, High=${sbom.high_count}\n`);
            } else {
                response.markdown(' No vulnerabilities flagged.\n');
            }
        }

        return { metadata: { command: 'sbom', filePath } };
    }

    // ── Freeform (no slash command) ────────────────────────────────────────────

    private async _handleFreeform(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        // Auto-route AI-related keywords to dedicated handlers
        const prompt = request.prompt.toLowerCase();
        const isAiQuery = (keywords: string[]) => keywords.some(k => prompt.includes(k));

        if (isAiQuery(['review plan', 'analyse plan', 'analyze plan', 'terraform plan', 'what will change', 'what changes', 'blast radius'])) {
            return this._handleReview(request, response, token);
        }
        if (isAiQuery(['diagnose', 'why did', 'what failed', 'last failure', 'deploy fail', 'error in deploy'])) {
            return this._handleDiagnose(request, response, token);
        }
        if (isAiQuery(['sbom', 'supply chain', 'dependencies', 'vulnerabilities', 'cve', 'components'])) {
            return this._handleSbom(request, response, token);
        }
        // Provide workspace context and let the LLM answer
        const status = await this._getStatus();
        // Fetch env status for richer deployment context (best-effort, non-blocking)
        let envSummary = '';
        if (this._client) {
            try {
                const envData = await this._client.getEnvStatus();
                if (envData.deployments.length > 0) {
                    const lines = envData.deployments.map((d) => {
                        const totalOutputs = d.stages.reduce((s, st) => s + (st.cache?.output_count ?? 0), 0);
                        return `${d.name}: ${d.cached_count}/${d.stage_count} stages cached, ${totalOutputs} outputs`;
                    });
                    envSummary = `Deployments: ${lines.join('; ')}.`;
                }
            } catch { /* non-critical — proceed without env data */ }
        }

        const contextBlock = [
            `The user is working in a strata infrastructure workspace.`,
            `Health: ${status.health.status}. Profile: ${status.profiles.active ?? 'none'}.`,
            `Readiness: ${status.readiness.phases_complete}/${status.readiness.phases_total} phases.`,
            status.readiness.next_step
                ? `Next step: ${status.readiness.next_step.label} (hint: ${status.readiness.next_step.hint ?? 'none'})`
                : '',
            `Repositories: ${(status.repositories ?? []).map(r => r.name).join(', ') || 'none'}.`,
            envSummary,
            `\nUser question: ${request.prompt}`,
        ].filter(Boolean).join('\n');

        // Use the model from the request to generate a response
        const messages = [
            vscode.LanguageModelChatMessage.User(
                `You are a helpful assistant for the strata infrastructure-as-code platform. ` +
                `Answer questions about strata workspaces, YAML configuration, deployment workflows, ` +
                `and troubleshooting. Use the workspace context below to give accurate answers.\n\n` +
                `## Workspace Context\n${contextBlock}`
            ),
        ];

        try {
            const chatResponse = await request.model.sendRequest(messages, {}, token);
            for await (const fragment of chatResponse.text) {
                response.markdown(fragment);
            }
        } catch (err) {
            // If model call fails, provide a static helpful response
            response.markdown(
                `I have your workspace context but couldn't generate a detailed response. ` +
                `Here's what I know:\n\n` +
                contextBlock.split('\n').map(l => `> ${l}`).join('\n') +
                `\n\nTry one of these commands:\n` +
                `- \`/status\` — workspace overview\n` +
                `- \`/validate\` — validate the current file\n` +
                `- \`/guide\` — readiness checklist\n` +
                `- \`/build\` — build instructions\n` +
                `- \`/deploy\` — deploy instructions\n`
            );
        }

        return { metadata: { command: 'freeform' } };
    }

    // ── /approvals ─────────────────────────────────────────────────────────────

    private async _handleApprovals(
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        if (!this._client) {
            response.markdown('⚠️ Strata CLI is not available.');
            return { metadata: { command: 'approvals' } };
        }

        response.progress('Loading pending work items…');

        let items: import('../strataClient').WorkItemSummary[] = [];
        try {
            items = await this._client.listWorkItems('pending');
        } catch (err) {
            response.markdown(`**Error loading work items:** ${err instanceof Error ? err.message : String(err)}`);
            return { metadata: { command: 'approvals' } };
        }

        if (items.length === 0) {
            response.markdown('✅ **No pending work items.** All deployment gates are clear.\n');
            response.button({ title: 'Refresh', command: 'strata.refreshWorkItems' });
            return { metadata: { command: 'approvals' } };
        }

        response.markdown(`## ⏸️ Pending Work Items (${items.length})\n\n`);

        for (const item of items) {
            const shortId = item.id.includes('/') ? item.id.split('/').slice(1).join('/') : item.id;
            const deployName = item.deployment.split('/').pop()?.replace('.yaml', '') ?? item.deployment;
            const created = item.created_at.slice(0, 19).replace('T', ' ');
            const expires = item.expires_at ? ` · expires ${item.expires_at.slice(0, 19).replace('T', ' ')} UTC` : '';
            const typeLabel = item.type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

            response.markdown(`### 🟡 ${typeLabel}: \`${shortId}\`\n`);
            response.markdown(`**Deployment:** ${deployName}  \n**Created:** ${created} UTC${expires}  \n**By:** ${item.created_by}\n\n`);

            // Show type-specific context
            const ctx = item.context as Record<string, unknown>;
            if (ctx.plan_summary && typeof ctx.plan_summary === 'object') {
                const ps = ctx.plan_summary as Record<string, unknown>;
                response.markdown(`**Plan:** ${ps.summary ?? ''}\n\n`);
            }
            if (ctx.cost_delta_monthly !== undefined) {
                const delta = Number(ctx.cost_delta_monthly);
                response.markdown(`**Cost delta:** ${delta >= 0 ? '+' : ''}$${delta.toFixed(2)}/month\n\n`);
            }
            if (ctx.cve_critical_count) {
                response.markdown(`**Critical CVEs:** ${ctx.cve_critical_count}\n\n`);
            }
            if (ctx.ai_risk) {
                const riskIcon = ctx.ai_risk === 'critical' ? '🔴' : ctx.ai_risk === 'high' ? '🟠' : '🟡';
                response.markdown(`**AI Risk:** ${riskIcon} ${String(ctx.ai_risk).toUpperCase()}\n\n`);
            }
            if (ctx.approvers && Array.isArray(ctx.approvers)) {
                response.markdown(`**Approvers:** ${(ctx.approvers as string[]).join(', ')}\n\n`);
            }

            response.button({ title: `✅ Approve`, command: 'strata.approveWorkItem', arguments: [{ workItem: item }] });
            response.button({ title: `❌ Reject`, command: 'strata.rejectWorkItem', arguments: [{ workItem: item }] });
            response.markdown('\n---\n\n');
        }

        response.markdown(`\n**CLI:** \`strata workitem list --status pending\`\n`);
        response.button({ title: 'Open Work Items panel', command: 'strataWorkItems.focus' });
        response.button({ title: 'Refresh', command: 'strata.refreshWorkItems' });

        return { metadata: { command: 'approvals' } };
    }

    // ── Follow-ups ─────────────────────────────────────────────────────────────

    private _provideFollowups(result: vscode.ChatResult): vscode.ChatFollowup[] {
        const cmd = (result.metadata as { command?: string } | undefined)?.command;

        switch (cmd) {
            case 'status':
                return [
                    { prompt: '', label: 'Show readiness guide', command: 'guide' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                    { prompt: '', label: 'Inspect values', command: 'values' },
                ];
            case 'validate':
                return [
                    { prompt: '', label: 'Build deployment', command: 'build' },
                    { prompt: '', label: 'Show workspace status', command: 'status' },
                ];
            case 'guide':
                return [
                    { prompt: '', label: 'Build my deployment', command: 'build' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'build':
                return [
                    { prompt: '', label: 'Deploy now', command: 'deploy' },
                    { prompt: '', label: 'Check values', command: 'values' },
                    { prompt: '', label: 'Detect drift', command: 'drift' },
                ];
            case 'deploy':
                return [
                    { prompt: '', label: 'Check status', command: 'status' },
                    { prompt: '', label: 'Detect drift', command: 'drift' },
                    { prompt: '', label: 'Inspect values', command: 'values' },
                ];
            case 'stage':
                return [
                    { prompt: '', label: 'Full deploy', command: 'deploy' },
                    { prompt: '', label: 'Detect drift', command: 'drift' },
                ];
            case 'values':
                return [
                    { prompt: '', label: 'Deploy', command: 'deploy' },
                    { prompt: '', label: 'Detect drift', command: 'drift' },
                ];
            case 'drift':
                return [
                    { prompt: '', label: 'Deploy', command: 'deploy' },
                    { prompt: '', label: 'Check status', command: 'status' },
                ];
            case 'repos':
                return [
                    { prompt: '', label: 'Workspace status', command: 'status' },
                    { prompt: '', label: 'Readiness guide', command: 'guide' },
                    { prompt: '', label: 'Promotions', command: 'promote' },
                ];
            case 'promote':
                return [
                    { prompt: 'history', label: 'Promotion history', command: 'promote' },
                    { prompt: '', label: 'Version pins', command: 'versions' },
                    { prompt: '', label: 'Deploy', command: 'deploy' },
                ];
            case 'versions':
                return [
                    { prompt: '', label: 'Promote', command: 'promote' },
                    { prompt: '', label: 'Deploy', command: 'deploy' },
                ];
            case 'review':
                return [
                    { prompt: '', label: 'Deploy now', command: 'deploy' },
                    { prompt: '', label: 'Check policies', command: 'validate' },
                    { prompt: '', label: 'Check SBOM risks', command: 'sbom' },
                ];
            case 'diagnose':
                return [
                    { prompt: '', label: 'Retry deploy', command: 'deploy' },
                    { prompt: '', label: 'Workspace status', command: 'status' },
                    { prompt: '', label: 'Review plan', command: 'review' },
                ];
            case 'sbom':
                return [
                    { prompt: '', label: 'Deploy', command: 'deploy' },
                    { prompt: '', label: 'Workspace status', command: 'status' },
                ];
            case 'approvals':
                return [
                    { prompt: '', label: 'Refresh approvals', command: 'approvals' },
                    { prompt: '', label: 'Deploy status', command: 'status' },
                    { prompt: '', label: 'Review plan', command: 'review' },
                ];
            default:
                return [
                    { prompt: '', label: 'Workspace status', command: 'status' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /** Extract a file path from the user prompt, falling back to the active editor. */
    private _resolveFilePath(prompt: string): string | undefined {
        // Check if prompt contains a .yaml path
        const match = prompt.match(/\S+\.ya?ml/i);
        if (match) return match[0];
        // Fall back to active editor
        const editor = vscode.window.activeTextEditor;
        if (editor && /\.ya?ml$/i.test(editor.document.fileName)) {
            return editor.document.fileName;
        }
        return undefined;
    }

    private async _getStatus(): Promise<WorkspaceStatus> {
        if (this._lastStatus) return this._lastStatus;
        if (this._client) {
            this._lastStatus = await this._client.getStatus();
            return this._lastStatus;
        }
        throw new Error('Strata CLI is not available. Check the strata.cliPath setting.');
    }

    private _healthIcon(status: string): string {
        switch (status) {
            case 'HEALTHY': return '🟢';
            case 'DEGRADED': return '🟡';
            case 'BROKEN': return '🔴';
            default: return '⚪';
        }
    }
}
