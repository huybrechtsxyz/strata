/**
 * DeploymentExplorerProvider — "Deployments" pane in the Strata activity bar.
 *
 * The deployment is the pivot point for all views.  When a deployment is selected
 * (via DeploymentContext), this provider shows its full dependency hierarchy:
 *
 *   $(cloud) deploy-prd.yaml          ← active deployment
 *     $(package) Workspace: platform_workspace → workspace.yaml
 *       ▸ Providers (2)
 *           $(plug) azure → provider-azure.yaml
 *       ▸ Provisioners (2)
 *           $(tools) terraform: platform_iac
 *       ▸ Namespaces (1)
 *           monitoring
 *     ▸ Environments (2)
 *         env-base.yaml
 *         env-prd.yaml
 *     ▸ Configurations (1)
 *         azure-aks-config.yaml
 *     ▸ Policies (lazy)
 *         cost_threshold: warn @ €5000
 *   ── Other Deployments ──
 *   $(cloud-outline) deploy-stg   [Switch]
 *   $(add) New File…
 *
 * YAML parsing is done inline (no external parser dependency) — reads the
 * specific strata deployment and workspace manifest shapes.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import type { StrataClient, WorkspaceStatus, RepositoryInfo, PolicyEntry } from '../strataClient';
import type { DeploymentContext } from './deploymentContext';

// ---------------------------------------------------------------------------
// Parsed manifest shapes
// ---------------------------------------------------------------------------

interface SourceRef {
    repository: string;
    sourcePath: string;
}

interface DeploymentManifest {
    name: string;
    workspaceName: string;
    workspaceSource: SourceRef;
    environments: Array<{ name: string; source: SourceRef }>;
    configurations: Array<{ name: string; source: SourceRef }>;
}

interface WorkspaceManifest {
    name: string;
    providers: Array<{ name: string; file: string }>;
    provisioners: Array<{ name: string; provisioner: string }>;
    topologyNames: string[];
    namespaceNames: string[];
}

// ---------------------------------------------------------------------------
// Tree item kind & data
// ---------------------------------------------------------------------------

export type ExplorerItemKind =
    | 'select-prompt'
    | 'deployment-active'
    | 'workspace-section'
    | 'providers-group'
    | 'provisioners-group'
    | 'topology-group'
    | 'namespaces-group'
    | 'provider-item'
    | 'provisioner-item'
    | 'topology-item'
    | 'namespace-item'
    | 'environments-section'
    | 'environment-item'
    | 'configurations-section'
    | 'configuration-item'
    | 'policies-section'
    | 'policy-item'
    | 'other-divider'
    | 'deployment-other'
    | 'new-file-prompt'
    | 'loading'
    | 'error'
    | 'empty';

export class ExplorerItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: ExplorerItemKind,
        collapsible: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
        /** Arbitrary data for async child resolution (file paths, names, etc.) */
        public readonly data?: unknown,
    ) {
        super(label, collapsible);
        this.contextValue = kind;
    }
}

// ---------------------------------------------------------------------------
// YAML manifest parsers (lightweight — handles strata's specific structure)
// ---------------------------------------------------------------------------

/**
 * Extract an array of objects from a named YAML list section.
 * Expects items at indent 4 (`    - key: val`) with nested content at 6 and 8.
 */
function extractYamlList(text: string, sectionKey: string): Array<Record<string, string>> {
    const lines = text.split('\n');
    const results: Array<Record<string, string>> = [];
    let inSection = false;
    let currentItem: Record<string, string> | null = null;
    let subSection = '';

    for (const raw of lines) {
        const indent = raw.search(/\S/);
        if (indent < 0) continue;
        const line = raw.trimStart();

        if (!inSection) {
            if (indent === 2 && line === `${sectionKey}:`) {
                inSection = true;
            }
            continue;
        }

        // Back to indent ≤ 2 means left section
        if (indent <= 2 && !line.startsWith('- ')) {
            if (currentItem) results.push(currentItem);
            currentItem = null;
            break;
        }

        // New list item at indent 4
        if (indent === 4 && line.startsWith('- ')) {
            if (currentItem) results.push(currentItem);
            currentItem = {};
            subSection = '';
            const rest = line.slice(2).trim();
            const sep = rest.indexOf(': ');
            if (sep > 0) {
                currentItem[rest.slice(0, sep)] = rest.slice(sep + 2).trim();
            }
            continue;
        }

        if (!currentItem) continue;

        // Key-value or sub-section header at indent 6
        if (indent === 6) {
            if (line.endsWith(':') && !line.includes(': ')) {
                subSection = line.slice(0, -1);
            } else {
                const sep = line.indexOf(': ');
                if (sep > 0) {
                    const key = subSection ? `${subSection}.${line.slice(0, sep)}` : line.slice(0, sep);
                    currentItem[key] = line.slice(sep + 2).trim();
                }
                // key with empty value → sub-section marker
                if (sep < 0 && line.endsWith(':')) {
                    subSection = line.slice(0, -1);
                }
            }
        }

        // Nested values at indent 8 (inside sub-section like source:)
        if (indent === 8 && subSection) {
            const sep = line.indexOf(': ');
            if (sep > 0) {
                currentItem[`${subSection}.${line.slice(0, sep)}`] = line.slice(sep + 2).trim();
            }
        }
    }

    if (currentItem) results.push(currentItem);
    return results;
}

/** Extract a scalar value at the first occurrence of `  key: value`. */
function extractScalar(text: string, key: string, indent = 2): string {
    const prefix = ' '.repeat(indent);
    const re = new RegExp(`^${prefix}${key}:\\s*(.+)$`, 'm');
    return text.match(re)?.[1]?.trim() ?? '';
}

function parseDeploymentManifest(text: string): DeploymentManifest {
    // meta.name
    const name = extractScalar(text, 'name', 2);

    // spec.workspace block
    const wsNameMatch = text.match(/^\s{4}name:\s*(.+)$/m);
    const wsName = wsNameMatch?.[1]?.trim() ?? '';

    // Find spec.workspace.source values
    let wsRepo = '/';
    let wsSrcPath = '';
    const lines = text.split('\n');
    let inWorkspace = false, inSource = false;

    for (const raw of lines) {
        const indent = raw.search(/\S/);
        if (indent < 0) continue;
        const line = raw.trimStart();
        if (indent === 2 && line === 'workspace:') { inWorkspace = true; inSource = false; continue; }
        if (indent === 2 && inWorkspace && line !== 'workspace:') { inWorkspace = false; }
        if (!inWorkspace) continue;
        if (indent === 4 && line === 'source:') { inSource = true; continue; }
        if (indent === 4 && inSource && line !== 'source:') { inSource = false; }
        if (inSource && indent === 6) {
            if (line.startsWith('repository:')) wsRepo = line.slice('repository:'.length).trim() || '/';
            if (line.startsWith('source_path:')) wsSrcPath = line.slice('source_path:'.length).trim();
        }
    }

    // spec.environments
    const envItems = extractYamlList(text, 'environments');
    const environments = envItems.map(e => ({
        name: e['name'] ?? '',
        source: {
            repository: e['source.repository'] ?? '/',
            sourcePath: e['source.source_path'] ?? '',
        },
    }));

    // spec.configurations
    const cfgItems = extractYamlList(text, 'configurations');
    const configurations = cfgItems.map(c => ({
        name: c['name'] ?? '',
        source: {
            repository: c['source.repository'] ?? '/',
            sourcePath: c['source.source_path'] ?? '',
        },
    }));

    return {
        name,
        workspaceName: wsName,
        workspaceSource: { repository: wsRepo, sourcePath: wsSrcPath },
        environments,
        configurations,
    };
}

function parseWorkspaceManifest(text: string): WorkspaceManifest {
    const name = extractScalar(text, 'name', 2);

    const providerItems = extractYamlList(text, 'providers');
    const providers = providerItems.map(p => ({
        name: p['name'] ?? '',
        file: p['file'] ?? '',
    }));

    const provItems = extractYamlList(text, 'provisioners');
    const provisioners = provItems.map(p => ({
        name: p['name'] ?? '',
        provisioner: p['provisioner'] ?? '',
    }));

    const topologyItems = extractYamlList(text, 'topology');
    const topologyNames = topologyItems.map(t => t['name'] ?? '').filter(Boolean);

    const nsItems = extractYamlList(text, 'namespaces');
    const namespaceNames = nsItems.map(n => n['name'] ?? '').filter(Boolean);

    return { name, providers, provisioners, topologyNames, namespaceNames };
}

/** Resolve repository + source_path to an absolute filesystem path. */
function resolveSourcePath(
    repository: string,
    sourcePath: string,
    workPath: string,
    repos: readonly RepositoryInfo[],
): string | undefined {
    if (!sourcePath) return undefined;
    const base = (repository === '/' || repository === '')
        ? workPath
        : repos.find(r => r.name === repository)?.path;
    if (!base) return undefined;
    return path.join(base, sourcePath);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class DeploymentExplorerProvider
    implements vscode.TreeDataProvider<ExplorerItem>, vscode.Disposable {

    private readonly _onChange =
        new vscode.EventEmitter<ExplorerItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onChange.event;

    private _client: StrataClient | undefined;
    private _status: WorkspaceStatus | undefined;
    private _error: string | undefined;

    /** Parsed workspace manifest cache: wsFilePath → manifest (null = parse error) */
    private _wsCache = new Map<string, WorkspaceManifest | null>();
    /** Policies cache: deploymentFilePath → policies (null = load error) */
    private _policyCache = new Map<string, PolicyEntry[] | null>();

    private _deployCtx: DeploymentContext | undefined;
    private _ctxSub: vscode.Disposable | undefined;
    private _workPath = '';

    // ── Public API ────────────────────────────────────────────────────────────

    setClient(client: StrataClient): void { this._client = client; }

    setWorkPath(p: string): void { this._workPath = p; }

    setDeploymentContext(ctx: DeploymentContext): void {
        this._ctxSub?.dispose();
        this._deployCtx = ctx;
        this._ctxSub = ctx.onDidChange(() => {
            this._wsCache.clear();
            this._policyCache.clear();
            this._onChange.fire();
        });
    }

    update(status: WorkspaceStatus): void {
        this._status = status;
        this._error = undefined;
        this._wsCache.clear();
        this._policyCache.clear();
        this._onChange.fire();
    }

    setError(msg: string): void {
        this._status = undefined;
        this._error = msg;
        this._onChange.fire();
    }

    setLoading(): void { this._onChange.fire(); }

    dispose(): void {
        this._onChange.dispose();
        this._ctxSub?.dispose();
    }

    // ── vscode.TreeDataProvider ───────────────────────────────────────────────

    getTreeItem(element: ExplorerItem): vscode.TreeItem { return element; }

    getChildren(element?: ExplorerItem): vscode.ProviderResult<ExplorerItem[]> {
        if (!element) return this._buildRoot();

        switch (element.kind) {
            case 'deployment-active':
                return this._buildDeploymentChildren(element.data as string);
            case 'workspace-section':
                return this._buildWorkspaceChildren(element.data as { wsPath: string; repos: RepositoryInfo[] });
            case 'providers-group':
                return this._buildProviderItems(element.data as WorkspaceManifest & { wsDir: string; repos: RepositoryInfo[] });
            case 'provisioners-group':
                return this._buildProvisionerItems(element.data as WorkspaceManifest);
            case 'topology-group':
                return this._buildSimpleItems((element.data as { names: string[] }).names, 'topology-item', '$(type-hierarchy)');
            case 'namespaces-group':
                return this._buildSimpleItems((element.data as { names: string[] }).names, 'namespace-item', '$(layers)');
            case 'environments-section':
                return this._buildFileItems(
                    element.data as Array<{ name: string; filePath?: string }>,
                    'environment-item', '$(globe)',
                );
            case 'configurations-section':
                return this._buildFileItems(
                    element.data as Array<{ name: string; filePath?: string }>,
                    'configuration-item', '$(settings-gear)',
                );
            case 'policies-section':
                return this._buildPoliciesChildren(element.data as string);
            default:
                return [];
        }
    }

    // ── Root ──────────────────────────────────────────────────────────────────

    private _buildRoot(): ExplorerItem[] {
        if (this._error) return [this._err(this._error)];

        const items: ExplorerItem[] = [];
        const activeFile = this._deployCtx?.activeFile;
        const allDeployments = this._status?.profiles.paths['deployment'] ?? [];

        // Select prompt (always shown when nothing is active)
        if (!activeFile) {
            const prompt = new ExplorerItem(
                '$(search)  Select Deployment…',
                'select-prompt',
            );
            prompt.command = { command: 'strata.selectDeployment', title: 'Select Active Deployment' };
            prompt.tooltip = 'Choose a deployment to focus the Strata views on';
            items.push(prompt);
        }

        // Active deployment tree
        if (activeFile) {
            const name = this._deployCtx?.activeName ?? path.basename(activeFile, '.yaml');
            const active = new ExplorerItem(
                `$(cloud)  ${name}`,
                'deployment-active',
                vscode.TreeItemCollapsibleState.Expanded,
                activeFile,
            );
            active.description = vscode.workspace.asRelativePath(activeFile);
            active.tooltip = activeFile;
            active.iconPath = new vscode.ThemeIcon('cloud');
            active.command = { command: 'strata.openFile', title: 'Open File', arguments: [{ filePath: activeFile }] };
            items.push(active);
        }

        // Other deployments divider + list
        const others = allDeployments.filter(d => d.path !== activeFile);
        if (others.length > 0) {
            if (activeFile) {
                const divider = new ExplorerItem('── Other Deployments ──', 'other-divider');
                divider.description = '';
                items.push(divider);
            }
            for (const d of others) {
                const other = new ExplorerItem(
                    `$(cloud-outline)  ${d.name}`,
                    'deployment-other',
                    vscode.TreeItemCollapsibleState.None,
                    d.path,
                );
                other.description = vscode.workspace.asRelativePath(d.path);
                other.tooltip = 'Click to set as active deployment';
                other.command = {
                    command: 'strata.setActiveDeployment',
                    title: 'Set as Active',
                    arguments: [d.path],
                };
                items.push(other);
            }
        }

        // New file button
        const newBtn = new ExplorerItem('$(add)  New File…', 'new-file-prompt');
        newBtn.command = { command: 'strata.newFile', title: 'Create New Strata File' };
        newBtn.tooltip = 'Scaffold a new strata YAML file';
        items.push(newBtn);

        return items;
    }

    // ── Active deployment children ────────────────────────────────────────────

    private async _buildDeploymentChildren(filePath: string): Promise<ExplorerItem[]> {
        let manifest: DeploymentManifest;
        try {
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
            manifest = parseDeploymentManifest(doc.getText());
        } catch {
            return [this._err('Could not read deployment file')];
        }

        const repos = this._status?.repositories ?? [];
        const items: ExplorerItem[] = [];

        // Workspace section
        const wsFilePath = resolveSourcePath(
            manifest.workspaceSource.repository,
            manifest.workspaceSource.sourcePath,
            this._workPath,
            repos,
        );
        const wsLabel = manifest.workspaceName
            ? `$(package)  Workspace: ${manifest.workspaceName}`
            : '$(package)  Workspace';
        const wsSection = new ExplorerItem(
            wsLabel,
            'workspace-section',
            vscode.TreeItemCollapsibleState.Expanded,
            { wsPath: wsFilePath, repos },
        );
        wsSection.description = wsFilePath ? vscode.workspace.asRelativePath(wsFilePath) : '';
        wsSection.tooltip = wsFilePath;
        if (wsFilePath) {
            wsSection.iconPath = new vscode.ThemeIcon('package');
        }
        items.push(wsSection);

        // Environments
        const envItems = manifest.environments.map(e => ({
            name: e.name,
            filePath: resolveSourcePath(e.source.repository, e.source.sourcePath, this._workPath, repos),
        }));
        if (envItems.length > 0) {
            const envSection = new ExplorerItem(
                `$(globe)  Environments  (${envItems.length})`,
                'environments-section',
                vscode.TreeItemCollapsibleState.Collapsed,
                envItems,
            );
            items.push(envSection);
        }

        // Configurations
        const cfgItems = manifest.configurations.map(c => ({
            name: c.name,
            filePath: resolveSourcePath(c.source.repository, c.source.sourcePath, this._workPath, repos),
        }));
        if (cfgItems.length > 0) {
            const cfgSection = new ExplorerItem(
                `$(settings-gear)  Configurations  (${cfgItems.length})`,
                'configurations-section',
                vscode.TreeItemCollapsibleState.Collapsed,
                cfgItems,
            );
            items.push(cfgSection);
        }

        // Policies (lazy)
        const policiesSection = new ExplorerItem(
            '$(shield)  Policies',
            'policies-section',
            vscode.TreeItemCollapsibleState.Collapsed,
            filePath,
        );
        items.push(policiesSection);

        return items;
    }

    // ── Workspace section children ─────────────────────────────────────────────

    private async _buildWorkspaceChildren(
        data: { wsPath: string | undefined; repos: RepositoryInfo[] },
    ): Promise<ExplorerItem[]> {
        const { wsPath, repos } = data;

        if (!wsPath) {
            return [this._err('Workspace file path could not be resolved')];
        }

        // Check cache
        if (!this._wsCache.has(wsPath)) {
            try {
                const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(wsPath));
                this._wsCache.set(wsPath, parseWorkspaceManifest(doc.getText()));
            } catch {
                this._wsCache.set(wsPath, null);
            }
        }

        const manifest = this._wsCache.get(wsPath);
        if (!manifest) {
            return [this._err('Could not read workspace file')];
        }

        const wsDir = path.dirname(wsPath);
        const items: ExplorerItem[] = [];

        // Link to workspace file
        const fileLink = new ExplorerItem(
            `$(go-to-file)  Open ${path.basename(wsPath)}`,
            'provider-item',
        );
        fileLink.command = { command: 'strata.openFile', title: 'Open File', arguments: [{ filePath: wsPath }] };
        fileLink.description = vscode.workspace.asRelativePath(wsPath);
        items.push(fileLink);

        // Providers group
        if (manifest.providers.length > 0) {
            const provGroup = new ExplorerItem(
                `$(plug)  Providers  (${manifest.providers.length})`,
                'providers-group',
                vscode.TreeItemCollapsibleState.Collapsed,
                { ...manifest, wsDir, repos },
            );
            items.push(provGroup);
        }

        // Provisioners group
        if (manifest.provisioners.length > 0) {
            const provisionGroup = new ExplorerItem(
                `$(tools)  Provisioners  (${manifest.provisioners.length})`,
                'provisioners-group',
                vscode.TreeItemCollapsibleState.Collapsed,
                manifest,
            );
            items.push(provisionGroup);
        }

        // Topology group
        if (manifest.topologyNames.length > 0) {
            const topoGroup = new ExplorerItem(
                `$(type-hierarchy)  Topology  (${manifest.topologyNames.length})`,
                'topology-group',
                vscode.TreeItemCollapsibleState.Collapsed,
                { names: manifest.topologyNames },
            );
            items.push(topoGroup);
        }

        // Namespaces group
        if (manifest.namespaceNames.length > 0) {
            const nsGroup = new ExplorerItem(
                `$(layers)  Namespaces  (${manifest.namespaceNames.length})`,
                'namespaces-group',
                vscode.TreeItemCollapsibleState.Collapsed,
                { names: manifest.namespaceNames },
            );
            items.push(nsGroup);
        }

        return items;
    }

    // ── Group children ────────────────────────────────────────────────────────

    private _buildProviderItems(
        data: WorkspaceManifest & { wsDir: string; repos: RepositoryInfo[] },
    ): ExplorerItem[] {
        return data.providers.map(p => {
            const filePath = p.file ? path.join(data.wsDir, p.file) : undefined;
            const item = new ExplorerItem(
                `$(plug)  ${p.name}`,
                'provider-item',
                vscode.TreeItemCollapsibleState.None,
                filePath,
            );
            item.description = p.file;
            if (filePath) {
                item.command = { command: 'strata.openFile', title: 'Open', arguments: [{ filePath }] };
                item.tooltip = filePath;
            }
            return item;
        });
    }

    private _buildProvisionerItems(manifest: WorkspaceManifest): ExplorerItem[] {
        return manifest.provisioners.map(p => {
            const icon = p.provisioner === 'ansible' ? '$(terminal)' : '$(server)';
            const item = new ExplorerItem(
                `${icon}  ${p.provisioner}: ${p.name}`,
                'provisioner-item',
            );
            item.description = p.provisioner;
            return item;
        });
    }

    private _buildSimpleItems(
        names: string[],
        kind: ExplorerItemKind,
        icon: string,
    ): ExplorerItem[] {
        if (names.length === 0) {
            return [this._emptyItem('none defined')];
        }
        return names.map(name => new ExplorerItem(`${icon}  ${name}`, kind));
    }

    private _buildFileItems(
        items: Array<{ name: string; filePath?: string }>,
        kind: ExplorerItemKind,
        icon: string,
    ): ExplorerItem[] {
        return items.map(e => {
            const item = new ExplorerItem(
                `${icon}  ${e.name}`,
                kind,
                vscode.TreeItemCollapsibleState.None,
                e.filePath,
            );
            if (e.filePath) {
                item.description = vscode.workspace.asRelativePath(e.filePath);
                item.command = { command: 'strata.openFile', title: 'Open', arguments: [{ filePath: e.filePath }] };
                item.tooltip = e.filePath;
            } else {
                item.description = '(path unresolved)';
            }
            return item;
        });
    }

    private async _buildPoliciesChildren(deployFilePath: string): Promise<ExplorerItem[]> {
        if (!this._client) return [this._loadingItem()];

        if (!this._policyCache.has(deployFilePath)) {
            try {
                const data = await this._client.listPolicies(deployFilePath);
                this._policyCache.set(deployFilePath, data.policies);
            } catch {
                this._policyCache.set(deployFilePath, null);
            }
        }

        const policies = this._policyCache.get(deployFilePath);
        if (!policies) return [this._emptyItem('could not load policies')];
        if (policies.length === 0) return [this._emptyItem('no policies defined')];

        return policies.filter(p => p.enabled).map(p => {
            const icon = p.enforcement === 'deny' ? '$(error)' : '$(warning)';
            const item = new ExplorerItem(
                `${icon}  ${p.name}`,
                'policy-item',
            );
            item.description = `${p.type} · ${p.enforcement}`;
            return item;
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private _loadingItem(): ExplorerItem {
        const item = new ExplorerItem('Loading…', 'loading');
        item.iconPath = new vscode.ThemeIcon('sync~spin');
        return item;
    }

    private _err(msg: string): ExplorerItem {
        const item = new ExplorerItem(msg, 'error');
        item.iconPath = new vscode.ThemeIcon('error');
        return item;
    }

    private _emptyItem(msg: string): ExplorerItem {
        const item = new ExplorerItem(msg, 'empty');
        item.iconPath = new vscode.ThemeIcon('info');
        return item;
    }
}
