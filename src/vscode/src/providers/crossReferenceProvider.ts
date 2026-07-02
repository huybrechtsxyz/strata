/**
 * CrossReferenceProvider — VS Code language feature provider for strata
 * cross-file references in the form `@repo_name/path/to/file.yaml`.
 *
 * Implements three language features:
 *
 *   DocumentLinkProvider  — Ctrl+Click opens the referenced file
 *   HoverProvider         — hover shows the resolved absolute path + file kind
 *   RenameProvider        — F2 propagates the rename across all YAML files
 *                           in the workspace
 *
 * Resolver logic:
 *   @repo_name/some/path.yaml
 *   → look up `repo_name` in the repository list from `sln status`
 *   → join the repo's local `path` with `some/path.yaml`
 *   → the result is an absolute filesystem path
 *
 * Repositories are updated by calling `update(repos)` from the shared
 * _refreshAll() in extension.ts.  The provider degrades gracefully when
 * the repo list is empty (no links shown until data arrives).
 */

import * as path from 'path';
import * as vscode from 'vscode';
import { RepositoryInfo } from '../strataClient';

// ---------------------------------------------------------------------------
// Regex — matches @repo_name/some/nested/file.yaml
//   group 1: repo name   (letters, digits, underscore, hyphen)
//   group 2: file path   (any combination of word chars, dots, slashes, ending in .yaml)
// ---------------------------------------------------------------------------
const REF_RE = /@([a-zA-Z0-9_-]+)\/([\w./%-]+\.yaml)\b/g;

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class CrossReferenceProvider
    implements
    vscode.DocumentLinkProvider,
    vscode.HoverProvider,
    vscode.RenameProvider,
    vscode.Disposable {

    private _repos: RepositoryInfo[] = [];
    private _disposables: vscode.Disposable[] = [];

    // -------------------------------------------------------------------------
    // Lifecycle
    // -------------------------------------------------------------------------

    register(context: vscode.ExtensionContext): void {
        this._disposables.push(
            vscode.languages.registerDocumentLinkProvider({ language: 'yaml' }, this),
            vscode.languages.registerHoverProvider({ language: 'yaml' }, this),
            vscode.languages.registerRenameProvider({ language: 'yaml' }, this),
        );
        context.subscriptions.push(...this._disposables);
    }

    update(repos: RepositoryInfo[]): void {
        this._repos = repos;
    }

    dispose(): void {
        this._disposables.forEach((d) => d.dispose());
        this._disposables = [];
    }

    // -------------------------------------------------------------------------
    // DocumentLinkProvider — Ctrl+Click
    // -------------------------------------------------------------------------

    provideDocumentLinks(document: vscode.TextDocument): vscode.DocumentLink[] {
        const links: vscode.DocumentLink[] = [];

        for (let i = 0; i < document.lineCount; i++) {
            const lineText = document.lineAt(i).text;
            const re = new RegExp(REF_RE.source, 'g');
            let m: RegExpExecArray | null;

            while ((m = re.exec(lineText)) !== null) {
                const resolved = this._resolve(m[1], m[2]);
                if (!resolved) continue;

                const startChar = m.index;
                const endChar = startChar + m[0].length;
                const range = new vscode.Range(i, startChar, i, endChar);
                const link = new vscode.DocumentLink(range, vscode.Uri.file(resolved));
                link.tooltip = resolved;
                links.push(link);
            }
        }

        return links;
    }

    // -------------------------------------------------------------------------
    // HoverProvider — shows resolved path + kind
    // -------------------------------------------------------------------------

    async provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<vscode.Hover | null> {
        const hit = this._hitTest(document.lineAt(position), position.character);
        if (!hit) return null;

        const { repoName, refPath, fullRef, startChar, endChar } = hit;
        const hoverRange = new vscode.Range(position.line, startChar, position.line, endChar);
        const resolved = this._resolve(repoName, refPath);

        if (!resolved) {
            // Repo name is present in the YAML but not found in the workspace registry.
            const md = new vscode.MarkdownString(
                `**Strata cross-reference** \`${fullRef}\`\n\n` +
                `⚠️ Repository \`${repoName}\` is not registered in this workspace.\n\n` +
                `Run \`strata repo add\` to register it.`,
            );
            return new vscode.Hover(md, hoverRange);
        }

        const kind = await this._readKind(resolved);
        const md = new vscode.MarkdownString('**Strata cross-reference**\n\n');
        md.appendMarkdown(`- **Path:** \`${resolved}\`\n`);
        if (kind) {
            md.appendMarkdown(`- **Kind:** \`${kind}\`\n`);
        }
        md.appendMarkdown('\n*Ctrl+Click to open — F2 to rename across workspace*');
        return new vscode.Hover(md, hoverRange);
    }

    // -------------------------------------------------------------------------
    // RenameProvider — F2 propagates across all YAML files in workspace
    // -------------------------------------------------------------------------

    prepareRename(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): vscode.Range | null {
        const hit = this._hitTest(document.lineAt(position), position.character);
        if (!hit) return null;
        return new vscode.Range(position.line, hit.startChar, position.line, hit.endChar);
    }

    async provideRenameEdits(
        document: vscode.TextDocument,
        position: vscode.Position,
        newName: string,
    ): Promise<vscode.WorkspaceEdit | null> {
        const hit = this._hitTest(document.lineAt(position), position.character);
        if (!hit) return null;

        const oldRef = hit.fullRef;                  // e.g. @infra/modules/api.yaml
        const edit = new vscode.WorkspaceEdit();

        // Search all YAML files in the workspace
        const yamlUris = await vscode.workspace.findFiles('**/*.yaml', '**/node_modules/**');

        for (const uri of yamlUris) {
            const doc = await vscode.workspace.openTextDocument(uri);
            for (let i = 0; i < doc.lineCount; i++) {
                const lineText = doc.lineAt(i).text;
                let idx = 0;
                while ((idx = lineText.indexOf(oldRef, idx)) !== -1) {
                    edit.replace(uri, new vscode.Range(i, idx, i, idx + oldRef.length), newName);
                    idx += oldRef.length;
                }
            }
        }

        return edit;
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    /**
     * Given a line and a character offset, return the cross-reference match
     * the cursor is inside (or null if none).
     */
    private _hitTest(
        line: vscode.TextLine,
        charOffset: number,
    ): { repoName: string; refPath: string; fullRef: string; startChar: number; endChar: number } | null {
        const re = new RegExp(REF_RE.source, 'g');
        let m: RegExpExecArray | null;
        while ((m = re.exec(line.text)) !== null) {
            const startChar = m.index;
            const endChar = startChar + m[0].length;
            if (charOffset >= startChar && charOffset <= endChar) {
                return {
                    repoName: m[1],
                    refPath: m[2],
                    fullRef: m[0],
                    startChar,
                    endChar,
                };
            }
        }
        return null;
    }

    /**
     * Resolve `@repo_name/ref_path` to an absolute filesystem path.
     * Returns null if the repo is not in the known list.
     */
    private _resolve(repoName: string, refPath: string): string | null {
        const repo = this._repos.find((r) => r.name === repoName);
        if (!repo?.path) return null;
        return path.join(repo.path, refPath);
    }

    /**
     * Try to read the `kind:` value from the first 20 lines of a file.
     * Silently returns null if the file cannot be opened.
     */
    private async _readKind(filePath: string): Promise<string | null> {
        try {
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
            for (let i = 0; i < Math.min(doc.lineCount, 20); i++) {
                const m = doc.lineAt(i).text.trim().match(/^kind:\s*(\S+)/);
                if (m) return m[1];
            }
        } catch {
            // file doesn't exist or can't be read — silent
        }
        return null;
    }
}
