/**
 * HelpViewProvider — context-sensitive help sidebar for the Strata activity bar.
 *
 * Listens to the active editor and detects the strata `kind:` from the open
 * YAML file. Renders a four-section tree:
 *
 *   1. "Workspace" — custom topics from .strata/help/*.md (only if present)
 *   2. "Suggested" — topics relevant to the current file's kind
 *   3. "Actions"   — quick actions (Validate, Schema, Guide) for the file
 *   4. "All Topics"— the full built-in topic list sorted alphabetically
 *
 * Workspace topics override built-in topics of the same name (matching CLI
 * behaviour in _render_topic).  A file watcher on .strata/help/ refreshes
 * the tree automatically when files are added, removed, or changed.
 *
 * Clicking a topic item fires `strata.showIntegrationHelp` which reuses the
 * existing IntegrationHelpProvider WebView panel.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// Kind → topic suggestion map
// ---------------------------------------------------------------------------

const KIND_TOPICS: Record<string, string[]> = {
    configuration: ['configuration', 'integrations', 'policies', 'audit', 'gates'],
    deployment: ['deployment', 'configuration', 'environments', 'gates', 'audit'],
    environment: ['environments', 'gates', 'profiles'],
    workspace: ['workspace', 'profiles', 'refs', 'config-merge'],
    module: ['cross-repo', 'terraform', 'ansible'],
    provider: ['integrations', 'terraform', 'ansible'],
    namespace: ['cross-repo', 'environments'],
    resource: ['integrations', 'policies'],
    firewall: ['integrations'],
    network: ['integrations'],
    dns: ['integrations'],
    tenant: ['environments', 'policies'],
};

/** Topics shown when no strata file is open. */
const FALLBACK_TOPICS = ['quickstart', 'workspace', 'troubleshooting'];

/** Full alphabetically-sorted topic list (mirrors cli_help.py _TOPICS keys). */
const ALL_TOPICS: string[] = [
    'quickstart', 'workspace', 'profiles', 'refs', 'config-merge', 'cross-repo',
    'environments', 'troubleshooting',
    'configuration', 'integrations', 'policies', 'gates', 'audit', 'workitem',
    'deployment', 'dns', 'environment', 'firewall', 'module', 'namespace', 'network', 'provider', 'resource', 'tenant',
    'git', 'terraform', 'terraform-cloud-auth', 'docker',
    'azure_appconfig', 'azure_keyvault', 'azure_cli', 'azure_scripts',
    'aws_cli', 'aws_scripts',
    'gcloud_cli', 'gcloud_scripts',
    'ansible', 'bicep', 'checkov', 'cve_scanner', 'etcd',
    'flagsmith', 'helm', 'infisical', 'infracost',
    'openbao', 'opentofu', 'opa',
    'sentinel', 'elk', 'otel', 'splunk',
    'ai_agent', 'bitwarden', 'hashicorp_consul', 'hashicorp_vault',
].sort((a, b) => a.localeCompare(b));

// ---------------------------------------------------------------------------
// Tree item types
// ---------------------------------------------------------------------------

export type HelpItemKind =
    | 'section'
    | 'topic'
    | 'topic-workspace'    // workspace custom topic (may override built-in)
    | 'topic-override'     // workspace topic that overrides a built-in
    | 'action';

export class HelpTreeItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly itemKind: HelpItemKind,
        collapsible: vscode.TreeItemCollapsibleState,
        command?: vscode.Command,
    ) {
        super(label, collapsible);
        this.contextValue = itemKind;
        if (command) {
            this.command = command;
        }
        if (itemKind === 'section') {
            this.iconPath = new vscode.ThemeIcon('symbol-namespace');
        } else if (itemKind === 'topic-workspace' || itemKind === 'topic-override') {
            this.iconPath = new vscode.ThemeIcon('file-text');
        } else if (itemKind === 'topic') {
            this.iconPath = new vscode.ThemeIcon('book');
        }
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class HelpViewProvider
    implements vscode.TreeDataProvider<HelpTreeItem>, vscode.Disposable {

    private readonly _onChange =
        new vscode.EventEmitter<HelpTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _currentKind: string | null = null;
    private _currentFile: string | null = null;
    private _workPath: string | undefined;
    /** topic name → true when it overrides a built-in; false when it's new */
    private _workspaceTopics: Map<string, boolean> = new Map();
    private _disposables: vscode.Disposable[] = [];

    // ── Public API ────────────────────────────────────────────────────────────

    setWorkPath(workPath: string): void {
        this._workPath = workPath;
        this._loadWorkspaceTopics();
        this._watchWorkspaceHelp();
    }

    /**
     * Call once from extension activate() to subscribe to editor changes.
     */
    register(context: vscode.ExtensionContext): void {
        this._onEditorChange(vscode.window.activeTextEditor);

        const sub = vscode.window.onDidChangeActiveTextEditor((editor) => {
            this._onEditorChange(editor);
        });
        this._disposables.push(sub);
        context.subscriptions.push(sub);
    }

    dispose(): void {
        this._onChange.dispose();
        this._disposables.forEach(d => d.dispose());
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: HelpTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: HelpTreeItem): HelpTreeItem[] {
        if (!element) {
            return this._buildRoots();
        }

        switch (element.label) {
            case 'Workspace': return this._buildWorkspaceItems();
            case 'Suggested': return this._buildSuggestedItems();
            case 'Actions': return this._buildActionItems();
            case 'All Topics': return this._buildAllTopicItems();
            default: return [];
        }
    }

    // ── Private: workspace topic discovery ───────────────────────────────────

    private _workspaceHelpDir(): string | undefined {
        return this._workPath
            ? path.join(this._workPath, '.strata', 'help')
            : undefined;
    }

    private _loadWorkspaceTopics(): void {
        this._workspaceTopics.clear();
        const dir = this._workspaceHelpDir();
        if (!dir || !fs.existsSync(dir)) {
            return;
        }
        try {
            for (const entry of fs.readdirSync(dir)) {
                if (!entry.endsWith('.md')) { continue; }
                const name = path.basename(entry, '.md');
                const overrides = ALL_TOPICS.includes(name);
                this._workspaceTopics.set(name, overrides);
            }
        } catch { /* directory unreadable */ }
    }

    private _watchWorkspaceHelp(): void {
        const dir = this._workspaceHelpDir();
        if (!dir) { return; }

        // Watch using VS Code's file system watcher (works for non-existent dirs too)
        const pattern = new vscode.RelativePattern(
            vscode.Uri.file(this._workPath!),
            '.strata/help/*.md',
        );
        const watcher = vscode.workspace.createFileSystemWatcher(pattern);

        const refresh = () => {
            this._loadWorkspaceTopics();
            this._onChange.fire();
        };

        watcher.onDidCreate(refresh);
        watcher.onDidDelete(refresh);
        watcher.onDidChange(refresh);

        this._disposables.push(watcher);
    }

    // ── Private: root sections ────────────────────────────────────────────────

    private _buildRoots(): HelpTreeItem[] {
        const roots: HelpTreeItem[] = [];

        if (this._workspaceTopics.size > 0) {
            roots.push(new HelpTreeItem(
                'Workspace',
                'section',
                vscode.TreeItemCollapsibleState.Expanded,
            ));
        }

        if (this._currentKind) {
            roots.push(new HelpTreeItem(
                'Suggested',
                'section',
                vscode.TreeItemCollapsibleState.Expanded,
            ));
            roots.push(new HelpTreeItem(
                'Actions',
                'section',
                vscode.TreeItemCollapsibleState.Expanded,
            ));
        }

        roots.push(new HelpTreeItem(
            'All Topics',
            'section',
            this._currentKind
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.Expanded,
        ));

        return roots;
    }

    // ── Private: section children ─────────────────────────────────────────────

    private _buildWorkspaceItems(): HelpTreeItem[] {
        return Array.from(this._workspaceTopics.entries())
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([name, overrides]) => {
                const item = this._topicItem(name, overrides ? 'topic-override' : 'topic-workspace');
                if (overrides) {
                    item.description = 'overrides built-in';
                    item.tooltip = `Custom workspace topic that overrides the built-in "${name}" topic`;
                } else {
                    item.description = 'workspace';
                    item.tooltip = `Custom topic from .strata/help/${name}.md`;
                }
                return item;
            });
    }

    private _buildSuggestedItems(): HelpTreeItem[] {
        const topics = this._currentKind
            ? (KIND_TOPICS[this._currentKind] ?? FALLBACK_TOPICS)
            : FALLBACK_TOPICS;

        return topics.map(topic => {
            const item = this._topicItem(topic);
            if (this._workspaceTopics.has(topic)) {
                item.description = 'workspace override';
            }
            return item;
        });
    }

    private _buildActionItems(): HelpTreeItem[] {
        const items: HelpTreeItem[] = [];

        if (this._currentFile) {
            const validateItem = new HelpTreeItem(
                '$(check) Validate',
                'action',
                vscode.TreeItemCollapsibleState.None,
                {
                    title: 'Validate',
                    command: 'strata.validateCurrentFile',
                    tooltip: 'Run strata validate on this file',
                },
            );
            validateItem.description = 'current file';
            items.push(validateItem);
        }

        if (this._currentKind) {
            const schemaItem = new HelpTreeItem(
                '$(file-code) Schema',
                'action',
                vscode.TreeItemCollapsibleState.None,
                {
                    title: 'Schema',
                    command: 'strata.openSchema',
                    tooltip: `View JSON schema for kind: ${this._currentKind}`,
                    arguments: [{ kind: this._currentKind }],
                },
            );
            schemaItem.description = this._currentKind;
            items.push(schemaItem);
        }

        const guideItem = new HelpTreeItem(
            '$(list-ordered) Guide',
            'action',
            vscode.TreeItemCollapsibleState.None,
            {
                title: 'Guide',
                command: 'strata.showGuide',
                tooltip: 'Show workspace readiness checklist',
            },
        );
        guideItem.description = 'workspace checklist';
        items.push(guideItem);

        return items;
    }

    private _buildAllTopicItems(): HelpTreeItem[] {
        return ALL_TOPICS.map(topic => {
            const item = this._topicItem(topic);
            if (this._workspaceTopics.has(topic)) {
                item.description = 'overridden';
                item.tooltip = `Built-in topic overridden by .strata/help/${topic}.md`;
            }
            return item;
        });
    }

    // ── Private: helpers ──────────────────────────────────────────────────────

    private _topicItem(topic: string, kind: HelpItemKind = 'topic'): HelpTreeItem {
        return new HelpTreeItem(
            topic,
            kind,
            vscode.TreeItemCollapsibleState.None,
            {
                title: 'Show Help',
                command: 'strata.showIntegrationHelp',
                tooltip: `Open help for: ${topic}`,
                arguments: [topic],
            },
        );
    }

    private _onEditorChange(editor: vscode.TextEditor | undefined): void {
        const newKind = editor ? this._detectKind(editor.document) : null;
        const newFile = editor?.document.uri.fsPath ?? null;

        if (newKind !== this._currentKind || newFile !== this._currentFile) {
            this._currentKind = newKind;
            this._currentFile = newFile;
            this._onChange.fire();
        }
    }

    /** Fast first-20-line scan, same logic as CodeLensProvider. */
    private _detectKind(document: vscode.TextDocument): string | null {
        if (document.languageId !== 'yaml') {
            return null;
        }

        let isStrata = false;
        let kind: string | null = null;

        for (let i = 0; i < Math.min(document.lineCount, 20); i++) {
            const line = document.lineAt(i).text.trim();
            if (line.startsWith('apiVersion: strata.')) {
                isStrata = true;
            }
            const m = line.match(/^kind:\s*(\S+)/);
            if (m) {
                kind = m[1].toLowerCase();
            }
            if (isStrata && kind) {
                return kind;
            }
        }

        return null;
    }
}
