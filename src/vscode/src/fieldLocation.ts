/**
 * fieldLocation.ts — map a diagnostic's dotted field path to a Range in its
 * YAML document.
 *
 * Trimmed port of v1's `diagnosticsProvider.ts::_findFieldRange` /
 * `_scanForKey` / `_keyRange`: v2's `Diagnostic.location` is the same shape
 * ("spec.stages.0.provisioner") this heuristic was built for, so the walk is
 * unchanged. Shared by the validate diagnostics provider and the graph
 * view's "open reference" command — both resolve the same kind of path
 * against an open document.
 *
 * Best-effort, not a parser: scans for the first `^\s*<key>\s*:` line,
 * walking from the most specific segment to its parents. Good enough for a
 * single-document strata YAML file; a document with the same key repeated
 * under different parents can land on the wrong one.
 */

import * as vscode from 'vscode';

/** Map a dotted field path (e.g. `spec.providers.0`) to a Range in `document`. */
export function findFieldRange(fieldPath: string, document: vscode.TextDocument): vscode.Range {
    const segments = fieldPath.split(/[.[\]]/).filter((s) => s && !/^\d+$/.test(s));

    if (segments.length === 0) {
        return new vscode.Range(0, 0, 0, document.lineAt(0).text.length);
    }

    for (let i = segments.length - 1; i >= 0; i--) {
        const line = scanForKey(segments[i], document);
        if (line !== -1) {
            return keyRange(line, segments[i], document);
        }
    }

    return new vscode.Range(0, 0, 0, document.lineAt(0).text.length);
}

function scanForKey(key: string, document: vscode.TextDocument): number {
    const pattern = new RegExp(`^\\s*${escapeRegex(key)}\\s*:`);
    for (let i = 0; i < document.lineCount; i++) {
        if (pattern.test(document.lineAt(i).text)) {
            return i;
        }
    }
    return -1;
}

function keyRange(lineNum: number, key: string, document: vscode.TextDocument): vscode.Range {
    const lineText = document.lineAt(lineNum).text;
    const keyStart = lineText.indexOf(key);
    if (keyStart === -1) {
        return new vscode.Range(lineNum, 0, lineNum, lineText.length);
    }
    const colonIdx = lineText.indexOf(':', keyStart + key.length);
    const valueStart = colonIdx !== -1 ? colonIdx + 1 : keyStart;
    const trimmedValue = lineText.slice(valueStart).trim();
    const valueOffset = trimmedValue.length > 0 ? lineText.lastIndexOf(trimmedValue) : keyStart;
    const end = valueOffset + trimmedValue.length || lineText.length;
    return new vscode.Range(lineNum, Math.max(0, valueOffset), lineNum, end);
}

function escapeRegex(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
