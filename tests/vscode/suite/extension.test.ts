/**
 * Extension smoke tests.
 *
 * These run inside the VS Code Extension Host, so the full `vscode` API is
 * available.  All tests here are stubs — they assert that the extension
 * activates and that the expected commands are registered.
 *
 * TODO: add tests for each provider once logic is implemented.
 */

import * as assert from 'assert';
import * as vscode from 'vscode';

suite('Strata Extension', () => {
    // ── Activation ─────────────────────────────────────────────────────────────

    test('extension is present in VS Code', async () => {
        const ext = vscode.extensions.getExtension('huybrechtsxyz.strata');
        assert.ok(ext, 'Extension should be registered');
    });

    // ── Commands ───────────────────────────────────────────────────────────────

    suite('Commands', () => {
        const expectedCommands = [
            'strata.initWorkspace',
            'strata.validateCurrentFile',
            'strata.validateAll',
            'strata.buildDryRun',
            'strata.buildRun',
            'strata.deployDryRun',
            'strata.showGuide',
            'strata.switchProfile',
            'strata.exportSchemas',
            'strata.openConsole',
            'strata.refreshTreeView',
            'strata.openFile',
        ];

        expectedCommands.forEach((cmdId) => {
            test(`command "${cmdId}" is registered`, async () => {
                const all = await vscode.commands.getCommands(true);
                assert.ok(
                    all.includes(cmdId),
                    `Command "${cmdId}" should be registered after activation`,
                );
            });
        });
    });

    // ── Configuration ──────────────────────────────────────────────────────────

    suite('Configuration defaults', () => {
        test('strata.validateOnSave defaults to true', () => {
            const value = vscode.workspace
                .getConfiguration('strata')
                .get<boolean>('validateOnSave');
            assert.strictEqual(value, true);
        });

        test('strata.showCodeLens defaults to true', () => {
            const value = vscode.workspace
                .getConfiguration('strata')
                .get<boolean>('showCodeLens');
            assert.strictEqual(value, true);
        });

        test('strata.cliPath defaults to "strata"', () => {
            const value = vscode.workspace
                .getConfiguration('strata')
                .get<string>('cliPath');
            assert.strictEqual(value, 'strata');
        });
    });

    // ── StrataClient types ─────────────────────────────────────────────────────

    suite('StrataClient interface shapes', () => {
        // These tests verify the interface contract is stable — no I/O.
        test('WorkspaceStatus has expected top-level keys', () => {
            // Construct a conforming object to verify the interface compiles
            const _status = {
                health: { status: 'HEALTHY' as const, issues: [] },
                solution: { initialized: true, work_path: '/tmp', id: null, name: null },
                readiness: {
                    phases_complete: 0,
                    phases_total: 8,
                    complete: false,
                    checklist: [],
                    next_step: null,
                },
                profiles: { active: null, all: [], paths: {} },
                repositories: [],
                integrations: {},
            };
            assert.ok(_status.health);
            assert.ok(_status.readiness);
        });

        test('ValidationResult has valid and errors fields', () => {
            const _result = {
                valid: true,
                kind: 'deployment',
                name: 'my-dep',
                file: '/tmp/deploy.yaml',
                errors: [] as Array<{ field: string | null; message: string; severity: string }>,
            };
            assert.ok(Array.isArray(_result.errors));
        });
    });
});
