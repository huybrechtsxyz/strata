/**
 * DiagramBuilderProvider — Visual Builder for `kind: diagram` definitions
 * (ADR-0034 Phase 4).
 *
 * Scope (see ADR-0034 "Phase 4: Generators", clarified 2026-08-24): composes
 * **one** diagram (sources → layout/style → live preview) against **one**
 * context. No composition/comparison/dashboard mode — that's Phase 5, and
 * "composed diagrams" already works today via multiple `sources` entries
 * (proven by the built-in `network`/`architecture` diagrams), which this
 * Builder exposes directly as "add another source".
 *
 * This provider only ever emits the Phase 1 sugar (`spec.sources` +
 * `spec.layout` + `spec.style`) — never a hand-written `spec.template`. A
 * definition that already has one can't be round-tripped into the Builder
 * (see `open()`); the escape route is `strata diagram show --print-template`,
 * same as the ADR specifies.
 *
 * Live preview reuses `DiagramPreviewProvider` wholesale (staging the current
 * form state to a temp file and calling `.show()` on it) rather than
 * reimplementing Mermaid rendering/theming/click-handling a second time —
 * that logic is intricate (see the nested-template-literal note in
 * `diagramPreviewProvider.ts`) and duplicating it would only add risk.
 *
 * Validation happens automatically as a side effect: `StrataClient.showDiagram()`
 * always runs the definition through `DiagramService.validate()` (ADR-0034
 * Phase 1/1.5/2 checks, including `--deep` link-rot checking when applicable)
 * before rendering — there is no separate "validate" round trip to build here.
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import { StrataCLIError, type StrataClient, type DiagramSourceSpec, type DiagramLayoutSpec, type DiagramStyleSpec } from '../strataClient';
import { DiagramPreviewProvider } from './diagramPreviewProvider';

/** `DiagramSourceType` values (src/strata/models/diagram_model.py) — the closed vocabulary the sources picker offers. */
export const SOURCE_TYPES = [
    'topology', 'files', 'resources', 'modules', 'namespaces', 'stages', 'promotion', 'network',
    'firewalls', 'dns', 'secrets', 'variables', 'features', 'drift', 'history', 'policies',
    'tenants', 'environments', 'repositories', 'sbom', 'approvals', 'locks', 'outputs', 'values',
] as const;

/** `MermaidDiagramType` values. */
export const LAYOUT_TYPES = ['flowchart', 'sequence', 'gantt', 'pie', 'mindmap', 'class', 'stateDiagram', 'timeline', 'quadrant', 'sankey'] as const;

/** `MermaidDirection` values — only meaningful for node/edge layout types, but harmless to always offer. */
export const DIRECTIONS = ['TD', 'LR', 'BT', 'RL'] as const;

/** Builder's in-memory form state — maps 1:1 onto `DiagramSpecModel` (sources/layout/style), the Phase 1 sugar. */
export interface BuilderState {
    name: string;
    description: string;
    sources: DiagramSourceSpec[];
    layout: DiagramLayoutSpec;
    style: DiagramStyleSpec;
}

type HostMessage =
    | { type: 'preview'; state: BuilderState }
    | { type: 'save'; state: BuilderState }
    | { type: 'exportMermaid'; state: BuilderState }
    | { type: 'exportImage'; state: BuilderState; format: 'svg' | 'png' };

function emptyState(): BuilderState {
    return { name: '', description: '', sources: [], layout: { type: 'flowchart', direction: 'TD' }, style: {} };
}

/** Quote a YAML scalar only when it isn't a plain unquoted-safe token. */
function yamlScalar(value: string): string {
    if (value === '') return "''";
    if (/^[A-Za-z0-9_./-]+$/.test(value) && !/^(true|false|null|~|yes|no)$/i.test(value)) return value;
    return `'${value.replace(/'/g, "''")}'`;
}

/**
 * Serialize a `BuilderState` to a `kind: diagram` YAML document. Deliberately
 * bespoke (not a generic YAML dumper) — the shape emitted here is exactly and
 * only the Phase 1 sugar, which keeps this trivially auditable.
 */
function buildDiagramYaml(state: BuilderState): string {
    const lines: string[] = ['apiVersion: strata.huybrechts.xyz/v1', 'kind: diagram', 'meta:', `  name: ${yamlScalar(state.name || 'builder-preview')}`];

    if (state.description.trim()) {
        lines.push('  annotations:', `    description: ${yamlScalar(state.description.trim())}`);
    }

    lines.push('spec:');

    if (state.sources.length > 0) {
        lines.push('  sources:');
        for (const s of state.sources) {
            lines.push(`    - type: ${yamlScalar(s.type)}`);
            if (s.as) lines.push(`      as: ${yamlScalar(s.as)}`);
            const filterEntries = Object.entries(s.filter ?? {}).filter(([k, v]) => k.trim() && String(v).trim());
            if (filterEntries.length > 0) {
                lines.push('      filter:');
                for (const [k, v] of filterEntries) {
                    lines.push(`        ${yamlScalar(k)}: ${yamlScalar(String(v))}`);
                }
            }
        }
    }

    lines.push('  layout:', `    type: ${yamlScalar(state.layout.type || 'flowchart')}`);
    if (state.layout.direction) lines.push(`    direction: ${yamlScalar(state.layout.direction)}`);

    const highlights = (state.style.highlight ?? []).filter((h) => h.condition.trim() && h.token.trim());
    const hasStyle = !!state.style.color_by?.trim() || !!state.style.group_by?.trim() || highlights.length > 0;
    if (hasStyle) {
        lines.push('  style:');
        if (state.style.color_by?.trim()) lines.push(`    color_by: ${yamlScalar(state.style.color_by.trim())}`);
        if (state.style.group_by?.trim()) lines.push(`    group_by: ${yamlScalar(state.style.group_by.trim())}`);
        if (highlights.length > 0) {
            lines.push('    highlight:');
            for (const h of highlights) {
                lines.push(`      - condition: ${yamlScalar(h.condition.trim())}`, `        token: ${yamlScalar(h.token.trim())}`);
            }
        }
    }

    return lines.join('\n') + '\n';
}

function extractErrors(err: unknown): string[] {
    if (err instanceof StrataCLIError) return err.response?.errors?.length ? err.response.errors : [err.message];
    return [err instanceof Error ? err.message : String(err)];
}

/**
 * Validate a `BuilderState` (stage to a throwaway temp file, run it through
 * `StrataClient.showDiagram()` which always calls `DiagramService.validate()`)
 * without opening any UI. Used by the chat `/diagram create` NL-generation
 * flow (ADR-0034 Phase 4) to validate an LLM proposal — with a one-retry
 * repair loop — before ever showing it to the user.
 */
export async function validateBuilderState(
    client: StrataClient,
    state: BuilderState,
): Promise<{ ok: true } | { ok: false; errors: string[] }> {
    const tempPath = path.join(os.tmpdir(), `strata-diagram-validate-${process.pid}-${Date.now()}.yaml`);
    await vscode.workspace.fs.writeFile(vscode.Uri.file(tempPath), Buffer.from(buildDiagramYaml(state), 'utf-8'));
    try {
        await client.showDiagram(tempPath);
        return { ok: true };
    } catch (err) {
        return { ok: false, errors: extractErrors(err) };
    } finally {
        fs.rm(tempPath, { force: true }, () => { /* best-effort cleanup */ });
    }
}

export class DiagramBuilderProvider implements vscode.Disposable {
    static readonly viewType = 'strataDiagramBuilder';

    private _panel: vscode.WebviewPanel | undefined;
    private _client: StrataClient | undefined;
    private readonly _preview: DiagramPreviewProvider;
    private readonly _tempPath: string;
    /** Absolute path of the definition currently being edited, once saved/loaded — lets "Save" overwrite in place instead of re-prompting. */
    private _savedPath: string | undefined;

    constructor(preview: DiagramPreviewProvider) {
        this._preview = preview;
        this._tempPath = path.join(os.tmpdir(), `strata-diagram-builder-${process.pid}-${Date.now()}.yaml`);
    }

    /**
     * Open the Builder, blank or pre-loaded from an existing definition
     * (`nameOrPath` — a built-in name, a `.strata/diagrams/` name, or a path).
     * Declines to open (with an explanatory message) when the definition has
     * a hand-written `spec.template` — see the round-trip note in the header.
     */
    async open(client: StrataClient, nameOrPath?: string): Promise<void> {
        this._client = client;
        let initial = emptyState();
        this._savedPath = undefined;

        if (nameOrPath) {
            try {
                const data = await client.showDiagram(nameOrPath);
                if (data.has_template) {
                    void vscode.window.showWarningMessage(
                        `Strata: "${data.diagram}" has a hand-written template and can't be edited visually. ` +
                        `Run "strata diagram show -f ${nameOrPath} --print-template" to get its Jinja source instead.`,
                    );
                    return;
                }
                initial = {
                    name: data.diagram,
                    description: '',
                    sources: data.sources,
                    layout: data.layout ?? { type: 'flowchart', direction: 'TD' },
                    style: data.style ?? {},
                };
                this._savedPath = data.definition;
            } catch (err) {
                void vscode.window.showErrorMessage(`Strata: could not load "${nameOrPath}": ${extractErrors(err).join('; ')}`);
                return;
            }
        }

        if (!this._panel) {
            this._panel = vscode.window.createWebviewPanel(
                DiagramBuilderProvider.viewType,
                'Strata: Diagram Builder',
                vscode.ViewColumn.One,
                { enableScripts: true, retainContextWhenHidden: true },
            );
            this._panel.onDidDispose(() => { this._panel = undefined; });
            this._panel.webview.onDidReceiveMessage((msg: HostMessage) => void this._onMessage(msg));
        } else {
            this._panel.reveal(vscode.ViewColumn.One);
        }

        this._panel.webview.html = this._html(initial);
    }

    /**
     * Load a Builder state generated elsewhere (e.g. the `/diagram create`
     * chat command's NL→sources/layout/style extraction) directly, bypassing
     * the CLI round trip `open()` does for existing files.
     */
    async openWithState(client: StrataClient, state: BuilderState): Promise<void> {
        this._client = client;
        this._savedPath = undefined;
        if (!this._panel) {
            this._panel = vscode.window.createWebviewPanel(
                DiagramBuilderProvider.viewType,
                'Strata: Diagram Builder',
                vscode.ViewColumn.One,
                { enableScripts: true, retainContextWhenHidden: true },
            );
            this._panel.onDidDispose(() => { this._panel = undefined; });
            this._panel.webview.onDidReceiveMessage((msg: HostMessage) => void this._onMessage(msg));
        } else {
            this._panel.reveal(vscode.ViewColumn.One);
        }
        this._panel.webview.html = this._html(state);
    }

    dispose(): void {
        this._panel?.dispose();
        fs.rm(this._tempPath, { force: true }, () => { /* best-effort cleanup */ });
    }

    // ── Message handling ──────────────────────────────────────────────────────

    private async _onMessage(msg: HostMessage): Promise<void> {
        if (!this._client) return;
        if (msg.type === 'preview') await this._previewState(msg.state);
        else if (msg.type === 'save') await this._saveState(msg.state);
        else if (msg.type === 'exportMermaid') await this._exportMermaid(msg.state);
        else if (msg.type === 'exportImage') await this._exportImage(msg.state, msg.format);
    }

    private async _stage(state: BuilderState): Promise<void> {
        await vscode.workspace.fs.writeFile(vscode.Uri.file(this._tempPath), Buffer.from(buildDiagramYaml(state), 'utf-8'));
    }

    private async _previewState(state: BuilderState): Promise<void> {
        const client = this._client!;
        await this._stage(state);
        try {
            await client.showDiagram(this._tempPath);
            void this._panel?.webview.postMessage({ type: 'previewResult', ok: true });
            await this._preview.show(client, this._tempPath);
        } catch (err) {
            void this._panel?.webview.postMessage({ type: 'previewResult', ok: false, errors: extractErrors(err) });
        }
    }

    private async _saveState(state: BuilderState): Promise<void> {
        const client = this._client!;
        if (!/^[a-z][a-z0-9_-]*$/.test(state.name)) {
            void this._panel?.webview.postMessage({
                type: 'saveResult', ok: false,
                errors: ["Name must start with a lowercase letter and contain only lowercase letters, digits, '_' or '-'."],
            });
            return;
        }

        await this._stage(state);
        try {
            await client.showDiagram(this._tempPath); // validates via DiagramService.validate()
        } catch (err) {
            void this._panel?.webview.postMessage({ type: 'saveResult', ok: false, errors: extractErrors(err) });
            return;
        }

        const targetPath = path.join(client.getWorkPath(), '.strata', 'diagrams', `${state.name}.yaml`);
        const isSameFile = this._savedPath && path.resolve(this._savedPath) === path.resolve(targetPath);
        if (!isSameFile && fs.existsSync(targetPath)) {
            const choice = await vscode.window.showWarningMessage(
                `"${state.name}.yaml" already exists in .strata/diagrams/. Overwrite it?`,
                { modal: true },
                'Overwrite',
            );
            if (choice !== 'Overwrite') {
                void this._panel?.webview.postMessage({ type: 'saveResult', ok: false, errors: ['Save cancelled.'] });
                return;
            }
        }

        await vscode.workspace.fs.createDirectory(vscode.Uri.file(path.dirname(targetPath)));
        await vscode.workspace.fs.writeFile(vscode.Uri.file(targetPath), Buffer.from(buildDiagramYaml(state), 'utf-8'));
        this._savedPath = targetPath;

        void this._panel?.webview.postMessage({ type: 'saveResult', ok: true, path: vscode.workspace.asRelativePath(targetPath) });
        void vscode.commands.executeCommand('strata.refreshDiagrams');
        void vscode.window.showInformationMessage(`Strata: saved diagram to ${vscode.workspace.asRelativePath(targetPath)}`);
    }

    private async _exportMermaid(state: BuilderState): Promise<void> {
        const client = this._client!;
        await this._stage(state);
        try {
            const data = await client.showDiagram(this._tempPath);
            await vscode.env.clipboard.writeText(data.mermaid);
            void this._panel?.webview.postMessage({ type: 'exportResult', ok: true });
            void vscode.window.showInformationMessage('Strata: Mermaid source copied to clipboard — paste it into a README, wiki, or GitHub markdown.');
        } catch (err) {
            void this._panel?.webview.postMessage({ type: 'exportResult', ok: false, errors: extractErrors(err) });
        }
    }

    /**
     * Export the current state as SVG/PNG, produced entirely client-side by
     * the preview webview from what it has already rendered (see
     * `DiagramPreviewProvider.requestExport()`) — no Kroki/network round trip.
     * `strata diagram show --format svg|png` (Kroki) remains the equivalent
     * for headless/CI use with no VS Code involved at all.
     */
    private async _exportImage(state: BuilderState, format: 'svg' | 'png'): Promise<void> {
        const client = this._client!;
        await this._previewState(state); // ensures the preview panel is showing this exact state
        const { data, error } = await this._preview.requestExport(format);
        if (error || !data) {
            void this._panel?.webview.postMessage({ type: 'exportResult', ok: false, errors: [error ?? 'Export failed.'] });
            return;
        }

        const defaultName = `${state.name || 'diagram'}.${format}`;
        const target = await vscode.window.showSaveDialog({
            defaultUri: vscode.Uri.file(path.join(client.getWorkPath(), defaultName)),
            filters: format === 'svg' ? { 'SVG image': ['svg'] } : { 'PNG image': ['png'] },
        });
        if (!target) {
            void this._panel?.webview.postMessage({ type: 'exportResult', ok: false, errors: ['Export cancelled.'] });
            return;
        }

        const bytes = format === 'svg' ? Buffer.from(data, 'utf-8') : Buffer.from(data.split(',')[1] ?? '', 'base64');
        await vscode.workspace.fs.writeFile(target, bytes);
        void this._panel?.webview.postMessage({ type: 'exportResult', ok: true });
        void vscode.window.showInformationMessage(`Strata: exported ${format.toUpperCase()} to ${vscode.workspace.asRelativePath(target)}`);
    }

    // ── Webview HTML ──────────────────────────────────────────────────────────

    private _html(initial: BuilderState): string {
        const initialJson = JSON.stringify(initial).replace(/</g, '\\u003c');
        const sourceTypesJson = JSON.stringify(SOURCE_TYPES);
        const layoutTypesJson = JSON.stringify(LAYOUT_TYPES);
        const directionsJson = JSON.stringify(DIRECTIONS);

        // Plain string concatenation (no template literal) for the embedded
        // script, matching diagramPreviewProvider.ts's precaution — see that
        // file's header comment on why nested template literals are unsafe here.
        const script = [
            "const vscode = acquireVsCodeApi();",
            "const SOURCE_TYPES = " + sourceTypesJson + ";",
            "const LAYOUT_TYPES = " + layoutTypesJson + ";",
            "const DIRECTIONS = " + directionsJson + ";",
            "let state = " + initialJson + ";",
            "",
            "function el(tag, props, children) {",
            "  const e = document.createElement(tag);",
            "  Object.assign(e, props || {});",
            "  (children || []).forEach(function (c) { e.appendChild(c); });",
            "  return e;",
            "}",
            "",
            "function optionList(select, values, current) {",
            "  select.innerHTML = '';",
            "  values.forEach(function (v) {",
            "    const o = el('option', { value: v, textContent: v });",
            "    if (v === current) o.selected = true;",
            "    select.appendChild(o);",
            "  });",
            "}",
            "",
            "function renderSources() {",
            "  const list = document.getElementById('sources-list');",
            "  list.innerHTML = '';",
            "  state.sources.forEach(function (src, i) {",
            "    const row = el('div', { className: 'row source-row' });",
            "    const typeSel = el('select', { className: 'source-type' });",
            "    optionList(typeSel, SOURCE_TYPES, src.type);",
            "    typeSel.addEventListener('change', function () { state.sources[i].type = typeSel.value; });",
            "    const asInput = el('input', { type: 'text', placeholder: 'bind name (optional)', value: src.as || '' });",
            "    asInput.addEventListener('input', function () { state.sources[i].as = asInput.value || undefined; });",
            "    const removeBtn = el('button', { textContent: '\\u2715', className: 'icon-btn', title: 'Remove source' });",
            "    removeBtn.addEventListener('click', function () { state.sources.splice(i, 1); renderSources(); });",
            "    row.appendChild(typeSel); row.appendChild(asInput); row.appendChild(removeBtn);",
            "    list.appendChild(row);",
            "  });",
            "}",
            "",
            "function renderHighlights() {",
            "  const list = document.getElementById('highlight-list');",
            "  list.innerHTML = '';",
            "  (state.style.highlight || []).forEach(function (h, i) {",
            "    const row = el('div', { className: 'row' });",
            "    const cond = el('input', { type: 'text', placeholder: \"condition, e.g. drift.severity == 'critical'\", value: h.condition });",
            "    cond.addEventListener('input', function () { state.style.highlight[i].condition = cond.value; });",
            "    const token = el('input', { type: 'text', placeholder: 'token, e.g. critical', value: h.token });",
            "    token.addEventListener('input', function () { state.style.highlight[i].token = token.value; });",
            "    const removeBtn = el('button', { textContent: '\\u2715', className: 'icon-btn', title: 'Remove rule' });",
            "    removeBtn.addEventListener('click', function () { state.style.highlight.splice(i, 1); renderHighlights(); });",
            "    row.appendChild(cond); row.appendChild(token); row.appendChild(removeBtn);",
            "    list.appendChild(row);",
            "  });",
            "}",
            "",
            "function setStatus(text, isError) {",
            "  const s = document.getElementById('status');",
            "  s.textContent = text || '';",
            "  s.className = isError ? 'status error' : 'status ok';",
            "}",
            "",
            "function collect() {",
            "  return {",
            "    name: document.getElementById('name').value.trim(),",
            "    description: document.getElementById('description').value,",
            "    sources: state.sources,",
            "    layout: { type: document.getElementById('layout-type').value, direction: document.getElementById('layout-direction').value },",
            "    style: {",
            "      color_by: document.getElementById('color-by').value || undefined,",
            "      group_by: document.getElementById('group-by').value || undefined,",
            "      highlight: (state.style.highlight || []).filter(function (h) { return h.condition && h.token; }),",
            "    },",
            "  };",
            "}",
            "",
            "document.getElementById('add-source').addEventListener('click', function () {",
            "  state.sources.push({ type: SOURCE_TYPES[0] });",
            "  renderSources();",
            "});",
            "document.getElementById('add-highlight').addEventListener('click', function () {",
            "  state.style.highlight = state.style.highlight || [];",
            "  state.style.highlight.push({ condition: '', token: '' });",
            "  renderHighlights();",
            "});",
            "document.getElementById('preview-btn').addEventListener('click', function () {",
            "  setStatus('Rendering preview\\u2026', false);",
            "  vscode.postMessage({ type: 'preview', state: collect() });",
            "});",
            "document.getElementById('save-btn').addEventListener('click', function () {",
            "  setStatus('Validating and saving\\u2026', false);",
            "  vscode.postMessage({ type: 'save', state: collect() });",
            "});",
            "document.getElementById('export-btn').addEventListener('click', function () {",
            "  setStatus('Rendering for export\\u2026', false);",
            "  vscode.postMessage({ type: 'exportMermaid', state: collect() });",
            "});",
            "document.getElementById('export-svg-btn').addEventListener('click', function () {",
            "  setStatus('Rendering SVG\\u2026', false);",
            "  vscode.postMessage({ type: 'exportImage', format: 'svg', state: collect() });",
            "});",
            "document.getElementById('export-png-btn').addEventListener('click', function () {",
            "  setStatus('Rendering PNG\\u2026', false);",
            "  vscode.postMessage({ type: 'exportImage', format: 'png', state: collect() });",
            "});",
            "",
            "window.addEventListener('message', function (event) {",
            "  const msg = event.data;",
            "  if (msg.type === 'previewResult') {",
            "    setStatus(msg.ok ? 'Preview rendered.' : 'Preview failed: ' + msg.errors.join('; '), !msg.ok);",
            "  } else if (msg.type === 'saveResult') {",
            "    setStatus(msg.ok ? 'Saved to ' + msg.path + '.' : msg.errors.join('; '), !msg.ok);",
            "  } else if (msg.type === 'exportResult') {",
            "    setStatus(msg.ok ? 'Export complete.' : 'Export failed: ' + msg.errors.join('; '), !msg.ok);",
            "  }",
            "});",
            "",
            "optionList(document.getElementById('layout-type'), LAYOUT_TYPES, state.layout.type);",
            "optionList(document.getElementById('layout-direction'), DIRECTIONS, state.layout.direction);",
            "document.getElementById('color-by').value = state.style.color_by || '';",
            "document.getElementById('group-by').value = state.style.group_by || '';",
            "renderSources();",
            "renderHighlights();",
        ].join('\n');

        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-editor-foreground); background: var(--vscode-editor-background); padding: 16px; }
  h2 { margin-top: 0; }
  fieldset { border: 1px solid var(--vscode-panel-border); border-radius: 4px; margin-bottom: 16px; }
  legend { padding: 0 6px; opacity: 0.8; }
  label { display: block; margin: 8px 0 4px; opacity: 0.85; }
  input[type="text"], textarea, select { width: 100%; box-sizing: border-box; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, transparent); border-radius: 2px; padding: 4px 6px; }
  .row { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; }
  .row > * { flex: 1; }
  .icon-btn { flex: 0 0 auto; background: transparent; border: none; color: var(--vscode-descriptionForeground); cursor: pointer; }
  button.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 6px 14px; border-radius: 2px; cursor: pointer; margin-right: 8px; }
  button.primary:hover { background: var(--vscode-button-hoverBackground); }
  .status { margin-top: 12px; min-height: 1.2em; }
  .status.error { color: var(--vscode-errorForeground); }
  .status.ok { color: var(--vscode-charts-green, var(--vscode-descriptionForeground)); }
  .two-col { display: flex; gap: 16px; }
  .two-col > div { flex: 1; }
</style>
</head>
<body>
  <h2>Strata: Diagram Builder</h2>

  <label for="name">Name</label>
  <input id="name" type="text" value="${escapeHtmlAttr(initial.name)}" placeholder="e.g. my-view">

  <label for="description">Description</label>
  <input id="description" type="text" value="${escapeHtmlAttr(initial.description)}" placeholder="optional">

  <fieldset>
    <legend>Sources</legend>
    <div id="sources-list"></div>
    <button class="primary" id="add-source">+ Add source</button>
  </fieldset>

  <fieldset>
    <legend>Layout</legend>
    <div class="two-col">
      <div><label for="layout-type">Diagram type</label><select id="layout-type"></select></div>
      <div><label for="layout-direction">Direction</label><select id="layout-direction"></select></div>
    </div>
  </fieldset>

  <fieldset>
    <legend>Style</legend>
    <div class="two-col">
      <div><label for="color-by">Color by</label><input id="color-by" type="text" placeholder="e.g. status"></div>
      <div><label for="group-by">Group by</label><input id="group-by" type="text" placeholder="e.g. namespace"></div>
    </div>
    <label>Highlight rules</label>
    <div id="highlight-list"></div>
    <button class="primary" id="add-highlight">+ Add highlight rule</button>
  </fieldset>

  <button class="primary" id="preview-btn">Preview</button>
  <button class="primary" id="save-btn">Save</button>
  <button class="primary" id="export-btn">Copy Mermaid</button>
  <button class="primary" id="export-svg-btn">Export SVG</button>
  <button class="primary" id="export-png-btn">Export PNG</button>
  <div class="status" id="status"></div>

  <script>${script}</script>
</body>
</html>`;
    }
}

function escapeHtmlAttr(value: string): string {
    return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}
