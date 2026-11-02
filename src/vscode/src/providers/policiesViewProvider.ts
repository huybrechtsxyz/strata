/**
 * policiesViewProvider.ts - the Policies tree view
 * (docs/work/vscode-integrations-policies-views.md V4, V5).
 *
 * One row per `strata policy list D` row for the selected deployment. The
 * icon is the last recorded result. "Check" runs `strata policy check`;
 * "Show findings" (only where the policy has findings) runs
 * `strata policy findings` and lists them as child rows - grouped per
 * target/image, at most MAX_ROWS per group plus a "+N more" row.
 */

import * as vscode from 'vscode';
import { FindingsData, PolicyRow, StrataClient } from '../strataClient';

const MAX_ROWS = 10;

type Node = PolicyItem | GroupItem | DetailItem;

export class PolicyItem extends vscode.TreeItem {
    constructor(
        readonly row: PolicyRow,
        hasFindings: boolean,
    ) {
        super(row.name, hasFindings ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None);
        this.description = `${row.phase} - ${row.enforcement}`;
        this.iconPath = resultIcon(row);
        this.tooltip = policyTooltip(row);
        this.contextValue = ['policy', row.check ? 'check' : '', row.findings ? 'findings' : ''].filter(Boolean).join(';');
    }
}

class GroupItem extends vscode.TreeItem {
    constructor(
        label: string,
        description: string,
        readonly children: DetailItem[],
    ) {
        super(label, children.length ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None);
        this.description = description;
        this.iconPath = new vscode.ThemeIcon('package');
    }
}

class DetailItem extends vscode.TreeItem {
    constructor(label: string, description?: string, icon?: vscode.ThemeIcon, link?: string | null) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.description = description;
        this.iconPath = icon;
        this.tooltip = [label, description, link].filter(Boolean).join('\n');
        if (link) this.command = { command: 'vscode.open', title: 'Open Issue', arguments: [vscode.Uri.parse(link)] };
    }
}

function resultIcon(row: PolicyRow): vscode.ThemeIcon {
    const result = row.last_result;
    if (!result) return new vscode.ThemeIcon('circle-outline');
    if (result.passed) return new vscode.ThemeIcon('pass', new vscode.ThemeColor('testing.iconPassed'));
    if (row.enforcement === 'deny') return new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground'));
    if (row.enforcement === 'warn') return new vscode.ThemeIcon('warning', new vscode.ThemeColor('problemsWarningIcon.foreground'));
    return new vscode.ThemeIcon('info');
}

function policyTooltip(row: PolicyRow): vscode.MarkdownString {
    const lines = [`**${row.name}** - ${row.phase}, ${row.enforcement}`, '', `backed by ${row.backing}`];
    const result = row.last_result;
    lines.push('', result ? `last result: ${result.passed ? 'passed' : 'failed'}` : 'not evaluated yet');
    for (const violation of result?.violations ?? []) lines.push(`- ${violation}`);
    return new vscode.MarkdownString(lines.join('\n'));
}

function severityIcon(severity: string): vscode.ThemeIcon {
    const upper = severity.toUpperCase();
    if (upper === 'CRITICAL' || upper === 'HIGH') return new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground'));
    if (upper === 'MEDIUM') return new vscode.ThemeIcon('warning', new vscode.ThemeColor('problemsWarningIcon.foreground'));
    return new vscode.ThemeIcon('info');
}

function capped(rows: DetailItem[]): DetailItem[] {
    if (rows.length <= MAX_ROWS) return rows;
    return [...rows.slice(0, MAX_ROWS), new DetailItem(`+${rows.length - MAX_ROWS} more`, 'strata policy findings --output json lists all')];
}

function marker(flag: boolean, text: string): string {
    return flag ? ` [${text}]` : '';
}

/** Turn any of the three `policy findings` shapes into groups of rows. */
export function findingGroups(data: FindingsData): GroupItem[] {
    if ('images' in data) {
        return data.images.map((image) => {
            const rows = image.issues.map(
                (issue) =>
                    new DetailItem(
                        `${issue.severity} ${issue.cve_id ?? issue.id}`,
                        `${issue.package ?? '?'}${marker(issue.allowlisted, 'allowlisted')}`,
                        severityIcon(issue.severity),
                        issue.link,
                    ),
            );
            const status = image.status === 'found' ? `${image.issues.length} issue(s)` : image.status;
            return new GroupItem(image.image, image.detail ? `${status} (${image.detail})` : status, capped(rows));
        });
    }
    if ('targets' in data) {
        return data.targets.map((target) => {
            const rows = target.findings.map((finding) => {
                const lines = finding.file_line_range ?? [];
                const where = lines.length ? `${finding.file_path}:${lines[0]}` : finding.file_path;
                return new DetailItem(
                    `${finding.severity} ${finding.check_id}`,
                    `${finding.resource} at ${where}${marker(finding.unrated, 'unrated')}`,
                    severityIcon(finding.severity),
                );
            });
            const status = target.missing ?? `${target.findings.length} finding(s)`;
            return new GroupItem(target.target, status, capped(rows));
        });
    }
    const rows = data.findings.map((finding) => {
        const fixed = finding.fixed_version ? ` (fixed in ${finding.fixed_version})` : '';
        return new DetailItem(
            `${finding.severity} ${finding.vulnerability_id}`,
            `${finding.package_name} ${finding.installed_version}${fixed}${marker(finding.allowlisted, 'allowlisted')}`,
            severityIcon(finding.severity),
        );
    });
    return [new GroupItem(`${data.scanner} ${data.scanner_version}`, `${data.findings.length} finding(s)`, capped(rows))];
}

export class PoliciesViewProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
    private readonly _onDidChange = new vscode.EventEmitter<Node | undefined>();
    readonly onDidChangeTreeData = this._onDidChange.event;
    private _rows: PolicyRow[] = [];
    private _message: string | undefined;
    private readonly _findings = new Map<string, GroupItem[] | DetailItem[]>();

    constructor(
        private readonly _client: StrataClient,
        private readonly _deployment: () => string | undefined,
    ) { }

    async refresh(): Promise<void> {
        this._findings.clear();
        const deployment = this._deployment();
        if (!deployment) {
            this._rows = [];
            this._message = undefined;
        } else {
            try {
                const envelope = await this._client.policyList(deployment);
                this._rows = envelope.data?.policies ?? [];
                const first = envelope.diagnostics.find((d) => d.severity === 'error');
                this._message = envelope.ok ? undefined : first?.message ?? 'strata policy list failed';
            } catch (err) {
                this._rows = [];
                this._message = (err as Error).message;
            }
        }
        this._onDidChange.fire(undefined);
    }

    /** Load one policy's findings and show them under its row. */
    async showFindings(policy: string): Promise<void> {
        const deployment = this._deployment();
        if (!deployment) return;
        let envelope;
        try {
            envelope = await this._client.policyFindings(deployment, policy);
        } catch (err) {
            this._findings.set(policy, [new DetailItem((err as Error).message, undefined, new vscode.ThemeIcon('warning'))]);
            this._onDidChange.fire(undefined);
            return;
        }
        if (envelope.ok && envelope.data) {
            this._findings.set(policy, findingGroups(envelope.data));
        } else {
            const messages = envelope.diagnostics.filter((d) => d.severity !== 'info').map((d) => d.message);
            this._findings.set(
                policy,
                (messages.length ? messages : ['no findings available']).map((m) => new DetailItem(m, undefined, new vscode.ThemeIcon('warning'))),
            );
        }
        this._onDidChange.fire(undefined);
    }

    getTreeItem(element: Node): vscode.TreeItem {
        return element;
    }

    getChildren(element?: Node): Node[] {
        if (!element) {
            if (!this._deployment()) {
                const item = new DetailItem('Select a deployment', undefined, new vscode.ThemeIcon('rocket'));
                item.command = { command: 'strata.selectDeployment', title: 'Select Deployment' };
                return [item];
            }
            if (this._message) return [new DetailItem(this._message, undefined, new vscode.ThemeIcon('warning'))];
            if (!this._rows.length) return [new DetailItem('No policies configured', undefined, new vscode.ThemeIcon('info'))];
            return this._rows.map((row) => new PolicyItem(row, this._findings.has(row.name)));
        }
        if (element instanceof PolicyItem) return this._findings.get(element.row.name) ?? [];
        if (element instanceof GroupItem) return element.children;
        return [];
    }

    dispose(): void {
        this._onDidChange.dispose();
    }
}
