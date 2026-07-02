// Import the vscode-test-cli
import { defineConfig } from '@vscode/test-cli';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    // Tests live outside the extension root — reference from here
    files: path.resolve(__dirname, '../../tests/vscode/suite/**/*.test.js'),
    extensionDevelopmentPath: __dirname,
    workspaceFolder: path.resolve(__dirname, '../../'),
    mocha: {
        ui: 'tdd',
        timeout: 20000,
    },
});
