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
 *   /build    — explain how to build the current deployment
 *   /deploy   — explain how to deploy the current deployment
 */

import * as vscode from 'vscode';
import type { StrataClient, WorkspaceStatus, ValidationResult } from '../strataClient';

const PARTICIPANT_ID = 'strata.chat';

/** Slash-command metadata registered in package.json `chatParticipants[].commands`. */
type SlashCommand = 'status' | 'validate' | 'guide' | 'build' | 'deploy' | 'repos';

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
                    return await this._handleBuildOrDeploy('build', response);
                case 'deploy':
                    return await this._handleBuildOrDeploy('deploy', response);
                case 'repos':
                    return await this._handleRepos(response, token);
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

    // ── /build and /deploy ─────────────────────────────────────────────────────

    private async _handleBuildOrDeploy(
        action: 'build' | 'deploy',
        response: vscode.ChatResponseStream,
    ): Promise<vscode.ChatResult> {
        const status = await this._getStatus();
        const profile = status.profiles.active ?? '*(no profile)*';

        // Find deployment files from the status
        const deployFiles = status.profiles.paths?.['deployment'] ?? [];

        response.markdown(`## ${action === 'build' ? 'Build' : 'Deploy'} Guide\n\n`);
        response.markdown(`**Active profile:** ${profile}\n\n`);

        if (deployFiles.length === 0) {
            response.markdown(`No deployment files found. Create one with:\n\n\`\`\`sh\nstrata new deployment\n\`\`\`\n`);
        } else {
            response.markdown(`**Available deployment files:**\n\n`);
            for (const df of deployFiles) {
                const rel = vscode.workspace.asRelativePath(df.path);
                if (action === 'build') {
                    response.markdown(`- \`${rel}\`\n  \`\`\`sh\n  strata build run -f ${rel} --dry-run\n  strata build run -f ${rel}\n  \`\`\`\n\n`);
                } else {
                    response.markdown(`- \`${rel}\`\n  \`\`\`sh\n  strata deploy run -f ${rel} --dry-run\n  strata deploy run -f ${rel} --force\n  \`\`\`\n\n`);
                }
            }
        }

        return { metadata: { command: action } };
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

    // ── Freeform (no slash command) ────────────────────────────────────────────

    private async _handleFreeform(
        request: vscode.ChatRequest,
        response: vscode.ChatResponseStream,
        _token: vscode.CancellationToken,
    ): Promise<vscode.ChatResult> {
        // Provide workspace context and let the LLM answer
        const status = await this._getStatus();

        const contextBlock = [
            `The user is working in a strata infrastructure workspace.`,
            `Health: ${status.health.status}. Profile: ${status.profiles.active ?? 'none'}.`,
            `Readiness: ${status.readiness.phases_complete}/${status.readiness.phases_total} phases.`,
            status.readiness.next_step
                ? `Next step: ${status.readiness.next_step.label} (hint: ${status.readiness.next_step.hint ?? 'none'})`
                : '',
            `Repositories: ${(status.repositories ?? []).map(r => r.name).join(', ') || 'none'}.`,
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
            const chatResponse = await request.model.sendRequest(messages, {}, _token);
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

    // ── Follow-ups ─────────────────────────────────────────────────────────────

    private _provideFollowups(result: vscode.ChatResult): vscode.ChatFollowup[] {
        const cmd = (result.metadata as { command?: string } | undefined)?.command;

        switch (cmd) {
            case 'status':
                return [
                    { prompt: '', label: 'Show readiness guide', command: 'guide' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'validate':
                return [
                    { prompt: '', label: 'Show workspace status', command: 'status' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'guide':
                return [
                    { prompt: '', label: 'Build my deployment', command: 'build' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'build':
                return [
                    { prompt: '', label: 'Deploy now', command: 'deploy' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'deploy':
                return [
                    { prompt: '', label: 'Check status', command: 'status' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
            case 'repos':
                return [
                    { prompt: '', label: 'Workspace status', command: 'status' },
                    { prompt: '', label: 'Readiness guide', command: 'guide' },
                ];
            default:
                return [
                    { prompt: '', label: 'Workspace status', command: 'status' },
                    { prompt: '', label: 'Check repositories', command: 'repos' },
                ];
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

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
